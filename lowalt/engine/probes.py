"""SYNTHETIC GEOMETRY PROBES -- explicitly NOT real low-altitude data.

Why these exist
---------------
On the published Qianhai case the two no-fly-zone polygons have ZERO spatial
intersection with the 27-node delivery network (airport zone lon 113.792-113.842
vs every node/trajectory point lon >= 113.845).  Conflict counts are therefore
legitimately 0 for both toolchains, which cannot validate a geometry routine.

These hand-constructed probe trajectories are used ONLY as algorithm unit tests
and are labelled synthetic everywhere.  They are never mixed into the business
evaluation set or the report's business conclusions.

They serve two purposes:
  1. an INDEPENDENT second point-in-polygon implementation (winding angle sum)
     cross-checks the ray-casting implementation used by the tool;
  2. they quantify the bounding-box approximation's over-count, which is the
     concrete failure mode of the generic baseline toolchain.
"""
from __future__ import annotations

import math

AIRPORT_POLY = [
    (22.612, 113.792), (22.658, 113.795), (22.662, 113.830),
    (22.640, 113.842), (22.613, 113.825),
]


def point_in_polygon_winding(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    """Independent implementation: sum of signed angles around the query point.
    Topologically equivalent to even-odd ray casting but shares no code with it."""
    total = 0.0
    n = len(poly)
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        ax, ay = x1 - lon, y1 - lat
        bx, by = x2 - lon, y2 - lat
        cross = ax * by - ay * bx
        dot = ax * bx + ay * by
        total += math.atan2(cross, dot)
    return abs(total) > math.pi


def _line(traj: str, lat0: float, lat1: float, lon0: float, lon1: float,
          t0: float, t1: float, day: int = 0) -> list[tuple]:
    n = 9
    rows = []
    for seq in range(n):
        t = t0 + (t1 - t0) * seq / (n - 1)
        clock = 7 * 60 + int(t)
        rows.append((
            traj, f"probe-{traj}", day, seq, t,
            f"{clock // 60:02d}:{clock % 60:02d}",
            round(lat0 + (lat1 - lat0) * seq / (n - 1), 6),
            round(lon0 + (lon1 - lon0) * seq / (n - 1), 6),
            60.0, "060-075m", "cruise", 1, 4, 1000.0,
        ))
    return rows


def probe_rows() -> list[tuple]:
    """A: bbox-only (outside polygon). B: through polygon. C: outside time window."""
    return (
        _line("T-PROBE-A", 22.6615, 22.6615, 113.800, 113.820, 500.0, 508.0)
        + _line("T-PROBE-B", 22.6300, 22.6300, 113.800, 113.828, 510.0, 518.0)
        + _line("T-PROBE-C", 22.6300, 22.6300, 113.800, 113.828, 100.0, 108.0)
    )


PROBE_EXPECTED = {
    "polygon": {"events": 1, "points": 9},
    "bbox": {"events": 2, "points": 18},
}


def install_probe_dataset(con):
    """Swap trajectory_points for synthetic probes. Call restore in a finally."""
    con.execute("DROP TABLE IF EXISTS trajectory_points_real")
    con.execute("CREATE TABLE trajectory_points_real AS SELECT * FROM trajectory_points")
    con.execute("DROP TABLE trajectory_points")
    con.execute("""
        CREATE TABLE trajectory_points(
            traj_id VARCHAR, order_id VARCHAR, day INTEGER, seq INTEGER,
            t_min DOUBLE, t_hhmm VARCHAR, lat DOUBLE, lon DOUBLE,
            altitude_m DOUBLE, altitude_layer VARCHAR, phase VARCHAR,
            origin_node_id INTEGER, customer_node_id INTEGER, leg_m DOUBLE)
    """)
    con.executemany("INSERT INTO trajectory_points VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    probe_rows())


def restore_probe_dataset(con) -> None:
    con.execute("DROP TABLE IF EXISTS trajectory_points")
    con.execute("CREATE TABLE trajectory_points AS SELECT * FROM trajectory_points_real")
    con.execute("DROP TABLE trajectory_points_real")
