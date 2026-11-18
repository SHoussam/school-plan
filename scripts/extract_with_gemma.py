#!/usr/bin/env python3
"""
Thin CLI over local_ai.extract_one for testing single academic task extraction.
"""
from pathlib import Path
import json
import sys

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import local_ai

DEFAULT_SAMPLE = (
    "ST Scolarité Tanger: Révisez le cours pour le passage au tableau lors de la prochaine "
    "séance de TD : NoSQL envoyé par votre prof Houda Zaim."
)


def main() -> int:
    sample = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else DEFAULT_SAMPLE
    if not local_ai.ensure_loaded(allow_load=True):
        print(json.dumps({"error": f"LM Studio model unavailable (state={local_ai.model_state()})"}, ensure_ascii=False))
        return 1
    res, meta = local_ai.extract_one(sample, include_meta=True)
    if res is None:
        print(json.dumps({"error": meta.get("error", "extraction failed")}, ensure_ascii=False))
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
