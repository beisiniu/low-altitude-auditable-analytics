// Secondary development: mirror recompute rules for the low-altitude tools.
//
// Upstream contract (packages/verify/src/recompute.ts, header comment):
//   "SQL facts are re-run with their stored parameters. Function facts are
//    recomputed from their input facts by a registry that mirrors each analysis
//    function; a function without an entry can't be verified, which is itself
//    an engine bug."
//
// Every new low-altitude function therefore MUST register a mirror here, or V2
// fails closed and the job ships nothing.  These three rules are the exact TS
// counterparts of RULES in lowalt/engine/audit.py, which the evaluation
// harness runs deterministically without Node/Bun.
//
// The tools themselves live in lowalt/engine/tools.py (and, for a TS
// deployment, would be added to packages/analysis following the same encoded
// calculation definitions).  The important architectural point is that the
// *verifier*, not the model, owns the last word on every published number.
import type { Fact } from "@agentic-analytics/schema";

type Rule = (
  f: Fact,
  inputs: readonly number[],
  byId: ReadonlyMap<string, Fact>,
) => { value: number | number[]; how: string };

const same = (a: number, b: number): boolean =>
  Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));

export const LOWALT_RULES: Record<string, Rule> = {
  // Inputs: one SQL count fact per (hour_bucket, launch vertiport) group.
  // Recompute the peak AND check the reported argmax group is a real argmax.
  "lowalt.demand_peak.v1": (f, x) => {
    if (!x.length) return { value: Number.NaN, how: "no per-group count facts" };
    const labels = (f.computation.params?.labels as string[] | undefined) ?? [];
    const claimed = f.computation.params?.peak_group as string | undefined;
    const peak = Math.max(...x);
    if (labels.length && claimed) {
      const winners = labels.filter((_, i) => same(x[i] ?? Number.NaN, peak));
      if (!winners.includes(claimed))
        return {
          value: [],
          how: `argmax mismatch: recomputed ${winners.join(",")}, reported ${claimed}`,
        };
    }
    return { value: peak, how: `max of ${x.length} per-group SQL counts (argmax group checked)` };
  },

  // Inputs: per-zone SQL counts over the materialised conflict_events table.
  // The geometric decision itself is checked by an independent second
  // implementation + labelled synthetic golden cases (lowalt/tests/).
  "lowalt.nfz_conflict.v1": (_f, x) => ({
    value: x.reduce((a, b) => a + b, 0),
    how: "Σ per-zone SQL counts over materialised conflict_events",
  }),

  // Inputs: cruise-point counts per 15 m altitude layer.
  "lowalt.altitude_profile.v1": (_f, x) => {
    const total = x.reduce((a, b) => a + b, 0);
    if (!total) return { value: Number.NaN, how: "no trajectory points" };
    return {
      value: (Math.max(...x) / total) * 100,
      how: "max layer count ÷ Σ layer counts × 100",
    };
  },
};
