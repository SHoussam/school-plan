#!/usr/bin/env python3
"""
Modal Edit/Create Task Dialog for the EMSI School-Plan Desktop Calendar Widget.
Follows Omarchy's sharp geometry, Nerd Font iconography, emphasis-without-fills actions,
keyboard-first controls (Esc to cancel, Ctrl+Return/Ctrl+S to save), and AI provenance cleanup.
"""

import os
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk

import config
from deadline_utils import resolve_task_deadline

ICON_FOLDER = "\uf114"
ICON_TRASH = "\uf1f8"

AI_FIELD_LABELS_FR = {
    "module": "module",
    "teacher": "enseignant",
    "deadline": "échéance",
    "title": "titre",
    "priority": "priorité",
    "task_type": "type",
}


def get_ai_fields(task: dict) -> list[str]:
    """Returns non-empty list of AI-populated field names on task, or []."""
    ai = task.get("ai")
    if not isinstance(ai, dict):
        return []
    fields = ai.get("fields")
    if not isinstance(fields, list):
        return []
    return [str(f) for f in fields if f and f != "drop"]


def format_ai_provenance_tooltip(task: dict) -> str:
    """Formats tooltip text like 'IA : module, enseignant · 82 %'."""
    fields = get_ai_fields(task)
    if not fields:
        return ""
    ai = task.get("ai") or {}
    labels = [AI_FIELD_LABELS_FR.get(f, f) for f in fields]
    conf = ai.get("confidence")
    if isinstance(conf, (int, float)) and conf > 0:
        pct = int(round(float(conf) * 100)) if float(conf) <= 1.0 else int(round(float(conf)))
        return f"IA : {', '.join(labels)} · {pct} %"
    return f"IA : {', '.join(labels)}"


