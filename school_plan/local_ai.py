#!/usr/bin/env python3
"""
Local AI Client for LM Studio (google/gemma-4-e4b).

Verified LM Studio REST & CLI contract on this machine (2026-11-18):
- GET /api/v0/models -> {"object": "list", "data": [{"id": "google/gemma-4-e4b", "state": "loaded"|"not-loaded", ...}]}
- CLI fallback: `~/.lmstudio/bin/lms ps --json` -> [{"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b", "status": "idle", ...}]
- CLI auto-load: `~/.lmstudio/bin/lms load <model-key> --ttl <seconds> -y`
- POST /v1/chat/completions requires `"reasoning_effort": "none"` alongside `"response_format": {"type": "json_schema", ...}`
  to keep Gemma 4 thinking disabled (0 reasoning tokens). `enableThinking` and `chat_template_kwargs` are NOT sent.
"""

from datetime import datetime
import json
import subprocess
import time
import urllib.error
import urllib.request

from school_plan import config
from school_plan.deadline_utils import format_iso_end, parse_deadline_text

DAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

TASK_TYPES = [
    "DEVOIR",
    "COMPTE_RENDU",
    "PREPARATION_TP",
    "PASSAGE_TABLEAU",
    "REVISION",
    "EXAMEN",
    "SUPPORT_COURS",
    "INFO",
]

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "is_actionable": {"type": "boolean"},
        "title": {"type": "string", "maxLength": 90},
        "module": {"type": "string", "enum": list(config.MODULES.keys())},
        "teacher": {"type": "string"},
        "task_type": {"type": "string", "enum": TASK_TYPES},
        "deadline_text": {"type": "string"},
        "priority": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "summary": {"type": "string", "maxLength": 300},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "is_actionable",
        "title",
        "module",
        "teacher",
        "task_type",
        "deadline_text",
        "priority",
        "summary",
        "confidence",
    ],
    "additionalProperties": False,
}

FEW_SHOT_MESSAGES = [
    {
        "role": "user",
        "content": (
            "Scolarité Tanger: Compte rendu des deux TPs à me présenter la séance prochaine : NoSQL. "
            "Bonjour Chers étudiants, Merci de trouver ci-joint le Compte rendu des deux TPs à me "
            "présenter la séance prochaine de la matière NoSQL envoyé par votre prof Houda Zaim."
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "is_actionable": True,
                "title": "Compte rendu des deux TPs NoSQL",
                "module": "NoSQL",
                "teacher": "Houda Zaim",
                "task_type": "COMPTE_RENDU",
                "deadline_text": "séance prochaine",
                "priority": "HIGH",
                "summary": "Préparer et présenter le compte rendu des deux TPs NoSQL lors de la prochaine séance.",
                "confidence": 0.96,
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "user",
        "content": (
            "DEV DOTNET G4 IIRT: othman bakkali posted a new assignment: "
            "Serie 2- Exception IDisposable Getsion des fichiers. Due Saturday 21 Nov at 23:59. À rendre sur Classroom."
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "is_actionable": True,
                "title": "Série 2 — Exceptions, IDisposable et Gestion des fichiers",
                "module": "Dotnet",
                "teacher": "Othman Bakkali",
                "task_type": "DEVOIR",
                "deadline_text": "Saturday 21 Nov at 23:59",
                "priority": "HIGH",
                "summary": "Terminer et remettre la Série 2 (.NET / C#) sur Google Classroom.",
                "confidence": 0.98,
            },
            ensure_ascii=False,
        ),
    },
    {
        "role": "user",
        "content": (
            "Virtualisation & Cloud (G4) - Imane: Préparation TP1 pour la prochaine séance: "
            "1. Téléchargez Alpine Linux édition VIRTUAL. 2. Créer une VM Prod-ERP sur VMware ou VirtualBox "
            "(512 Mo RAM, 4 Go disque, NAT). 3. Tapez setup-alpine."
        ),
    },
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "is_actionable": True,
                "title": "Préparation TP1 — VM Alpine Linux (Prod-ERP)",
                "module": "Virtualisation",
                "teacher": "Imane Elabid",
                "task_type": "PREPARATION_TP",
                "deadline_text": "prochaine séance",
                "priority": "HIGH",
                "summary": "Télécharger Alpine Linux Virtual et configurer la VM Prod-ERP (512 Mo RAM, 4 Go disque, NAT).",
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
    },
]


