# School-Plan — Local AI Overhaul & Widget Design Walkthrough

## 1. Summary of Changes

| Component | File(s) | What Changed |
| :--- | :--- | :--- |
| **Shared Config & Taxonomy** | `config.py` | Centralized paths, LM Studio URL/model/TTL/budgets, and the 13-module canonical taxonomy (`MODULES`, `canonical_module`, `module_for_text`, `teacher_module_map`). Fixed substring false-positives (`\bml\b`, `\bvm\b`, `\bro\b`). |
| **Atomic Task Store & Locks** | `task_store.py` | Added `fcntl.flock` write lock (`school-plan.lock`) and pipeline run lock (`school-plan.run.lock`), atomic temp-file + `os.fsync` + `os.replace` writes, rolling `tasks.json.prev`, and timestamped backups. |
| **Local AI Client (Gemma 4 E4B)** | `local_ai.py`, `extract_with_gemma.py`, `test_local_model_live.py` | Queries `/api/v0/models` (with `lms ps --json` fallback) for real model load state; auto-loads via `lms load google/gemma-4-e4b --ttl 900 -y`; sends `reasoning_effort: "none"` + strict `json_schema` (0 reasoning tokens); enriches per item and merges by **list index** with field-level AI provenance (`task["ai"]`). |
| **Refresher Pipeline** | `ai_refresher.py` | Uses `run_locked(blocking=False)` (exits `3` when busy), writes phase-by-phase `ai_status.json`, runs one-time v2 schema migration (canonicalizes modules, removes `"book"` icon artifact), removes duplicate desktop notification, and supports `--no-ai`, `--no-load`, `--dry-run`, `--json`. |
| **Scheduler & RFC 5545 Calendar** | `sync.sh`, `generate_calendar.py`, `~/.config/systemd/user/school-plan.service` | `sync.sh` runs `ai_refresher.py --no-load` when CDP port 9222 is up (else `generate_calendar.py`). Removed hard-coded display env vars from `school-plan.service`. Added RFC 5545 text escaping, 75-octet UTF-8 line folding, `TZID=Africa/Casablanca` with `VTIMEZONE`, and `CATEGORIES:EXAMEN` + `-P1D` alarm. |
| **CC1 Exams Import** | `import_exams.py` | Idempotently parses the 8 CC1 exams (23–26 Nov 2026) from `~/Documents/9raya/STUDY_PLAN_CC1_4IIR.md` into `tasks.json` as `task_type: "EXAMEN"`. |
| **Desktop Calendar Widget** | `desktop_calendar_widget.py`, `widget_dialogs.py`, `widget_style.css` | Extracted CSS to `widget_style.css` with a strict 4-step type scale (`10px`, `11px`, `12px`, `14px`). Extracted `EditTaskDialog` to `widget_dialogs.py` with AI provenance note and per-field verification cleanup on save. Replaced static `● LIVE` with live status pill (`● GEMMA`, `○ GEMMA veille`, `○ IA hors ligne`, `◐ <phase>…`), busy/result-aware `✨ IA` button, `EXAM` badges + rose border, and compact exam countdown (`CC dans N j`). |

---

## 2. Automated Test Suite & Live Evaluation

### Offline Unit Tests (`uv run --with pytest pytest -q tests/`)
- `tests/test_deadline_utils.py` (9 tests)
- `tests/test_task_store.py` (4 tests)
- `tests/test_local_ai.py` (5 tests)
- `tests/test_generate_calendar.py` (3 tests)
- **Result:** `21 passed in 2.80s`

### Live Gemma 4 E4B Evaluation (`python3 test_local_model_live.py`)
```text
[1/5] PASS | 2935 ms | r_tok=0 | Outlook Web (Prof. Houda Zaim) -> mod=NoSQL prof=Houda Zaim dl='séance prochaine'
[2/5] PASS | 3099 ms | r_tok=0 | Outlook Web (Prof. Soundouss Abroun) -> mod=Machine Learning prof=Soundouss Abroun dl=''
[3/5] PASS | 3280 ms | r_tok=0 | Google Classroom (Prof. Othman Bakkali - DEV DOTNET) -> mod=Dotnet prof=Othman Bakkali dl='Saturday 21 Nov at 23:59'
[4/5] PASS | 3533 ms | r_tok=0 | Google Classroom (Prof. Imane Elabid - Virtualisation) -> mod=Virtualisation prof=Imane Elabid dl='prochaine séance'
[5/5] PASS | 3374 ms | r_tok=0 | Google Classroom (Prof. Hassan Badir - Oracle 19c) -> mod=Administration Oracle 1 prof=Hassan Badir dl='today at 23:59'
----------------------------------------------------------------------------------------
SUMMARY: Accuracy 5/5 | p50=3280 ms | p95=3533 ms | max_reasoning_tokens=0
```

---

## 3. Compatibility Matrix Results

| Scenario | Command | Exit Code | `result` | `lms_state` | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Auto-load from unloaded state** | `lms unload --all && python3 ai_refresher.py --dry-run --json` | `0` | `ok` | `loaded` | Auto-loaded `google/gemma-4-e4b` with `--ttl 900`, enriched 2/2 items in 10.7 s |
| **Server offline fallback** | `lms server stop && python3 ai_refresher.py --dry-run --json` | `2` | `offline` | `offline` | Gracefully fell back to heuristic extraction without crashing |
| **Concurrent run lock** | Two `ai_refresher.py` processes launched in parallel | `0` / `3` | `ok` / `locked` | `loaded` | Second instance exited immediately with code `3` (`Actualisation déjà en cours`) |
| **Full live run + migration** | `python3 ai_refresher.py --json` | `0` | `ok` | `loaded` | 100% canonical modules, 8 `EXAMEN` tasks, 0 `"book"` junk tasks, backup `tasks.json.bak-pre-v2-20261118-135715` verified |

---

## 4. Task Completion Status

All 11 tasks (`TSK-01` through `TSK-11`) in `prompt.md` completed with zero partial or blocked tasks.
