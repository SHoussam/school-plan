#!/usr/bin/env python3
"""
Omarchy-Native Desktop Calendar Widget for EMSI Tanger (GTK4 + Gtk4LayerShell).
- Live Omarchy theme sync (colors.toml, shell.toml, active system font, Hyprland rounding)
- Sharp square geometry with 2px left-rail cards in the active theme's ANSI palette
- 7-Day Weekly Grid (Semaine), Detailed List (Liste), and Compact Pill modes
- Unclipped single-row header with live Gemma status dot and one-click CC1 exam jump
- Keyboard-first overlay popup (Alt+P / SIGUSR1) with toggleable `?` shortcut bar
- Direct links to Google Classroom & Outlook Web + Nautilus local file reveal
- Atomic flock-protected persistence via task_store.py
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

USE_LAYER_SHELL = (
    os.environ.get("SCHOOL_NO_LAYER_SHELL", "") != "1"
    and os.environ.get("GDK_BACKEND", "").lower() != "broadway"
)

# Automatic preload safeguard for GTK4 Layer Shell on Wayland
if USE_LAYER_SHELL and "libgtk4-layer-shell.so" not in os.environ.get("LD_PRELOAD", ""):
    env = dict(os.environ)
    preload = "/usr/lib/libgtk4-layer-shell.so"
    if "LD_PRELOAD" in env and env["LD_PRELOAD"]:
        env["LD_PRELOAD"] = f"{preload}:{env['LD_PRELOAD']}"
    else:
        env["LD_PRELOAD"] = preload
    os.execvpe(sys.executable, [sys.executable] + sys.argv, env)

import gi

gi.require_version("Gtk", "4.0")
if USE_LAYER_SHELL:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell
else:
    from gi.repository import Gdk, Gio, GLib, Gtk
    Gtk4LayerShell = None

PLAN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLAN_DIR not in sys.path:
    sys.path.insert(0, PLAN_DIR)

from school_plan import config, local_ai, omarchy_theme, task_store
from school_plan.config import MODULES, canonical_module, module_for_text
from school_plan.deadline_utils import parse_iso_date_time, resolve_task_calendar_slot, resolve_task_deadline
from school_plan.widget_dialogs import EditTaskDialog, format_ai_provenance_tooltip, get_ai_fields

TASKS_PATH = str(config.TASKS_PATH)
POS_PATH = str(config.POS_PATH)
STYLE_PATH = config.STYLE_PATH

DAY_NAMES_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MONTH_NAMES_FR = [
    "", "Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
    "Juil", "Aoû", "Sep", "Oct", "Nov", "Déc",
]

# Nerd Font glyphs (verified present in JetBrainsMono Nerd Font; zero emoji chrome)
ICON_REFRESH = "\uf021"
ICON_FOLDER = "\uf114"
ICON_EDIT = "\uf040"
ICON_EXT = "\uf08e"
ICON_WARN = "!"

# Logical display dimensions (1920x1080 @ 1.25 = 1536x864)
DEFAULT_SCREEN_W = 1536
DEFAULT_SCREEN_H = 864
GRID_WIDTH = 1040
LIST_WIDTH = 480
COMPACT_WIDTH = 360

SHORT_MODULE_LABELS = {
    "Administration Unix": "UNIX",
    "Administration Oracle 1": "ORACLE",
    "NoSQL": "NOSQL",
    "Dotnet": ".NET",
    "Machine Learning": "ML",
    "Virtualisation": "VIRT",
    "Développement Mobile": "MOBILE",
    "Analyse des données": "DATA",
    "Introduction et éthique de l'IA": "IA",
    "Programmation Avancée (Java)": "JAVA",
    "Recherche opérationnelle": "RO",
    "Emploi du Temps": "PLANNING",
    "Divers": "COURS",
}


def set_accessible_label(widget, label: str) -> None:
    """Assigns an explicit accessible name to icon-only controls."""
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    except Exception:
        pass


def parse_task_date_time(task):
    """Extract (date_obj_or_None, 'HH:MM') from task's real deadline."""
    return resolve_task_deadline(task)


def get_module_theme(module_name, title=""):
    """Map module/title to theme rail slot key."""
    canon = canonical_module(module_name)
    if canon == "Divers" and title:
        canon = module_for_text(title)
    return MODULES.get(canon, MODULES["Divers"]).get("theme", "slate")


def short_module_tag(task):
    canon = canonical_module(task.get("module"))
    if canon == "Divers" and task.get("title"):
        canon = module_for_text(task.get("title"))
    return SHORT_MODULE_LABELS.get(canon, "COURS")


def extract_exam_room(task):
    desc = task.get("description") or ""
    if "Salle " in desc:
        part = desc.split("Salle ", 1)[1].split("•", 1)[0].split("(", 1)[0].strip()
        if part:
            return part
    return ""


def format_task_time_slot(task, slot):
    is_done = slot["is_done"]
    is_missing_dl = slot["is_missing_deadline"] and not is_done
    if task.get("task_type") == "EXAMEN":
        _sd, st = parse_iso_date_time(task.get("start", ""))
        _ed, et = parse_iso_date_time(task.get("end", ""))
        if st and et and st != et:
            return f"{st}–{et}"
    return slot["due_time"] or ("Sans date" if is_missing_dl else "")


def clean_grid_title(task):
    """Build a crisp, scannable French title for calendar blocks and list cards."""
    title = (task.get("title") or "Tâche").strip()
    for prefix in (
        "Assignment: ",
        "Devoir : ",
        "Oracle 19c : ",
        "Admin Linux : ",
        "NoSQL : ",
        "Machine Learning : ",
        "Virtualisation : ",
        "DEV .NET : ",
        "EMSI Tanger : ",
    ):
        if title.startswith(prefix) and len(title) > len(prefix) + 3:
            title = title[len(prefix):].strip()
    if task.get("task_type") == "EXAMEN" and title.startswith("CC1 — "):
        title = title[len("CC1 — "):].strip()
    return title


def compact_done_title(task):
    """Shortens repetitive certification prefixes for 1-line completed rows."""
    title = clean_grid_title(task)
    for token in (
        "CERTIFICATION ORACLE19C ",
        "CERTIFICATION ORA-19C ",
        "CERTIFICATION Oracle19c ",
    ):
        title = title.replace(token, "")
    return title.strip() or "Tâche"


def next_exam_week_info(task_slots, today: date):
    """Returns (days_left, week_offset) for the nearest upcoming exam, or None."""
    upcoming_dates = sorted(
        s["due_date"]
        for t, s in task_slots
        if not s["is_done"]
        and t.get("task_type") == "EXAMEN"
        and s["due_date"] is not None
        and s["due_date"] >= today
    )
    if not upcoming_dates:
        return None
    first_d = upcoming_dates[0]
    days_left = (first_d - today).days
    this_monday = today - timedelta(days=today.weekday())
    exam_monday = first_d - timedelta(days=first_d.weekday())
    target_offset = (exam_monday - this_monday).days // 7
    return days_left, target_offset


