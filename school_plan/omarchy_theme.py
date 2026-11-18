#!/usr/bin/env python3
"""
Live Omarchy theme engine for the EMSI School-Plan Desktop Calendar Widget.

Follows Omarchy's official theme contract:
- Reads $HOME/.local/state/omarchy/current/theme/colors.toml (+ shell.toml, theme.name)
- Falls back to legacy $HOME/.config/omarchy/current ONLY when state/current does not exist
- Supports both semantic and ANSI (color0..color15) palettes
- Falls back atomically to built-in Tokyo Night if required roles are missing or invalid
- Derives contrast-safe surfaces, borders, foreground-tinted control states, and readable inks
  across dark, light, and monochrome themes
- Tracks live system font (`fc-match monospace`) and Hyprland corner rounding (`hyprctl`)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from typing import Callable

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_THEME_NAME = "Tokyo Night"
DEFAULT_FONT_FAMILY = "JetBrainsMono Nerd Font"

# Complete atomic fallback palette (Tokyo Night, per Omarchy Application Design §1.4)
DEFAULT_PALETTE: dict[str, str] = {
    "mode": "dark",
    "background": "#1a1b26",
    "dark_background": "#13141c",
    "darker_background": "#0e0e14",
    "lighter_background": "#24283b",
    "foreground": "#a9b1d6",
    "bright_foreground": "#c0caf5",
    "dark_foreground": "#565f89",
    "accent": "#7aa2f7",
    "selection": "#292e42",
    "muted": "#414868",
    "red": "#f7768e",
    "yellow": "#e0af68",
    "orange": "#eb927b",
    "green": "#9ece6a",
    "cyan": "#449dab",
    "blue": "#7aa2f7",
    "magenta": "#ad8ee6",
    "bright_red": "#ff7a93",
    "bright_yellow": "#ff9e64",
    "bright_green": "#b9f27c",
    "bright_cyan": "#0db9d7",
    "bright_blue": "#7da6ff",
    "bright_magenta": "#bb9af7",
}


@dataclass(frozen=True)
class OmarchyTheme:
    name: str
    mode: str
    font_family: str
    font_base: int
    radius: int
    small_radius: int
    colors: dict[str, str]


def is_valid_hex(value: object) -> bool:
    return isinstance(value, str) and bool(HEX_RE.match(value.strip()))


def _norm_hex(value: str) -> str:
    return value.strip().lower()


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return f"#{r:02x}{g:02x}{b:02x}"


def mix_hex(c1: str, c2: str, ratio: float) -> str:
    """Linearly mixes c1 toward c2 by ratio in [0.0, 1.0]."""
    t = max(0.0, min(1.0, float(ratio)))
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    return rgb_to_hex(
        r1 + (r2 - r1) * t,
        g1 + (g2 - g1) * t,
        b1 + (b2 - b1) * t,
    )


def rgba_css(hex_color: str, alpha: float) -> str:
    r, g, b = hex_to_rgb(hex_color)
    a = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {a:.3f})"


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance in [0.0, 1.0]."""
    r, g, b = hex_to_rgb(hex_color)

    def _lin(ch: int) -> float:
        s = ch / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(c1: str, c2: str) -> float:
    l1 = relative_luminance(c1)
    l2 = relative_luminance(c2)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


def pick_on_color(bg_hex: str) -> str:
    """Returns high-contrast black or white ink for a filled surface."""
    return "#0c0c0c" if relative_luminance(bg_hex) > 0.179 else "#ffffff"


def ensure_contrast(
    ink_hex: str,
    bg_hex: str,
    target_hex: str,
    min_ratio: float = 4.5,
) -> str:
    """Moves ink_hex minimally toward target_hex until contrast_ratio >= min_ratio."""
    if contrast_ratio(ink_hex, bg_hex) >= min_ratio:
        return _norm_hex(ink_hex)

    # Ensure the direction can actually reach min_ratio; if not, aim at pure black/white.
    aim = target_hex
    if contrast_ratio(aim, bg_hex) < min_ratio:
        aim = pick_on_color(bg_hex)

    lo, hi = 0.0, 1.0
    best = aim
    for _ in range(14):
        mid = (lo + hi) / 2.0
        cand = mix_hex(ink_hex, aim, mid)
        if contrast_ratio(cand, bg_hex) >= min_ratio:
            best = cand
            hi = mid
        else:
            lo = mid
    return _norm_hex(best)


