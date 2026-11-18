#!/usr/bin/env python3
"""
Centralized configuration, paths, LM Studio settings, and canonical academic module taxonomy
for the EMSI School-Plan system.
"""

from pathlib import Path
import os
import re
import unicodedata
from urllib.parse import urlparse

HOME = Path.home()
PLAN_DIR = Path(__file__).resolve().parent


def load_dotenv(env_path: Path | str | None = None) -> dict[str, str]:
    """Loads KEY=VALUE pairs from .env without overriding already-set environment variables."""
    target = Path(env_path) if env_path is not None else (PLAN_DIR / ".env")
    loaded: dict[str, str] = {}
    if not target.is_file():
        return loaded
    try:
        raw_lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        loaded[key] = val
        os.environ.setdefault(key, val)
    return loaded


load_dotenv()

TASKS_PATH = PLAN_DIR / "tasks.json"
POS_PATH = PLAN_DIR / "widget_pos.json"
META_PATH = PLAN_DIR / "meta.json"
STATUS_PATH = PLAN_DIR / "ai_status.json"
ICS_PATH = PLAN_DIR / "school-schedule.ics"
HARVESTER_SCRIPT = PLAN_DIR / "browser_harvester.js"

_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
LOCK_PATH = _RUNTIME_DIR / "school-plan.lock"
RUN_LOCK_PATH = _RUNTIME_DIR / "school-plan.run.lock"

BASE_9RAYA = Path(os.path.expanduser(os.environ.get("SCHOOL_DOCS_DIR", str(HOME / "Documents" / "9raya"))))
STUDY_PLAN_PATH = Path(
    os.path.expanduser(os.environ.get("SCHOOL_STUDY_PLAN_PATH", str(BASE_9RAYA / "STUDY_PLAN_CC1_4IIR.md")))
)
DOWNLOADS_DIR = Path(os.path.expanduser(os.environ.get("SCHOOL_DOWNLOADS_DIR", str(HOME / "Downloads"))))

LMS_BASE = os.environ.get("SCHOOL_LMS_URL", "http://127.0.0.1:1234").rstrip("/")
LMS_MODEL = os.environ.get("SCHOOL_LMS_MODEL", "google/gemma-4-e4b")
LMS_BIN = Path(os.path.expanduser(os.environ.get("SCHOOL_LMS_BIN", str(HOME / ".lmstudio" / "bin" / "lms"))))
LMS_TTL_S = 900
LMS_ITEM_TIMEOUT_S = 25
PIPELINE_BUDGET_S = 150

CDP_URL = os.environ.get("SCHOOL_CDP_URL", "http://127.0.0.1:9222").rstrip("/")
CLASSROOM_URL = os.environ.get("SCHOOL_CLASSROOM_URL", "https://classroom.google.com").rstrip("/")
CLASSROOM_HOST = urlparse(CLASSROOM_URL).netloc or "classroom.google.com"
CLASSROOM_ACCOUNT_INDEX = os.environ.get("SCHOOL_CLASSROOM_ACCOUNT_INDEX", "0").strip() or "0"
OUTLOOK_URL = os.environ.get("SCHOOL_OUTLOOK_URL", "https://outlook.cloud.microsoft/mail/")
OUTLOOK_HOSTS = [
    h.strip().lower()
    for h in os.environ.get("SCHOOL_OUTLOOK_HOSTS", "outlook.cloud.microsoft,outlook.live.com").split(",")
    if h.strip()
]
CALENDAR_UID_DOMAIN = os.environ.get("SCHOOL_CALENDAR_UID_DOMAIN", "school-plan.local").strip() or "school-plan.local"

GROUP_LABEL = os.environ.get("SCHOOL_GROUP_LABEL", "S1 IIR4 G4")