class SchoolCalendarWidget:
    def __init__(self, app, popup_mode=False, on_close=None):
        self.app = app
        self.popup_mode = popup_mode
        self.on_close_cb = on_close
        self.popup_instance = None
        self.closed = False
        self._modal_open_count = 0
        self.show_help = False
        self.win = Gtk.Window()
        self.win.set_application(app)

        # Persistent position & view state
        self.is_compact = False
        self.view_mode = "grid"  # "grid" (7-day weekly) or "list"
        self.show_done = True
        self.week_offset = 0
        self.margin_top = 86
        self.margin_left = 300
        self.load_state()

        if self.popup_mode:
            self.is_compact = False
            sw, sh = self.get_screen_size()
            self.margin_left = max(16, (sw - self.current_expected_width()) // 2)
            self.margin_top = max(36, (sh - 600) // 2)

        # Init Layer Shell (when running on Wayland compositor)
        if Gtk4LayerShell is not None:
            Gtk4LayerShell.init_for_window(self.win)
            if self.popup_mode:
                Gtk4LayerShell.set_namespace(self.win, "school-calendar-popup")
                Gtk4LayerShell.set_layer(self.win, Gtk4LayerShell.Layer.OVERLAY)
                Gtk4LayerShell.set_keyboard_mode(self.win, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            else:
                Gtk4LayerShell.set_namespace(self.win, "school-calendar")
                Gtk4LayerShell.set_layer(self.win, Gtk4LayerShell.Layer.BOTTOM)
                Gtk4LayerShell.set_keyboard_mode(self.win, Gtk4LayerShell.KeyboardMode.NONE)

            Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.TOP, True)
            Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.LEFT, True)
            Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.RIGHT, False)
            Gtk4LayerShell.set_anchor(self.win, Gtk4LayerShell.Edge.BOTTOM, False)
            Gtk4LayerShell.set_exclusive_zone(self.win, 0)
        else:
            self.win.set_decorated(False)

        self.apply_clamped_margins()

        # Root container
        self.root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.set_child(self.root_box)

        # Timers & live AI status state
        self.toast_timer_id = None
        self.ai_status_data = self._read_ai_status_disk()
        self.lms_live_state = self.ai_status_data.get("lms_state") or "not-loaded"
        self.ai_refresh_in_progress = False
        self._status_check_inflight = False
        self.lbl_beacon = None
        self.btn_ai = None
        self.help_box = None
        self.btn_help = None

        # Drag state
        self.is_dragging = False
        self.drag_start_top = self.margin_top
        self.drag_start_left = self.margin_left
        self.header_drag_target = False

        self.setup_drag_gesture()
        self.setup_hover_controller()
        if self.popup_mode:
            self.setup_popup_keyboard()
        else:
            try:
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.toggle_popup)
            except Exception:
                pass

        self.populate()
        self.refresh_ai_status_async()

        # File monitor for live tasks.json updates
        self.file_monitor = None
        try:
            tasks_gfile = Gio.File.new_for_path(TASKS_PATH)
            self.file_monitor = tasks_gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)

            def on_file_changed(_monitor, _file, _other, event_type):
                if not self.closed and event_type in (
                    Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                    Gio.FileMonitorEvent.CHANGED,
                    Gio.FileMonitorEvent.CREATED,
                ):
                    GLib.idle_add(self.populate)

            self.file_monitor.connect("changed", on_file_changed)
        except Exception:
            pass

        if not self.popup_mode:
            GLib.timeout_add_seconds(30, self.auto_refresh)
            GLib.timeout_add_seconds(1, self._fast_status_tick)

    def get_screen_size(self):
        try:
            display = Gdk.Display.get_default()
            if display:
                monitors = display.get_monitors()
                if monitors and monitors.get_n_items() > 0:
                    mon = monitors.get_item(0)
                    geo = mon.get_geometry()
                    if geo.width > 0 and geo.height > 0:
                        return geo.width, geo.height
        except Exception:
            pass
        return DEFAULT_SCREEN_W, DEFAULT_SCREEN_H

    def close_self(self):
        if self.closed:
            return False
        self.closed = True
        if self.file_monitor:
            try:
                self.file_monitor.cancel()
            except Exception:
                pass
        if self.on_close_cb:
            try:
                self.on_close_cb()
            except Exception:
                pass
            self.win.destroy()
        else:
            self.app.quit()
        return False

    def toggle_popup(self):
        """Instantly opens or closes the floating overlay popup on Alt+P (SIGUSR1)."""
        if self.popup_instance and not self.popup_instance.closed:
            self.popup_instance.close_self()
            self.popup_instance = None
        else:
            def on_popup_closed():
                self.popup_instance = None

            self.popup_instance = SchoolCalendarWidget(self.app, popup_mode=True, on_close=on_popup_closed)
            self.popup_instance.win.present()
        return True

    def current_expected_width(self):
        if self.is_compact:
            return COMPACT_WIDTH
        return GRID_WIDTH if self.view_mode == "grid" else LIST_WIDTH

    def apply_clamped_margins(self):
        if Gtk4LayerShell is None:
            return
        sw, sh = self.get_screen_size()
        w = self.current_expected_width()
        max_left = max(16, sw - w - 20)
        clamped_left = max(16, min(max_left, self.margin_left))
        clamped_top = max(16, min(sh - 120, self.margin_top))
        Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.TOP, clamped_top)
        Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.LEFT, clamped_left)

    def load_state(self):
        if os.path.exists(POS_PATH):
            try:
                with open(POS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.margin_top = int(data.get("top", 86))
                    self.margin_left = int(data.get("left", 300))
                    self.is_compact = bool(data.get("compact", False))
                    self.view_mode = data.get("view_mode", "grid")
                    if self.view_mode not in ("grid", "list"):
                        self.view_mode = "grid"
                    self.show_done = bool(data.get("show_done", True))
            except Exception:
                pass
        sw, _ = self.get_screen_size()
        if self.view_mode == "grid" and not self.is_compact and self.margin_left > (sw - GRID_WIDTH - 20):
            self.margin_left = (sw - GRID_WIDTH) // 2

    def toggle_help(self):
        self.show_help = not self.show_help
        if self.help_box is not None:
            self.help_box.set_visible(self.show_help)
        if self.btn_help is not None:
            if self.show_help:
                self.btn_help.add_css_class("is-selected")
            else:
                self.btn_help.remove_css_class("is-selected")

    def jump_to_exam_week(self):
        tasks = self.load_tasks()
        today = date.today()
        slots = [(t, resolve_task_calendar_slot(t, today=today)) for t in tasks]
        info = next_exam_week_info(slots, today)
        if not info:
            self.show_toast("Aucun examen à venir", duration=4)
            return
        _days, offset = info
        self.view_mode = "grid"
        self.is_compact = False
        self.week_offset = offset
        self.apply_clamped_margins()
        self.populate()

    def setup_popup_keyboard(self):
        """Keyboard-first shortcuts for the floating popup overlay (suspended during modals)."""
        key_ctrl = Gtk.EventControllerKey()

        def on_key_pressed(_controller, keyval, _keycode, state):
            if self._modal_open_count > 0:
                return False
            # Ignore when Ctrl/Alt modifiers are held so WM bindings pass cleanly
            if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                return False

            if keyval == Gdk.KEY_Escape:
                if self.show_help:
                    self.toggle_help()
                else:
                    self.close_self()
                return True
            if keyval in (Gdk.KEY_q, Gdk.KEY_Q):
                self.close_self()
                return True
            if keyval == Gdk.KEY_question:
                self.toggle_help()
                return True
            if keyval in (Gdk.KEY_Left, Gdk.KEY_h, Gdk.KEY_H) and self.view_mode == "grid":
                self.shift_week(-1)
                return True
            if keyval in (Gdk.KEY_Right, Gdk.KEY_l, Gdk.KEY_L) and self.view_mode == "grid":
                self.shift_week(1)
                return True
            if keyval in (Gdk.KEY_t, Gdk.KEY_T, Gdk.KEY_0) and self.view_mode == "grid":
                self.reset_week_today()
                return True
            if keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.jump_to_exam_week()
                return True
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                self.set_view_mode("list" if self.view_mode == "grid" else "grid")
                return True
            if keyval in (Gdk.KEY_d, Gdk.KEY_D):
                self.show_done = not self.show_done
                self.save_state()
                self.populate()
                return True
            if keyval in (Gdk.KEY_r, Gdk.KEY_R):
                self.trigger_ai_refresh()
                return True
            if keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self.open_add_dialog()
                return True
            return False

        key_ctrl.connect("key-pressed", on_key_pressed)
        self.win.add_controller(key_ctrl)

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(POS_PATH), exist_ok=True)
            existing = {}
            if os.path.exists(POS_PATH):
                try:
                    with open(POS_PATH, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            if self.popup_mode:
                data = {
                    "top": existing.get("top", 64),
                    "left": existing.get("left", 298),
                    "compact": existing.get("compact", False),
                    "view_mode": self.view_mode,
                    "show_done": self.show_done,
                }
            else:
                data = {
                    "top": self.margin_top,
                    "left": self.margin_left,
                    "compact": self.is_compact,
                    "view_mode": self.view_mode,
                    "show_done": self.show_done,
                }
            with open(POS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def setup_drag_gesture(self):
        drag_gesture = Gtk.GestureDrag()
        drag_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_drag_begin(gesture, _start_x, _start_y):
            state = gesture.get_current_event_state()
            is_alt = bool(state & Gdk.ModifierType.ALT_MASK)
            button = gesture.get_current_button()

            if Gtk4LayerShell is not None and button == 1 and (is_alt or self.header_drag_target):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.is_dragging = True
                self.drag_start_top = Gtk4LayerShell.get_margin(self.win, Gtk4LayerShell.Edge.TOP)
                self.drag_start_left = Gtk4LayerShell.get_margin(self.win, Gtk4LayerShell.Edge.LEFT)
                self.win.set_cursor_from_name("grabbing")
            else:
                self.is_dragging = False
                gesture.set_state(Gtk.EventSequenceState.DENIED)

        def on_drag_update(_gesture, offset_x, offset_y):
            if self.is_dragging:
                sw, sh = self.get_screen_size()
                w = self.current_expected_width()
                max_left = max(16, sw - w - 12)
                new_top = max(10, min(sh - 80, int(self.drag_start_top + offset_y)))
                new_left = max(10, min(max_left, int(self.drag_start_left + offset_x)))
                Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.TOP, new_top)
                Gtk4LayerShell.set_margin(self.win, Gtk4LayerShell.Edge.LEFT, new_left)
                self.margin_top = new_top
                self.margin_left = new_left

        def on_drag_end(_gesture, _offset_x, _offset_y):
            if self.is_dragging:
                self.is_dragging = False
                self.header_drag_target = False
                self.win.set_cursor_from_name(None)
                self.save_state()
                self.show_toast(f"Position fixée ({self.margin_left}, {self.margin_top})", duration=3)

        drag_gesture.connect("drag-begin", on_drag_begin)
        drag_gesture.connect("drag-update", on_drag_update)
        drag_gesture.connect("drag-end", on_drag_end)
        self.win.add_controller(drag_gesture)

    def setup_hover_controller(self):
        motion = Gtk.EventControllerMotion()

        def on_motion(controller, _x, _y):
            if not self.is_dragging:
                state = controller.get_current_event_state()
                if state & Gdk.ModifierType.ALT_MASK:
                    self.win.set_cursor_from_name("grab")
                else:
                    self.win.set_cursor_from_name(None)

        def on_leave(_controller):
            if not self.is_dragging:
                self.win.set_cursor_from_name(None)

        motion.connect("motion", on_motion)
        motion.connect("leave", on_leave)
        self.win.add_controller(motion)

    def show_toast(self, message, duration=6):
        if not hasattr(self, "toast_box") or not hasattr(self, "toast_lbl"):
            return

        self.toast_lbl.set_text(message)
        self.toast_box.set_visible(True)

        if self.toast_timer_id is not None:
            GLib.source_remove(self.toast_timer_id)

        def hide():
            self.toast_box.set_visible(False)
            self.toast_timer_id = None
            return False

        self.toast_timer_id = GLib.timeout_add_seconds(duration, hide)

    def set_view_mode(self, mode):
        if self.view_mode != mode:
            self.view_mode = mode
            self.is_compact = False
            self.save_state()
            self.apply_clamped_margins()
            self.populate()

    def shift_week(self, delta):
        self.week_offset += delta
        self.populate()

    def reset_week_today(self):
        self.week_offset = 0
        self.populate()

    def toggle_compact(self):
        self.is_compact = not self.is_compact
        self.save_state()
        self.apply_clamped_margins()
        self.populate()

    def _read_ai_status_disk(self):
        if config.STATUS_PATH.exists():
            try:
                with open(config.STATUS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _compute_status_presentation(self):
        st = self.ai_status_data or {}
        is_busy = bool(self.ai_refresh_in_progress or st.get("running"))
        live_st = self.lms_live_state or st.get("lms_state") or "not-loaded"

        if is_busy:
            dot_text = "◐"
            dot_css = "beacon-running"
        elif live_st == "loaded":
            dot_text = "●"
            dot_css = "beacon-loaded"
        elif live_st == "not-loaded":
            dot_text = "○"
            dot_css = "beacon-standby"
        else:
            dot_text = "○"
            dot_css = "beacon-offline"

        res_map = {
            "ok": "IA OK",
            "partial": "Partiel (heuristique)",
            "offline": "IA hors ligne",
            "locked": "En cours",
            "error": "Erreur",
        }
        res_code = st.get("result") or "ok"
        res_fr = res_map.get(res_code, res_code)
        fin = st.get("finished_at") or ""
        ai_info = st.get("ai") or {}
        phase = st.get("phase") or "sync"

        state_label = {
            "loaded": "actif",
            "not-loaded": "veille",
            "offline": "hors ligne",
        }.get(live_st, live_st)

        tt_lines = [f"Gemma ({st.get('model') or config.LMS_MODEL}) : {phase + '…' if is_busy else state_label}"]
        if fin:
            tt_lines.append(f"Dernière sync : {fin.replace('T', ' ')}")
        tt_lines.append(f"Résultat : {res_fr}")
        tt_lines.append(f"Enrichis : {ai_info.get('ok', 0)}/{ai_info.get('sent', 0)} ({ai_info.get('ms', 0)} ms)")
        if st.get("summary"):
            tt_lines.append(st.get("summary"))

        return dot_text, dot_css, "\n".join(tt_lines), is_busy

    def _update_status_widgets(self):
        dot_text, dot_css, pill_tt, is_busy = self._compute_status_presentation()
        if self.lbl_beacon is not None:
            self.lbl_beacon.set_label(dot_text)
            for cls in ("beacon-loaded", "beacon-standby", "beacon-offline", "beacon-running"):
                self.lbl_beacon.remove_css_class(cls)
            self.lbl_beacon.add_css_class(dot_css)
            self.lbl_beacon.set_tooltip_text(pill_tt)
        if self.btn_ai is not None:
            self.btn_ai.set_sensitive(not is_busy)
            self.btn_ai.set_label("…" if is_busy else ICON_REFRESH)
            if is_busy:
                self.btn_ai.add_css_class("btn-ai-busy")
            else:
                self.btn_ai.remove_css_class("btn-ai-busy")
            self.btn_ai.set_tooltip_text(f"Actualiser Outlook & Classroom (r)\n{pill_tt}")

    def refresh_ai_status_async(self):
        if self._status_check_inflight or self.closed:
            return
        self._status_check_inflight = True

        def _worker():
            st_disk = self._read_ai_status_disk()
            live_st = local_ai.model_state()

            def _apply():
                self._status_check_inflight = False
                if self.closed:
                    return False
                self.ai_status_data = st_disk
                self.lms_live_state = live_st
                self._update_status_widgets()
                return False

            GLib.idle_add(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _fast_status_tick(self):
        if self.closed:
            return False
        st_disk = self._read_ai_status_disk()
        was_running = bool(self.ai_status_data.get("running"))
        now_running = bool(st_disk.get("running"))
        if self.ai_refresh_in_progress or now_running or was_running != now_running:
            self.ai_status_data = st_disk
            if st_disk.get("lms_state"):
                self.lms_live_state = st_disk["lms_state"]
            self._update_status_widgets()
        return True

    def auto_refresh(self):
        if self.closed:
            return False
        self.refresh_ai_status_async()
        self.populate()
        return True

    def load_tasks(self):
        try:
            return task_store.load()
        except Exception:
            return []

    def _commit_task_mutation(self, mutator_fn, on_after=None, _retry=True):
        try:
            task_store.update(mutator_fn, timeout=2.0)
        except task_store.LockBusy:
            self.show_toast("Synchronisation en cours… réessayez", duration=6)
            if _retry:
                def _retry_once():
                    if not self.closed:
                        self._commit_task_mutation(mutator_fn, on_after=on_after, _retry=False)
                    return False

                GLib.timeout_add(2000, _retry_once)
            return False
        except Exception:
            return False

        try:
            subprocess.Popen([sys.executable, str(config.PLAN_DIR / "generate_calendar.py")])
        except Exception:
            pass
        if on_after is not None:
            on_after()
        return True

    def save_tasks(self, tasks):
        self._commit_task_mutation(lambda _cur: tasks)

    def mark_task_done(self, task_id):
        tasks = self.load_tasks()
        found_title = ""
        mismatch_warning = False
        for t in tasks:
            if t.get("id") == task_id:
                t["prev_status"] = t.get("status", "UPCOMING")
                t["status"] = "DONE"
                t["completed_at"] = datetime.now().isoformat()
                found_title = t.get("title", "")

                is_classroom = (
                    t.get("source") == "Google Classroom"
                    or config.CLASSROOM_HOST in t.get("url", "")
                )
                sub_status = t.get("submission_status", "NOT_SUBMITTED")
                if is_classroom and sub_status != "SUBMITTED":
                    t["submission_mismatch"] = "DONE_BUT_NOT_SUBMITTED"
                    mismatch_warning = True
                else:
                    t["submission_mismatch"] = None
                break

        self.save_tasks(tasks)
        title_disp = found_title[:32] + "…" if len(found_title) > 32 else found_title
        if mismatch_warning:
            self.show_toast(f"! {title_disp} : fait, mais non remis sur Classroom", duration=8)
            try:
                subprocess.Popen(
                    [
                        "notify-send",
                        "-u",
                        "critical",
                        "-a",
                        "9raya Planning",
                        "Devoir non remis sur Classroom",
                        f"'{found_title}' est marqué terminé, mais n'a pas encore été déposé sur Google Classroom.",
                    ]
                )
            except Exception:
                pass
        else:
            self.show_toast(f"✓ Terminé : {title_disp}", duration=5)
        self.populate()

    def unmark_task_done(self, task_id):
        tasks = self.load_tasks()
        found_title = ""
        for t in tasks:
            if t.get("id") == task_id:
                t["status"] = t.get("prev_status", "UPCOMING")
                if t["status"] == "DONE":
                    t["status"] = "UPCOMING"
                t.pop("completed_at", None)
                t["submission_mismatch"] = None
                found_title = t.get("title", "")
                break

        self.save_tasks(tasks)
        title_disp = found_title[:32] + "…" if len(found_title) > 32 else found_title
        self.show_toast(f"↩ Rétabli : {title_disp}", duration=5)
        self.populate()

    def open_task_url(self, task):
        url = task.get("url")
        source_name = task.get("source", "Lien")
        if url:
            subprocess.Popen(["xdg-open", url])
            title_short = (task.get("title") or "")[:30]
            self.show_toast(f"Ouvert ({source_name}) : {title_short}", duration=5)
            if self.popup_mode:
                GLib.timeout_add(120, self.close_self)
        elif task.get("local_path") and os.path.exists(os.path.expanduser(task.get("local_path"))):
            self.open_local_file(task.get("local_path"), task.get("file_name"))

    def open_local_file(self, local_path, file_name=None):
        full_lp = os.path.expanduser(local_path)
        if not os.path.exists(full_lp):
            return
        name = file_name or os.path.basename(full_lp)
        if os.path.isfile(full_lp):
            subprocess.Popen(["nautilus", "--select", full_lp])
        else:
            subprocess.Popen(["xdg-open", full_lp])
        self.show_toast(f"Fichier : {name}", duration=5)
        if self.popup_mode:
            GLib.timeout_add(120, self.close_self)

    def _present_modal_dialog(self, dlg):
        self._modal_open_count += 1
        if self.popup_mode and Gtk4LayerShell is not None:
            Gtk4LayerShell.set_keyboard_mode(self.win, Gtk4LayerShell.KeyboardMode.ON_DEMAND)

        def on_closed(_w):
            self._modal_open_count = max(0, self._modal_open_count - 1)
            if self.popup_mode and not self.closed and self._modal_open_count == 0 and Gtk4LayerShell is not None:
                Gtk4LayerShell.set_keyboard_mode(self.win, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)

        dlg.connect("destroy", on_closed)
        dlg.present()

    def open_edit_dialog(self, task):
        def on_saved(updated_task):
            tasks = self.load_tasks()
            for i, t in enumerate(tasks):
                if t.get("id") == updated_task.get("id"):
                    tasks[i] = updated_task
                    break
            self.save_tasks(tasks)
            t_title = updated_task.get("title", "")
            t_disp = t_title[:30] + "…" if len(t_title) > 30 else t_title
            self.show_toast(f"Mis à jour : {t_disp}", duration=5)
            self.populate()

        def on_deleted(task_id):
            tasks = [t for t in self.load_tasks() if t.get("id") != task_id]
            self.save_tasks(tasks)
            self.show_toast("Devoir supprimé", duration=5)
            self.populate()

        dlg = EditTaskDialog(self.app, task, on_saved, on_deleted, is_new=False)
        self._present_modal_dialog(dlg)

    def open_add_dialog(self):
        today_iso = date.today().strftime("%Y%m%d")
        new_task = {
            "id": f"task-manual-{int(time.time())}",
            "title": "",
            "module": "Administration Oracle 1",
            "teacher": "",
            "start": f"{today_iso}T090000Z",
            "end": f"{today_iso}T235900Z",
            "deadline": "Aujourd'hui 23:59",
            "priority": "HIGH",
            "status": "UPCOMING",
            "source": "Manuel",
            "url": "",
            "local_path": "",
            "file_name": "",
            "description": "",
            "submission_status": "NON_APPLICABLE",
            "submission_mismatch": None,
        }

        def on_saved(created_task):
            tasks = self.load_tasks()
            tasks.append(created_task)
            self.save_tasks(tasks)
            t_title = created_task.get("title", "Nouvelle tâche")
            t_disp = t_title[:30] + "…" if len(t_title) > 30 else t_title
            self.show_toast(f"Ajouté : {t_disp}", duration=5)
            self.populate()

        dlg = EditTaskDialog(self.app, new_task, on_saved, is_new=True)
        self._present_modal_dialog(dlg)

    def trigger_ai_refresh(self):
        if self.ai_refresh_in_progress or self.ai_status_data.get("running"):
            self.show_toast("Actualisation déjà en cours", duration=5)
            return

        self.ai_refresh_in_progress = True
        self._update_status_widgets()
        self.show_toast("Actualisation Outlook & Classroom…", duration=6)

        def run_proc():
            outcome = "error"
            st_out = {}
            try:
                proc = subprocess.run(
                    [sys.executable, str(config.PLAN_DIR / "ai_refresher.py"), "--json"],
                    capture_output=True,
                    text=True,
                    timeout=config.PIPELINE_BUDGET_S + 30,
                )
                lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
                if lines:
                    try:
                        st_out = json.loads(lines[-1])
                    except Exception:
                        st_out = {}
                outcome = st_out.get("result") or ("ok" if proc.returncode == 0 else "error")
            except subprocess.TimeoutExpired:
                outcome = "timeout"
            except Exception:
                outcome = "error"

            GLib.idle_add(self.on_ai_refresh_done, outcome, st_out)

        threading.Thread(target=run_proc, daemon=True).start()

    def on_ai_refresh_done(self, outcome="ok", st_out=None):
        self.ai_refresh_in_progress = False
        if isinstance(st_out, dict) and st_out:
            self.ai_status_data = st_out
            if st_out.get("lms_state"):
                self.lms_live_state = st_out["lms_state"]
        else:
            self.ai_status_data = self._read_ai_status_disk()

        summary = (st_out or {}).get("summary") or self.ai_status_data.get("summary") or "Planning à jour."
        if outcome == "offline":
            toast_msg = "IA hors ligne — extraction heuristique utilisée"
        elif outcome == "locked":
            toast_msg = "Actualisation déjà en cours"
        elif outcome == "timeout":
            toast_msg = "Actualisation expirée"
        elif outcome == "error":
            toast_msg = "Erreur de synchronisation — voir le journal"
        else:
            toast_msg = summary

        self.populate()
        self.show_toast(toast_msg, duration=6)
        self.refresh_ai_status_async()
        return False

    def get_week_dates(self):
        today = date.today()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=self.week_offset)
        return [monday + timedelta(days=i) for i in range(7)]

    def _build_help_bar(self):
        """Compact Omarchy-style keycap shortcut legend toggled by `?`."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.add_css_class("help-bar")
        bar.set_halign(Gtk.Align.FILL)

        shortcuts = [
            ("←/→", "semaine"),
            ("t", "auj."),
            ("e", "CC1"),
            ("v", "vue"),
            ("d", "faits"),
            ("r", "sync"),
            ("n", "ajout"),
            ("Esc", "fermer"),
        ]
        for key_txt, desc_txt in shortcuts:
            k = Gtk.Label(label=key_txt)
            k.add_css_class("keycap")
            bar.append(k)
            d = Gtk.Label(label=desc_txt)
            d.add_css_class("help-text")
            bar.append(d)

        bar.set_visible(self.show_help)
        return bar

    def populate(self):
        while child := self.root_box.get_first_child():
            self.root_box.remove(child)

        for cls in ("glass-panel-grid", "glass-panel-list", "glass-panel-compact", "popup-elevated"):
            self.root_box.remove_css_class(cls)

        if self.is_compact:
            self.root_box.add_css_class("glass-panel-compact")
            self.win.set_default_size(COMPACT_WIDTH, -1)
        elif self.view_mode == "grid":
            self.root_box.add_css_class("glass-panel-grid")
            self.win.set_default_size(GRID_WIDTH, -1)
        else:
            self.root_box.add_css_class("glass-panel-list")
            self.win.set_default_size(LIST_WIDTH, -1)

        if self.popup_mode:
            self.root_box.add_css_class("popup-elevated")

        tasks = self.load_tasks()
        today = date.today()
        task_slots = sorted(
            ((t, resolve_task_calendar_slot(t, today=today)) for t in tasks),
            key=lambda item: item[1]["list_sort_key"],
        )
        mismatches = [t for t, s in task_slots if s["is_mismatch"]]
        urgent = [
            t
            for t, s in task_slots
            if not s["is_done"]
            and not s["is_mismatch"]
            and (
                s["is_overdue"]
                or s["is_missing_deadline"]
                or s["due_date"] == today
                or t.get("status") in ("URGENT_TODAY", "LATE")
            )
        ]
        urgent_ids = {id(t) for t in urgent}
        upcoming = [
            t for t, s in task_slots if not s["is_done"] and not s["is_mismatch"] and id(t) not in urgent_ids
        ]
        done = [t for t, s in task_slots if s["is_done"]]
        exam_info = next_exam_week_info(task_slots, today)

        # ------------------------------------------------------------
        # 1. SINGLE-ROW UNCLIPPED HEADER BAR
        # ------------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_valign(Gtk.Align.CENTER)
        if not self.is_compact:
            header.add_css_class("header-bar")

        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        brand_box.set_valign(Gtk.Align.CENTER)
        brand_box.set_hexpand(True)

        lbl_drag = Gtk.Label(label="⠿")
        lbl_drag.add_css_class("drag-handle")
        lbl_drag.set_tooltip_text("Glisser pour déplacer (ou Alt + Clic)")
        h_click = Gtk.GestureClick()

        def on_h_down(_g, _n, _x, _y):
            self.header_drag_target = True

        h_click.connect("pressed", on_h_down)
        lbl_drag.add_controller(h_click)
        lbl_drag.set_cursor_from_name("grab")
        brand_box.append(lbl_drag)

        dot_text, dot_css, pill_tt, is_busy = self._compute_status_presentation()

        if self.is_compact:
            exam_prefix = f"CC1 J-{exam_info[0]} · " if exam_info else ""
            next_txt = "Aucun"
            if mismatches:
                next_txt = f"{ICON_WARN} " + clean_grid_title(mismatches[0])[:18]
            elif urgent:
                next_txt = clean_grid_title(urgent[0])[:20]
            elif upcoming:
                next_txt = clean_grid_title(upcoming[0])[:20]

            comp_lbl = Gtk.Label(label=f"EMSI · {len(urgent)} urg · {exam_prefix}{next_txt}", xalign=0)
            comp_lbl.set_ellipsize(3)
            comp_lbl.set_width_chars(12)
            comp_lbl.set_max_width_chars(28)
            comp_lbl.add_css_class("compact-summary")
            comp_lbl.set_hexpand(True)
            comp_gesture = Gtk.GestureClick()
            comp_gesture.connect("released", lambda _g, _n, _x, _y: self.toggle_compact())
            comp_lbl.add_controller(comp_gesture)
            comp_lbl.set_cursor_from_name("pointer")
            brand_box.append(comp_lbl)
            self.lbl_beacon = None
        else:
            title_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            brand_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

            lbl_title = Gtk.Label(label="EMSI 4IIR", xalign=0)
            lbl_title.add_css_class("brand-title")
            brand_line.append(lbl_title)

            lbl_beacon = Gtk.Label(label=dot_text, xalign=0)
            lbl_beacon.add_css_class(dot_css)
            lbl_beacon.set_tooltip_text(pill_tt)
            self.lbl_beacon = lbl_beacon
            brand_line.append(lbl_beacon)
            title_vbox.append(brand_line)

            date_txt = f"{DAY_NAMES_FR[today.weekday()].upper()} {today.day} {MONTH_NAMES_FR[today.month].upper()}"
            if self.view_mode == "grid":
                date_txt = f"{date_txt} · {config.GROUP_LABEL}"
            lbl_sub = Gtk.Label(label=date_txt, xalign=0)
            lbl_sub.add_css_class("sub-header")
            title_vbox.append(lbl_sub)
            brand_box.append(title_vbox)

            # Compact inline alerts in Grid header (zero pill bloat)
            if self.view_mode == "grid":
                if mismatches:
                    lbl_m = Gtk.Label(label=f"{ICON_WARN} {len(mismatches)}")
                    lbl_m.add_css_class("hdr-alert-mismatch")
                    lbl_m.set_valign(Gtk.Align.CENTER)
                    lbl_m.set_tooltip_text(f"{len(mismatches)} devoir(s) fait(s) mais non remis sur Classroom")
                    brand_box.append(lbl_m)

                if exam_info:
                    days_left, target_offset = exam_info
                    exam_lbl = "CC1 AUJ." if days_left == 0 else f"CC1 J-{days_left}"
                    btn_cc = Gtk.Button(label=exam_lbl)
                    btn_cc.add_css_class("btn-exam-jump")
                    if self.week_offset == target_offset:
                        btn_cc.add_css_class("btn-exam-jump-active")
                    btn_cc.set_valign(Gtk.Align.CENTER)
                    btn_cc.set_cursor_from_name("pointer")
                    btn_cc.set_tooltip_text("Aller à la semaine des examens CC1 (e)")
                    btn_cc.connect("clicked", lambda _b: self.jump_to_exam_week())
                    brand_box.append(btn_cc)

        header.append(brand_box)

        # Center: Week navigation controls (Grid mode only)
        if not self.is_compact and self.view_mode == "grid":
            week_days = self.get_week_dates()
            nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            nav_box.add_css_class("week-nav-group")
            nav_box.set_valign(Gtk.Align.CENTER)

            btn_prev = Gtk.Button(label="‹")
            btn_prev.add_css_class("btn-nav")
            btn_prev.set_tooltip_text("Semaine précédente (← / h)")
            set_accessible_label(btn_prev, "Semaine précédente")
            btn_prev.set_cursor_from_name("pointer")
            btn_prev.connect("clicked", lambda _b: self.shift_week(-1))
            nav_box.append(btn_prev)

            d_start, d_end = week_days[0], week_days[6]
            if d_start.month == d_end.month:
                range_txt = f"{d_start.day}–{d_end.day} {MONTH_NAMES_FR[d_end.month]} {d_end.year}"
            else:
                range_txt = f"{d_start.day} {MONTH_NAMES_FR[d_start.month]}–{d_end.day} {MONTH_NAMES_FR[d_end.month]}"
            lbl_range = Gtk.Label(label=range_txt)
            lbl_range.add_css_class("week-range-lbl")
            nav_box.append(lbl_range)

            btn_next = Gtk.Button(label="›")
            btn_next.add_css_class("btn-nav")
            btn_next.set_tooltip_text("Semaine suivante (→ / l)")
            set_accessible_label(btn_next, "Semaine suivante")
            btn_next.set_cursor_from_name("pointer")
            btn_next.connect("clicked", lambda _b: self.shift_week(1))
            nav_box.append(btn_next)

            if self.week_offset != 0:
                btn_today = Gtk.Button(label="Auj.")
                btn_today.add_css_class("btn-today")
                btn_today.set_tooltip_text("Revenir à cette semaine (t)")
                btn_today.set_cursor_from_name("pointer")
                btn_today.connect("clicked", lambda _b: self.reset_week_today())
                nav_box.append(btn_today)

            header.append(nav_box)

        # Right: View switcher + quiet ghost tools
        if not self.is_compact:
            view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            view_box.add_css_class("view-switch-group")
            view_box.set_valign(Gtk.Align.CENTER)

            btn_grid = Gtk.Button(label="Semaine")
            btn_grid.add_css_class("btn-view")
            if self.view_mode == "grid":
                btn_grid.add_css_class("btn-view-active")
            btn_grid.set_cursor_from_name("pointer")
            btn_grid.set_tooltip_text("Vue semaine 7 jours (v)")
            btn_grid.connect("clicked", lambda _b: self.set_view_mode("grid"))
            view_box.append(btn_grid)

            btn_list = Gtk.Button(label="Liste")
            btn_list.add_css_class("btn-view")
            if self.view_mode == "list":
                btn_list.add_css_class("btn-view-active")
            btn_list.set_cursor_from_name("pointer")
            btn_list.set_tooltip_text("Vue liste détaillée (v)")
            btn_list.connect("clicked", lambda _b: self.set_view_mode("list"))
            view_box.append(btn_list)

            header.append(view_box)

            sep = Gtk.Box()
            sep.add_css_class("hdr-sep")
            header.append(sep)

            if self.view_mode == "grid" and done:
                btn_done_filter = Gtk.Button(label="✓")
                btn_done_filter.add_css_class("btn-ghost")
                if self.show_done:
                    btn_done_filter.add_css_class("is-selected")
                btn_done_filter.set_cursor_from_name("pointer")
                tip = (
                    f"Masquer les {len(done)} tâches terminées (d)"
                    if self.show_done
                    else f"Afficher les {len(done)} tâches terminées (d)"
                )
                btn_done_filter.set_tooltip_text(tip)
                set_accessible_label(btn_done_filter, tip)

                def on_toggle_done_grid(_b):
                    self.show_done = not self.show_done
                    self.save_state()
                    self.populate()

                btn_done_filter.connect("clicked", on_toggle_done_grid)
                header.append(btn_done_filter)

        btn_ai = Gtk.Button(label="…" if is_busy else ICON_REFRESH)
        btn_ai.add_css_class("btn-ghost")
        if is_busy:
            btn_ai.add_css_class("btn-ai-busy")
        btn_ai.set_sensitive(not is_busy)
        btn_ai.set_cursor_from_name("pointer")
        btn_ai.set_tooltip_text(f"Actualiser Outlook & Classroom (r)\n{pill_tt}")
        set_accessible_label(btn_ai, "Actualiser avec l'IA")
        btn_ai.connect("clicked", lambda _b: self.trigger_ai_refresh())
        self.btn_ai = btn_ai
        header.append(btn_ai)

        btn_add = Gtk.Button(label="+")
        btn_add.add_css_class("btn-ghost")
        btn_add.set_cursor_from_name("pointer")
        btn_add.set_tooltip_text("Ajouter un devoir (n)")
        set_accessible_label(btn_add, "Ajouter un devoir")
        btn_add.connect("clicked", lambda _b: self.open_add_dialog())
        header.append(btn_add)

        if self.popup_mode:
            btn_help = Gtk.Button(label="?")
            btn_help.add_css_class("btn-ghost")
            if self.show_help:
                btn_help.add_css_class("is-selected")
            btn_help.set_cursor_from_name("pointer")
            btn_help.set_tooltip_text("Raccourcis clavier (?)")
            set_accessible_label(btn_help, "Afficher les raccourcis clavier")
            btn_help.connect("clicked", lambda _b: self.toggle_help())
            self.btn_help = btn_help
            header.append(btn_help)
        else:
            self.btn_help = None
            btn_comp = Gtk.Button(label="⤢" if self.is_compact else "─")
            btn_comp.add_css_class("btn-ghost")
            btn_comp.set_cursor_from_name("pointer")
            tip = "Agrandir" if self.is_compact else "Réduire en barre compacte"
            btn_comp.set_tooltip_text(tip)
            set_accessible_label(btn_comp, tip)
            btn_comp.connect("clicked", lambda _b: self.toggle_compact())
            header.append(btn_comp)

        btn_close = Gtk.Button(label="×")
        btn_close.add_css_class("btn-ghost")
        btn_close.add_css_class("btn-close")
        btn_close.set_cursor_from_name("pointer")
        tip_close = "Fermer (Esc)" if self.popup_mode else "Masquer le widget"
        btn_close.set_tooltip_text(tip_close)
        set_accessible_label(btn_close, tip_close)
        btn_close.connect("clicked", lambda _b: self.close_self())
        header.append(btn_close)

        self.root_box.append(header)

        if self.is_compact:
            return

        # ------------------------------------------------------------
        # 2. MAIN CONTENT (Weekly Grid vs List)
        # ------------------------------------------------------------
        if self.view_mode == "grid":
            self.build_weekly_grid_view(tasks)
        else:
            self.build_list_view(tasks, mismatches, urgent, upcoming, done)

        # ------------------------------------------------------------
        # 3. TRANSIENT TOAST BAR
        # ------------------------------------------------------------
        self.toast_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toast_box.add_css_class("toast-bar")
        self.toast_box.set_halign(Gtk.Align.CENTER)
        self.toast_lbl = Gtk.Label(label="", xalign=0.5)
        self.toast_lbl.add_css_class("toast-text")
        self.toast_box.append(self.toast_lbl)
        self.toast_box.set_visible(False)
        self.root_box.append(self.toast_box)

        # ------------------------------------------------------------
        # 4. OPTIONAL KEYBOARD HELP BAR (Popup only, toggled by `?`)
        # ------------------------------------------------------------
        if self.popup_mode:
            self.help_box = self._build_help_bar()
            self.root_box.append(self.help_box)
        else:
            self.help_box = None

    def build_weekly_grid_view(self, tasks):
        """Builds the sharp 7-column weekly calendar grid."""
        week_days = self.get_week_dates()
        today = date.today()

        by_date = {d: [] for d in week_days}
        for t in tasks:
            slot = resolve_task_calendar_slot(t, today=today)
            if slot["is_done"] and not self.show_done:
                continue
            disp_d = slot["display_date"]
            if disp_d in by_date:
                by_date[disp_d].append((slot, t))

        for d in week_days:
            by_date[d].sort(key=lambda item: item[0]["within_day_key"])

        grid_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid_frame.add_css_class("cal-grid-frame")
        grid_frame.set_overflow(Gtk.Overflow.HIDDEN)

        # Sticky Day Headers Row (7 equal columns + 1px hairlines)
        headers_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        headers_row.set_homogeneous(True)

        for idx, d in enumerate(week_days):
            col_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            if idx > 0:
                div = Gtk.Box()
                div.add_css_class("cal-col-divider")
                col_wrap.append(div)

            hdr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            hdr_box.set_hexpand(True)
            hdr_box.add_css_class("cal-day-header")
            is_today = d == today
            if is_today:
                hdr_box.add_css_class("cal-day-header-today")

            lbl_wd = Gtk.Label(label=DAY_NAMES_FR[idx].upper())
            lbl_wd.add_css_class("cal-weekday")
            if is_today:
                lbl_wd.add_css_class("cal-weekday-today")
            lbl_wd.set_halign(Gtk.Align.CENTER)
            hdr_box.append(lbl_wd)

            lbl_num = Gtk.Label(label=f"{d.day:02d}")
            lbl_num.add_css_class("cal-day-num")
            if is_today:
                lbl_num.add_css_class("cal-day-num-today")
            lbl_num.set_halign(Gtk.Align.CENTER)
            hdr_box.append(lbl_num)

            col_wrap.append(hdr_box)
            headers_row.append(col_wrap)

        grid_frame.append(headers_row)

        # Scrollable 7-Column Body
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_overlay_scrolling(True)
        scrolled.set_propagate_natural_width(False)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(380)
        scrolled.set_max_content_height(520)

        cols_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cols_row.set_homogeneous(True)

        for idx, d in enumerate(week_days):
            col_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            if idx > 0:
                div = Gtk.Box()
                div.add_css_class("cal-col-divider")
                col_wrap.append(div)

            day_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            day_box.set_hexpand(True)
            day_box.add_css_class("cal-day-body")
            if d == today:
                day_box.add_css_class("cal-day-body-today")

            active_items = [(slot, t) for slot, t in by_date[d] if not slot["is_done"]]
            done_items = [(slot, t) for slot, t in by_date[d] if slot["is_done"]]

            if not active_items and not done_items:
                lbl_empty = Gtk.Label(label="·", xalign=0.5)
                lbl_empty.add_css_class("cal-empty-day")
                lbl_empty.set_tooltip_text("Aucun devoir ni examen ce jour")
                day_box.append(lbl_empty)

            for slot, task in active_items:
                day_box.append(self.create_grid_event_block(task, slot))

            if done_items:
                lbl_sep = Gtk.Label(label=f"FAIT ({len(done_items)})", xalign=0)
                lbl_sep.add_css_class("cal-done-sep")
                if active_items:
                    lbl_sep.add_css_class("cal-done-sep-divider")
                day_box.append(lbl_sep)
                for _slot, task in done_items:
                    day_box.append(self.create_grid_done_bar(task))

            col_wrap.append(day_box)
            cols_row.append(col_wrap)

        scrolled.set_child(cols_row)
        grid_frame.append(scrolled)
        self.root_box.append(grid_frame)

    def create_grid_done_bar(self, task):
        """Quiet 1-line monochrome row for completed / submitted assignments."""
        is_submitted = task.get("submission_status") == "SUBMITTED"

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        bar.add_css_class("cal-event-done-compact")
        bar.set_valign(Gtk.Align.CENTER)

        tt_parts = [
            f"✓ {task.get('title', 'Tâche')}",
            f"Module : {task.get('module', '—')}",
            "Statut : Remis sur Google Classroom" if is_submitted else "Statut : Terminé",
        ]
        if task.get("url"):
            tt_parts.append(f"Clic : ouvrir ({task.get('source', 'web')})")
        bar.set_tooltip_text("\n".join(tt_parts))

        click_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        click_row.set_hexpand(True)
        click_row.set_valign(Gtk.Align.CENTER)
        click_row.set_cursor_from_name("pointer")

        lbl_chk = Gtk.Label(label="✓")
        lbl_chk.add_css_class("ev-done-check")
        click_row.append(lbl_chk)

        lbl_title = Gtk.Label(label=compact_done_title(task), xalign=0)
        lbl_title.set_ellipsize(3)
        lbl_title.set_hexpand(True)
        lbl_title.add_css_class("ev-title-done-slim")
        click_row.append(lbl_title)

        open_click = Gtk.GestureClick()

        def on_open_done(_g, _n, _x, _y, t=task):
            if not self.is_dragging:
                self.open_task_url(t)

        open_click.connect("released", on_open_done)
        click_row.add_controller(open_click)
        bar.append(click_row)

        btn_undo = Gtk.Button(label="↩")
        btn_undo.add_css_class("ev-mini-btn-slim")
        btn_undo.set_cursor_from_name("pointer")
        btn_undo.set_tooltip_text("Rétablir dans les devoirs actifs")
        set_accessible_label(btn_undo, "Rétablir")
        btn_undo.connect("clicked", lambda _b, tid=task.get("id"): self.unmark_task_done(tid))
        bar.append(btn_undo)

        return bar

    def create_grid_event_block(self, task, slot):
        """Sharp theme-surface card with a 2px left accent rail and clean 3-line hierarchy."""
        is_done = slot["is_done"]
        is_mismatch = slot["is_mismatch"]
        is_late = slot["is_overdue"] and not is_done
        due_date = slot["due_date"]
        theme = get_module_theme(task.get("module"), task.get("title"))

        is_exam = task.get("task_type") == "EXAMEN"
        ai_fields = get_ai_fields(task)
        ai_tt = format_ai_provenance_tooltip(task)

        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        block.add_css_class("cal-event")
        block.add_css_class(f"ev-{theme}")
        if is_exam:
            block.add_css_class("block-exam")
        if is_mismatch:
            block.add_css_class("cal-event-mismatch")

        # Tooltip with full metadata
        tt_parts = [
            f"{task.get('title', 'Tâche')}",
            f"Module : {task.get('module', '—')}",
        ]
        if task.get("teacher"):
            tt_parts.append(f"Prof : {task.get('teacher')}")
        if task.get("deadline"):
            tt_parts.append(f"Échéance : {task.get('deadline')}")
        if ai_tt:
            tt_parts.append(ai_tt)
        if is_mismatch:
            tt_parts.append("! Marqué fait mais NON REMIS sur Classroom")
        elif task.get("submission_status") == "SUBMITTED":
            tt_parts.append("✓ Remis sur Google Classroom")
        if task.get("url"):
            tt_parts.append(f"Clic : ouvrir ({task.get('source', 'navigateur')})")
        block.set_tooltip_text("\n".join(tt_parts))

        # Clickable upper area -> opens specific Classroom / Outlook URL or local file
        click_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        click_area.set_cursor_from_name("pointer")

        # Line 1: Plain module tag on left + at most one clean status indicator on right
        tag_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tag_row.add_css_class("ev-tag-row")
        tag_row.set_valign(Gtk.Align.CENTER)

        if is_exam:
            room_txt = extract_exam_room(task)
            ex_txt = f"EXAM · {room_txt}" if room_txt else "EXAM · CC1"
            lbl_ex = Gtk.Label(label=ex_txt, xalign=0)
            lbl_ex.set_hexpand(True)
            lbl_ex.add_css_class("ev-status-exam")
            tag_row.append(lbl_ex)
        else:
            lbl_mod = Gtk.Label(label=short_module_tag(task), xalign=0)
            lbl_mod.set_hexpand(True)
            lbl_mod.add_css_class("ev-mod-tag")
            tag_row.append(lbl_mod)

        if is_mismatch:
            lbl_m = Gtk.Label(label="! NON REMIS", xalign=1)
            lbl_m.add_css_class("ev-status-mismatch")
            tag_row.append(lbl_m)
        elif is_late:
            lbl_l = Gtk.Label(label="RETARD", xalign=1)
            lbl_l.add_css_class("ev-status-late")
            tag_row.append(lbl_l)

        if ai_fields:
            lbl_ai = Gtk.Label(label="✦", xalign=1)
            lbl_ai.add_css_class("ev-ai-mark")
            lbl_ai.set_tooltip_text(ai_tt)
            tag_row.append(lbl_ai)

        click_area.append(tag_row)

        # Line 2: Readable 12px title
        lbl_title = Gtk.Label(label=clean_grid_title(task), xalign=0, wrap=True)
        lbl_title.set_width_chars(1)
        lbl_title.set_max_width_chars(18)
        lbl_title.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
        lbl_title.add_css_class("ev-title")
        click_area.append(lbl_title)

        open_click = Gtk.GestureClick()

        def on_open_block(_g, _n, _x, _y, t=task):
            if not self.is_dragging:
                self.open_task_url(t)

        open_click.connect("released", on_open_block)
        click_area.add_controller(open_click)
        block.append(click_area)

        # Line 3: Time / range on left + clean Nerd Font micro-actions on right
        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        meta_row.add_css_class("ev-meta-row")
        meta_row.set_valign(Gtk.Align.CENTER)

        lbl_time = Gtk.Label(label=format_task_time_slot(task, slot), xalign=0)
        lbl_time.set_ellipsize(3)
        lbl_time.set_width_chars(5)
        lbl_time.add_css_class("ev-time")
        lbl_time.set_hexpand(True)
        time_click = Gtk.GestureClick()
        time_click.connect("released", on_open_block)
        lbl_time.add_controller(time_click)
        lbl_time.set_cursor_from_name("pointer")
        meta_row.append(lbl_time)

        lp = task.get("local_path")
        if lp and os.path.exists(os.path.expanduser(lp)):
            btn_file = Gtk.Button(label=ICON_FOLDER)
            btn_file.add_css_class("ev-mini-btn")
            btn_file.set_cursor_from_name("pointer")
            fname = task.get("file_name") or os.path.basename(lp)
            btn_file.set_tooltip_text(f"Ouvrir : {fname}")
            set_accessible_label(btn_file, f"Ouvrir le fichier {fname}")
            btn_file.connect("clicked", lambda _b, p=lp, fn=task.get("file_name"): self.open_local_file(p, fn))
            meta_row.append(btn_file)

        btn_edit = Gtk.Button(label=ICON_EDIT)
        btn_edit.add_css_class("ev-mini-btn")
        btn_edit.set_cursor_from_name("pointer")
        btn_edit.set_tooltip_text("Modifier")
        set_accessible_label(btn_edit, "Modifier le devoir")
        btn_edit.connect("clicked", lambda _b, t=task: self.open_edit_dialog(t))
        meta_row.append(btn_edit)

        if is_done:
            btn_done = Gtk.Button(label="↩")
            btn_done.add_css_class("ev-mini-btn")
            btn_done.set_cursor_from_name("pointer")
            btn_done.set_tooltip_text("Rétablir")
            set_accessible_label(btn_done, "Rétablir le devoir")
            btn_done.connect("clicked", lambda _b, tid=task.get("id"): self.unmark_task_done(tid))
            meta_row.append(btn_done)
        else:
            btn_done = Gtk.Button(label="✓")
            btn_done.add_css_class("ev-mini-btn")
            btn_done.set_cursor_from_name("pointer")
            btn_done.set_tooltip_text("Marquer comme fait")
            set_accessible_label(btn_done, "Marquer comme fait")
            btn_done.connect("clicked", lambda _b, tid=task.get("id"): self.mark_task_done(tid))
            meta_row.append(btn_done)

        block.append(meta_row)
        return block

    def build_list_view(self, tasks, mismatches, urgent, upcoming, done):
        """Builds the sharp vertical list view with hairline section dividers."""
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        stats_box.add_css_class("stats-bar")

        if mismatches:
            lbl_m = Gtk.Label(label=f"! {len(mismatches)} NON REMIS")
            lbl_m.add_css_class("stat-item")
            lbl_m.add_css_class("stat-mismatch")
            stats_box.append(lbl_m)

        lbl_u = Gtk.Label(label=f"{len(urgent)} URG.")
        lbl_u.add_css_class("stat-item")
        if urgent:
            lbl_u.add_css_class("stat-urgent")
        stats_box.append(lbl_u)

        lbl_up = Gtk.Label(label=f"{len(upcoming)} À VENIR")
        lbl_up.add_css_class("stat-item")
        stats_box.append(lbl_up)

        if done:
            lbl_d = Gtk.Label(label=f"✓ {len(done)} FAIT")
            lbl_d.add_css_class("stat-item")
            lbl_d.add_css_class("stat-done")
            stats_box.append(lbl_d)

        self.root_box.append(stats_box)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_propagate_natural_width(False)
        scrolled.set_min_content_height(350)
        scrolled.set_max_content_height(500)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled.set_child(list_box)

        if mismatches:
            lbl_m = Gtk.Label(label="NON REMIS SUR CLASSROOM", xalign=0)
            lbl_m.add_css_class("section-header")
            lbl_m.add_css_class("section-title-mismatch")
            list_box.append(lbl_m)
            for t in mismatches:
                list_box.append(self.create_list_card(t, urgent=True))

        if urgent:
            lbl_u = Gtk.Label(label="PRIORITÉS & RATTRAPAGES", xalign=0)
            lbl_u.add_css_class("section-header")
            lbl_u.add_css_class("section-title-urgent")
            list_box.append(lbl_u)
            for t in urgent:
                list_box.append(self.create_list_card(t, urgent=True))

        if upcoming:
            lbl_up = Gtk.Label(label="À VENIR & EXAMENS", xalign=0)
            lbl_up.add_css_class("section-header")
            list_box.append(lbl_up)
            for t in upcoming:
                list_box.append(self.create_list_card(t, urgent=False))

        if done:
            arrow = "▾" if self.show_done else "▸"
            btn_toggle = Gtk.Button(label=f"{arrow} FAIT ({len(done)})")
            btn_toggle.add_css_class("btn-toggle-done")
            btn_toggle.set_halign(Gtk.Align.START)
            btn_toggle.set_cursor_from_name("pointer")

            def on_td(_b):
                self.show_done = not self.show_done
                self.save_state()
                self.populate()

            btn_toggle.connect("clicked", on_td)
            list_box.append(btn_toggle)

            if self.show_done:
                for t in done:
                    list_box.append(self.create_list_card(t, is_done=True))

        self.root_box.append(scrolled)

    def create_list_card(self, task, urgent=False, is_done=False):
        is_mismatch = task.get("submission_mismatch") == "DONE_BUT_NOT_SUBMITTED"
        is_submitted = task.get("submission_status") == "SUBMITTED"
        theme = get_module_theme(task.get("module"), task.get("title"))

        # Quiet single-row monochrome bar for completed tasks in List view
        if is_done and not is_mismatch:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.add_css_class("card-done-compact")
            row.set_valign(Gtk.Align.CENTER)

            lbl_chk = Gtk.Label(label="✓")
            lbl_chk.add_css_class("ev-done-check")
            row.append(lbl_chk)

            lbl_t = Gtk.Label(label=compact_done_title(task), xalign=0)
            lbl_t.set_ellipsize(3)
            lbl_t.set_hexpand(True)
            lbl_t.add_css_class("ev-title-done-slim")
            lbl_t.set_cursor_from_name("pointer")
            c_click = Gtk.GestureClick()
            c_click.connect("released", lambda _g, _n, _x, _y, t=task: (not self.is_dragging) and self.open_task_url(t))
            lbl_t.add_controller(c_click)
            row.append(lbl_t)

            if is_submitted:
                lbl_s = Gtk.Label(label="REMIS")
                lbl_s.add_css_class("badge-submitted")
                row.append(lbl_s)

            btn_u = Gtk.Button(label="↩")
            btn_u.add_css_class("ev-mini-btn-slim")
            btn_u.set_cursor_from_name("pointer")
            btn_u.set_tooltip_text("Rétablir")
            set_accessible_label(btn_u, "Rétablir")
            btn_u.connect("clicked", lambda _b, tid=task.get("id"): self.unmark_task_done(tid))
            row.append(btn_u)

            return row

        is_exam = task.get("task_type") == "EXAMEN"
        ai_fields = get_ai_fields(task)
        ai_tt = format_ai_provenance_tooltip(task)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("task-card")
        card.add_css_class(f"ev-{theme}")
        if is_exam:
            card.add_css_class("block-exam")
        if is_mismatch:
            card.add_css_class("card-mismatch")

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text_box.set_cursor_from_name("pointer")

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if is_exam:
            room_txt = extract_exam_room(task)
            ex_txt = f"EXAM · {room_txt}" if room_txt else "EXAM · CC1"
            lbl_ex = Gtk.Label(label=ex_txt, xalign=0)
            lbl_ex.add_css_class("ev-status-exam")
            top_row.append(lbl_ex)
        else:
            lbl_mod = Gtk.Label(label=(task.get("module") or "COURS").upper(), xalign=0)
            lbl_mod.add_css_class("ev-mod-tag")
            top_row.append(lbl_mod)

        if ai_fields:
            lbl_ai = Gtk.Label(label="✦", xalign=0)
            lbl_ai.add_css_class("ev-ai-mark")
            lbl_ai.set_tooltip_text(ai_tt)
            top_row.append(lbl_ai)

        top_spacer = Gtk.Box()
        top_spacer.set_hexpand(True)
        top_row.append(top_spacer)

        slot = resolve_task_calendar_slot(task, today=date.today())
        if is_mismatch:
            dl_label = "! NON REMIS"
            dl_cls = "ev-status-mismatch"
        elif slot["is_missing_deadline"]:
            dl_label = "SANS ÉCHÉANCE"
            dl_cls = "badge-deadline"
        else:
            d_obj = slot["due_date"]
            t_str = format_task_time_slot(task, slot)
            prefix = "RETARD · " if slot["is_overdue"] else ""
            dl_label = f"{prefix}{DAY_NAMES_FR[d_obj.weekday()].upper()} {d_obj.day} {MONTH_NAMES_FR[d_obj.month].upper()}"
            if t_str:
                dl_label = f"{dl_label} · {t_str}"
            dl_cls = "badge-deadline-urgent" if (urgent or slot["is_overdue"]) else "badge-deadline"

        lbl_dl = Gtk.Label(label=dl_label, xalign=1)
        lbl_dl.add_css_class(dl_cls)
        top_row.append(lbl_dl)
        text_box.append(top_row)

        t_title = Gtk.Label(label=clean_grid_title(task), xalign=0, wrap=True)
        t_title.set_width_chars(1)
        t_title.set_wrap_mode(2)
        t_title.add_css_class("task-title")
        text_box.append(t_title)

        if task.get("teacher"):
            t_meta = Gtk.Label(label=task.get("teacher"), xalign=0)
            t_meta.add_css_class("task-meta")
            text_box.append(t_meta)

        if is_mismatch:
            lbl_warn = Gtk.Label(
                label="! Marqué terminé en local, mais non remis sur Classroom.",
                xalign=0,
                wrap=True,
            )
            lbl_warn.add_css_class("warning-desc")
            text_box.append(lbl_warn)

        card_click = Gtk.GestureClick()

        def on_text_click(_g, _n, _x, _y, t=task):
            if not self.is_dragging:
                self.open_task_url(t)

        card_click.connect("released", on_text_click)
        text_box.add_controller(card_click)
        card.append(text_box)

        # Peer outline action chips
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions_box.add_css_class("card-actions")

        if not is_done:
            btn_done = Gtk.Button(label="✓ Fait")
            btn_done.add_css_class("btn-action-chip")
            btn_done.add_css_class("btn-done-chip")
            btn_done.set_cursor_from_name("pointer")
            btn_done.connect("clicked", lambda _b, tid=task.get("id"): self.mark_task_done(tid))
            actions_box.append(btn_done)
        else:
            btn_undo = Gtk.Button(label="↩ Rétablir")
            btn_undo.add_css_class("btn-action-chip")
            btn_undo.set_cursor_from_name("pointer")
            btn_undo.connect("clicked", lambda _b, tid=task.get("id"): self.unmark_task_done(tid))
            actions_box.append(btn_undo)

        btn_edit = Gtk.Button(label=f"{ICON_EDIT} Modifier")
        btn_edit.add_css_class("btn-action-chip")
        btn_edit.set_cursor_from_name("pointer")
        btn_edit.connect("clicked", lambda _b, t=task: self.open_edit_dialog(t))
        actions_box.append(btn_edit)

        if task.get("url"):
            source_name = task.get("source", "Lien")
            link_lbl = f"{ICON_EXT} Déposer" if is_mismatch else f"{ICON_EXT} {source_name}"
            btn_link = Gtk.Button(label=link_lbl)
            btn_link.add_css_class("btn-action-chip")
            if is_mismatch:
                btn_link.add_css_class("btn-submit-alert-chip")
            btn_link.set_cursor_from_name("pointer")
            btn_link.connect("clicked", lambda _b, t=task: self.open_task_url(t))
            actions_box.append(btn_link)

        lp = task.get("local_path")
        if lp and os.path.exists(os.path.expanduser(lp)):
            fname = task.get("file_name") or os.path.basename(lp)
            disp = fname if len(fname) <= 18 else fname[:15] + "…"
            btn_file = Gtk.Button(label=f"{ICON_FOLDER} {disp}")
            btn_file.add_css_class("btn-action-chip")
            btn_file.set_cursor_from_name("pointer")
            btn_file.connect("clicked", lambda _b, p=lp, fn=fname: self.open_local_file(p, fn))
            actions_box.append(btn_file)

        card.append(actions_box)
        return card


edit_target_id = None
is_add_mode = False
is_popup_mode = False
_css_provider = None
_theme_watcher = None


def _apply_theme_css(provider: Gtk.CssProvider, theme: omarchy_theme.OmarchyTheme) -> None:
    css_bytes = omarchy_theme.build_full_css(theme, STYLE_PATH)
    if hasattr(provider, "load_from_bytes"):
        provider.load_from_bytes(GLib.Bytes.new(css_bytes))
    else:
        provider.load_from_data(css_bytes)


def _setup_live_omarchy_theme(app: Gtk.Application) -> None:
    global _css_provider, _theme_watcher
    if _css_provider is not None:
        return

    _css_provider = Gtk.CssProvider()
    display = Gdk.Display.get_default()
    Gtk.StyleContext.add_provider_for_display(
        display, _css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    def on_theme_changed(new_theme: omarchy_theme.OmarchyTheme) -> None:
        GLib.idle_add(_apply_theme_css, _css_provider, new_theme)

    _theme_watcher = omarchy_theme.ThemeWatcher(on_changed=on_theme_changed)
    _apply_theme_css(_css_provider, _theme_watcher.current)

    # Allow manual/hook live reload via SIGUSR2
    try:
        def _on_sigusr2():
            if _theme_watcher is not None:
                _theme_watcher.reload(rearm=True, force=True, refresh_font=True, refresh_radius=True)
            return True

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, _on_sigusr2)
    except Exception:
        pass

    def _on_shutdown(_app):
        if _theme_watcher is not None:
            _theme_watcher.stop()

    app.connect("shutdown", _on_shutdown)


def on_activate(app):
    global edit_target_id, is_add_mode, is_popup_mode
    _setup_live_omarchy_theme(app)

    if is_add_mode:
        today_iso = date.today().strftime("%Y%m%d")
        new_task = {
            "id": f"task-manual-{int(time.time())}",
            "title": "",
            "module": "Administration Oracle 1",
            "teacher": "",
            "start": f"{today_iso}T090000Z",
            "end": f"{today_iso}T235900Z",
            "deadline": "Aujourd'hui 23:59",
            "priority": "HIGH",
            "status": "UPCOMING",
            "source": "Manuel",
            "url": "",
            "local_path": "",
            "file_name": "",
            "description": "",
            "submission_status": "NON_APPLICABLE",
            "submission_mismatch": None,
        }

        def on_saved(created_task):
            task_store.update(lambda items: items + [created_task], timeout=2.0)
            subprocess.Popen([sys.executable, str(config.PLAN_DIR / "generate_calendar.py")])
            app.quit()

        dlg = EditTaskDialog(app, new_task, on_saved=on_saved, is_new=True)
        dlg.connect("close-request", lambda _w: (app.quit(), False)[1])
        dlg.present()
        return

    if edit_target_id:
        tasks = task_store.load()
        target = next((t for t in tasks if t.get("id") == edit_target_id), None)
        if target:
            def on_saved(updated_task):
                def _apply_edit(items):
                    for i, t in enumerate(items):
                        if t.get("id") == updated_task.get("id"):
                            items[i] = updated_task
                            break
                    return items

                task_store.update(_apply_edit, timeout=2.0)
                subprocess.Popen([sys.executable, str(config.PLAN_DIR / "generate_calendar.py")])
                app.quit()

            def on_deleted(task_id):
                task_store.update(lambda items: [t for t in items if t.get("id") != task_id], timeout=2.0)
                subprocess.Popen([sys.executable, str(config.PLAN_DIR / "generate_calendar.py")])
                app.quit()

            dlg = EditTaskDialog(app, target, on_saved=on_saved, on_deleted=on_deleted, is_new=False)
            dlg.connect("close-request", lambda _w: (app.quit(), False)[1])
            dlg.present()
            return
        print(f"Task with ID {edit_target_id} not found.")
        app.quit()
        return

    widget = SchoolCalendarWidget(app, popup_mode=is_popup_mode)
    widget.win.present()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--popup":
        is_popup_mode = True
        app = Gtk.Application(application_id="com.emsi.schoolcalendar.popup")
        app.connect("activate", on_activate)
        app.run(None)
    elif len(sys.argv) > 1 and sys.argv[1] == "--add":
        is_add_mode = True
        app_id = f"com.emsi.schoolcalendar.adder_{int(time.time())}"
        app = Gtk.Application(application_id=app_id)
        app.connect("activate", on_activate)
        app.run(None)
    elif len(sys.argv) > 2 and sys.argv[1] == "--edit":
        edit_target_id = sys.argv[2]
        app_id = f"com.emsi.schoolcalendar.editor_{int(time.time())}"
        app = Gtk.Application(application_id=app_id)
        app.connect("activate", on_activate)
        app.run(None)
    else:
        app = Gtk.Application(application_id="com.emsi.schoolcalendar")
        app.connect("activate", on_activate)
        app.run(None)
