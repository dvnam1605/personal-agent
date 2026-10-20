"""Run the RAG gold set against a running backend and compare with a baseline.

Run:
    uv run python scripts/eval_rag.py                                 # dev split
    uv run python scripts/eval_rag.py --split all --save-baseline     # record baseline
    uv run python scripts/eval_rag.py --compare eval_results/baseline.json

Results: eval_results/<timestamp>.jsonl (one line per item, resumable) and
eval_results/<timestamp>.summary.json. Exit code 1 when a metric regresses
beyond --tolerance versus the baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.evaluation.rag_eval import (  # noqa: E402
    RagEvaluator,
    compare,
    load_gold,
    summarize,
)

DEFAULT_GOLD = REPO_ROOT / "tests" / "evaluation" / "gold" / "rag_gold_v1.json"
RESULTS_DIR = REPO_ROOT / "eval_results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--split", default="dev", choices=["dev", "holdout", "all"])
    parser.add_argument("--resume", type=Path, help="continue an interrupted .jsonl run")
    parser.add_argument("--compare", type=Path, help="baseline summary JSON")
    parser.add_argument("--tolerance", type=float, default=0.05)
    parser.add_argument("--save-baseline", action="store_true")
    args = parser.parse_args()

    items = load_gold(args.gold, args.split)
    RESULTS_DIR.mkdir(exist_ok=True)
    out = args.resume or RESULTS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{args.split}.jsonl"
    done: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row

    evaluator = RagEvaluator(args.base_url)
    try:
        with out.open("a", encoding="utf-8") as fh:
            for item in items:
                if item["id"] in done:
                    continue
                result = evaluator.run_item(item).as_dict()
                fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                fh.flush()
                done[item["id"]] = result
                flag = "ERR " if result["error"] else ""
                print(f"{flag}{item['id']:20} {result['latency_s']:6.1f}s {result['metrics']}")
    finally:
        evaluator.close()

    results = [done[i["id"]] for i in items if i["id"] in done]
    summary = summarize(results)
    summary.update({"gold": args.gold.name, "split": args.split, "results": out.name})
    summary_path = out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.save_baseline:
        (RESULTS_DIR / "baseline.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("baseline saved")
    if args.compare:
        regressions = compare(
            summary, json.loads(args.compare.read_text(encoding="utf-8")), args.tolerance
        )
        if regressions:
            print("REGRESSIONS:\n  " + "\n  ".join(regressions))
            return 1
        print("no regressions vs baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
