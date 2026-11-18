#!/usr/bin/env python3
"""
Generate standard RFC 5545 iCalendar (.ics) from tasks.json
and provide desktop notify-send integration for Hyprland / Omarchy.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import sys

PLAN_DIR = Path(__file__).resolve().parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

import config

TASKS_PATH = str(config.TASKS_PATH)
ICS_PATH = str(config.ICS_PATH)


def escape_ics_text(text: str | None) -> str:
    """Escapes RFC 5545 TEXT values: backslash, semicolon, comma, and newlines."""
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\n", "\\n")
    return s


def fold_ics_line(line: str, max_octets: int = 75) -> list[str]:
    """
    Folds an RFC 5545 content line so no physical line exceeds max_octets UTF-8 bytes
    (excluding the CRLF terminator). Continuation lines begin with a single space (1 octet).
    """
    raw_bytes = line.encode("utf-8")
    if len(raw_bytes) <= max_octets:
        return [line]

    chunks: list[str] = []
    cur_chars: list[str] = []
    cur_bytes = 0
    limit = max_octets

    for ch in line:
        ch_len = len(ch.encode("utf-8"))
        if cur_bytes + ch_len > limit and cur_chars:
            chunks.append("".join(cur_chars))
            cur_chars = [" ", ch]
            cur_bytes = 1 + ch_len
            limit = max_octets
        else:
            cur_chars.append(ch)
            cur_bytes += ch_len

    if cur_chars:
        chunks.append("".join(cur_chars))
    return chunks


def _to_local_tz_dt(ts: str) -> str:
    """
    deadline_utils.format_iso_end stamps local Casablanca wall-clock times with a trailing 'Z'.
    Strip trailing 'Z' so it can be paired with TZID=Africa/Casablanca.
    """
    cleaned = (ts or "").strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
    cleaned = cleaned.replace("-", "").replace(":", "")
    return cleaned


def render_ics(tasks: list[dict], now_utc: datetime | None = None) -> tuple[str, int]:
    """Builds the RFC 5545 calendar content string and returns (ics_text, event_count)."""
    now_dt = now_utc or datetime.now(timezone.utc)
    now_str = now_dt.strftime("%Y%m%dT%H%M%SZ")
    today_end = datetime.now().strftime("%Y%m%dT235900")

    raw_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//EMSI Tanger//School Plan Agent 2.0//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:EMSI - Planning & Devoirs",
        "X-WR-TIMEZONE:Africa/Casablanca",
        f"X-WR-CALDESC:{escape_ics_text('Calendrier scolaire synchronisé automatiquement depuis Outlook et Google Classroom')}",
        "BEGIN:VTIMEZONE",
        "TZID:Africa/Casablanca",
        "BEGIN:STANDARD",
        "DTSTART:20200101T000000",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0100",
        "TZNAME:+01",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    event_count = 0
    for t in tasks:
        if t.get("status") == "DONE":
            continue
        event_count += 1
        uid = f"{t.get('id', 'task')}@{config.CALENDAR_UID_DOMAIN}"
        dtend = _to_local_tz_dt(t.get("end") or "") or today_end
        dtstart = _to_local_tz_dt(t.get("start") or "") or dtend
        summary_raw = t.get("title", "Tâche scolaire")
        summary = escape_ics_text(summary_raw)
        desc_raw = (
            f"Module: {t.get('module')} | Prof: {t.get('teacher')}\n"
            f"Source: {t.get('source')}\n\n"
            f"{t.get('description', '')}"
        )
        desc = escape_ics_text(desc_raw)
        priority = "1" if t.get("priority") == "HIGH" else "5"
        is_exam = t.get("task_type") == "EXAMEN"

        raw_lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_str}",
                f"DTSTART;TZID=Africa/Casablanca:{dtstart}",
                f"DTEND;TZID=Africa/Casablanca:{dtend}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{desc}",
                f"PRIORITY:{priority}",
                "STATUS:CONFIRMED",
            ]
        )
        if is_exam:
            raw_lines.append("CATEGORIES:EXAMEN")

        raw_lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{escape_ics_text('Rappel: ' + summary_raw)}",
                "TRIGGER:-PT2H",
                "END:VALARM",
            ]
        )
        if is_exam:
            raw_lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{escape_ics_text('Examen J-1: ' + summary_raw)}",
                    "TRIGGER:-P1D",
                    "END:VALARM",
                ]
            )
        raw_lines.append("END:VEVENT")

    raw_lines.append("END:VCALENDAR")

    folded_lines: list[str] = []
    for line in raw_lines:
        folded_lines.extend(fold_ics_line(line, max_octets=75))

    return "\r\n".join(folded_lines) + "\r\n", event_count


def build_ics(tasks_path: str = TASKS_PATH, ics_path: str = ICS_PATH) -> int:
    if not os.path.exists(tasks_path):
        print(f"Error: {tasks_path} not found.")
        return 0

    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    ics_content, event_count = render_ics(tasks)
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics_content)

    print(f"Generated {ics_path} with {event_count} events.")
    return event_count


def notify_today(tasks_path: str = TASKS_PATH) -> None:
    if not os.path.exists(tasks_path):
        return

    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_tasks = [
        t
        for t in tasks
        if t.get("status") != "DONE"
        and (t.get("deadline", "").startswith(today_str) or t.get("status") == "URGENT_TODAY")
    ]

    if today_tasks:
        body = "\n".join(
            [f"• {t['title']} (avant {(t.get('deadline', '') or '23:59').split()[-1]})" for t in today_tasks]
        )
        cmd = [
            "notify-send",
            "-a",
            "School Planner",
            "-u",
            "critical",
            "-i",
            "calendar",
            "📅 Devoirs Urgents Aujourd'hui !",
            body,
        ]
        try:
            subprocess.run(cmd)
        except Exception:
            pass


if __name__ == "__main__":
    build_ics()
    notify_today()