def build_system_prompt(now: datetime | None = None) -> str:
    """Builds the concise French system prompt with current date and professor->module mapping."""
    dt = now or datetime.now()
    weekday_fr = DAY_NAMES_FR[dt.weekday()]
    t_map = config.teacher_module_map()
    teacher_lines = ", ".join(f"{prof} -> {mod}" for prof, mod in t_map.items())
    modules_str = ", ".join(config.MODULES.keys())
    return (
        f"Tu es l'assistant académique d'un étudiant ingénieur à l'EMSI Tanger (4IIR).\n"
        f"Date actuelle : {weekday_fr} {dt.strftime('%Y-%m-%d')}.\n"
        f"Modules canoniques autorisés : {modules_str}.\n"
        f"Correspondance Enseignant -> Module : {teacher_lines}.\n"
        "Règles strictes :\n"
        "1. Choisis toujours le module canonique exact correspondant au cours ou à l'enseignant.\n"
        "2. Copie la mention de date limite mot pour mot dans deadline_text (ex: 'Saturday 21 Nov at 23:59', "
        "'today at 23:59', 'séance prochaine', ou '' si aucune date limite n'est mentionnée). N'invente jamais de date.\n"
        "3. Mets is_actionable=false pour les notifications système, alertes de sécurité, codes secrets, publicités ou messages non scolaires.\n"
        "4. Réponds uniquement avec l'objet JSON conforme au schéma."
    )