def default_state_dir() -> Path:
    override = os.environ.get("SCHOOL_OMARCHY_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "omarchy" / "current"


def default_legacy_dir() -> Path:
    return Path.home() / ".config" / "omarchy" / "current"


def default_user_shell_toml() -> Path:
    return Path.home() / ".config" / "omarchy" / "shell.toml"


def resolve_current_dir(
    state_dir: Path | str | None = None,
    legacy_dir: Path | str | None = None,
) -> Path | None:
    """
    Returns active Omarchy current dir.
    Uses legacy_dir ONLY when state_dir does not exist at all.
    """
    s_dir = Path(state_dir) if state_dir is not None else default_state_dir()
    if s_dir.exists() or s_dir.is_symlink():
        return s_dir
    l_dir = Path(legacy_dir) if legacy_dir is not None else default_legacy_dir()
    if l_dir.exists() or l_dir.is_symlink():
        return l_dir
    return None


def _pick_hex(data: dict, *keys: str) -> str | None:
    for k in keys:
        if k in data:
            val = data[k]
            if not is_valid_hex(val):
                return None  # Invalid hex for a present key fails validation
            return _norm_hex(val)
    return None


def parse_colors_toml(path: Path) -> dict[str, str] | None:
    """
    Parses a semantic or ANSI colors.toml file.
    Returns normalized color dict if all required roles are valid #RRGGBB hex, else None.
    """
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None

    # Validate any present hex-like color key we read; invalid hex fails atomically.
    bg = _pick_hex(raw, "background")
    fg = _pick_hex(raw, "foreground")
    accent = _pick_hex(raw, "accent")
    red = _pick_hex(raw, "red", "color1")
    green = _pick_hex(raw, "green", "color2")
    yellow = _pick_hex(raw, "yellow", "color3")

    if not all((bg, fg, accent, red, green, yellow)):
        return None

    # Verify optional keys don't contain malformed hex values when present
    optional_map = {
        "dark_background": ("dark_background",),
        "darker_background": ("darker_background",),
        "lighter_background": ("lighter_background",),
        "bright_foreground": ("bright_foreground", "selection_foreground", "cursor"),
        "dark_foreground": ("dark_foreground",),
        "light_foreground": ("light_foreground",),
        "selection": ("selection", "selection_background"),
        "muted": ("muted", "color8"),
        "active_border_color": ("active_border_color",),
        "orange": ("orange",),
        "blue": ("blue", "color4"),
        "magenta": ("magenta", "color5"),
        "cyan": ("cyan", "color6"),
        "bright_red": ("bright_red", "color9"),
        "bright_green": ("bright_green", "color10"),
        "bright_yellow": ("bright_yellow", "color11"),
        "bright_blue": ("bright_blue", "color12"),
        "bright_magenta": ("bright_magenta", "color13"),
        "bright_cyan": ("bright_cyan", "color14"),
    }

    parsed: dict[str, str] = {
        "background": bg,
        "foreground": fg,
        "accent": accent,
        "red": red,
        "green": green,
        "yellow": yellow,
    }

    for dest, keys in optional_map.items():
        present_keys = [k for k in keys if k in raw]
        if present_keys:
            val = _pick_hex(raw, present_keys[0])
            if val is None:
                return None
            parsed[dest] = val

    mode = raw.get("mode")
    if isinstance(mode, str) and mode.strip().lower() in ("dark", "light"):
        parsed["mode"] = mode.strip().lower()
    else:
        parsed["mode"] = "light" if relative_luminance(bg) > relative_luminance(fg) else "dark"

    return parsed


def _derive_roles(raw: dict[str, str]) -> tuple[str, dict[str, str]]:
    bg = raw["background"]
    fg = raw["foreground"]
    accent = raw["accent"]
    mode = raw.get("mode") or ("light" if relative_luminance(bg) > relative_luminance(fg) else "dark")
    is_light = mode == "light"

    # Surface: use lighter_background when it subtly separates from bg; otherwise derive by mixing
    raw_surface = raw.get("lighter_background")
    if raw_surface:
        lum_delta = abs(relative_luminance(raw_surface) - relative_luminance(bg))
        max_delta = 0.12 if is_light else 0.16
        if 0.008 <= lum_delta <= max_delta:
            surface = raw_surface
        else:
            surface = mix_hex(bg, fg, 0.045 if is_light else 0.055)
    else:
        surface = mix_hex(bg, fg, 0.045 if is_light else 0.055)

    surface_hover = mix_hex(surface, fg, 0.055 if is_light else 0.065)

    raw_inset = raw.get("dark_background")
    if raw_inset and abs(relative_luminance(raw_inset) - relative_luminance(bg)) <= 0.18:
        inset = raw_inset
    else:
        inset = mix_hex(bg, fg, 0.08)

    bright = raw.get("bright_foreground") or fg
    secondary = ensure_contrast(mix_hex(bg, fg, 0.72), surface, fg, min_ratio=4.6)
    muted = raw.get("muted") or mix_hex(bg, fg, 0.25)
    border = muted
    outer_border = raw.get("active_border_color") or accent
    selection = raw.get("selection") or mix_hex(bg, accent, 0.20)
    on_accent = pick_on_color(accent)

    red = raw["red"]
    yellow = raw["yellow"]
    orange = raw.get("orange") or yellow
    green = raw["green"]
    cyan = raw.get("cyan") or accent
    blue = raw.get("blue") or accent
    magenta = raw.get("magenta") or accent

    bright_cyan = raw.get("bright_cyan") or cyan
    bright_magenta = raw.get("bright_magenta") or magenta

    # Status and accent text colors guaranteed WCAG AA readable on both surface and bg
    ink_target = "#0c0c0c" if is_light else fg
    danger_ink = ensure_contrast(red, surface, ink_target, min_ratio=4.6)
    danger_ink = ensure_contrast(danger_ink, bg, ink_target, min_ratio=4.6)

    warning_ink = ensure_contrast(yellow, surface, ink_target, min_ratio=4.6)
    warning_ink = ensure_contrast(warning_ink, bg, ink_target, min_ratio=4.6)

    success_ink = ensure_contrast(green, surface, ink_target, min_ratio=4.6)
    success_ink = ensure_contrast(success_ink, bg, ink_target, min_ratio=4.6)

    accent_ink = ensure_contrast(accent, surface, ink_target, min_ratio=4.6)
    accent_ink = ensure_contrast(accent_ink, bg, ink_target, min_ratio=4.6)

    # 2px module rails: keep clearly visible against card surface (>= 2.2:1 non-text UI contrast)
    def _rail(col: str) -> str:
        return ensure_contrast(col, surface, fg, min_ratio=2.3)

    colors = {
        "bg": bg,
        "surface": surface,
        "surface_hover": surface_hover,
        "inset": inset,
        "fg": ensure_contrast(fg, surface, ink_target, min_ratio=4.6),
        "bright": ensure_contrast(bright, surface, ink_target, min_ratio=4.6),
        "secondary": secondary,
        "muted": muted,
        "border": border,
        "outer_border": outer_border,
        "selection": selection,
        "accent": accent,
        "accent_ink": accent_ink,
        "on_accent": on_accent,
        "danger": red,
        "danger_ink": danger_ink,
        "warning": yellow,
        "warning_ink": warning_ink,
        "success": green,
        "success_ink": success_ink,
        # Module left-rail slots mapped purely from active Omarchy theme ANSI palette
        "rail_teal": _rail(cyan),
        "rail_cyan": _rail(bright_cyan),
        "rail_amber": _rail(orange),
        "rail_bronze": _rail(yellow),
        "rail_emerald": _rail(green),
        "rail_indigo": _rail(accent),
        "rail_cobalt": _rail(blue),
        "rail_plum": _rail(magenta),
        "rail_violet": _rail(bright_magenta),
        "rail_rose": _rail(red),
        "rail_slate": _rail(border),
    }
    return mode, colors


def _read_shell_font_size(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        font_sec = data.get("font")
        if isinstance(font_sec, dict) and "base-size" in font_sec:
            val = int(round(float(font_sec["base-size"])))
            if val > 0:
                return val
    except Exception:
        pass
    return None


def parse_shell_font_base(
    theme_dir: Path | None = None,
    user_shell_toml: Path | None = None,
) -> int:
    """
    Reads [font] base-size from theme shell.toml and user ~/.config/omarchy/shell.toml.
    Floors at 12px so compact bar font settings (e.g. 9px) do not shrink calendar copy
    below readable size, while honoring larger accessibility sizes up to 16px.
    """
    val = _read_shell_font_size(theme_dir / "shell.toml") if theme_dir else None
    user_val = _read_shell_font_size(user_shell_toml)
    if user_val is not None:
        val = user_val
    if val is None:
        return 12
    return max(12, min(16, val))


def detect_system_font(fc_match_bin: str | None = "fc-match") -> str:
    """Resolves the active system monospace font family via fc-match."""
    if not fc_match_bin:
        return DEFAULT_FONT_FAMILY
    exe = shutil.which(fc_match_bin) if os.path.sep not in fc_match_bin else fc_match_bin
    if not exe or not os.path.exists(exe):
        return DEFAULT_FONT_FAMILY
    try:
        proc = subprocess.run(
            [exe, "monospace", "-f", "%{family}\n"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.splitlines()[0].strip()
            family = first_line.split(",")[0].strip()
            if family:
                return family
    except Exception:
        pass
    return DEFAULT_FONT_FAMILY


def detect_hyprland_radius(hyprctl_bin: str | None = None) -> int:
    """Queries effective Hyprland decoration:rounding; defaults to 0 (Omarchy square)."""
    exe = hyprctl_bin
    if exe is None:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            exe = shutil.which("hyprctl") or ""
        else:
            exe = ""
    if not exe or not os.path.exists(exe):
        return 0
    try:
        proc = subprocess.run(
            [exe, "-j", "getoption", "decoration:rounding"],
            capture_output=True,
            text=True,
            timeout=0.8,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            val = data.get("int")
            if isinstance(val, (int, float)) and val >= 0:
                return int(val)
    except Exception:
        pass
    return 0


def load_theme(
    state_dir: Path | str | None = None,
    legacy_dir: Path | str | None = None,
    user_shell_toml: Path | str | None = None,
    hyprctl_bin: str | None = None,
    fc_match_bin: str | None = "fc-match",
) -> OmarchyTheme:
    """Loads the active Omarchy theme or falls back atomically to built-in Tokyo Night."""
    cur_dir = resolve_current_dir(state_dir=state_dir, legacy_dir=legacy_dir)
    theme_dir = (cur_dir / "theme") if cur_dir is not None else None
    parsed = parse_colors_toml(theme_dir / "colors.toml") if theme_dir is not None else None

    if parsed is None:
        name = DEFAULT_THEME_NAME
        raw_palette = dict(DEFAULT_PALETTE)
    else:
        raw_palette = parsed
        name = "Omarchy"
        name_file = cur_dir / "theme.name" if cur_dir else None
        if name_file and name_file.is_file():
            try:
                txt = name_file.read_text(encoding="utf-8").strip()
                if txt:
                    name = txt
            except Exception:
                pass

    mode, colors = _derive_roles(raw_palette)
    u_shell = Path(user_shell_toml) if user_shell_toml is not None else default_user_shell_toml()
    font_base = parse_shell_font_base(theme_dir if parsed is not None else None, u_shell)
    font_family = detect_system_font(fc_match_bin=fc_match_bin)
    radius = detect_hyprland_radius(hyprctl_bin=hyprctl_bin)
    small_radius = min(4, radius)

    return OmarchyTheme(
        name=name,
        mode=mode,
        font_family=font_family,
        font_base=font_base,
        radius=radius,
        small_radius=small_radius,
        colors=colors,
    )


def render_theme_css(theme: OmarchyTheme) -> str:
    """Generates GTK4 CSS @define-color tokens, font family, type scale, and corner radii."""
    c = theme.colors
    fg = c["fg"]
    bg = c["bg"]
    is_light = theme.mode == "light"

    # Shared Omarchy control states (foreground-tinted alphas per design-guides.md)
    ctrl_normal_bg = rgba_css(fg, 0.04)
    ctrl_hover_bg = rgba_css(fg, 0.08)
    ctrl_selected_bg = rgba_css(fg, 0.18)
    ctrl_pressed_bg = rgba_css(fg, 0.22)
    ctrl_border = rgba_css(fg, 0.36 if is_light else 0.32)
    ctrl_hover_border = rgba_css(fg, 0.45 if is_light else 0.30)
    hairline = rgba_css(fg, 0.14 if is_light else 0.12)
    card_border = rgba_css(fg, 0.11 if is_light else 0.09)
    scroll_thumb = rgba_css(fg, 0.32)
    scroll_thumb_hover = rgba_css(fg, 0.52)

    panel_bg = rgba_css(bg, 0.96)
    popup_bg = rgba_css(bg, 0.98)
    today_tint = rgba_css(c["accent"], 0.07 if is_light else 0.06)
    accent_tint = rgba_css(c["accent"], 0.12)
    danger_tint = rgba_css(c["danger"], 0.10)
    danger_border = rgba_css(c["danger"], 0.55)
    warn_tint = rgba_css(c["warning"], 0.12)
    warn_border = rgba_css(c["warning"], 0.60)
    ok_tint = rgba_css(c["success"], 0.12)
    text_sel_bg = rgba_css(fg, 0.30)

    # Scale type ramp relative to font_base (baseline 12)
    scale = theme.font_base / 12.0
    sz_cap = max(10, round(10 * scale))
    sz_sm = max(11, round(11 * scale))
    sz_body = max(12, round(12 * scale))
    sz_sub = max(13, round(13 * scale))
    sz_title = max(14, round(14 * scale))

    r = theme.radius
    sr = theme.small_radius
    safe_font = theme.font_family.replace('"', "")

    lines = [
        f"/* Auto-generated Omarchy theme tokens: {theme.name} ({theme.mode}) */",
        f"@define-color bg {bg};",
        f"@define-color panel_bg {panel_bg};",
        f"@define-color popup_bg {popup_bg};",
        f"@define-color surface {c['surface']};",
        f"@define-color surface_hover {c['surface_hover']};",
        f"@define-color inset {c['inset']};",
        f"@define-color fg {fg};",
        f"@define-color bright {c['bright']};",
        f"@define-color secondary {c['secondary']};",
        f"@define-color muted {c['muted']};",
        f"@define-color border {c['border']};",
        f"@define-color outer_border {c['outer_border']};",
        f"@define-color selection {c['selection']};",
        f"@define-color accent {c['accent']};",
        f"@define-color accent_ink {c['accent_ink']};",
        f"@define-color on_accent {c['on_accent']};",
        f"@define-color danger {c['danger']};",
        f"@define-color danger_ink {c['danger_ink']};",
        f"@define-color warning {c['warning']};",
        f"@define-color warning_ink {c['warning_ink']};",
        f"@define-color success {c['success']};",
        f"@define-color success_ink {c['success_ink']};",
        f"@define-color ctrl_normal_bg {ctrl_normal_bg};",
        f"@define-color ctrl_hover_bg {ctrl_hover_bg};",
        f"@define-color ctrl_selected_bg {ctrl_selected_bg};",
        f"@define-color ctrl_pressed_bg {ctrl_pressed_bg};",
        f"@define-color ctrl_border {ctrl_border};",
        f"@define-color ctrl_hover_border {ctrl_hover_border};",
        f"@define-color hairline {hairline};",
        f"@define-color card_border {card_border};",
        f"@define-color scroll_thumb {scroll_thumb};",
        f"@define-color scroll_thumb_hover {scroll_thumb_hover};",
        f"@define-color today_tint {today_tint};",
        f"@define-color accent_tint {accent_tint};",
        f"@define-color danger_tint {danger_tint};",
        f"@define-color danger_border {danger_border};",
        f"@define-color warn_tint {warn_tint};",
        f"@define-color warn_border {warn_border};",
        f"@define-color ok_tint {ok_tint};",
        f"@define-color text_sel_bg {text_sel_bg};",
        f"@define-color rail_teal {c['rail_teal']};",
        f"@define-color rail_cyan {c['rail_cyan']};",
        f"@define-color rail_amber {c['rail_amber']};",
        f"@define-color rail_bronze {c['rail_bronze']};",
        f"@define-color rail_emerald {c['rail_emerald']};",
        f"@define-color rail_indigo {c['rail_indigo']};",
        f"@define-color rail_cobalt {c['rail_cobalt']};",
        f"@define-color rail_plum {c['rail_plum']};",
        f"@define-color rail_violet {c['rail_violet']};",
        f"@define-color rail_rose {c['rail_rose']};",
        f"@define-color rail_slate {c['rail_slate']};",
        "",
        f'* {{ font-family: "{safe_font}", "JetBrainsMono Nerd Font", monospace; font-size: {sz_body}px; }}',
        "",
        "/* Dynamic Omarchy corner rounding & font scale */",
        f".glass-panel-grid, .glass-panel-list, .glass-panel-compact, .edit-panel {{ border-radius: {r}px; }}",
        f".cal-grid-frame, .cal-event, .task-card, .toast-bar, .help-bar {{ border-radius: {sr}px; }}",
        f"button, .form-entry, .keycap, .hdr-alert-mismatch {{ border-radius: {sr}px; }}",
        f".brand-title, .edit-header-title {{ font-size: {sz_sub}px; }}",
        f".ev-title, .task-title {{ font-size: {sz_body}px; }}",
        f".cal-day-num {{ font-size: {sz_title}px; }}",
        f".task-meta, .warning-desc, .form-entry, .form-field-label, button.btn-view, .week-range-lbl, .toast-text, button.btn-action-chip, .stat-item, button.btn-primary, button.btn-secondary, button.btn-danger, button.btn-browse {{ font-size: {sz_sm}px; }}",
        f".sub-header, .cal-weekday, .ev-mod-tag, .ev-status-exam, .ev-status-late, .ev-status-mismatch, .ev-ai-mark, .ev-time, .cal-done-sep, .ev-done-check, .ev-title-done-slim, .badge-submitted, .badge-deadline, .badge-deadline-urgent, .section-header, .hdr-alert-mismatch, button.btn-exam-jump, button.btn-today, button.btn-toggle-done, .keycap, .help-text, .compact-summary, .edit-header-sub, .ai-provenance-note {{ font-size: {sz_cap}px; }}",
    ]
    return "\n".join(lines) + "\n"


def build_full_css(theme: OmarchyTheme, style_path: Path | None = None) -> bytes:
    """Combines live Omarchy @define-color tokens with structural widget_style.css."""
    tokens_css = render_theme_css(theme)
    body_css = ""
    if style_path is not None and Path(style_path).is_file():
        body_css = Path(style_path).read_text(encoding="utf-8")
    # Put @define-color definitions BEFORE widget_style.css, then append dynamic font/radius overrides AFTER
    split_marker = "/* Dynamic Omarchy corner rounding & font scale */"
    if split_marker in tokens_css:
        defs_part, overrides_part = tokens_css.split(split_marker, 1)
        combined = f"{defs_part}\n{body_css}\n{split_marker}{overrides_part}"
    else:
        combined = f"{tokens_css}\n{body_css}\n"
    return combined.encode("utf-8")


class ThemeWatcher:
    """
    Watches Omarchy's active theme, fontconfig, shell overrides, and Hyprland rounding.
    Re-arms file monitors after each change because `omarchy theme set` replaces files/symlinks.
    """

    def __init__(
        self,
        on_changed: Callable[[OmarchyTheme], None],
        state_dir: Path | None = None,
        legacy_dir: Path | None = None,
        user_shell_toml: Path | None = None,
    ):
        from gi.repository import Gio, GLib  # Imported lazily so headless tests need no GTK

        self._Gio = Gio
        self._GLib = GLib
        self._on_changed = on_changed
        self._state_dir = state_dir or default_state_dir()
        self._legacy_dir = legacy_dir or default_legacy_dir()
        self._user_shell = user_shell_toml or default_user_shell_toml()

        self._monitors: list = []
        self._debounce_id: int | None = None
        self._poll_id: int | None = None
        self._poll_count: int = 0
        self._stopped = False

        self.current: OmarchyTheme = load_theme(
            state_dir=self._state_dir,
            legacy_dir=self._legacy_dir,
            user_shell_toml=self._user_shell,
        )
        self._cached_font: str = self.current.font_family
        self._cached_radius: int = self.current.radius
        self._last_sig: tuple = self._fs_signature()
        self._arm_monitors()
        # Lightweight 2s poll: pure stat() check for theme/file swaps; hyprctl every 6s
        self._poll_id = self._GLib.timeout_add_seconds(2, self._poll_tick)

    def _fs_signature(self) -> tuple:
        sig = []
        for p in (
            self._state_dir / "theme.generation",
            self._state_dir / "theme" / "colors.toml",
            self._state_dir / "theme.name",
            self._user_shell,
            Path.home() / ".config" / "fontconfig" / "fonts.conf",
        ):
            try:
                st = p.stat()
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        return tuple(sig)

    def _clear_monitors(self) -> None:
        for mon in self._monitors:
            try:
                mon.cancel()
            except Exception:
                pass
        self._monitors.clear()

    def _watch_path(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            gfile = self._Gio.File.new_for_path(str(path))
            if path.is_dir():
                mon = gfile.monitor_directory(self._Gio.FileMonitorFlags.NONE, None)
            else:
                mon = gfile.monitor_file(self._Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", self._on_fs_event)
            self._monitors.append(mon)
        except Exception:
            pass

    def _arm_monitors(self) -> None:
        self._clear_monitors()
        candidates: list[Path] = []

        # Watch nearest existing ancestor of state_dir so creating/replacing current triggers reload
        anc = self._state_dir
        while not anc.exists() and anc != anc.parent:
            anc = anc.parent
        candidates.extend(
            [
                anc,
                anc.parent,
                self._state_dir,
                self._state_dir / "theme",
                self._state_dir / "theme" / "colors.toml",
                self._state_dir / "theme.generation",
                self._user_shell.parent,
                self._user_shell,
                Path.home() / ".config" / "fontconfig",
                Path.home() / ".config" / "fontconfig" / "fonts.conf",
            ]
        )

        seen: set[Path] = set()
        for p in candidates:
            if p not in seen and p.exists():
                seen.add(p)
                self._watch_path(p)

    def _on_fs_event(self, _monitor, _file, _other, _event_type) -> None:
        if self._stopped:
            return
        if self._debounce_id is not None:
            self._GLib.source_remove(self._debounce_id)
        self._debounce_id = self._GLib.timeout_add(80, self._debounced_reload)

    def _debounced_reload(self) -> bool:
        self._debounce_id = None
        if not self._stopped:
            self.reload(rearm=True)
        return False

    def _poll_tick(self) -> bool:
        if self._stopped:
            return False
        self._poll_count += 1
        sig = self._fs_signature()
        if sig != self._last_sig:
            self.reload(rearm=True, refresh_font=True, refresh_radius=True)
            return True
        # Check Hyprland rounding every ~6s without running fc-match
        if self._poll_count % 3 == 0:
            new_radius = detect_hyprland_radius()
            if new_radius != self._cached_radius:
                self._cached_radius = new_radius
                self.reload(rearm=False, refresh_font=False, refresh_radius=False)
        return True

    def reload(
        self,
        rearm: bool = True,
        force: bool = False,
        refresh_font: bool = True,
        refresh_radius: bool = True,
    ) -> bool:
        if rearm:
            self._arm_monitors()
        self._last_sig = self._fs_signature()
        if refresh_font:
            self._cached_font = detect_system_font()
        if refresh_radius:
            self._cached_radius = detect_hyprland_radius()
        base_theme = load_theme(
            state_dir=self._state_dir,
            legacy_dir=self._legacy_dir,
            user_shell_toml=self._user_shell,
            hyprctl_bin="",
            fc_match_bin="",
        )
        new_theme = OmarchyTheme(
            name=base_theme.name,
            mode=base_theme.mode,
            font_family=self._cached_font,
            font_base=base_theme.font_base,
            radius=self._cached_radius,
            small_radius=min(4, self._cached_radius),
            colors=base_theme.colors,
        )
        if force or new_theme != self.current:
            self.current = new_theme
            try:
                self._on_changed(new_theme)
            except Exception:
                pass
            return True
        return False

    def stop(self) -> None:
        self._stopped = True
        self._clear_monitors()
        if self._debounce_id is not None:
            self._GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        if self._poll_id is not None:
            self._GLib.source_remove(self._poll_id)
            self._poll_id = None
