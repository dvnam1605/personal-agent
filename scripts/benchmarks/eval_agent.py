"""Run the agent behaviour gold set offline and print a scorecard.

Run:
    uv run python scripts/benchmarks/eval_agent.py
    uv run python scripts/benchmarks/eval_agent.py --json eval_results/agent.json

No backend, Google account or LLM is needed: scenarios run through the real query
orchestrator against the simulated world in tests/evaluation/agent_eval.py. Exit code 1
when any scenario fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.agent_eval import GOLD, run_all, scorecard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=GOLD, help="Scenario file")
    parser.add_argument("--json", type=Path, default=None, help="Also write the scorecard here")
    args = parser.parse_args()

    card = scorecard(asyncio.run(run_all(args.gold)))
    print(f"Agent eval: {card['passed']}/{card['total']} passed ({card['pass_rate']:.0%})")
    for name, counts in card["categories"].items():
        print(f"  {name:<10} {counts['passed']}/{counts['total']}")
    for scenario, failures in card["failures"].items():
        print(f"FAIL {scenario}")
        for failure in failures:
            print(f"    {failure}")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not card["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