def model_state() -> str:
    """Returns 'offline' | 'not-loaded' | 'loaded' for config.LMS_MODEL on LM Studio."""
    base = config.LMS_BASE.rstrip("/")
    url_v0 = f"{base}/api/v0/models"
    try:
        req = urllib.request.Request(url_v0, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data", []) if isinstance(data, dict) else []
            for item in items:
                if item.get("id") == config.LMS_MODEL:
                    return "loaded" if item.get("state") == "loaded" else "not-loaded"
            return "not-loaded"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return "offline"
    except Exception:
        return "offline"

    # Fallback if /api/v0/models returned 404: check /v1/models reachability + lms ps --json
    try:
        req_v1 = urllib.request.Request(f"{base}/v1/models", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req_v1, timeout=2.0):
            pass
    except Exception:
        return "offline"

    if config.LMS_BIN.exists():
        try:
            proc = subprocess.run(
                [str(config.LMS_BIN), "ps", "--json"],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                loaded_list = json.loads(proc.stdout)
                for item in loaded_list:
                    if config.LMS_MODEL in (item.get("identifier"), item.get("modelKey")):
                        return "loaded"
                return "not-loaded"
        except Exception:
            pass

    return "not-loaded"


def ensure_loaded(allow_load: bool = True) -> bool:
    """Ensures config.LMS_MODEL is loaded in LM Studio; optionally runs `lms load` with TTL."""
    st = model_state()
    if st == "loaded":
        return True
    if st == "offline" or not allow_load:
        return False
    if not config.LMS_BIN.exists():
        return False
    try:
        subprocess.run(
            [
                str(config.LMS_BIN),
                "load",
                config.LMS_MODEL,
                "--ttl",
                str(config.LMS_TTL_S),
                "-y",
            ],
            capture_output=True,
            text=True,
            timeout=90.0,
        )
    except Exception:
        return False
    return model_state() == "loaded"


def _is_unloaded_error(body_text: str) -> bool:
    low = (body_text or "").lower()
    return "unloaded" in low or "not loaded" in low or "no model loaded" in low


def _chat(
    messages: list[dict],
    schema: dict = TASK_SCHEMA,
    timeout: float | None = None,
    allow_reload: bool = True,
) -> tuple[dict | None, dict]:
    """
    Sends a structured chat completion request to LM Studio with reasoning_effort='none'.
    Returns (parsed_json_or_None, meta_dict).
    Never reads reasoning_content as the answer.
    """
    req_timeout = timeout if timeout is not None else config.LMS_ITEM_TIMEOUT_S
    url = f"{config.LMS_BASE.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": config.LMS_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 400,
        "reasoning_effort": "none",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "school_task",
                "strict": True,
                "schema": schema,
            },
        },
    }

    start_t = time.monotonic()
    raw_body = ""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
            raw_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            raw_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw_body = str(e)
        if allow_reload and _is_unloaded_error(raw_body):
            if ensure_loaded(allow_load=True):
                return _chat(messages, schema=schema, timeout=req_timeout, allow_reload=False)
        elapsed_ms = int((time.monotonic() - start_t) * 1000)
        return None, {"error": f"HTTP {e.code}: {raw_body[:200]}", "ms": elapsed_ms, "reasoning_tokens": 0}
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start_t) * 1000)
        return None, {"error": str(e), "ms": elapsed_ms, "reasoning_tokens": 0}

    elapsed_ms = int((time.monotonic() - start_t) * 1000)
    try:
        data = json.loads(raw_body)
    except Exception as e:
        return None, {"error": f"Invalid JSON response: {e}", "ms": elapsed_ms, "reasoning_tokens": 0}

    if isinstance(data, dict) and data.get("error"):
        err_str = json.dumps(data["error"], ensure_ascii=False)
        if allow_reload and _is_unloaded_error(err_str):
            if ensure_loaded(allow_load=True):
                return _chat(messages, schema=schema, timeout=req_timeout, allow_reload=False)
        return None, {"error": err_str, "ms": elapsed_ms, "reasoning_tokens": 0}

    choices = data.get("choices") or []
    if not choices:
        return None, {"error": "No choices in response", "ms": elapsed_ms, "reasoning_tokens": 0}

    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()

    usage = data.get("usage") or {}
    comp_details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = int(comp_details.get("reasoning_tokens") or 0)

    # Strict rule: never fall back to reasoning_content, and reject truncated outputs
    if not content or finish_reason == "length":
        return None, {
            "error": f"Empty or truncated content (finish_reason={finish_reason})",
            "ms": elapsed_ms,
            "reasoning_tokens": reasoning_tokens,
        }

    try:
        parsed = json.loads(content)
    except Exception as e:
        return None, {
            "error": f"Malformed content JSON: {e}",
            "ms": elapsed_ms,
            "reasoning_tokens": reasoning_tokens,
        }

    if not isinstance(parsed, dict):
        return None, {"error": "Parsed content is not a JSON object", "ms": elapsed_ms, "reasoning_tokens": reasoning_tokens}

    parsed["module"] = config.canonical_module(parsed.get("module"))
    return parsed, {"error": None, "ms": elapsed_ms, "reasoning_tokens": reasoning_tokens}


def extract_one(
    text: str,
    include_meta: bool = False,
    timeout: float | None = None,
) -> dict | tuple[dict | None, dict] | None:
    """Extracts a single structured task dictionary from raw communication text."""
    messages = [
        {"role": "system", "content": build_system_prompt()},
        *FEW_SHOT_MESSAGES,
        {"role": "user", "content": text},
    ]
    parsed, meta = _chat(messages, schema=TASK_SCHEMA, timeout=timeout)
    if include_meta:
        return parsed, meta
    return parsed


def _format_candidate_prompt(cand: dict) -> str:
    parts = [
        f"Source: {cand.get('source', '')}",
        f"Titre brut: {cand.get('title', '')}",
        f"Module heuristique: {cand.get('module', 'Divers')}",
        f"Enseignant détecté: {cand.get('teacher', '')}",
        f"Date limite brute: {cand.get('due_text') or cand.get('deadline', '')}",
        f"Contenu: {cand.get('description', '')}",
    ]
    return "\n".join(parts)


