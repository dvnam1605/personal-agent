"""CI gate over the agent gold set: every scenario must pass in the offline sandbox."""

from __future__ import annotations

import json

from tests.evaluation.agent_eval import GOLD, run_all, scorecard


async def test_agent_gold_set_passes() -> None:
    results = await run_all()
    card = scorecard(results)
    assert card["failures"] == {}, json.dumps(card, ensure_ascii=False, indent=2)
    categories = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    assert {item["category"] for item in categories} == set(card["categories"])
