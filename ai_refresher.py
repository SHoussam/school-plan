#!/usr/bin/env python3
"""
AI Academic Refresher for EMSI Tanger.
Coordinates:
1. Browser Harvester (live signed-in Google Classroom & Outlook Web via read-only CDP)
2. Local AI Model (Gemma 4 on LM Studio via local_ai.py)
3. Course Document Organizer (copies files to SCHOOL_DOCS_DIR/<Module>/)
4. Calendar & Task Synchronizer (atomically updates tasks.json via task_store.py and regenerates school-schedule.ics)
"""

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

PLAN_DIR = Path(__file__).resolve().parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

import config
from config import (
    BASE_9RAYA,
    DOWNLOADS_DIR,
    HARVESTER_SCRIPT,
    META_PATH,
    MODULE_PATTERNS,
    PROFESSORS,
    STATUS_PATH,
    canonical_module,
    module_for_text,
)
from deadline_utils import extract_due_text_from_raw, format_iso_end, parse_deadline_text
import local_ai
import task_store

HARVESTER_TIMEOUT_S = 28


def write_status(status: dict) -> None:
    """Atomically writes status dictionary to config.STATUS_PATH."""
    target = Path(STATUS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def resolve_module_dir(module_name: str, hint_text: str = "") -> str:
    """Finds or creates the appropriate canonical course folder in ~/Documents/9raya."""
    canon = canonical_module(module_name)
    if canon == "Divers" and hint_text:
        canon = module_for_text(f"{module_name} {hint_text}")

    if canon != "Divers":
        target = os.path.join(str(BASE_9RAYA), canon)
        os.makedirs(target, exist_ok=True)
        return target

    safe_name = re.sub(r'[\/:*?"<>|]', "_", (module_name or "").strip()) or "Divers"
    target = os.path.join(str(BASE_9RAYA), safe_name)
    os.makedirs(target, exist_ok=True)
    return target


def inspect_file_sample_text(fpath: str) -> str:
    """Extracts text sample from office documents / PDFs for intelligent classification."""
    low = fpath.lower()
    try:
        if low.endswith(".docx"):
            with zipfile.ZipFile(fpath) as z:
                tree = ET.fromstring(z.read("word/document.xml"))
                return " ".join(tree.itertext())[:2000]
        elif low.endswith(".pptx"):
            with zipfile.ZipFile(fpath) as z:
                slides = [n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")][:5]
                text = ""
                for s in slides:
                    tree = ET.fromstring(z.read(s))
                    text += " " + " ".join(tree.itertext())
                return text[:2000]
        elif low.endswith(".pdf"):
            try:
                return subprocess.check_output(["pdftotext", fpath, "-"], text=True, timeout=5)[:2000]
            except Exception:
                with open(fpath, "rb") as f:
                    return f.read(3000).decode("latin1", errors="ignore")
    except Exception:
        pass
    return ""


def classify_file(fname: str, fpath: str) -> str:
    """Returns canonical module name for a file based on name and inner content."""
    mod = module_for_text(fname)
    if mod != "Divers":
        return mod

    sample = inspect_file_sample_text(fpath)
    if sample:
        mod = module_for_text(sample)
        if mod != "Divers":
            return mod

    return "Divers"


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def organize_downloaded_files(dry_run: bool = False) -> list[dict]:
    """Scans ~/Downloads for academic files and organizes them into ~/Documents/9raya/<Module>/."""
    moved_files = []
    dl_dir = str(DOWNLOADS_DIR)
    if not os.path.exists(dl_dir):
        return moved_files

    extensions = (".pdf", ".docx", ".pptx", ".zip", ".ipynb", ".sql", ".ova", ".xlsx", ".txt")
    for fname in os.listdir(dl_dir):
        fpath = os.path.join(dl_dir, fname)
        if not os.path.isfile(fpath) or not fname.lower().endswith(extensions):
            continue

        mod = classify_file(fname, fpath)
        if mod and mod != "Divers":
            target_dir = os.path.join(str(BASE_9RAYA), mod)
            dest_path = os.path.join(target_dir, fname)

            if os.path.exists(dest_path) and sha256_file(fpath) == sha256_file(dest_path):
                continue

            if not dry_run:
                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(fpath, dest_path)
            moved_files.append({"source": fpath, "dest": dest_path, "module": mod})

    return moved_files


def run_harvester() -> dict:
    """Runs browser_harvester.js (strictly read-only CDP) to capture open Classroom & Outlook state."""
    if not os.path.exists(str(HARVESTER_SCRIPT)):
        return {"classroom": [], "outlook": []}
    try:
        proc = subprocess.run(
            ["node", str(HARVESTER_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=HARVESTER_TIMEOUT_S,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception as e:
        print(f"[AI Refresher] Browser harvester error: {e}", file=sys.stderr)
    return {"classroom": [], "outlook": []}


def extract_academic_tasks(harvested: dict, dry_run: bool = False):
    """Accurately parses all academic posts & emails into structured task items."""
    extracted = []
    seen_titles = set()
    seen_files = set()
    classroom_submissions = {}
    specific_urls = {}
    classroom_due_texts = {}
    classroom_missing_keys = set()

    # 1. Process Google Classroom tabs
    for c_tab in harvested.get("classroom", []):
        url = c_tab.get("url", config.CLASSROOM_URL)
        title_tab = c_tab.get("title", "")
        current_sub = c_tab.get("currentSubmission")

        if current_sub in ("SUBMITTED", "NOT_SUBMITTED"):
            if title_tab:
                classroom_submissions[title_tab.lower()] = current_sub
            if url:
                classroom_submissions[url.lower()] = current_sub

        for asgn in c_tab.get("assignments", []):
            a_title = asgn.get("title", "").strip()
            a_status = asgn.get("submission_status")
            a_url = asgn.get("url", "")
            raw_txt = asgn.get("raw", "")
            due_text = (asgn.get("due_text") or "").strip() or extract_due_text_from_raw(raw_txt)

            if not a_title or len(a_title) < 4:
                continue

            lower_title = a_title.lower().strip()
            if lower_title == "book" or any(
                n in lower_title
                for n in [
                    "post by",
                    "more options",
                    "comment",
                    "announcement",
                    "more_vert",
                    "assignment",
                    "to do",
                    "à faire",
                    "non remis",
                    "remis",
                ]
            ):
                continue

            if a_status in ("SUBMITTED", "NOT_SUBMITTED"):
                if classroom_submissions.get(lower_title) != "SUBMITTED":
                    classroom_submissions[lower_title] = a_status
                if a_url and classroom_submissions.get(a_url.lower()) != "SUBMITTED":
                    classroom_submissions[a_url.lower()] = a_status

            if due_text:
                classroom_due_texts[lower_title] = due_text
                if a_url:
                    classroom_due_texts[a_url.lower()] = due_text

            if asgn.get("is_missing"):
                classroom_missing_keys.add(lower_title)
                if a_url:
                    classroom_missing_keys.add(a_url.lower())

            if a_url and ("/a/" in a_url or "/sa/" in a_url or "/m/" in a_url):
                specific_urls[lower_title] = a_url

            if c_tab.get("is_status_sync"):
                continue

            if (
                not ("/a/" in a_url or "/sa/" in a_url)
                or "not-turned-in" in a_url
                or "turned-in" in a_url
                or "/calendar/" in a_url
            ):
                continue

            norm_k = re.sub(r"[\s:_-]+", "", lower_title)
            if norm_k in seen_titles:
                continue
            seen_titles.add(norm_k)

            mod = module_for_text(f"{title_tab} {a_title} {raw_txt}")

            extracted.append(
                {
                    "title": a_title,
                    "module": mod,
                    "teacher": "",
                    "deadline": due_text or "À rendre",
                    "due_text": due_text,
                    "priority": "HIGH",
                    "status": "DONE" if a_status == "SUBMITTED" else "UPCOMING",
                    "source": "Google Classroom",
                    "url": a_url,
                    "file_name": "",
                    "local_path": "",
                    "submission_status": a_status if a_status in ("SUBMITTED", "NOT_SUBMITTED") else "NOT_SUBMITTED",
                    "submission_mismatch": None,
                    "description": raw_txt[:300],
                }
            )

    # 2. Process Outlook Web emails
    for o_view in harvested.get("outlook", []):
        for em in o_view.get("emails", []):
            raw = em.get("raw", "")
            sender = em.get("sender", "").strip()

            if (
                sender.lower() in ("downloaded", "374 kb")
                or sender.endswith("@messaging.microsoft.com")
                or "@oracle.com" in sender.lower()
            ):
                continue

            if any(
                ign in raw.lower()
                for ign in [
                    "security: you have messages",
                    "code secret",
                    "password has been reset",
                    "compte corpexternalprod",
                    "job alerts",
                    "linkedin",
                    "usage unique",
                    "verrouillé",
                    "reset your oracle account",
                    "your oracle account",
                    "quarantine",
                ]
            ):
                continue

            parts = [p.strip() for p in raw.split("|") if p.strip()]

            title = ""
            for p in parts:
                if any(
                    k in p.lower()
                    for k in [
                        "planning des cc",
                        "planning",
                        "support de cours",
                        "compte rendu",
                        "révisez",
                        "td :",
                        "tp :",
                        "devoir",
                        "session",
                    ]
                ):
                    title = p
                    break

            if not title:
                for p in parts:
                    if len(p) > 5 and not p.startswith("ATTENTION") and not p.startswith("Bonjour") and "@" not in p:
                        if re.match(
                            r"^(mon|tue|wed|thu|fri|sat|sun|lun|mar|mer|jeu|ven|sam|dim)\s+\d{1,2}[:h]\d{2}$",
                            p.strip().lower(),
                        ):
                            continue
                        if re.match(r"^\d{1,2}[:h]\d{2}$", p.strip().lower()):
                            continue
                        if p.strip().lower().endswith((".pdf", ".docx", ".pptx", ".zip", ".xlsx")):
                            continue
                        if any(prof.lower() in p.lower() for prof in PROFESSORS) or (
                            sender and p.lower() == sender.lower()
                        ):
                            continue
                        title = p
                        break

            if not title or title.strip().lower().endswith((".pdf", ".docx", ".pptx", ".zip")):
                continue

            norm_title = re.sub(r"[\s:_-]+", "", title.lower())
            if norm_title in seen_titles:
                continue

            teacher = ""
            for t in PROFESSORS:
                if t.lower() in raw.lower():
                    teacher = t
                    break
            if not teacher:
                if sender and sender not in ["Oracle", "ST", "ZR", "", "Downloaded"]:
                    teacher = sender

            mod = module_for_text(raw)

            att_match = re.search(
                r"([\w\s_.-]+\.(?:pdf|xlsx?|docx?|pptx?|zip|sql|ova|ipynb))",
                raw,
                re.IGNORECASE,
            )
            file_name = att_match.group(1).strip() if att_match else ""
            if file_name and (len(file_name) < 5 or file_name.lower().startswith("attention")):
                file_name = ""

            local_path = ""
            if file_name:
                if mod != "Divers":
                    expected_dest = os.path.join(str(BASE_9RAYA), mod, file_name)
                    if os.path.exists(expected_dest):
                        local_path = expected_dest

                if not local_path and os.path.exists(str(BASE_9RAYA)):
                    for root, _, files in os.walk(str(BASE_9RAYA)):
                        if file_name in files:
                            local_path = os.path.join(root, file_name)
                            break

                if not local_path:
                    dl_file = os.path.join(str(DOWNLOADS_DIR), file_name)
                    if os.path.exists(dl_file):
                        target_mod_dir = os.path.join(str(BASE_9RAYA), mod if mod != "Divers" else "Divers")
                        dest_path = os.path.join(target_mod_dir, file_name)
                        if not dry_run:
                            os.makedirs(target_mod_dir, exist_ok=True)
                            shutil.copy2(dl_file, dest_path)
                        local_path = dest_path

            due_text = extract_due_text_from_raw(raw)
            deadline = due_text or "Séance prochaine"
            if not due_text:
                if "aujourd'hui" in raw.lower():
                    deadline = "Aujourd'hui 23:59"
                elif "cc" in raw.lower() or "planning" in raw.lower():
                    deadline = "Session CC S1"

            seen_titles.add(norm_title)
            if file_name:
                seen_files.add(file_name.lower())

            extracted.append(
                {
                    "title": title,
                    "module": mod,
                    "teacher": teacher,
                    "deadline": deadline,
                    "due_text": due_text,
                    "priority": "HIGH"
                    if any(k in title.lower() for k in ["cc", "compte rendu", "tp", "td"])
                    else "MEDIUM",
                    "status": "URGENT_TODAY" if "aujourd'hui" in deadline.lower() else "UPCOMING",
                    "source": "Outlook",
                    "url": config.OUTLOOK_URL,
                    "file_name": file_name,
                    "local_path": local_path,
                    "description": raw[:350],
                }
            )

    return extracted, classroom_submissions, specific_urls, classroom_due_texts, classroom_missing_keys


def _is_v2_migrated() -> bool:
    if not META_PATH.exists():
        return False
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("_schema") or 0) >= 2
    except Exception:
        return False


def _mark_v2_migrated() -> None:
    meta = {}
    if META_PATH.exists():
        try:
            with open(META_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta["_schema"] = 2
    meta["migrated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _is_junk_task(task: dict) -> bool:
    title = (task.get("title") or "").strip()
    if title.lower() in ("book", "more_vert", "assignment"):
        return True
    if len(title) > 4:
        return False
    url = (task.get("url") or "").strip()
    has_specific_url = any(x in url for x in ("/a/", "/sa/", "/m/"))
    has_local_path = bool((task.get("local_path") or "").strip())
    return not has_specific_url and not has_local_path


def _merge_and_migrate_tasks(
    tasks: list[dict],
    candidates: list[dict],
    classroom_submissions: dict,
    specific_urls: dict,
    classroom_due_texts: dict,
    classroom_missing_keys: set,
    dry_run: bool = False,
) -> dict:
    """
    Applies one-time schema v2 migration (canonical modules + junk removal)
    and merges harvested/AI-enriched candidates into tasks list in place.
    Returns counters dict.
    """
    migrated_modules = 0
    removed_junk = []

    need_migration = not _is_v2_migrated()
    if need_migration and not dry_run:
        task_store.backup("pre-v2")

    # Always canonicalize modules; if migrating (or junk present), remove junk <= 4 chars without specific URL/file
    kept_tasks = []
    for t in tasks:
        if _is_junk_task(t):
            removed_junk.append(t.get("title", ""))
            print(f"🗑️ [Migration] Removed junk task: {t.get('id')} ({t.get('title')!r})")
            continue
        old_mod = t.get("module")
        new_mod = canonical_module(old_mod)
        if new_mod == "Divers" and t.get("title"):
            inferred = module_for_text(f"{old_mod or ''} {t.get('title', '')}")
            if inferred != "Divers":
                new_mod = inferred
        if old_mod != new_mod:
            t["module"] = new_mod
            migrated_modules += 1
        kept_tasks.append(t)

    tasks[:] = kept_tasks
    if need_migration and not dry_run:
        _mark_v2_migrated()

    # Upgrade generic Classroom URLs to specific assignment details URLs
    upgraded_url_count = 0
    for t in tasks:
        t_title = t.get("title", "").strip().lower()
        t_url = t.get("url", "").strip()
        is_generic = config.CLASSROOM_HOST in t_url and not any(x in t_url for x in ["/a/", "/sa/", "/m/"])

        target_spec = specific_urls.get(t_title)
        if not target_spec:
            for s_title, s_url in specific_urls.items():
                sig_words = [
                    w
                    for w in s_title.split()
                    if len(w) > 4 and w not in ["posted", "assignment", "material", "devoir", "nouvelle", "nouveau"]
                ]
                if sig_words and all(w in t_title for w in sig_words):
                    target_spec = s_url
                    break

        if target_spec and (is_generic or not t_url):
            t["url"] = target_spec
            upgraded_url_count += 1
            print(f"🔗 [Upgraded Specific URL] {t.get('title')} -> {target_spec}")

    # Two-Way Submission Sync with Google Classroom
    auto_done_count = 0
    mismatch_warning_count = 0

    for t in tasks:
        t_title = t.get("title", "").strip().lower()
        t_url = t.get("url", "").strip().lower()

        detected_sub = None
        if t_url and t_url in classroom_submissions:
            detected_sub = classroom_submissions[t_url]
        else:
            for c_k, c_v in classroom_submissions.items():
                if len(c_k) > 4 and (c_k in t_title or t_title in c_k):
                    detected_sub = c_v
                    break

        if detected_sub:
            t["submission_status"] = detected_sub
            if detected_sub == "SUBMITTED":
                t["classroom_missing"] = False
                if t.get("status") != "DONE":
                    t["status"] = "DONE"
                    t["completed_at"] = datetime.now().isoformat()
                    t["submission_mismatch"] = None
                    auto_done_count += 1
                    print(f"✓ [Auto-Done] Remis sur Classroom : {t.get('title')}")
                else:
                    t["submission_mismatch"] = None
            elif detected_sub == "NOT_SUBMITTED":
                if (t_url and t_url in classroom_missing_keys) or any(
                    len(mk) > 4 and (mk in t_title or t_title in mk) for mk in classroom_missing_keys
                ):
                    t["classroom_missing"] = True
                if t.get("status") == "DONE":
                    t["submission_mismatch"] = "DONE_BUT_NOT_SUBMITTED"
                    mismatch_warning_count += 1
                    print(f"⚠️ [Mismatch] Fait en local mais NON remis sur Classroom : {t.get('title')}")
                else:
                    t["submission_mismatch"] = None

        # Reconcile real Classroom due date if task had no explicit end date
        if not t.get("end"):
            matched_due = classroom_due_texts.get(t_url) or next(
                (dv for dk, dv in classroom_due_texts.items() if len(dk) > 4 and (dk in t_title or t_title in dk)),
                "",
            )
            if matched_due:
                due_d, due_t = parse_deadline_text(matched_due, ref_date=datetime.now().date())
                if due_d is not None:
                    t["end"] = format_iso_end(due_d, due_t)
                    t["deadline"] = matched_due

    def find_matching_existing_task(cand_title: str, cand_url: str):
        ct = cand_title.strip().lower()
        cu = cand_url.strip()

        if cu and any(x in cu for x in ["/a/", "/sa/", "/m/"]):
            for t in tasks:
                if (t.get("url") or "").strip() == cu:
                    return t

        clean_c = re.sub(r"^(assignment|material|devoir|cours)\s*:\s*", "", ct).strip(" \"'")
        for t in tasks:
            t_tit = (t.get("title") or "").strip().lower()
            clean_t = re.sub(r"^(assignment|material|devoir|cours)\s*:\s*", "", t_tit).strip(" \"'")
            if clean_c == clean_t or (len(clean_c) > 6 and (clean_c in clean_t or clean_t in clean_c)):
                return t

        stop_words = {
            "devoir",
            "sur",
            "les",
            "des",
            "pour",
            "dans",
            "avec",
            "oracle",
            "19c",
            "etape",
            "tp",
            "td",
            "le",
            "la",
            "un",
            "une",
            "assignment",
        }
        words_c = set(re.findall(r"\w+", clean_c)) - stop_words
        if len(words_c) >= 1:
            for t in tasks:
                t_tit = (t.get("title") or "").strip().lower()
                words_t = set(re.findall(r"\w+", t_tit)) - stop_words
                if words_c and words_c.issubset(words_t):
                    return t

        return None

    new_tasks_added = 0
    for cand in candidates:
        if cand.get("drop"):
            continue
        t_title = cand.get("title", "").strip()
        if not t_title or _is_junk_task(cand):
            continue

        cand_mod = canonical_module(cand.get("module"))
        if cand_mod == "Divers":
            cand_mod = module_for_text(f"{t_title} {cand.get('description', '')}")
        cand["module"] = cand_mod

        existing_t = find_matching_existing_task(t_title, cand.get("url", ""))
        if existing_t:
            cand_u = cand.get("url", "")
            if cand_u and any(x in cand_u for x in ["/a/", "/sa/", "/m/"]):
                existing_t["url"] = cand_u
            if not existing_t.get("local_path") and cand.get("local_path"):
                existing_t["local_path"] = cand["local_path"]
            if cand.get("submission_status") and not existing_t.get("submission_status"):
                existing_t["submission_status"] = cand["submission_status"]
            if not existing_t.get("teacher") and cand.get("teacher"):
                existing_t["teacher"] = cand["teacher"]
            if existing_t.get("module") in (None, "", "Divers") and cand_mod != "Divers":
                existing_t["module"] = cand_mod
            if cand.get("ai") and cand["ai"].get("fields"):
                existing_t["ai"] = cand["ai"]
            if not existing_t.get("end"):
                cand_due = cand.get("due_text") or cand.get("deadline", "")
                due_d, due_t = parse_deadline_text(cand_due, ref_date=datetime.now().date())
                if due_d is not None:
                    existing_t["end"] = format_iso_end(due_d, due_t)
                    existing_t["deadline"] = cand_due
            continue

        target_dir = resolve_module_dir(cand_mod, t_title) if not dry_run else os.path.join(str(BASE_9RAYA), cand_mod)

        local_path = cand.get("local_path", "")
        file_name = cand.get("file_name", "")
        if not local_path and file_name:
            cand_path = os.path.join(target_dir, file_name)
            if os.path.exists(cand_path):
                local_path = cand_path
            else:
                dl_path = os.path.join(str(DOWNLOADS_DIR), file_name)
                if os.path.exists(dl_path):
                    if not dry_run:
                        os.makedirs(target_dir, exist_ok=True)
                        shutil.copy2(dl_path, cand_path)
                    local_path = cand_path

        cand_u_l = cand.get("url", "").strip().lower()
        cand_t_l = t_title.lower()
        sub_resolved = (
            classroom_submissions.get(cand_u_l)
            or classroom_submissions.get(cand_t_l)
            or cand.get("submission_status", "UNKNOWN")
        )
        status_resolved = "DONE" if sub_resolved == "SUBMITTED" else cand.get("status", "UPCOMING")
        is_missing_cls = (sub_resolved == "NOT_SUBMITTED") and (
            (cand_u_l and cand_u_l in classroom_missing_keys) or (cand_t_l in classroom_missing_keys)
        )

        due_source = (
            cand.get("due_text")
            or classroom_due_texts.get(cand_u_l)
            or classroom_due_texts.get(cand_t_l)
            or cand.get("deadline", "")
        )
        due_d, due_t = parse_deadline_text(due_source, ref_date=datetime.now().date())
        iso_end = cand.get("end") or (format_iso_end(due_d, due_t) if due_d is not None else "")

        new_entry = {
            "id": f"task-ai-{int(time.time())}-{new_tasks_added}",
            "title": t_title.replace("\n", " — "),
            "module": cand_mod,
            "teacher": cand.get("teacher", ""),
            "start": datetime.now().strftime("%Y%m%dT%H%M00Z"),
            "end": iso_end,
            "deadline": cand.get("deadline", "Séance prochaine"),
            "priority": cand.get("priority", "HIGH"),
            "status": status_resolved,
            "source": cand.get("source", "Outlook"),
            "url": cand.get("url", config.OUTLOOK_URL),
            "local_path": local_path,
            "file_name": file_name,
            "submission_status": sub_resolved,
            "submission_mismatch": None,
            "classroom_missing": is_missing_cls,
            "description": cand.get("description", ""),
        }
        if cand.get("task_type"):
            new_entry["task_type"] = cand["task_type"]
        if cand.get("ai"):
            new_entry["ai"] = cand["ai"]

        tasks.insert(0, new_entry)
        new_tasks_added += 1
        print(f"➕ [AI Refresher] Added task: {t_title} ({cand_mod})")

    return {
        "new_tasks_added": new_tasks_added,
        "auto_done_count": auto_done_count,
        "mismatch_warning_count": mismatch_warning_count,
        "upgraded_url_count": upgraded_url_count,
        "migrated_modules": migrated_modules,
        "removed_junk": removed_junk,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EMSI School-Plan AI Academic Refresher")
    parser.add_argument("--no-ai", action="store_true", help="Skip local AI enrichment (heuristics only)")
    parser.add_argument("--no-load", action="store_true", help="Do not auto-load model if currently unloaded")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print diff without writing files")
    parser.add_argument("--json", action="store_true", help="Print final status JSON as the last line of stdout")
    return parser.parse_args(argv)


def refresh_pipeline(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start_mono = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")

    status = {
        "running": True,
        "phase": "init",
        "started_at": started_at,
        "finished_at": None,
        "result": "ok",
        "lms_state": local_ai.model_state(),
        "model": config.LMS_MODEL,
        "ai": {"state": "idle", "sent": 0, "ok": 0, "failed": 0, "skipped": 0, "ms": 0},
        "summary": "",
    }

    try:
        with task_store.run_locked(blocking=False):
            return _execute_pipeline(args, start_mono, status)
    except task_store.LockBusy:
        status["running"] = False
        status["phase"] = "done"
        status["finished_at"] = datetime.now().isoformat(timespec="seconds")
        status["result"] = "locked"
        status["summary"] = "Actualisation déjà en cours"
        if not args.dry_run:
            write_status(status)
        print("already running")
        if args.json:
            print(json.dumps(status, ensure_ascii=False))
        return 3
    except Exception as e:
        status["running"] = False
        status["phase"] = "done"
        status["finished_at"] = datetime.now().isoformat(timespec="seconds")
        status["result"] = "error"
        status["summary"] = f"Erreur: {e}"
        if not args.dry_run:
            write_status(status)
        print(f"[AI Refresher] Fatal error: {e}", file=sys.stderr)
        if args.json:
            print(json.dumps(status, ensure_ascii=False))
        return 1


def _execute_pipeline(args: argparse.Namespace, start_mono: float, status: dict) -> int:
    def update_phase(phase_name: str):
        status["phase"] = phase_name
        if not args.dry_run:
            write_status(status)

    print("=" * 65)
    print("✨ AI Academic Refresher Pipeline Started" + (" [DRY-RUN]" if args.dry_run else ""))
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # 1. Organize downloaded files
    moved = organize_downloaded_files(dry_run=args.dry_run)
    if moved:
        print(f"📁 Organized {len(moved)} course file(s) into ~/Documents/9raya/:")
        for m in moved:
            print(f"   ✓ {os.path.basename(m['dest'])} -> 9raya/{m['module']}/")

    # 2. Harvest browser state
    update_phase("harvest")
    harvested = run_harvester()
    classroom_count = len(harvested.get("classroom", []))
    outlook_count = len(harvested.get("outlook", []))
    print(f"🌐 Harvested: {classroom_count} Classroom tab(s), {outlook_count} Outlook view(s)")

    # 3. Extract candidate academic tasks & submission statuses
    update_phase("extract")
    candidates, classroom_submissions, specific_urls, classroom_due_texts, classroom_missing_keys = (
        extract_academic_tasks(harvested, dry_run=args.dry_run)
    )
    for c in candidates:
        c["module"] = canonical_module(c.get("module"))

    print(f"📋 Detected {len(candidates)} academic item(s) from emails & posts.")
    if classroom_submissions:
        print(f"🎓 Tracked {len(classroom_submissions)} Google Classroom submission states.")
    if specific_urls:
        print(f"🔗 Resolved {len(specific_urls)} specific assignment URL(s).")

    # 4. Enrich with Local Gemma 4 on LM Studio
    update_phase("ai")
    ai_result_code = "ok"
    if args.no_ai:
        status["lms_state"] = local_ai.model_state()
        status["ai"] = {
            "state": "disabled",
            "sent": 0,
            "ok": 0,
            "failed": 0,
            "skipped": len(candidates),
            "ms": 0,
        }
    else:
        ai_loaded = local_ai.ensure_loaded(allow_load=not args.no_load)
        lms_st = local_ai.model_state()
        status["lms_state"] = lms_st
        if not ai_loaded:
            status["ai"] = {
                "state": lms_st,
                "sent": 0,
                "ok": 0,
                "failed": 0,
                "skipped": len(candidates),
                "ms": 0,
            }
            if lms_st == "offline":
                ai_result_code = "offline"
                print("[AI Refresher] LM Studio offline, continuing with heuristic extraction.")
            elif candidates:
                ai_result_code = "partial"
                print("[AI Refresher] Model not loaded (--no-load), continuing with heuristic extraction.")
        else:
            elapsed = time.monotonic() - start_mono
            # Reserve 12s for save + calendar generation within PIPELINE_BUDGET_S
            remaining_budget = max(5.0, float(config.PIPELINE_BUDGET_S) - elapsed - 12.0)
            if candidates:
                print(f"[AI Refresher] Enriching {len(candidates)} item(s) with {config.LMS_MODEL} (budget {remaining_budget:.1f}s)...")
                candidates, ai_stats = local_ai.enrich(candidates, budget_s=remaining_budget)
                status["ai"] = ai_stats
                status["lms_state"] = ai_stats.get("state", lms_st)
                if ai_stats["failed"] > 0 or ai_stats["skipped"] > 0:
                    ai_result_code = "partial"
            else:
                status["ai"] = {"state": "loaded", "sent": 0, "ok": 0, "failed": 0, "skipped": 0, "ms": 0}

    candidates = [c for c in candidates if not c.get("drop")]

    # 5. Merge & Save (or Dry-Run Diff)
    update_phase("save")
    merge_counts = {}
    if args.dry_run:
        before_tasks = task_store.load()
        working_tasks = copy.deepcopy(before_tasks)
        merge_counts = _merge_and_migrate_tasks(
            working_tasks,
            candidates,
            classroom_submissions,
            specific_urls,
            classroom_due_texts,
            classroom_missing_keys,
            dry_run=True,
        )
        print(
            f"🔎 [Dry-Run Diff] before={len(before_tasks)} after={len(working_tasks)} "
            f"added={merge_counts['new_tasks_added']} migrated_modules={merge_counts['migrated_modules']} "
            f"removed_junk={merge_counts['removed_junk']}"
        )
    else:
        def _do_update(current_tasks: list[dict]) -> list[dict]:
            nonlocal merge_counts
            merge_counts = _merge_and_migrate_tasks(
                current_tasks,
                candidates,
                classroom_submissions,
                specific_urls,
                classroom_due_texts,
                classroom_missing_keys,
                dry_run=False,
            )
            return current_tasks

        task_store.update(_do_update, timeout=5.0)
        print("💾 Updated tasks.json atomically via task_store.")

        # 6. Regenerate calendar (.ics)
        update_phase("calendar")
        try:
            subprocess.run([sys.executable, str(PLAN_DIR / "generate_calendar.py")], check=True)
        except Exception as e:
            print(f"Error generating calendar: {e}", file=sys.stderr)

    # 7. Summary & single Desktop Notification
    summary_parts = []
    if merge_counts.get("new_tasks_added", 0) > 0:
        summary_parts.append(f"{merge_counts['new_tasks_added']} nouveau(x) devoir(s)")
    if merge_counts.get("auto_done_count", 0) > 0:
        summary_parts.append(f"{merge_counts['auto_done_count']} marqué(s) Fait (remis)")
    if merge_counts.get("mismatch_warning_count", 0) > 0:
        summary_parts.append(f"⚠️ {merge_counts['mismatch_warning_count']} non remis sur Classroom")
    if moved:
        summary_parts.append(f"{len(moved)} fichier(s) classé(s)")
    if merge_counts.get("migrated_modules", 0) > 0:
        summary_parts.append(f"{merge_counts['migrated_modules']} module(s) normalisé(s)")
    if merge_counts.get("removed_junk"):
        summary_parts.append(f"{len(merge_counts['removed_junk'])} élément(s) parasite(s) supprimé(s)")

    summary_msg = " • ".join(summary_parts) if summary_parts else "Planning à jour. Aucun changement."

    status["running"] = False
    status["phase"] = "done"
    status["finished_at"] = datetime.now().isoformat(timespec="seconds")
    status["result"] = ai_result_code
    status["summary"] = summary_msg

    if not args.dry_run:
        write_status(status)
        try:
            subprocess.run(
                [
                    "notify-send",
                    "-a",
                    "Planning Scolaire",
                    "-i",
                    "calendar",
                    " Actualisation IA Terminée",
                    summary_msg,
                ]
            )
        except Exception:
            pass

    print("=" * 65)
    print(f" Refresher Finished ({status['result']}): {summary_msg}")
    print("=" * 65)

    if args.json:
        print(json.dumps(status, ensure_ascii=False))

    if ai_result_code == "ok":
        return 0
    if ai_result_code in ("partial", "offline"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(refresh_pipeline())