def enrich(
    candidates: list[dict],
    budget_s: float | None = None,
) -> tuple[list[dict], dict]:
    """
    Enriches candidates one by one with Gemma 4 and merges results by list index.
    Returns (candidates, stats={'state', 'sent', 'ok', 'failed', 'skipped', 'ms'}).
    """
    max_budget = budget_s if budget_s is not None else float(config.PIPELINE_BUDGET_S)
    start_all = time.monotonic()
    st = model_state()
    stats = {
        "state": st,
        "sent": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "ms": 0,
    }

    if not candidates:
        return candidates, stats

    if st == "offline":
        stats["skipped"] = len(candidates)
        return candidates, stats

    now_date = datetime.now().date()
    now_iso = datetime.now().isoformat(timespec="seconds")

    for idx in range(len(candidates)):
        elapsed = time.monotonic() - start_all
        remaining = max_budget - elapsed
        if remaining <= 0.2:
            stats["skipped"] += len(candidates) - idx
            break

        cand = candidates[idx]
        item_timeout = min(float(config.LMS_ITEM_TIMEOUT_S), max(1.0, remaining))
        stats["sent"] += 1

        prompt_text = _format_candidate_prompt(cand)
        ai_obj, _meta = extract_one(prompt_text, include_meta=True, timeout=item_timeout)
        if ai_obj is None:
            stats["failed"] += 1
            continue

        stats["ok"] += 1
        changed_fields: list[str] = []
        conf = float(ai_obj.get("confidence") or 0.0)

        # 1. Drop non-actionable Outlook items
        if not ai_obj.get("is_actionable", True) and cand.get("source") == "Outlook":
            cand["drop"] = True
            changed_fields.append("drop")

        # 2. Module merge rule: accept AI if heuristic is Divers, or if confidence >= 0.7 and AI != Divers
        heur_mod = config.canonical_module(cand.get("module"))
        cand["module"] = heur_mod
        ai_mod = config.canonical_module(ai_obj.get("module"))
        if ai_mod != "Divers" and (heur_mod == "Divers" or conf >= 0.7):
            if ai_mod != heur_mod:
                cand["module"] = ai_mod
                changed_fields.append("module")

        # 3. Teacher merge rule: fill only if empty
        ai_teacher = (ai_obj.get("teacher") or "").strip()
        if not (cand.get("teacher") or "").strip() and ai_teacher:
            cand["teacher"] = ai_teacher
            changed_fields.append("teacher")

        # 4. Deadline merge rule: accept only if deadline_utils parses it AND task has no end
        ai_dl = (ai_obj.get("deadline_text") or "").strip()
        if ai_dl and not cand.get("end"):
             existing_due = cand.get("due_text") or cand.get("deadline", "")
             ex_d, _ = parse_deadline_text(existing_due, ref_date=now_date)
             ai_d, ai_t = parse_deadline_text(ai_dl, ref_date=now_date)
             if ai_d is not None and ex_d is None:
                 cand["due_text"] = ai_dl
                 cand["deadline"] = ai_dl
                 cand["end"] = format_iso_end(ai_d, ai_t)
                 changed_fields.append("deadline")

        # 5. Title merge rule: only upgrade if original title was very short (< 6 chars)
        ai_title = (ai_obj.get("title") or "").strip()
        if ai_title and len((cand.get("title") or "").strip()) < 6:
            cand["title"] = ai_title
            changed_fields.append("title")

        # 6. Task type & priority
        ai_type = (ai_obj.get("task_type") or "").strip()
        if ai_type in TASK_TYPES and not cand.get("task_type"):
            cand["task_type"] = ai_type
            changed_fields.append("task_type")

        ai_prio = (ai_obj.get("priority") or "").strip()
        if ai_prio in ("HIGH", "MEDIUM", "LOW") and cand.get("priority") != ai_prio and conf >= 0.7:
            cand["priority"] = ai_prio
            changed_fields.append("priority")

        cand["ai"] = {
            "model": config.LMS_MODEL,
            "confidence": round(conf, 2),
            "fields": changed_fields,
            "at": now_iso,
        }

    stats["ms"] = int((time.monotonic() - start_all) * 1000)
    stats["state"] = model_state() if stats["sent"] > 0 else st
    return candidates, stats
