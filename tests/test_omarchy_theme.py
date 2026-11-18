#!/usr/bin/env python3
"""Offline unit tests for omarchy_theme.py (palette resolution, contrast, rounding, CSS)."""

import os
from pathlib import Path
import stat
import sys

import pytest

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import omarchy_theme  # noqa: E402


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_semantic_dark_theme_and_derives_distinct_surface_and_contrast(tmp_path):
    """last-horizon sets lighter_background == background and dark yellow #6B5E73."""
    state_dir = tmp_path / "state" / "current"
    _write_file(state_dir / "theme.name", "last-horizon\n")
    _write_file(
        state_dir / "theme" / "colors.toml",
        """
mode = "dark"
accent = "#b59790"
selection = "#584e51"
muted = "#584e51"
background = "#0c0b0c"
dark_background = "#090809"
lighter_background = "#0c0b0c"
foreground = "#FAFCFB"
bright_foreground = "#e2dddc"
active_border_color = "#d6d3de"
red = "#c38b7b"
yellow = "#6B5E73"
green = "#87a9b0"
cyan = "#a5a0b6"
blue = "#b59790"
magenta = "#c4d8e2"
""".strip(),
    )

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=tmp_path / "legacy",
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )

    assert theme.name == "last-horizon"
    assert theme.mode == "dark"
    assert theme.colors["bg"] == "#0c0b0c"
    assert theme.colors["surface"] != theme.colors["bg"]
    assert theme.colors["outer_border"] == "#d6d3de"
    # Even dark plum yellow (#6B5E73) must be lifted to readable >= 4.5:1 ink on surface
    assert omarchy_theme.contrast_ratio(theme.colors["warning_ink"], theme.colors["surface"]) >= 4.5
    assert omarchy_theme.contrast_ratio(theme.colors["danger_ink"], theme.colors["surface"]) >= 4.5
    assert omarchy_theme.contrast_ratio(theme.colors["secondary"], theme.colors["surface"]) >= 4.5
    assert omarchy_theme.contrast_ratio(theme.colors["on_accent"], theme.colors["accent"]) >= 4.5


def test_loads_ansi_only_palette(tmp_path):
    state_dir = tmp_path / "state" / "current"
    _write_file(state_dir / "theme.name", "ansi-custom\n")
    _write_file(
        state_dir / "theme" / "colors.toml",
        """
background = "#101218"
foreground = "#e0e4f0"
accent = "#5ea1ff"
color1 = "#ff6e5e"
color2 = "#5eff6c"
color3 = "#f1ff5e"
color4 = "#5ea1ff"
color5 = "#bd5eff"
color6 = "#5ef1ff"
color8 = "#495162"
""".strip(),
    )

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=tmp_path / "legacy",
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )

    assert theme.name == "ansi-custom"
    assert theme.mode == "dark"
    assert theme.colors["danger"] == "#ff6e5e"
    assert theme.colors["success"] == "#5eff6c"
    assert theme.colors["rail_cobalt"] == "#5ea1ff"
    assert theme.colors["rail_plum"] == "#bd5eff"


def test_light_theme_darkens_status_inks_and_avoids_overdark_surface(tmp_path):
    state_dir = tmp_path / "state" / "current"
    _write_file(state_dir / "theme.name", "white\n")
    _write_file(
        state_dir / "theme" / "colors.toml",
        """
mode = "light"
accent = "#6e6e6e"
background = "#ffffff"
lighter_background = "#c0c0c0"
foreground = "#000000"
muted = "#808080"
red = "#d20f39"
yellow = "#f9e2af"
green = "#a6e3a1"
cyan = "#179299"
blue = "#1e66f5"
magenta = "#ea76cb"
""".strip(),
    )

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=tmp_path / "legacy",
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )

    assert theme.mode == "light"
    # Surface should stay bright/subtle on a #ffffff canvas rather than muddy #c0c0c0
    assert omarchy_theme.relative_luminance(theme.colors["surface"]) > 0.85
    # Pale yellow & green must be darkened until readable on light surface
    assert omarchy_theme.contrast_ratio(theme.colors["warning_ink"], theme.colors["surface"]) >= 4.5
    assert omarchy_theme.contrast_ratio(theme.colors["success_ink"], theme.colors["surface"]) >= 4.5
    assert omarchy_theme.contrast_ratio(theme.colors["danger_ink"], theme.colors["surface"]) >= 4.5


def test_broken_or_partial_theme_falls_back_atomically_without_reviving_legacy(tmp_path):
    state_dir = tmp_path / "state" / "current"
    legacy_dir = tmp_path / "legacy" / "current"

    # Legacy has a valid theme, but state_dir exists with broken colors.toml
    _write_file(
        legacy_dir / "theme" / "colors.toml",
        """
background = "#111111"
foreground = "#eeeeee"
accent = "#123456"
red = "#ff0000"
yellow = "#ffff00"
green = "#00ff00"
""".strip(),
    )
    _write_file(
        state_dir / "theme" / "colors.toml",
        """
background = "#222222"
foreground = "#eeeeee"
accent = "not-a-hex"
red = "#ff0000"
yellow = "#ffff00"
green = "#00ff00"
""".strip(),
    )

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=legacy_dir,
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )

    # Atomic fallback to built-in Tokyo Night — must NOT mix #222222 or revive legacy #111111
    assert theme.name == "Tokyo Night"
    assert theme.colors["bg"] == omarchy_theme.DEFAULT_PALETTE["background"]
    assert theme.colors["accent"] == omarchy_theme.DEFAULT_PALETTE["accent"]