class EditTaskDialog(Gtk.Window):
    """Floating modal dialog to edit or create a calendar task."""

    def __init__(self, app, task, on_saved, on_deleted=None, is_new=False):
        super().__init__(application=app, title="Ajouter un devoir" if is_new else "Modifier le devoir")
        self.set_modal(True)
        self.set_default_size(450, 620)
        self.add_css_class("edit-window")

        self.is_new = is_new
        self.task = dict(task)
        if isinstance(task.get("ai"), dict):
            self.task["ai"] = dict(task["ai"])
            if isinstance(task["ai"].get("fields"), list):
                self.task["ai"]["fields"] = list(task["ai"]["fields"])
        self.on_saved = on_saved
        self.on_deleted = on_deleted

        d_obj, t_str = resolve_task_deadline(task)
        init_date_str = d_obj.strftime("%Y-%m-%d") if d_obj else ""
        init_time_str = t_str or ("23:59" if d_obj else "")

        self._initial_values = {
            "title": (task.get("title") or "").strip(),
            "module": (task.get("module") or "").strip(),
            "teacher": (task.get("teacher") or "").strip(),
            "deadline": (task.get("deadline") or "").strip(),
            "priority": (task.get("priority") or "MEDIUM").upper(),
            "date": init_date_str,
            "time": init_time_str,
        }

        # Keyboard shortcuts: Esc cancels, Ctrl+Return / Ctrl+S saves
        key_ctrl = Gtk.EventControllerKey()

        def on_key(_ctrl, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape:
                self.destroy()
                return True
            is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if is_ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_s, Gdk.KEY_S):
                self.on_save(None)
                return True
            return False

        key_ctrl.connect("key-pressed", on_key)
        self.add_controller(key_ctrl)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_child(scroll)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        container.add_css_class("edit-panel")
        scroll.set_child(container)

        lbl_hdr = Gtk.Label(
            label="NOUVEAU DEVOIR" if is_new else "MODIFIER LE DEVOIR",
            xalign=0,
        )
        lbl_hdr.add_css_class("edit-header-title")
        container.append(lbl_hdr)

        sub_text = (
            "Saisie manuelle · Esc annuler · Ctrl+Enter enregistrer"
            if is_new
            else f"{task.get('id', '')} · Esc annuler · Ctrl+Enter enregistrer"
        )
        lbl_sub = Gtk.Label(label=sub_text, xalign=0)
        lbl_sub.add_css_class("edit-header-sub")
        container.append(lbl_sub)

        ai_fields = get_ai_fields(self.task)
        if ai_fields:
            ai_info = self.task.get("ai") or {}
            labels_fr = ", ".join(AI_FIELD_LABELS_FR.get(f, f) for f in ai_fields)
            conf = ai_info.get("confidence")
            conf_str = ""
            if isinstance(conf, (int, float)) and conf > 0:
                pct = int(round(float(conf) * 100)) if float(conf) <= 1.0 else int(round(float(conf)))
                conf_str = f" ({pct} %)"
            lbl_ai = Gtk.Label(
                label=f"IA · champs remplis : {labels_fr}{conf_str}",
                xalign=0,
                wrap=True,
            )
            lbl_ai.add_css_class("ai-provenance-note")
            container.append(lbl_ai)

        # 1. Titre
        lbl = Gtk.Label(label="Titre", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_title = Gtk.Entry(text=task.get("title", ""))
        self.entry_title.set_placeholder_text("ex: Compte-rendu TP3")
        self.entry_title.add_css_class("form-entry")
        container.append(self.entry_title)

        # 2. Module
        lbl = Gtk.Label(label="Module", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_module = Gtk.Entry(text=task.get("module", ""))
        self.entry_module.set_placeholder_text("ex: Administration Oracle 1, Administration Unix, NoSQL")
        self.entry_module.add_css_class("form-entry")
        container.append(self.entry_module)

        # 3. Date & Heure
        row_dt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        v_date = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v_date.set_hexpand(True)
        lbl_d = Gtk.Label(label="Date (AAAA-MM-JJ)", xalign=0)
        lbl_d.add_css_class("form-field-label")
        v_date.append(lbl_d)
        self.entry_date = Gtk.Entry(text=init_date_str)
        self.entry_date.set_placeholder_text("2026-11-21")
        self.entry_date.add_css_class("form-entry")
        v_date.append(self.entry_date)
        row_dt.append(v_date)

        v_time = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v_time.set_hexpand(True)
        lbl_t = Gtk.Label(label="Heure limite (HH:MM)", xalign=0)
        lbl_t.add_css_class("form-field-label")
        v_time.append(lbl_t)
        self.entry_time = Gtk.Entry(text=init_time_str)
        self.entry_time.set_placeholder_text("23:59")
        self.entry_time.add_css_class("form-entry")
        v_time.append(self.entry_time)
        row_dt.append(v_time)

        container.append(row_dt)

        # 4. Enseignant
        lbl = Gtk.Label(label="Enseignant", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_teacher = Gtk.Entry(text=task.get("teacher", ""))
        self.entry_teacher.set_placeholder_text("ex: Hassan Badir")
        self.entry_teacher.add_css_class("form-entry")
        container.append(self.entry_teacher)

        # 5. Libellé d'échéance
        lbl = Gtk.Label(label="Libellé d'échéance", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_deadline = Gtk.Entry(text=task.get("deadline", ""))
        self.entry_deadline.set_placeholder_text("Aujourd'hui 23:59")
        self.entry_deadline.add_css_class("form-entry")
        container.append(self.entry_deadline)

        # 6. Source & URL
        lbl = Gtk.Label(label="Source", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_source = Gtk.Entry(text=task.get("source", ""))
        self.entry_source.set_placeholder_text("Google Classroom, Outlook, Manuel")
        self.entry_source.add_css_class("form-entry")
        container.append(self.entry_source)

        lbl = Gtk.Label(label="Lien web / URL", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_url = Gtk.Entry(text=task.get("url", ""))
        self.entry_url.set_placeholder_text("https://...")
        self.entry_url.add_css_class("form-entry")
        container.append(self.entry_url)

        # 7. Document local
        lbl = Gtk.Label(label="Document local", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)

        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.entry_path = Gtk.Entry(text=task.get("local_path", ""))
        self.entry_path.set_placeholder_text(f"{config.BASE_9RAYA}/...")
        self.entry_path.add_css_class("form-entry")
        self.entry_path.set_hexpand(True)
        file_row.append(self.entry_path)

        btn_browse = Gtk.Button(label=f"{ICON_FOLDER} Parcourir")
        btn_browse.add_css_class("btn-browse")
        btn_browse.set_cursor_from_name("pointer")
        btn_browse.connect("clicked", self.on_browse)
        file_row.append(btn_browse)
        container.append(file_row)

        lbl = Gtk.Label(label="Nom du fichier", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_fname = Gtk.Entry(text=task.get("file_name", ""))
        self.entry_fname.set_placeholder_text("document.pdf")
        self.entry_fname.add_css_class("form-entry")
        container.append(self.entry_fname)

        # 8. Description
        lbl = Gtk.Label(label="Consignes / notes", xalign=0)
        lbl.add_css_class("form-field-label")
        container.append(lbl)
        self.entry_desc = Gtk.Entry(text=task.get("description", ""))
        self.entry_desc.set_placeholder_text("Détails ou instructions")
        self.entry_desc.add_css_class("form-entry")
        container.append(self.entry_desc)

        # 9. Dropdowns
        row_dropdowns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        v_pri = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v_pri.set_hexpand(True)
        lbl_p = Gtk.Label(label="Priorité", xalign=0)
        lbl_p.add_css_class("form-field-label")
        v_pri.append(lbl_p)
        self.drop_priority = Gtk.DropDown.new_from_strings(["HIGH", "MEDIUM", "LOW"])
        cur_p = task.get("priority", "MEDIUM").upper()
        p_idx = 0 if cur_p == "HIGH" else (2 if cur_p == "LOW" else 1)
        self.drop_priority.set_selected(p_idx)
        v_pri.append(self.drop_priority)
        row_dropdowns.append(v_pri)

        v_stat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v_stat.set_hexpand(True)
        lbl_s = Gtk.Label(label="Statut", xalign=0)
        lbl_s.add_css_class("form-field-label")
        v_stat.append(lbl_s)
        stat_list = ["URGENT_TODAY", "UPCOMING", "LATE", "DONE"]
        self.drop_status = Gtk.DropDown.new_from_strings(stat_list)
        cur_s = task.get("status", "UPCOMING")
        s_idx = stat_list.index(cur_s) if cur_s in stat_list else 1
        self.drop_status.set_selected(s_idx)
        v_stat.append(self.drop_status)
        row_dropdowns.append(v_stat)

        v_sub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v_sub.set_hexpand(True)
        lbl_subm = Gtk.Label(label="Classroom", xalign=0)
        lbl_subm.add_css_class("form-field-label")
        v_sub.append(lbl_subm)
        self.sub_opts = ["NOT_SUBMITTED", "SUBMITTED", "NON_APPLICABLE"]
        self.drop_sub = Gtk.DropDown.new_from_strings(["Non soumis", "Soumis", "N/A"])
        cur_sub = task.get("submission_status", "NOT_SUBMITTED")
        sub_idx = self.sub_opts.index(cur_sub) if cur_sub in self.sub_opts else 0
        self.drop_sub.set_selected(sub_idx)
        v_sub.append(self.drop_sub)
        row_dropdowns.append(v_sub)

        container.append(row_dropdowns)

        # Peer outline action bar (Omarchy emphasis without solid fills)
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_top(14)

        if not self.is_new:
            btn_del = Gtk.Button(label=f"{ICON_TRASH} Supprimer")
            btn_del.add_css_class("btn-danger")
            btn_del.set_cursor_from_name("pointer")
            btn_del.connect("clicked", self.on_delete)
            btn_bar.append(btn_del)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_bar.append(spacer)

        btn_cancel = Gtk.Button(label="Annuler")
        btn_cancel.add_css_class("btn-secondary")
        btn_cancel.set_cursor_from_name("pointer")
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        btn_bar.append(btn_cancel)

        save_label = "Ajouter" if self.is_new else "Enregistrer"
        btn_save = Gtk.Button(label=save_label)
        btn_save.add_css_class("btn-primary")
        btn_save.set_cursor_from_name("pointer")
        btn_save.connect("clicked", self.on_save)
        btn_bar.append(btn_save)

        container.append(btn_bar)

    def on_browse(self, _btn):
        try:
            fd = Gtk.FileDialog()
            fd.set_title("Sélectionner un fichier de cours")
            cur = self.entry_path.get_text().strip()
            if cur and os.path.exists(cur):
                init_dir = os.path.dirname(cur) if os.path.isfile(cur) else cur
                fd.set_initial_folder(Gio.File.new_for_path(init_dir))
            else:
                docs_dir = os.path.expanduser("~/Documents/9raya")
                if os.path.exists(docs_dir):
                    fd.set_initial_folder(Gio.File.new_for_path(docs_dir))

            def on_chosen(dialog, res):
                try:
                    gfile = dialog.open_finish(res)
                    if gfile:
                        path = gfile.get_path()
                        self.entry_path.set_text(path)
                        if not self.entry_fname.get_text().strip():
                            self.entry_fname.set_text(os.path.basename(path))
                except Exception:
                    pass

            fd.open(self, None, on_chosen)
        except Exception:
            pass

    def on_save(self, _btn):
        new_title = self.entry_title.get_text().strip()
        new_module = self.entry_module.get_text().strip()
        new_teacher = self.entry_teacher.get_text().strip()
        new_deadline = self.entry_deadline.get_text().strip()
        new_date = self.entry_date.get_text().strip()
        new_time = self.entry_time.get_text().strip()

        self.task["title"] = new_title
        self.task["module"] = new_module
        self.task["teacher"] = new_teacher
        self.task["deadline"] = new_deadline
        self.task["source"] = self.entry_source.get_text().strip()
        self.task["url"] = self.entry_url.get_text().strip()
        self.task["local_path"] = self.entry_path.get_text().strip()
        self.task["file_name"] = self.entry_fname.get_text().strip()
        self.task["description"] = self.entry_desc.get_text().strip()

        raw_date = new_date.replace("-", "")
        raw_time = new_time.replace(":", "")
        if len(raw_date) == 8 and raw_date.isdigit():
            hhmm = raw_time[:4] if (len(raw_time) >= 4 and raw_time[:4].isdigit()) else "2359"
            iso_end = f"{raw_date}T{hhmm}00Z"
            self.task["end"] = iso_end
            self.task.pop("deadline_missing", None)
            if not self.task.get("start"):
                self.task["start"] = f"{raw_date}T090000Z"
        elif not raw_date:
            self.task["end"] = ""

        pri_opts = ["HIGH", "MEDIUM", "LOW"]
        new_priority = pri_opts[self.drop_priority.get_selected()]
        self.task["priority"] = new_priority

        stat_opts = ["URGENT_TODAY", "UPCOMING", "LATE", "DONE"]
        self.task["status"] = stat_opts[self.drop_status.get_selected()]

        self.task["submission_status"] = self.sub_opts[self.drop_sub.get_selected()]

        is_classroom = (
            self.task.get("source") == "Google Classroom"
            or config.CLASSROOM_HOST in self.task.get("url", "")
        )
        if self.task["status"] == "DONE" and is_classroom and self.task["submission_status"] != "SUBMITTED":
            self.task["submission_mismatch"] = "DONE_BUT_NOT_SUBMITTED"
        else:
            self.task["submission_mismatch"] = None

        if self.task["local_path"] and not self.task["file_name"]:
            self.task["file_name"] = os.path.basename(self.task["local_path"])

        # Clear AI provenance for any field the user edited
        if isinstance(self.task.get("ai"), dict) and isinstance(self.task["ai"].get("fields"), list):
            edited = set()
            if new_title != self._initial_values["title"]:
                edited.add("title")
            if new_module != self._initial_values["module"]:
                edited.add("module")
            if new_teacher != self._initial_values["teacher"]:
                edited.add("teacher")
            if (
                new_deadline != self._initial_values["deadline"]
                or new_date != self._initial_values["date"]
                or new_time != self._initial_values["time"]
            ):
                edited.add("deadline")
            if new_priority != self._initial_values["priority"]:
                edited.add("priority")
            self.task["ai"]["fields"] = [f for f in self.task["ai"]["fields"] if f not in edited]

        self.on_saved(self.task)
        self.destroy()

    def on_delete(self, _btn):
        if self.on_deleted:
            self.on_deleted(self.task.get("id"))
        self.destroy()
