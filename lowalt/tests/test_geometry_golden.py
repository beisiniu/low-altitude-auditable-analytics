"""Golden cases for the low-altitude geometry + verifier contract.

Run from the repo root:
    python -m unittest discover -s lowalt/tests -v
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lowalt.engine.audit import Verifier, load_duckdb
from lowalt.engine.probes import (
    AIRPORT_POLY,
    PROBE_EXPECTED,
    install_probe_dataset,
    point_in_polygon_winding,
    probe_rows,
    restore_probe_dataset,
)
from lowalt.engine.tools import (
    TOOLS,
    _segment_events,
    point_in_polygon,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "out"


class TestIndependentGeometry(unittest.TestCase):
    def test_ray_casting_matches_winding_on_grid(self):
        """Two independent implementations must agree everywhere in the bbox."""
        n = 0
        for i in range(41):
            lat = 22.605 + i * 0.0015
            for j in range(41):
                lon = 113.785 + j * 0.0015
                a = point_in_polygon(lat, lon, AIRPORT_POLY)
                b = point_in_polygon_winding(lat, lon, AIRPORT_POLY)
                self.assertEqual(a, b, f"disagreement at ({lat}, {lon})")
                n += 1
        self.assertEqual(n, 41 * 41)

    def test_probe_classification(self):
        """A is inside the bbox but outside the polygon; B is inside both."""
        rows = probe_rows()
        a = [r for r in rows if r[0] == "T-PROBE-A"]
        b = [r for r in rows if r[0] == "T-PROBE-B"]
        self.assertTrue(all(not point_in_polygon(float(r[6]), float(r[7]), AIRPORT_POLY)
                            for r in a))
        self.assertTrue(all(point_in_polygon(float(r[6]), float(r[7]), AIRPORT_POLY)
                            for r in b))

    def test_run_length_segmentation(self):
        """A gap in seq starts a new conflict event."""
        hits = [("T", "Z", 1.0, 0), ("T", "Z", 2.0, 1),
                ("T", "Z", 5.0, 5), ("T", "Z", 6.0, 6)]
        self.assertEqual(len(_segment_events(hits)), 2)


class TestToolOnSyntheticProbes(unittest.TestCase):
    def setUp(self):
        self.con = load_duckdb(DATA)
        install_probe_dataset(self.con)

    def tearDown(self):
        restore_probe_dataset(self.con)
        self.con.close()

    def test_polygon_tool_counts_exactly(self):
        r = TOOLS["no_fly_conflict_count"](self.con, {})
        self.assertEqual(r.value["events"], PROBE_EXPECTED["polygon"]["events"])
        self.assertEqual(r.value["points"], PROBE_EXPECTED["polygon"]["points"])
        self.assertEqual(r.verify.passed, r.verify.total, "every published fact must recompute")

    def test_bbox_baseline_overcounts(self):
        r = TOOLS["bbox_conflict_approx"](self.con, {})
        self.assertEqual(r.value["events"], PROBE_EXPECTED["bbox"]["events"])
        self.assertEqual(r.value["points"], PROBE_EXPECTED["bbox"]["points"])
        self.assertGreater(r.value["events"], PROBE_EXPECTED["polygon"]["events"])
        self.assertEqual(r.verify.passed, r.verify.total)

    def test_time_window_is_respected(self):
        r = TOOLS["no_fly_conflict_count"](self.con, {"traj_id": "T-PROBE-C"})
        self.assertEqual(r.value["events"], 0, "a hit outside the activation window is not a conflict")


class TestRealDataFindings(unittest.TestCase):
    def setUp(self):
        self.con = load_duckdb(DATA)

    def tearDown(self):
        self.con.close()

    def test_published_case_has_zero_geometric_intersection(self):
        """Regression guard for the report's key data-quality finding.

        Airport NFZ lon 113.792-113.842; every node and trajectory point has
        lon >= 113.845.  If a future data build changes this, the evaluation's
        interpretation must be revisited rather than silently reporting 0.
        """
        min_lon = self.con.execute("SELECT MIN(lon) FROM trajectory_points").fetchone()[0]
        max_zone_lon = self.con.execute("SELECT MAX(lon) FROM no_fly_zones "
                                        "WHERE zone_id='z_airport_szx'").fetchone()[0]
        self.assertGreater(min_lon, max_zone_lon)
        r = TOOLS["no_fly_conflict_count"](self.con, {})
        self.assertEqual(r.value["events"], 0)
        self.assertEqual(r.verify.passed, r.verify.total)

    def test_verifier_fails_closed_on_unknown_function(self):
        from lowalt.engine.audit import Fact
        f = Fact(id="N1", label="unregistered", value=1.0, fn="lowalt.not_registered.v1")
        res = Verifier(self.con).verify([f], strict=False)
        self.assertFalse(res.ok)
        self.assertIn("no recompute rule", res.checks[0].detail)


if __name__ == "__main__":
    unittest.main()
