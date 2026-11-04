"""Auditable fact layer -- a Python mirror of the upstream verifier contract.

Upstream reference (cloned at this repo's root):
  packages/schema/src/report.ts     Computation = {kind: "sql"|"function", sql, params, fn, inputs}
  packages/verify/src/recompute.ts  V2: SQL facts are re-run with stored params;
                                    function facts are recomputed by RULES[fn];
                                    a missing rule is a hard failure, not a skip.
  packages/verify/src/verify.ts     V1-V8; a V2 mismatch raises VerifyError and
                                    the job ships nothing (fail closed).

This module reproduces that contract 1:1 so the low-altitude evaluation harness
can run unattended, deterministically, with no model and no Bun/Node runtime.
The three new mirror rules are the ones the TS integration patch registers in
packages/verify/src/recompute.ts -- see lowalt/integration/.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import duckdb

TOLERANCE = 1e-9  # same as upstream recompute.ts

TABLES = {
    "nodes": "nodes.csv",
    "no_fly_zones": "no_fly_zones.csv",
    "orders": "orders.csv",
    "trajectory_points": "trajectory_points.csv",
    "congestion": "congestion.csv",
}


def same(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return abs(a - b) <= TOLERANCE * max(1.0, abs(a), abs(b))


@dataclass
class Fact:
    """A published number. Mirrors the upstream `Fact` Zod schema."""

    id: str
    label: str
    value: float
    unit: str = "count"
    sql: str | None = None
    params: list = field(default_factory=list)
    fn: str | None = None
    inputs: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    how: str = ""

    def __post_init__(self) -> None:
        if self.sql is None and self.fn is None:
            raise ValueError(f"fact {self.id}: sql facts need sql, function facts need fn")

    @property
    def kind(self) -> str:
        return "sql" if self.sql is not None else "function"


@dataclass
class CheckRow:
    check: str
    fact: str
    passed: bool
    detail: str


@dataclass
class VerifyResult:
    ok: bool
    checks: list[CheckRow]
    passed: int
    total: int

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


Rule = Callable[[Fact, list[float], dict[str, Fact]], "tuple[float | list[float], str]"]
RULES: dict[str, Rule] = {}


def register(fn: str):
    def deco(func: Rule) -> Rule:
        if fn in RULES:
            raise ValueError(f"duplicate recompute rule: {fn}")
        RULES[fn] = func
        return func

    return deco


# ---- mirror of upstream RULES (the subset this project exercises) ----------
@register("sum")
def _r_sum(f: Fact, x: list[float], by: dict[str, Fact]):
    return sum(x), "Σ inputs"


@register("delta")
def _r_delta(f: Fact, x: list[float], by: dict[str, Fact]):
    return (x[0] if x else math.nan) - (x[1] if len(x) > 1 else math.nan), "a − b"


@register("pct_change")
def _r_pct(f: Fact, x: list[float], by: dict[str, Fact]):
    return ((x[0] / x[1] - 1) * 100 if len(x) > 1 and x[1] else math.nan), "(a ÷ b − 1) × 100"


@register("mean")
def _r_mean(f: Fact, x: list[float], by: dict[str, Fact]):
    return (sum(x) / len(x) if x else math.nan), "mean of inputs"


# ---- NEW: three low-altitude mirror rules (this project's contribution) ----
@register("lowalt.demand_peak.v1")
def _r_demand_peak(f: Fact, x: list[float], by: dict[str, Fact]):
    """Inputs are the per-group SQL count facts. Recompute the peak AND check
    that the reported argmax group is the group the inputs actually imply."""
    if not x:
        return math.nan, "no per-group count facts"
    labels = list(f.params[0]) if f.params else []
    peak = max(x)
    claimed = f.how  # tool stores the reported group label here
    if labels and claimed:
        winners = [labels[i] for i, v in enumerate(x) if same(v, peak)]
        if claimed not in winners:
            return [], f"argmax mismatch: recomputed {winners}, reported {claimed}"
    return peak, f"max of {len(x)} per-group SQL counts (argmax group checked)"


@register("lowalt.nfz_conflict.v1")
def _r_nfz_conflict(f: Fact, x: list[float], by: dict[str, Fact]):
    """Inputs are per-zone SQL counts over the materialised conflict table.
    The geometric decision itself is independently checked by golden cases."""
    return sum(x), "Σ per-zone SQL counts over materialised conflict_events"


@register("lowalt.altitude_profile.v1")
def _r_alt_profile(f: Fact, x: list[float], by: dict[str, Fact]):
    total = sum(x)
    if not total:
        return math.nan, "no trajectory points"
    return max(x) / total * 100, "max layer count ÷ Σ layer counts × 100"


class VerifyError(Exception):
    """Upstream behaviour: a V2 mismatch is an engine bug, nothing ships."""


class Verifier:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def v2(self, facts: list[Fact], universe: dict[str, Fact] | None = None) -> list[CheckRow]:
        by = dict(universe or {})
        for f in facts:
            by.setdefault(f.id, f)
        rows: list[CheckRow] = []
        for f in facts:
            if f.kind == "sql":
                try:
                    cur = self.con.execute(f.sql or "", f.params)
                    got = cur.fetchone()
                    v = float(got[0]) if got and got[0] is not None else 0.0
                    rows.append(CheckRow("V2", f.id, same(f.value, v),
                                         f"SQL re-run: stored {f.value}, computed {v}"))
                except Exception as exc:  # noqa: BLE001 - recorded as a failed check
                    rows.append(CheckRow("V2", f.id, False, f"SQL re-run failed: {exc}"))
                continue

            rule = RULES.get(f.fn or "")
            if rule is None:
                rows.append(CheckRow("V2", f.id, False,
                                     f"no recompute rule for {f.fn or '(none)'}"))
                continue
            inputs = [by[i].value if i in by else math.nan for i in f.inputs]
            value, how = rule(f, inputs, by)
            ok = (any(same(f.value, v) for v in value)
                  if isinstance(value, list) else same(f.value, value))
            rows.append(CheckRow("V2", f.id, ok,
                                 f"{how}: stored {f.value}, computed {value}"))
        return rows

    def verify(self, facts: list[Fact], strict: bool = True) -> VerifyResult:
        checks = self.v2(facts)
        passed = sum(1 for c in checks if c.passed)
        ok = passed == len(checks)
        if strict and not ok:
            raise VerifyError(f"{len(checks) - passed} fact(s) don't recompute: "
                              + ", ".join(c.fact for c in checks if not c.passed))
        return VerifyResult(ok=ok, checks=checks, passed=passed, total=len(checks))


def load_duckdb(out_dir: str | Path) -> duckdb.DuckDBPyConnection:
    out = Path(out_dir).resolve()
    con = duckdb.connect(":memory:")
    for table, filename in TABLES.items():
        path = out / filename
        if not path.exists():
            raise FileNotFoundError(f"run build_dataset.py first: missing {path}")
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true)",
                    [str(path)])
    return con
