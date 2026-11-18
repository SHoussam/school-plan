#!/usr/bin/env python3
"""
Shared deadline parsing, calendar placement, and sorting utilities
for the EMSI Tanger School Plan widget and AI refresher.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

MONTH_MAP = {
    "jan": 1, "janv": 1, "janvier": 1, "january": 1,
    "fev": 2, "fevr": 2, "fevrier": 2, "feb": 2, "february": 2,
    "mar": 3, "mars": 3, "march": 3,
    "avr": 4, "avril": 4, "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "jun": 6, "june": 6,
    "juil": 7, "juillet": 7, "jul": 7, "july": 7,
    "aou": 8, "aout": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "septembre": 9, "september": 9,
    "oct": 10, "octobre": 10, "october": 10,
    "nov": 11, "novembre": 11, "november": 11,
    "dec": 12, "decembre": 12, "december": 12,
}

WEEKDAY_MAP = {
    "lundi": 0, "lun": 0, "monday": 0, "mon": 0,
    "mardi": 1, "mar": 1, "tuesday": 1, "tue": 1,
    "mercredi": 2, "mer": 2, "wednesday": 2, "wed": 2,
    "jeudi": 3, "jeu": 3, "thursday": 3, "thu": 3,
    "vendredi": 4, "ven": 4, "friday": 4, "fri": 4,
    "samedi": 5, "sam": 5, "saturday": 5, "sat": 5,
    "dimanche": 6, "dim": 6, "sunday": 6, "sun": 6,
}

UNDATED_MARKERS = (
    "aucune date limite",
    "no due date",
    "pas de date limite",
    "sans echeance",
    "sans date",
)

PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _resolve_year_for_month_day(month: int, day: int, ref_date: date) -> date | None:
    """Resolve the most plausible year for a month/day near ref_date."""
    candidates = []
    for yr in (ref_date.year - 1, ref_date.year, ref_date.year + 1):
        try:
            d = date(yr, month, day)
        except ValueError:
            continue
        delta = (d - ref_date).days
        # Prefer dates within [-60, +300] days of ref_date
        score = abs(delta) if -60 <= delta <= 300 else abs(delta) + 400
        candidates.append((score, d))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def parse_iso_date_time(raw_ts: str) -> tuple[date | None, str]:
    """Extract (date_obj, 'HH:MM') from an ISO-like timestamp string."""
    if not raw_ts:
        return None, ""
    raw = str(raw_ts).strip()
    # Extract date portion from YYYY-MM-DD... or YYYYMMDD...
    m_date = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", raw)
    if not m_date:
        return None, ""
    try:
        d_obj = date(int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3)))
    except ValueError:
        return None, ""

    t_str = ""
    if "T" in raw:
        tp = raw.split("T", 1)[1].replace(":", "")
        if len(tp) >= 4 and tp[:4].isdigit():
            hh, mm = int(tp[:2]), int(tp[2:4])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                t_str = f"{hh:02d}:{mm:02d}"
    return d_obj, t_str


def _extract_time_str(norm_text: str) -> str:
    m_time = re.search(r"\b(\d{1,2})\s*[:h]\s*(\d{2})\b", norm_text)
    if not m_time:
        return ""
    hh, mm = int(m_time.group(1)), int(m_time.group(2))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return ""


def parse_deadline_text(
    text: str,
    ref_date: date | None = None,
    allow_relative: bool = True,
) -> tuple[date | None, str]:
    """Parse an explicit date and optional HH:MM from a deadline label."""
    if not text:
        return None, ""
    ref = ref_date or date.today()
    norm = _strip_accents(str(text)).lower().strip()

    if any(marker in norm for marker in UNDATED_MARKERS):
        return None, ""

    t_str = _extract_time_str(norm)

    # 1. YYYY-MM-DD or YYYY/MM/DD
    m_iso = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", norm)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))), t_str
        except ValueError:
            pass

    # 2. DD/MM/YYYY or DD-MM-YYYY
    m_dmy_full = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b", norm)
    if m_dmy_full:
        try:
            return date(int(m_dmy_full.group(3)), int(m_dmy_full.group(2)), int(m_dmy_full.group(1))), t_str
        except ValueError:
            pass

    # 3. DD/MM (without year; require '/' to avoid matching ranges like 10-11)
    m_dm = re.search(r"\b(\d{1,2})/(\d{1,2})\b", norm)
    if m_dm:
        d_obj = _resolve_year_for_month_day(int(m_dm.group(2)), int(m_dm.group(1)), ref)
        if d_obj:
            return d_obj, t_str

    # 4. "21 nov", "21 nov.", "21 novembre"
    month_pat = "|".join(sorted(MONTH_MAP.keys(), key=len, reverse=True))
    m_d_mon = re.search(rf"\b(\d{{1,2}})\s+({month_pat})\.?\b", norm)
    if m_d_mon:
        mon = MONTH_MAP.get(m_d_mon.group(2))
        if mon:
            d_obj = _resolve_year_for_month_day(mon, int(m_d_mon.group(1)), ref)
            if d_obj:
                return d_obj, t_str

    # 5. "Nov 21", "Nov. 21", "November 21"
    m_mon_d = re.search(rf"\b({month_pat})\.?\s+(\d{{1,2}})\b", norm)
    if m_mon_d:
        mon = MONTH_MAP.get(m_mon_d.group(1))
        if mon:
            d_obj = _resolve_year_for_month_day(mon, int(m_mon_d.group(2)), ref)
            if d_obj:
                return d_obj, t_str

    if not allow_relative:
        return None, ""

    # 6. Relative keywords
    if "apres-demain" in norm or "apres demain" in norm:
        return ref + timedelta(days=2), t_str
    if "aujourd'hui" in norm or "aujourdhui" in norm or re.search(r"\btoday\b", norm):
        return ref, t_str
    if re.search(r"\bdemain\b|\btomorrow\b", norm):
        return ref + timedelta(days=1), t_str
    if re.search(r"\bhier\b|\byesterday\b", norm):
        return ref - timedelta(days=1), t_str

    # 7. Weekday names (avoid matching "mar" as March — March already matched with day number above)
    wd_pat = "|".join(sorted(WEEKDAY_MAP.keys(), key=len, reverse=True))
    m_wd = re.search(rf"\b({wd_pat})\.?\b", norm)
    if m_wd:
        target_wd = WEEKDAY_MAP[m_wd.group(1)]
        delta = (target_wd - ref.weekday()) % 7
        return ref + timedelta(days=delta), t_str

    return None, ""


def extract_due_text_from_raw(raw_text: str) -> str:
    """Extract an explicit deadline phrase from raw Classroom/Outlook text."""
    if not raw_text:
        return ""

    # Ignore creation/edit timestamps when scanning raw posts
    cleaned = re.sub(
        r"(?:created|posted|edited|cree(?:\s+le)?|publie(?:\s+le)?|modifie(?:\s+le)?)\s+[^\n|]{1,35}",
        " ",
        raw_text,
        flags=re.IGNORECASE,
    )

    patterns = [
        r"(?:date\s+limite|echeance|échéance)\s*[:\-]?\s*([^\n|]{2,45})",
        r"\b(?:due(?:\s+date)?)\s*[:\-]?\s*([^\n|]{2,45})",
        r"(?:a\s+rendre|à\s+rendre)\s+(?:avant\s+le|pour\s+le|avant|pour)\s*([^\n|]{2,45})",
        r"(?:report[eé]e?\s+au)\s+([^\n|]{2,45})",
    ]
    for pat in patterns:
        m = re.search(pat, cleaned, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" .:-")
            norm_cand = _strip_accents(candidate).lower()
            if any(marker in norm_cand for marker in UNDATED_MARKERS):
                return ""
            d_obj, _ = parse_deadline_text(candidate, allow_relative=True)
            if d_obj is not None:
                return candidate
    return ""


def format_iso_end(d_obj: date, t_str: str = "") -> str:
    """Format a date and optional HH:MM as compact UTC-style ISO timestamp."""
    hhmm = t_str.replace(":", "") if t_str else "2359"
    if len(hhmm) != 4 or not hhmm.isdigit():
        hhmm = "2359"
    return f"{d_obj.strftime('%Y%m%d')}T{hhmm}00Z"


def resolve_task_deadline(task: dict, today: date | None = None) -> tuple[date | None, str]:
    """Return the task's real (due_date, due_time) or (None, '') if missing a deadline."""
    today = today or date.today()
    if task.get("deadline_missing"):
        return None, ""

    end_d, end_t = parse_iso_date_time(task.get("end", ""))
    if end_d is not None:
        return end_d, end_t

    dl_text = (task.get("deadline") or "").strip()
    if dl_text:
        start_d, _ = parse_iso_date_time(task.get("start", ""))
        ref = start_d or today
        dl_d, dl_t = parse_deadline_text(dl_text, ref_date=ref, allow_relative=True)
        if dl_d is not None:
            return dl_d, dl_t

    return None, ""