# Canonical module taxonomy (ordered: first pattern match wins)
MODULES: dict[str, dict] = {
    "Virtualisation": {
        "aliases": ["Virtualisation & Cloud", "Virtualization"],
        "teachers": ["Imane Elabid", "El Abid Imane"],
        "theme": "plum",
        "patterns": [
            r"\bvirtualis", r"\bvirtualization\b", r"\bvm\b", r"\balpine\b", r"\belabid\b",
            r"el\s+abid", r"\bdocker\b", r"\bova\b", r"\braid\b", r"disque virtuel",
            r"\bvmdk\b", r"\bvmx\b"
        ],
    },
    "Administration Oracle 1": {
        "aliases": ["Oracle 19c", "Oracle", "Administration Oracle", "ORA-19C"],
        "teachers": ["Hassan Badir"],
        "theme": "amber",
        "patterns": [
            r"\boracle\b", r"\b19c\b", r"\bbadir\b", r"sql workshop", r"\bdba\b",
            r"privil[eè]g", r"arr[êe]t et d[ée]marrage", r"1z0-082", r"ora-19c"
        ],
    },
    "Administration Unix": {
        "aliases": ["Admin Linux", "Linux", "Unix", "Administration Linux"],
        "teachers": ["Asaad Chahboun", "Chahboun Asaad", "Imane Elabid"],
        "theme": "teal",
        "patterns": [
            r"\blinux\b", r"\bunix\b", r"\bchahboun\b", r"\bpaquetage", r"\bbash\b",
            r"\bshell\b", r"\balmalinux\b", r"gestion des disques", r"gestion des utilisateurs",
            r"\bredhat\b", r"\bdebian\b"
        ],
    },
    "Analyse des données": {
        "aliases": ["Analyse de données", "Data Analysis"],
        "teachers": [],
        "theme": "cyan",
        "patterns": [
            r"analyse.*donn", r"\bstatistique", r"fouille de donn[ée]es"
        ],
    },
    "Développement Mobile": {
        "aliases": ["Dev Mobile", "Mobile"],
        "teachers": [],
        "theme": "cyan",
        "patterns": [
            r"\bmobile\b", r"\bandroid\b", r"\bflutter\b", r"\bdart\b", r"\bkotli"
        ],
    },
    "Dotnet": {
        "aliases": ["DEV .NET", "DOTNET", ".NET", "DEV DOTNET", "C#"],
        "teachers": ["Othman Bakkali", "Bakkaliyedri Othman"],
        "theme": "cobalt",
        "patterns": [
            r"\bdotnet\b", r"(?:^|\s)\.net\b", r"\bc#", r"\bcsharp\b", r"\bbakkali",
            r"\bidisposable\b", r"visual studio"
        ],
    },
    "Emploi du Temps": {
        "aliases": ["EMSI", "Orientation & Scolarité", "Scolarité", "Planning"],
        "teachers": ["Zineb Remch"],
        "theme": "slate",
        "patterns": [
            r"emploi du temps", r"\bplanning\b", r"\bplanification\b", r"\bcalendrier\b",
            r"\bcc\b.*4iir", r"planification cc", r"planning des cc",
            r"journ[ée]e d['’]orientation", r"journ[ée]e d['’]int[ée]gration"
        ],
    },
    "Introduction et éthique de l'IA": {
        "aliases": ["Introduction et éthique de l’IA", "Ethique IA", "Éthique de l'IA"],
        "teachers": ["Mourad Zbakh", "Zbakh Mourad"],
        "theme": "rose",
        "patterns": [
            r"[ée]thique", r"introduction.*ia\b", r"ai ethics", r"\bzbakh\b"
        ],
    },
    "Machine Learning": {
        "aliases": ["ML"],
        "teachers": ["Soundouss Abroun", "Abroun Soundous"],
        "theme": "indigo",
        "patterns": [
            r"machine learning", r"\babroun\b", r"\bsoundouss?\b", r"r[ée]gression",
            r"\bml\b", r"\bclassification\b", r"\bsvm\b", r"\bknn\b"
        ],
    },
    "NoSQL": {
        "aliases": ["BD NoSQL"],
        "teachers": ["Houda Zaim", "Zaim Houda"],
        "theme": "emerald",
        "patterns": [
            r"\bnosql\b", r"\bmongodb\b", r"\bcypher\b", r"\bzaim\b", r"\bhouda\b",
            r"\bneo4j\b", r"\bcouchdb\b", r"\bmql\b"
        ],
    },
    "Programmation Avancée (Java)": {
        "aliases": ["Programmation avancée", "Java", "Programmation Avancée"],
        "teachers": ["Houssam Bazza", "Bazza Houssam"],
        "theme": "violet",
        "patterns": [
            r"\bjava\b", r"programmation avanc[ée]e", r"stream\(", r"public class",
            r"\bthreads\b", r"\bswing\b", r"\bspring\b", r"\bccjava\b", r"\bbazza\b"
        ],
    },
    "Recherche opérationnelle": {
        "aliases": ["RO", "Recherche Operationnelle"],
        "teachers": ["Ilias Aarab", "Aarab Ilias"],
        "theme": "bronze",
        "patterns": [
            r"recherche op", r"\bsimplexe\b", r"\bro\b", r"\bgraphes\b",
            r"\boptimisation\b", r"\baarab\b"
        ],
    },
    "Divers": {
        "aliases": ["Autre", "Other"],
        "teachers": [],
        "theme": "slate",
        "patterns": [],
    },
}

MODULE_PATTERNS: list[tuple[str, list[str]]] = [
    (mod_name, info["patterns"])
    for mod_name, info in MODULES.items()
    if info.get("patterns")
]

PROFESSORS: list[str] = [
    "Soundouss Abroun",
    "Houda Zaim",
    "Hassan Badir",
    "Othman Bakkali",
    "Asaad Chahboun",
    "Imane Elabid",
    "Zineb Remch",
    "Mourad Zbakh",
    "Ilias Aarab",
    "Houssam Bazza",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


_ALIAS_MAP: dict[str, str] = {}
for _canon, _info in MODULES.items():
    _ALIAS_MAP[_norm(_canon)] = _canon
    for _al in _info.get("aliases", []):
        _ALIAS_MAP[_norm(_al)] = _canon


def canonical_module(name: str | None) -> str:
    """Normalizes any module name or alias to its canonical MODULES key; unknown -> 'Divers'."""
    if not name or not str(name).strip():
        return "Divers"
    n = _norm(str(name))
    if n in _ALIAS_MAP:
        return _ALIAS_MAP[n]
    # Try pattern matching on the provided module string before falling back to Divers
    matched = module_for_text(str(name))
    return matched if matched != "Divers" else "Divers"


def module_for_text(text: str | None) -> str:
    """Returns canonical module for arbitrary text based on ordered regex patterns."""
    if not text:
        return "Divers"
    low = text.lower()
    for mod_name, patterns in MODULE_PATTERNS:
        for pat in patterns:
            if re.search(pat, low):
                return mod_name
    return "Divers"


def teacher_module_map() -> dict[str, str]:
    """Returns mapping of canonical professor name -> primary canonical module."""
    out: dict[str, str] = {}
    for mod_name, info in MODULES.items():
        for t in info.get("teachers", []):
            if t not in out:
                out[t] = mod_name
    return out


def get_module_theme(module_name: str | None, title: str = "") -> str:
    """Returns the widget theme color key for a module + optional title fallback."""
    canon = canonical_module(module_name)
    if canon == "Divers" and title:
        canon = module_for_text(title)
    return MODULES.get(canon, MODULES["Divers"]).get("theme", "slate")
