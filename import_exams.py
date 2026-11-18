#!/usr/bin/env python3
"""
Idempotently parses the CC1 exam table from ~/Documents/9raya/STUDY_PLAN_CC1_4IIR.md
and upserts the 8 exam tasks (task_type='EXAMEN') into tasks.json via task_store.
"""

from datetime import date
from pathlib import Path
import re
import sys
import unicodedata

PLAN_DIR = Path(__file__).resolve().parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

import config
from deadline_utils import format_iso_end, parse_deadline_text
import generate_calendar
import task_store


def _slugify(text: str) -> str:
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s or "exam"


def _strip_md(cell: str) -> str:
    return re.sub(r"\*+", "", cell or "").strip()


def parse_exams_from_markdown(md_path: Path | None = None) -> list[dict]:
    """Parses the official exam calendar markdown table in STUDY_PLAN_CC1_4IIR.md."""
    target = Path(md_path) if md_path is not None else config.STUDY_PLAN_PATH
    if not target.exists():
        raise FileNotFoundError(f"Study plan not found: {target}")

    content = target.read_text(encoding="utf-8")
    exams: list[dict] = []

    for line in content.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [_strip_md(c) for c in s.strip("|").split("|")]
        if len(cells) < 5:
            continue
        date_cell, time_cell, subject_cell, teachers_cell, room_cell = cells[:5]
        if not re.search(r"\d{1,2}\s*:\s*\d{2}\s*-\s*\d{1,2}\s*:\s*\d{2}", time_cell):
            continue

        m_time = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", time_cell)
        if not m_time:
            continue
        t_start, t_end = m_time.group(1), m_time.group(2)

        d_obj, _ = parse_deadline_text(date_cell, ref_date=date(2026, 11, 18))
        if d_obj is None:
            continue

        # Format deadline: "<Jour> <d> Nov <HH:MM>" using exam start time
        day_word = date_cell.split()[0] if date_cell.split() else "Lundi"
        deadline_label = f"{day_word} {d_obj.day} Nov {t_start}"

        mod = config.canonical_module(subject_cell)
        if mod == "Divers":
            mod = config.module_for_text(f"{subject_cell} {teachers_cell}")

        slug = _slugify(subject_cell)
        exam_id = f"exam-cc1-{slug}"

        exams.append(
            {
                "id": exam_id,
                "title": f"CC1 — {subject_cell}",
                "module": mod,
                "teacher": teachers_cell,
                "start": format_iso_end(d_obj, t_start),
                "end": format_iso_end(d_obj, t_end),
                "deadline": deadline_label,
                "priority": "HIGH",
                "status": "UPCOMING",
                "task_type": "EXAMEN",
                "source": "Planification CC 4IIR.xlsx",
                "url": "",
                "local_path": str(target),
                "file_name": target.name,
                "submission_status": "UNKNOWN",
                "submission_mismatch": None,
                "description": f"Salle {room_cell} • {teachers_cell} ({t_start} – {t_end})",
            }
        )

    return exams


def import_exams(md_path: Path | None = None) -> int:
    exams = parse_exams_from_markdown(md_path=md_path)

    def _upsert(tasks: list[dict]) -> list[dict]:
        by_id = {t.get("id"): t for t in tasks if t.get("id")}
        for ex in exams:
            ex_id = ex["id"]
            if ex_id in by_id:
                existing = by_id[ex_id]
                # Never overwrite a task the user has marked DONE
                if existing.get("status") == "DONE":
                    continue
                for k, v in ex.items():
                    if k not in ("status", "completed_at"):
                        existing[k] = v
            else:
                tasks.append(ex)
        return tasks

    task_store.update(_upsert, timeout=5.0)
    generate_calendar.build_ics()
    return len(exams)


if __name__ == "__main__":
    count = import_exams()
    print(f"Imported/updated {count} CC1 exam(s).")