def resolve_task_calendar_slot(task: dict, today: date | None = None) -> dict:
    """Resolve which calendar day column a task belongs to and how it should be sorted.

    Rules:
    - Active tasks with an explicit future/today deadline day sit on that deadline day.
    - Active tasks missing a deadline OR overdue/missed (due_date < today, status == 'LATE',
      or classroom_missing) are placed on the current day (today).
    - Completed (DONE, non-mismatch) tasks stay on their deadline day, or their completion
      day (completed_at -> start -> today) when they had no deadline.
    """
    today = today or date.today()
    is_mismatch = (task.get("submission_mismatch") == "DONE_BUT_NOT_SUBMITTED")
    is_done = (task.get("status") == "DONE" and not is_mismatch)

    due_date, due_time = resolve_task_deadline(task, today=today)
    is_missing_deadline = (due_date is None)
    is_overdue = (
        not is_done
        and (
            (due_date is not None and due_date < today)
            or task.get("status") == "LATE"
            or bool(task.get("classroom_missing"))
        )
    )

    if is_done:
        if due_date is not None:
            display_date = due_date
        else:
            comp_d, _ = parse_iso_date_time(task.get("completed_at", ""))
            start_d, _ = parse_iso_date_time(task.get("start", ""))
            display_date = comp_d or start_d or today
    else:
        if is_missing_deadline or is_overdue:
            display_date = today
        else:
            display_date = due_date

    pri_rank = PRIORITY_RANK.get((task.get("priority") or "MEDIUM").upper(), 1)
    title_l = (task.get("title") or "").lower()

    if is_done:
        urgency_bucket = 4
    elif is_mismatch:
        urgency_bucket = 0
    elif is_overdue:
        urgency_bucket = 1
    elif not is_missing_deadline:
        urgency_bucket = 2
    else:
        urgency_bucket = 3

    within_day_key = (
        1 if is_done else 0,
        urgency_bucket,
        due_date or display_date,
        due_time or "99:99",
        pri_rank,
        title_l,
    )
    list_sort_key = (
        display_date,
        urgency_bucket,
        due_date or display_date,
        due_time or "99:99",
        pri_rank,
        title_l,
    )

    return {
        "display_date": display_date,
        "due_date": due_date,
        "due_time": due_time,
        "is_done": is_done,
        "is_mismatch": is_mismatch,
        "is_missing_deadline": is_missing_deadline,
        "is_overdue": is_overdue,
        "within_day_key": within_day_key,
        "list_sort_key": list_sort_key,
    }
