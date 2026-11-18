#!/usr/bin/env python3
"""Unit tests for generate_calendar.py (RFC 5545 escaping, line folding, and EXAMEN alarms)."""

from pathlib import Path
import sys

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

import generate_calendar


def test_escape_ics_text_escapes_special_characters():
    raw = "TD 1, Série 2; Chemin C:\\temp\nDeuxième ligne"
    escaped = generate_calendar.escape_ics_text(raw)
    assert escaped == "TD 1\\, Série 2\\; Chemin C:\\\\temp\\nDeuxième ligne"


def test_fold_ics_line_respects_75_octet_limit_with_multibyte_utf8():
    long_line = "SUMMARY:" + ("Épreuve avancée de virtualisation, " * 6)
    folded = generate_calendar.fold_ics_line(long_line, max_octets=75)
    assert len(folded) > 1
    for idx, physical_line in enumerate(folded):
        octets = len(physical_line.encode("utf-8"))
        assert octets <= 75, f"Line {idx} has {octets} octets (> 75)"
        if idx > 0:
            assert physical_line.startswith(" ")

    # Unfolding restores exact original string
    unfolded = folded[0] + "".join(part[1:] for part in folded[1:])
    assert unfolded == long_line


def test_render_ics_includes_examen_category_and_p1d_alarm_and_counts_active_only():
    tasks = [
        {
            "id": "exam-cc1-nosql",
            "title": "CC1 — NoSQL, MongoDB; Cypher",
            "module": "NoSQL",
            "teacher": "Houda Zaim",
            "start": "20261124T110000Z",
            "end": "20261124T123000Z",
            "priority": "HIGH",
            "status": "UPCOMING",
            "task_type": "EXAMEN",
            "source": "Planification CC 4IIR.xlsx",
            "description": "Salle SC 31, Groupe 1;\nBonne chance",
        },
        {
            "id": "done-task",
            "title": "Terminé",
            "status": "DONE",
        },
    ]

    ics_text, count = generate_calendar.render_ics(tasks)
    assert count == 1
    assert "CATEGORIES:EXAMEN" in ics_text
    assert "TRIGGER:-P1D" in ics_text
    assert "TRIGGER:-PT2H" in ics_text
    assert "DTSTART;TZID=Africa/Casablanca:20261124T110000" in ics_text
    assert "DTEND;TZID=Africa/Casablanca:20261124T123000" in ics_text

    for line in ics_text.split("\r\n"):
        if line:
            assert len(line.encode("utf-8")) <= 75