def test_legacy_path_used_only_when_state_current_absent(tmp_path):
    state_dir = tmp_path / "state" / "current"  # does not exist
    legacy_dir = tmp_path / "legacy" / "current"
    _write_file(legacy_dir / "theme.name", "legacy-ok\n")
    _write_file(
        legacy_dir / "theme" / "colors.toml",
        """
background = "#14161b"
foreground = "#e2e6f0"
accent = "#45a1ff"
red = "#e06c75"
yellow = "#e5c07b"
green = "#98c379"
""".strip(),
    )

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=legacy_dir,
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )

    assert theme.name == "legacy-ok"
    assert theme.colors["bg"] == "#14161b"
    assert theme.colors["accent"] == "#45a1ff"


def test_hyprland_rounding_follows_fake_hyprctl(tmp_path):
    resp_file = tmp_path / "resp.json"
    fake_hyprctl = tmp_path / "hyprctl"
    fake_hyprctl.write_text(f"#!/bin/sh\ncat '{resp_file}'\n", encoding="utf-8")
    fake_hyprctl.chmod(fake_hyprctl.stat().st_mode | stat.S_IXUSR)

    resp_file.write_text('{"int": 0}\n', encoding="utf-8")
    assert omarchy_theme.detect_hyprland_radius(str(fake_hyprctl)) == 0

    resp_file.write_text('{"int": 8}\n', encoding="utf-8")
    assert omarchy_theme.detect_hyprland_radius(str(fake_hyprctl)) == 8


def test_shell_font_base_respects_user_override_and_readable_floor(tmp_path):
    theme_dir = tmp_path / "theme"
    user_shell = tmp_path / "user-shell.toml"
    _write_file(theme_dir / "shell.toml", "[font]\nbase-size = 12\n")

    # Small bar font override (base-size = 9) must stay floored at readable 12px for calendar
    _write_file(user_shell, "[font]\nbase-size = 9\n")
    assert omarchy_theme.parse_shell_font_base(theme_dir, user_shell) == 12

    # Larger accessibility font override (base-size = 15) scales up
    _write_file(user_shell, "[font]\nbase-size = 15\n")
    assert omarchy_theme.parse_shell_font_base(theme_dir, user_shell) == 15


def test_render_theme_css_emits_tokens_radius_and_font(tmp_path):
    state_dir = tmp_path / "state" / "current"
    _write_file(
        state_dir / "theme" / "colors.toml",
        """
mode = "dark"
background = "#1a1b26"
foreground = "#c0caf5"
accent = "#7aa2f7"
red = "#f7768e"
yellow = "#e0af68"
green = "#9ece6a"
""".strip(),
    )
    resp_file = tmp_path / "resp.json"
    resp_file.write_text('{"int": 0}\n', encoding="utf-8")
    fake_hyprctl = tmp_path / "hyprctl"
    fake_hyprctl.write_text(f"#!/bin/sh\ncat '{resp_file}'\n", encoding="utf-8")
    fake_hyprctl.chmod(fake_hyprctl.stat().st_mode | stat.S_IXUSR)

    theme = omarchy_theme.load_theme(
        state_dir=state_dir,
        legacy_dir=tmp_path / "legacy",
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin=str(fake_hyprctl),
        fc_match_bin="",
    )
    css = omarchy_theme.render_theme_css(theme)
    assert "@define-color bg #1a1b26;" in css
    assert "@define-color accent #7aa2f7;" in css
    assert "border-radius: 0px;" in css
    assert "JetBrainsMono Nerd Font" in css


def test_all_installed_omarchy_stock_themes_load_with_readable_contrast(tmp_path):
    stock_root = Path("/usr/share/omarchy/themes")
    if not stock_root.exists():
        pytest.skip("No stock Omarchy themes installed")

    theme_dirs = sorted(p.parent for p in stock_root.glob("*/colors.toml"))
    assert theme_dirs, "Expected stock themes with colors.toml"

    for tdir in theme_dirs:
        cur = tmp_path / tdir.name / "current"
        cur.mkdir(parents=True, exist_ok=True)
        (cur / "theme.name").write_text(tdir.name, encoding="utf-8")
        (cur / "theme").symlink_to(tdir)

        theme = omarchy_theme.load_theme(
            state_dir=cur,
            legacy_dir=tmp_path / "none",
            user_shell_toml=tmp_path / "no-shell.toml",
            hyprctl_bin="",
            fc_match_bin="",
        )
        assert theme.name == tdir.name, f"Stock theme {tdir.name} unexpectedly fell back"
        for ink_key in ("fg", "secondary", "danger_ink", "warning_ink", "success_ink", "accent_ink"):
            ratio = omarchy_theme.contrast_ratio(theme.colors[ink_key], theme.colors["surface"])
            assert ratio >= 4.4, f"{tdir.name}: {ink_key}={theme.colors[ink_key]} on surface={theme.colors['surface']} ratio={ratio:.2f}"


def test_widget_stylesheet_has_no_hardcoded_colors_and_builds_in_order(tmp_path):
    import re

    style_path = PLAN_DIR / "school_plan" / "widget_style.css"
    style_text = style_path.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", style_text), "Hardcoded color literal found in widget_style.css"

    theme = omarchy_theme.load_theme(
        state_dir=tmp_path / "missing-state",
        legacy_dir=tmp_path / "missing-legacy",
        user_shell_toml=tmp_path / "no-shell.toml",
        hyprctl_bin="",
        fc_match_bin="",
    )
    full_css = omarchy_theme.build_full_css(theme, style_path).decode("utf-8")
    assert full_css.index("@define-color bg ") < full_css.index(".glass-panel-grid {")
    assert full_css.index(".glass-panel-grid {") < full_css.index("/* Dynamic Omarchy corner rounding & font scale */")
