#!/usr/bin/env python3
"""
Live Evaluation Harness for local model (google/gemma-4-e4b on LM Studio)
via local_ai.extract_one.
Verifies accuracy (>= 4/5), latency (p50/p95), and reasoning_tokens == 0.
"""
import json
import math
from pathlib import Path
import sys

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import config
from school_plan import local_ai

TEST_CASES = [
    {
        "source": "Outlook Web (Prof. Houda Zaim)",
        "raw_text": (
            "Scolarité Tanger: Compte rendu des deux TPs à me présenter la séance prochaine : NoSQL. "
            "Bonjour Chers étudiants, Merci de trouver ci-joint le Compte rendu des deux TPs à me présenter "
            "la séance prochaine de la matière NoSQL envoyé par votre prof Houda Zaim."
        ),
        "expected": {
            "module": "NoSQL",
            "teacher_contains": "zaim",
            "has_deadline": True,
        },
    },
    {
        "source": "Outlook Web (Prof. Soundouss Abroun)",
        "raw_text": (
            "Scolarité Tanger: TD : Machine Learning. Bonjour Chers étudiants, Merci de trouver ci-joint "
            "le TD de la matière Machine Learning envoyé par votre prof Soundouss Abroun. "
            "Pièce jointe: TD_Machine Learning_202621132156.pdf"
        ),
        "expected": {
            "module": "Machine Learning",
            "teacher_contains": "abroun",
            "has_deadline": False,
        },
    },
    {
        "source": "Google Classroom (Prof. Othman Bakkali - DEV DOTNET)",
        "raw_text": (
            "DEV DOTNET G4 IIRT: othman bakkali posted a new assignment: "
            "Serie 2- Exception IDisposable Getsion des fichiers. Due Saturday 21 Nov at 23:59. À rendre sur Classroom."
        ),
        "expected": {
            "module": "Dotnet",
            "teacher_contains": "bakkali",
            "has_deadline": True,
        },
    },
    {
        "source": "Google Classroom (Prof. Imane Elabid - Virtualisation)",
        "raw_text": (
            "Virtualisation & Cloud (G4) - Imane: Préparation TP1 pour la prochaine séance: "
            "1. Téléchargez Alpine Linux édition VIRTUAL. 2. Créer une VM Prod-ERP sur VMware ou VirtualBox "
            "(512 Mo RAM, 4 Go disque, NAT). 3. Tapez setup-alpine."
        ),
        "expected": {
            "module": "Virtualisation",
            "teacher_contains": "imane",
            "has_deadline": True,
        },
    },
    {
        "source": "Google Classroom (Prof. Hassan Badir - Oracle 19c)",
        "raw_text": (
            "S1 IIR4 Gr 4 26-27: Hassan BADIR posted a new assignment: "
            "CERTIFICATION ORACLE19C Etape 0 (SQL WORKSHOP). Due today at 23:59."
        ),
        "expected": {
            "module": "Administration Oracle 1",
            "teacher_contains": "badir",
            "has_deadline": True,
        },
    },
]


def _percentile(sorted_vals: list[int], pct: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil((pct / 100.0) * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def run_tests() -> int:
    st = local_ai.model_state()
    if st == "offline":
        print("SKIPPED: LM Studio offline")
        return 0

    if not local_ai.ensure_loaded(allow_load=True):
        print(f"FAILED: Could not load model {config.LMS_MODEL} (state={local_ai.model_state()})")
        return 1

    print("=" * 88)
    print(f"🚀 LIVE EVAL: Local Model [{config.LMS_MODEL}] at {config.LMS_BASE}")
    print("=" * 88)

    passed = 0
    latencies: list[int] = []
    max_reasoning_tokens = 0
    rows = []

    for idx, tc in enumerate(TEST_CASES, 1):
        exp = tc["expected"]
        data, meta = local_ai.extract_one(tc["raw_text"], include_meta=True)
        ms = int(meta.get("ms") or 0)
        r_tok = int(meta.get("reasoning_tokens") or 0)
        latencies.append(ms)
        max_reasoning_tokens = max(max_reasoning_tokens, r_tok)

        ok = False
        reason = ""
        if data is None:
            reason = f"error={meta.get('error')}"
        else:
            mod_ok = data.get("module") == exp["module"]
            teacher_low = (data.get("teacher") or "").lower()
            teacher_ok = exp["teacher_contains"] in teacher_low
            dl_str = (data.get("deadline_text") or "").strip()
            dl_ok = bool(dl_str) if exp["has_deadline"] else True
            ok = mod_ok and teacher_ok and dl_ok and (r_tok == 0)
            reason = f"mod={data.get('module')} prof={data.get('teacher')} dl={dl_str!r}"

        if ok:
            passed += 1
        status_str = "PASS" if ok else "FAIL"
        rows.append((idx, status_str, tc["source"], ms, r_tok, reason))
        print(f"[{idx}/{len(TEST_CASES)}] {status_str} | {ms:4d} ms | r_tok={r_tok} | {tc['source']} -> {reason}")

    s_lat = sorted(latencies)
    p50 = _percentile(s_lat, 50)
    p95 = _percentile(s_lat, 95)

    print("-" * 88)
    print(
        f"📊 SUMMARY: Accuracy {passed}/{len(TEST_CASES)} | "
        f"p50={p50} ms | p95={p95} ms | max_reasoning_tokens={max_reasoning_tokens}"
    )
    print("=" * 88)

    if passed >= 4 and max_reasoning_tokens == 0:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(run_tests())
