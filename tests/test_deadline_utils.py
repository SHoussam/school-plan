#!/usr/bin/env python3
"""Unit tests for deadline_utils.py."""
from datetime import date
from pathlib import Path
import sys
import unittest

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan.deadline_utils import (
    extract_due_text_from_raw,
    format_iso_end,
    parse_deadline_text,
    parse_iso_date_time,
    resolve_task_calendar_slot,
    resolve_task_deadline,
)


class DeadlineUtilsTest(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 17)  # Tuesday

    def test_parse_iso_date_time(self):
        self.assertEqual(parse_iso_date_time("20261119T170000Z"), (date(2026, 11, 19), "17:00"))
        self.assertEqual(parse_iso_date_time("2026-11-21T23:59:00Z"), (date(2026, 11, 21), "23:59"))
        self.assertEqual(parse_iso_date_time(""), (None, ""))

    def test_parse_deadline_text_formats(self):
        self.assertEqual(parse_deadline_text("Samedi 21 Nov 23:59", self.today), (date(2026, 11, 21), "23:59"))
        self.assertEqual(parse_deadline_text("2026-11-20 14:30", self.today), (date(2026, 11, 20), "14:30"))
        self.assertEqual(parse_deadline_text("21/11/2026 18h00", self.today), (date(2026, 11, 21), "18:00"))
        self.assertEqual(parse_deadline_text("Demain 12:00", self.today), (date(2026, 11, 18), "12:00"))
        self.assertEqual(parse_deadline_text("Jeudi 10:00", self.today), (date(2026, 11, 19), "10:00"))
        self.assertEqual(parse_deadline_text("Aucune date limite", self.today), (None, ""))
        self.assertEqual(parse_deadline_text("No due date", self.today), (None, ""))
        self.assertEqual(parse_deadline_text("À rendre", self.today), (None, ""))
        self.assertEqual(parse_deadline_text("CERTIFICATION Oracle19c Etape 4: (10-11)", self.today), (None, ""))

    def test_extract_due_text_ignores_created(self):
        raw_created_only = "Created 3 Nov\nAssignment: Exercice\nAucune date limite"
        self.assertEqual(extract_due_text_from_raw(raw_created_only), "")

        raw_with_due = "Created 3 Nov\nDate limite : 21 nov., 23:59\nExercice"
        self.assertEqual(extract_due_text_from_raw(raw_with_due), "21 nov., 23:59")

    def test_future_deadline_stays_on_deadline_day(self):
        task = {"title": "Serie 2", "end": "20261121T235900Z", "status": "UPCOMING"}
        slot = resolve_task_calendar_slot(task, today=self.today)
        self.assertEqual(slot["display_date"], date(2026, 11, 21))
        self.assertFalse(slot["is_missing_deadline"])
        self.assertFalse(slot["is_overdue"])

    def test_missing_deadline_active_moves_to_today(self):
        task = {
            "title": "TD 1 Chapitre 2",
            "start": "20261116T100000Z",
            "end": "",
            "deadline": "À rendre",
            "status": "UPCOMING",
        }
        slot = resolve_task_calendar_slot(task, today=self.today)
        self.assertEqual(slot["display_date"], self.today)
        self.assertTrue(slot["is_missing_deadline"])
        self.assertFalse(slot["is_overdue"])

    def test_overdue_active_moves_to_today(self):
        task = {
            "title": "NoSQL TP",
            "end": "20261116T103000Z",
            "deadline": "Séance prochaine",
            "status": "UPCOMING",
        }
        slot = resolve_task_calendar_slot(task, today=self.today)
        self.assertEqual(slot["display_date"], self.today)
        self.assertEqual(slot["due_date"], date(2026, 11, 16))
        self.assertTrue(slot["is_overdue"])

    def test_done_task_keeps_deadline_or_completion_day(self):
        done_with_dl = {
            "title": "Etape 0",
            "end": "20261115T235900Z",
            "status": "DONE",
            "completed_at": "2026-11-16T00:08:23",
        }
        slot1 = resolve_task_calendar_slot(done_with_dl, today=self.today)
        self.assertEqual(slot1["display_date"], date(2026, 11, 15))

        done_no_dl = {
            "title": "Connexion Oracle",
            "start": "20261116T232200Z",
            "end": "",
            "deadline": "À rendre",
            "status": "DONE",
            "completed_at": "2026-11-17T10:16:21",
        }
        slot2 = resolve_task_calendar_slot(done_no_dl, today=date(2026, 11, 18))
        self.assertEqual(slot2["display_date"], date(2026, 11, 17))

    def test_mismatch_treated_as_active_and_sorted_first(self):
        mismatch = {
            "title": "Mismatch Task",
            "end": "",
            "status": "DONE",
            "submission_mismatch": "DONE_BUT_NOT_SUBMITTED",
        }
        overdue = {"title": "Overdue", "end": "20261116T103000Z", "status": "UPCOMING"}
        today_timed = {"title": "Today 14:00", "end": "20261117T140000Z", "status": "UPCOMING"}
        missing_dl = {"title": "No date", "end": "", "priority": "HIGH", "status": "UPCOMING"}

        items = [missing_dl, today_timed, overdue, mismatch]
        ordered = sorted(items, key=lambda t: resolve_task_calendar_slot(t, today=self.today)["within_day_key"])
        self.assertEqual([t["title"] for t in ordered], ["Mismatch Task", "Overdue", "Today 14:00", "No date"])

    def test_format_iso_end(self):
        self.assertEqual(format_iso_end(date(2026, 11, 21), "23:59"), "20261121T235900Z")
        self.assertEqual(format_iso_end(date(2026, 11, 21), ""), "20261121T235900Z")


if __name__ == "__main__":
    unittest.main()
