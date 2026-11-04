"""Held-out paraphrases for the v2 natural-language resolver.

Why this file exists
--------------------
Fixing the 5 v1 failures by adding those 5 questions' keywords would be
overfitting.  These paraphrases were written independently, using different
wording, to check that the resolver learned the ASKED-FOR-QUANTITY idea rather
than the evaluation set's vocabulary.  Results are reported honestly in
EVAL_REPORT.md.

    python -m unittest discover -s lowalt/tests -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from lowalt.engine.tools import resolve_question

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE.parent / "eval" / "questions.yaml"

# (question, expected tool, expected state) -- none of these strings appears
# in questions.yaml.
HELD_OUT = [
    ("全天一共派了多少单？", "drone_demand_aggregate", "EXACT"),
    ("下午两点到四点，哪个起降场最忙？", "drone_demand_aggregate", "EXACT"),
    ("15:00 到 18:00 之间一共下了多少笔订单？", "drone_demand_aggregate", "EXACT"),
    ("帮我看下各时段的单量差异", "drone_demand_aggregate", "EXACT"),
    ("统计一下所有轨迹闯入禁区的次数", "no_fly_conflict_count", "EXACT"),
    ("机场那个禁飞区在生效时段里，飞过多少架次？", "no_fly_conflict_count", "EXACT"),
    ("哪一层高度上的飞行最密集？", "altitude_distribution", "EXACT"),
    ("巡航点的高度分布是怎样的？", "altitude_distribution", "EXACT"),
]

AMBIGUOUS = "有多少订单和禁飞区命中点？"       # two asked-for quantities, nearly tied
UNRESOLVED = "今天天气怎么样？"                  # no analytical quantity at all


class TestResolverHeldOut(unittest.TestCase):
    def test_eight_paraphrases(self):
        for question, tool, state in HELD_OUT:
            with self.subTest(question=question):
                r = resolve_question(question)
                self.assertEqual(r["tool"], tool)
                self.assertEqual(r["state"], state)

    def test_explicit_ambiguity_state(self):
        """A genuine tie must be surfaced, not silently guessed.  This is the
        state that upstream would hand to a planner model."""
        r = resolve_question(AMBIGUOUS)
        self.assertEqual(r["state"], "AMBIGUOUS")
        self.assertIn("scores", r)

    def test_unresolved_refuses_to_guess(self):
        r = resolve_question(UNRESOLVED)
        self.assertEqual(r["state"], "UNRESOLVED")
        self.assertEqual(r["tool"], "sql_aggregate")

    def test_main_suite_regression(self):
        """All 24 published questions must now resolve EXACTly."""
        spec = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
        failures = []
        for q in spec["questions"]:
            r = resolve_question(q["question"])
            if r["tool"] != q["tool"] or r["state"] != "EXACT":
                failures.append((q["id"], r["tool"], r["state"], r["scores"]))
        self.assertEqual(failures, [], f"resolver regressions: {failures}")
        self.assertEqual(len(spec["questions"]), 24)


if __name__ == "__main__":
    unittest.main()
