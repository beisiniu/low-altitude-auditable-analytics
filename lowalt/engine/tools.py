"""Low-altitude analysis tools + a deterministic natural-language resolver.

Two toolchains are deliberately built on the SAME DuckDB tables:

  BASELINE  generic tools only (arbitrary read-only SQL + bounding-box spatial
            approximation).  It can attempt every question; it just has no
            encoded business definitions and no polygon geometry.
  LOWALT    baseline tools + three registered low-altitude functions with
            explicit, documented calculation definitions.

This is a fair comparison: the baseline is never disabled from a category.
Where it fails, it fails on *definition/geometry*, which is exactly what the
secondary development adds.  See EVAL_REPORT.md for the honest caveats.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import duckdb

from .audit import Fact, Verifier, VerifyResult

DAY_START_MIN = 7 * 60


# --------------------------------------------------------------------------
# Result / registry
# --------------------------------------------------------------------------
@dataclass
class ToolResult:
    tool: str
    answer: str
    value: Any
    facts: list[Fact] = field(default_factory=list)
    steps: int = 1
    evidence: dict = field(default_factory=dict)
    verify: VerifyResult | None = None


TOOLS: dict[str, Any] = {}
LOWALT_TOOLS: set[str] = set()


def tool(name: str, *, lowalt: bool = False):
    def deco(fn):
        TOOLS[name] = fn
        if lowalt:
            LOWALT_TOOLS.add(name)
        return fn

    return deco


def hhmm_to_min(s: str) -> float:
    h, m = (int(x) for x in s.split(":"))
    return (h * 60 + m) - DAY_START_MIN


def min_to_hhmm(t: float) -> str:
    total = int(round(t)) + DAY_START_MIN
    return f"{total // 60:02d}:{total % 60:02d}"


def point_in_polygon(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    """Even-odd ray casting. Deliberately written from scratch (no Shapely) so
    the independent second implementation in tests/ can cross-check it."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def zone_polygons(con) -> dict[str, tuple[str, list[tuple[float, float]], float, float]]:
    rows = con.execute("""
        SELECT zone_id, zone_name, lat, lon, active_from_min, active_to_min
        FROM no_fly_zones ORDER BY zone_id, vertex_seq
    """).fetchall()
    out: dict[str, tuple[str, list[tuple[float, float]], float, float]] = {}
    for zid, zname, lat, lon, t0, t1 in rows:
        if zid not in out:
            out[zid] = (zname, [], float(t0), float(t1))
        out[zid][1].append((float(lat), float(lon)))
    return out


# --------------------------------------------------------------------------
# BASELINE tool 1: generic read-only SQL aggregate
# --------------------------------------------------------------------------
@tool("sql_aggregate")
def sql_aggregate(con, p: dict, *, verifier: Verifier | None = None) -> ToolResult:
    """The original toolchain: an analyst writes the SQL by hand. No encoded
    definition of 'launch node', 'cruise layer' or 'conflict event'."""
    sql = p["sql"]
    params = list(p.get("params", []))
    row = con.execute(sql, params).fetchone()
    value = float(row[0]) if row and row[0] is not None else 0.0
    fact = Fact(id="N1", label=p.get("label", "sql_aggregate"), value=value,
                unit=p.get("unit", "count"), sql=sql, params=params,
                citations=("orders.csv", "trajectory_points.csv"))
    vr = (verifier or Verifier(con)).verify([fact], strict=False)
    return ToolResult(tool="sql_aggregate", answer=f"{value:g}", value=value,
                      facts=[fact], steps=1, verify=vr,
                      evidence={"method": "ad-hoc SQL", "sql": sql, "params": params})


# --------------------------------------------------------------------------
# BASELINE tool 2: bounding-box spatial approximation (plain SQL)
# --------------------------------------------------------------------------
@tool("bbox_conflict_approx")
def bbox_conflict_approx(con, p: dict, *, verifier: Verifier | None = None) -> ToolResult:
    """What the generic toolchain can do without a polygon routine: replace the
    NFZ polygon by its axis-aligned bounding box, in SQL. We give it the SAME
    event-segmentation rule as the low-altitude tool, so the only difference is
    geometry (bbox vs polygon) -- not a missing capability."""
    where, params = [], []
    if p.get("day") is not None:
        where.append("t.day = ?")
        params.append(p["day"])
    if p.get("zone_id"):
        where.append("z.zone_id = ?")
        params.append(p["zone_id"])
    if p.get("traj_id"):
        where.append("t.traj_id = ?")
        params.append(p["traj_id"])
    w = (" AND " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT t.traj_id, z.zone_id, t.t_min, t.seq
        FROM trajectory_points t
        JOIN (
            SELECT zone_id, MIN(lat) AS lo_lat, MAX(lat) AS hi_lat,
                   MIN(lon) AS lo_lon, MAX(lon) AS hi_lon,
                   MIN(active_from_min) AS t0, MAX(active_to_min) AS t1
            FROM no_fly_zones GROUP BY zone_id
        ) z ON t.lat BETWEEN z.lo_lat AND z.hi_lat
           AND t.lon BETWEEN z.lo_lon AND z.hi_lon
           AND t.t_min >= z.t0 AND t.t_min < z.t1
        WHERE 1=1{w}
        ORDER BY t.traj_id, z.zone_id, t.t_min, t.seq
    """
    hits = con.execute(sql, params).fetchall()
    events = _segment_events(hits)

    con.execute("DROP TABLE IF EXISTS conflict_events_baseline")
    con.execute("""CREATE TABLE conflict_events_baseline(
        traj_id VARCHAR, zone_id VARCHAR, first_t DOUBLE, last_t DOUBLE,
        n_points INTEGER, method VARCHAR)""")
    if events:
        con.executemany("INSERT INTO conflict_events_baseline VALUES (?,?,?,?,?,?)", events)

    sub_facts, inputs = [], []
    for i, (zid, n) in enumerate(sorted(_count_by_zone(events).items()), start=1):
        fid = f"N{i}"
        s = "SELECT COUNT(*) FROM conflict_events_baseline WHERE zone_id = ?"
        sub_facts.append(Fact(id=fid, label=f"bbox_conflicts:{zid}", value=float(n),
                              sql=s, params=[zid], citations=("no_fly_zones.csv",)))
        inputs.append(fid)
    total = float(len(events))
    total_fact = Fact(id="N99", label="bbox_conflicts:total", value=total,
                      fn="lowalt.nfz_conflict.v1", inputs=tuple(inputs),
                      citations=("conflict_events_baseline",))
    all_facts = sub_facts + [total_fact]
    vr = (verifier or Verifier(con)).verify(all_facts, strict=False)
    by_zone = _count_by_zone(events)
    minutes = float(sum(e[3] - e[2] for e in events))
    window_points = 0
    zp_where, zp_base = [], []
    if p.get("day") is not None:
        zp_where.append("day = ?")
        zp_base.append(p["day"])
    if p.get("traj_id"):
        zp_where.append("traj_id = ?")
        zp_base.append(p["traj_id"])
    zone_rows = con.execute(
        "SELECT zone_id, MIN(active_from_min), MAX(active_to_min) "
        "FROM no_fly_zones GROUP BY zone_id").fetchall()
    for zid, z0, z1 in zone_rows:
        if p.get("zone_id") and zid != p["zone_id"]:
            continue
        wp_sql = "SELECT COUNT(*) FROM trajectory_points"
        w = list(zp_where) + ["t_min >= ?", "t_min < ?"]
        if w:
            wp_sql += " WHERE " + " AND ".join(w)
        window_points += int(con.execute(wp_sql, zp_base + [z0, z1]).fetchone()[0])
    value = {"events": int(total), "points": len(hits),
             "minutes": round(minutes, 6), "by_zone": dict(sorted(by_zone.items())),
             "window_points": window_points}
    answer = (f"bbox 近似冲突 {int(total)} 次（{len(hits)} 个命中点，"
              f"累计 {minutes:.1f} 分钟）；激活窗口内轨迹点 {window_points} 个")
    return ToolResult(tool="bbox_conflict_approx", answer=answer,
                      value=value, facts=all_facts, steps=2, verify=vr,
                      evidence={"method": "SQL bounding box (over-approximation)",
                                "sql": sql, "params": params,
                                "materialised_table": "conflict_events_baseline"})


def _segment_events(hits: list[tuple]) -> list[tuple]:
    """Generic run-length segmentation of (traj, zone, t, seq) hits.

    One event = a maximal run of *consecutive sampled points* (seq differs by
    1) for the same (traj, zone).  Shared by both toolchains: the baseline uses
    it too, so the comparison isolates geometry rather than segmentation.
    """
    events: list[tuple] = []
    by_key: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for traj, zone, t, seq in hits:
        by_key.setdefault((traj, zone), []).append((int(seq), float(t)))
    for (traj, zone), pts in sorted(by_key.items()):
        pts.sort()
        start = prev_t = pts[0][1]
        prev_seq, n = pts[0][0], 1
        for seq, t in pts[1:]:
            if seq != prev_seq + 1:
                events.append((traj, zone, start, prev_t, n, "segment"))
                start, n = t, 1
            else:
                n += 1
            prev_t, prev_seq = t, seq
        events.append((traj, zone, start, prev_t, n, "segment"))
    return events


def _count_by_zone(events: list[tuple]) -> dict[str, int]:
    out: dict[str, int] = {}
    for _traj, zone, *_ in events:
        out[zone] = out.get(zone, 0) + 1
    return out


# --------------------------------------------------------------------------
# LOWALT tool 1: drone_demand_aggregate
# --------------------------------------------------------------------------
@tool("drone_demand_aggregate", lowalt=True)
def drone_demand_aggregate(con, p: dict, *, verifier: Verifier | None = None) -> ToolResult:
    """Definition (encoded in the tool, not left to the question writer):
      demand = COUNT(DISTINCT order_id) of orders released in the window,
      attributed to the launch vertiport recorded at trajectory seq=0.
      Group = (hour_bucket, origin_node_id).  Peak = argmax of the group counts.
    """
    t0 = hhmm_to_min(p.get("from_hhmm", "07:00"))
    t1 = hhmm_to_min(p.get("to_hhmm", "19:00"))
    day = p.get("day")
    params: list = [t0, t1]
    day_clause = ""
    if day is not None:
        day_clause = " AND o.day = ?"
        params.append(day)
    group_sql = """
        SELECT o.hour_bucket AS grp, t.origin_node_id AS node_id,
               COUNT(DISTINCT o.order_id) AS n
        FROM orders o
        JOIN trajectory_points t ON t.order_id = o.order_id AND t.seq = 0
        WHERE o.release_min >= ? AND o.release_min < ?{day_clause}
        GROUP BY 1, 2 ORDER BY n DESC, grp, node_id
    """.format(day_clause=day_clause)
    rows = con.execute(group_sql, params).fetchall()
    if not rows:
        return ToolResult(tool="drone_demand_aggregate", answer="窗口内无订单",
                          value={"peak": 0, "peak_node": None}, steps=1,
                          evidence={"sql": group_sql, "params": params})

    facts, inputs = [], []
    for i, (grp, node, n) in enumerate(rows, start=1):
        fid = f"N{i}"
        s = """
            SELECT COUNT(DISTINCT o.order_id)
            FROM orders o JOIN trajectory_points t
              ON t.order_id = o.order_id AND t.seq = 0
            WHERE o.release_min >= ? AND o.release_min < ?
              AND o.hour_bucket = ? AND t.origin_node_id = ?{day_clause}
        """.format(day_clause=day_clause)
        sp = [t0, t1, grp, node] + ([day] if day is not None else [])
        facts.append(Fact(id=fid, label=f"demand:{grp}:node{node}", value=float(n),
                          sql=s, params=sp, citations=("orders.csv", "trajectory_points.csv")))
        inputs.append(fid)

    peak = int(rows[0][2])
    peak_hour, peak_node = rows[0][0], int(rows[0][1])
    labels = [f"{g}:node{n}" for g, n, _ in rows]
    peak_fact = Fact(id="N98", label="demand:peak", value=float(peak),
                     fn="lowalt.demand_peak.v1", inputs=tuple(inputs),
                     params=[labels], how=f"{peak_hour}:node{peak_node}",
                     citations=("orders.csv",))

    # One auditable SQL fact per published number: total, per-hour, per-node.
    total_sql = """
        SELECT COUNT(DISTINCT order_id) FROM orders
        WHERE release_min >= ? AND release_min < ?{day_clause}
    """.format(day_clause=" AND day = ?" if day is not None else "")
    total = int(con.execute(total_sql, params).fetchone()[0])
    total_fact = Fact(id="N90", label="demand:total", value=float(total),
                      sql=total_sql, params=list(params), citations=("orders.csv",))

    by_hour: dict[str, int] = {}
    by_node: dict[int, int] = {}
    for grp, node, n in rows:
        by_hour[grp] = by_hour.get(grp, 0) + int(n)
        by_node[int(node)] = by_node.get(int(node), 0) + int(n)

    hour_facts, node_facts = [], []
    for i, (h, n) in enumerate(sorted(by_hour.items()), start=1):
        s = """SELECT COUNT(DISTINCT o.order_id) FROM orders o
               JOIN trajectory_points t ON t.order_id=o.order_id AND t.seq=0
               WHERE o.release_min >= ? AND o.release_min < ?
                 AND o.hour_bucket = ?{day_clause}""".format(
            day_clause=" AND o.day = ?" if day is not None else "")
        hour_facts.append(Fact(id=f"H{i}", label=f"demand:hour:{h}", value=float(n),
                               sql=s, params=[t0, t1, h] + ([day] if day is not None else []),
                               citations=("orders.csv",)))
    for i, (node, n) in enumerate(sorted(by_node.items()), start=1):
        s = """SELECT COUNT(DISTINCT o.order_id) FROM orders o
               JOIN trajectory_points t ON t.order_id=o.order_id AND t.seq=0
               WHERE o.release_min >= ? AND o.release_min < ?
                 AND t.origin_node_id = ?{day_clause}""".format(
            day_clause=" AND o.day = ?" if day is not None else "")
        node_facts.append(Fact(id=f"V{i}", label=f"demand:node:{node}", value=float(n),
                               sql=s, params=[t0, t1, node] + ([day] if day is not None else []),
                               citations=("trajectory_points.csv",)))

    all_facts = facts + hour_facts + node_facts + [total_fact, peak_fact]
    vr = (verifier or Verifier(con)).verify(all_facts, strict=False)
    answer = (f"窗口内 {total} 单；峰值 {peak} 单出现在 {peak_hour}、起降节点 {peak_node}"
              f"（{len(rows)} 个分组，{len(by_hour)} 个时段，{len(by_node)} 个节点）")
    value = {"total": total, "peak": peak, "peak_hour": peak_hour,
             "peak_node": peak_node, "n_groups": len(rows),
             "by_hour": dict(sorted(by_hour.items())), "by_node": by_node}
    return ToolResult(tool="drone_demand_aggregate", answer=answer, value=value,
                      facts=all_facts, steps=2, verify=vr,
                      evidence={"method": "encoded demand definition + per-group SQL facts",
                                "definition": "COUNT(DISTINCT order_id) by (hour_bucket, launch vertiport)",
                                "window": [p.get("from_hhmm"), p.get("to_hhmm")],
                                "groups": len(rows), "verifiable_numbers": len(all_facts)})


# --------------------------------------------------------------------------
# LOWALT tool 2: no_fly_conflict_count
# --------------------------------------------------------------------------
@tool("no_fly_conflict_count", lowalt=True)
def no_fly_conflict_count(con, p: dict, *, verifier: Verifier | None = None) -> ToolResult:
    """Definition:
      A trajectory point is a HIT when its WGS-84 position is inside a
      published NFZ polygon (even-odd ray casting) AND its timestamp is inside
      that zone's activation window [from, to).
      A CONFLICT EVENT is one maximal run of hits of the same (traj, zone).
      Output: event_count (the answer), point_count, conflict_minutes.
    The geometric decisions are materialised into DuckDB tables so the
    original SQL-based verifier can re-count every published number.
    """
    zones = zone_polygons(con)
    if p.get("zone_id"):
        zones = {k: v for k, v in zones.items() if k == p["zone_id"]}
    where, params = [], []
    if p.get("day") is not None:
        where.append("day = ?")
        params.append(p["day"])
    if p.get("traj_id"):
        where.append("traj_id = ?")
        params.append(p["traj_id"])
    w = (" WHERE " + " AND ".join(where)) if where else ""
    pts = con.execute(
        "SELECT traj_id, seq, t_min, lat, lon FROM trajectory_points"
        + w + " ORDER BY traj_id, seq", params).fetchall()

    hits: list[tuple] = []
    for traj, seq, t, lat, lon in pts:
        t, lat, lon = float(t), float(lat), float(lon)
        for zid, (_name, poly, t0, t1) in zones.items():
            if t0 <= t < t1 and point_in_polygon(lat, lon, poly):
                hits.append((traj, zid, t, seq))
    events = _segment_events(hits)

    con.execute("DROP TABLE IF EXISTS conflict_point_hits")
    con.execute("""CREATE TABLE conflict_point_hits(
        traj_id VARCHAR, zone_id VARCHAR, t_min DOUBLE, seq INTEGER,
        method VARCHAR)""")
    if hits:
        con.executemany("INSERT INTO conflict_point_hits VALUES (?,?,?,?,?)",
                        [(a, b, c, d, "ray_casting") for a, b, c, d in hits])
    con.execute("DROP TABLE IF EXISTS conflict_events")
    con.execute("""CREATE TABLE conflict_events(
        traj_id VARCHAR, zone_id VARCHAR, first_t DOUBLE, last_t DOUBLE,
        n_points INTEGER, method VARCHAR)""")
    if events:
        con.executemany("INSERT INTO conflict_events VALUES (?,?,?,?,?,?)", events)

    by_zone = _count_by_zone(events)
    facts, inputs = [], []
    for i, zid in enumerate(sorted(by_zone), start=1):
        fid = f"N{i}"
        s = "SELECT COUNT(*) FROM conflict_events WHERE zone_id = ?"
        facts.append(Fact(id=fid, label=f"nfz_conflicts:{zid}",
                          value=float(by_zone[zid]), sql=s, params=[zid],
                          citations=("conflict_events", "no_fly_zones.csv")))
        inputs.append(fid)
    total = float(len(events))
    total_fact = Fact(id="N99", label="nfz_conflicts:total", value=total,
                      fn="lowalt.nfz_conflict.v1", inputs=tuple(inputs),
                      citations=("conflict_events",))
    all_facts = facts + [total_fact]
    vr = (verifier or Verifier(con)).verify(all_facts, strict=False)
    minutes = float(sum(e[3] - e[2] for e in events))
    # Trajectory points that are merely IN the activation window (regardless of
    # geometry) -- needed for the overlap questions in the composite tier.
    window_points = 0
    for _zid, (_n, _poly, z0, z1) in zones.items():
        wp_sql = "SELECT COUNT(*) FROM trajectory_points"
        wp_where = list(where) + ["t_min >= ?", "t_min < ?"]
        if wp_where:
            wp_sql += " WHERE " + " AND ".join(wp_where)
        window_points += int(con.execute(wp_sql, params + [z0, z1]).fetchone()[0])
    answer = (f"冲突 {int(total)} 次（{len(hits)} 个命中点，累计 {minutes:.1f} 分钟）；"
              f"激活窗口内轨迹点 {window_points} 个")
    value = {"events": int(total), "points": len(hits),
             "minutes": round(minutes, 6), "by_zone": dict(sorted(by_zone.items())),
             "window_points": window_points}
    return ToolResult(tool="no_fly_conflict_count", answer=answer, value=value,
                      facts=all_facts, steps=3, verify=vr,
                      evidence={"method": "even-odd ray casting + time window",
                                "definition": "1 event = 1 maximal run of (traj,zone) hits",
                                "materialised_tables": ["conflict_point_hits", "conflict_events"],
                                "point_count": len(hits), "conflict_minutes": minutes,
                                "zones": sorted(zones)})


# --------------------------------------------------------------------------
# LOWALT tool 3: altitude_distribution
# --------------------------------------------------------------------------
@tool("altitude_distribution", lowalt=True)
def altitude_distribution(con, p: dict, *, verifier: Verifier | None = None) -> ToolResult:
    """Definition (this is the part the ad-hoc SQL baseline does not encode):
      Only CRUISE-phase points count.  Group by the published 15 m layer
      label.  Output = share % of the dominant layer.
    Excluding climb/descend/ground is a business definition, not a SQL
    capability -- the baseline can write the SQL but has no encoded definition.
    """
    where, params = ["phase = 'cruise'"], []
    if p.get("day") is not None:
        where.append("day = ?")
        params.append(p["day"])
    if p.get("from_hhmm"):
        where.append("t_min >= ?")
        params.append(hhmm_to_min(p["from_hhmm"]))
    if p.get("to_hhmm"):
        where.append("t_min < ?")
        params.append(hhmm_to_min(p["to_hhmm"]))
    w = " WHERE " + " AND ".join(where)
    rows = con.execute(
        "SELECT altitude_layer, COUNT(*) FROM trajectory_points" + w
        + " GROUP BY 1 ORDER BY 1", params).fetchall()

    facts, inputs = [], []
    for i, (layer, n) in enumerate(rows, start=1):
        fid = f"N{i}"
        s = ("SELECT COUNT(*) FROM trajectory_points" + w
             + " AND altitude_layer = ?")
        facts.append(Fact(id=fid, label=f"altitude:{layer}", value=float(n),
                          sql=s, params=params + [layer],
                          citations=("trajectory_points.csv",)))
        inputs.append(fid)
    total = sum(int(n) for _, n in rows)
    dominant, dominant_n = max(rows, key=lambda r: r[1]) if rows else ("n/a", 0)
    share = dominant_n / total * 100 if total else 0.0
    share_fact = Fact(id="N97", label="altitude:dominant_share", value=share,
                      unit="percent", fn="lowalt.altitude_profile.v1",
                      inputs=tuple(inputs), citations=("trajectory_points.csv",))
    all_facts = facts + [share_fact]
    vr = (verifier or Verifier(con)).verify(all_facts, strict=False)
    answer = (f"主导高度层 {dominant}，{dominant_n}/{total} 个巡航点，占 {share:.2f}%")
    point_where, point_params = [], []
    if p.get("day") is not None:
        point_where.append("day = ?")
        point_params.append(p["day"])
    if p.get("from_hhmm"):
        point_where.append("t_min >= ?")
        point_params.append(hhmm_to_min(p["from_hhmm"]))
    if p.get("to_hhmm"):
        point_where.append("t_min < ?")
        point_params.append(hhmm_to_min(p["to_hhmm"]))
    total_points = int(con.execute(
        "SELECT COUNT(*) FROM trajectory_points"
        + ((" WHERE " + " AND ".join(point_where)) if point_where else ""),
        point_params).fetchone()[0])
    value = {"dominant_layer": dominant, "dominant_share_pct": round(share, 6),
             "dominant_count": int(dominant_n), "total_cruise_points": total,
             "total_points": total_points,
             "cruise_ratio_pct": round(total / total_points * 100, 6) if total_points else 0.0,
             "by_layer": {layer: int(n) for layer, n in rows}}
    return ToolResult(tool="altitude_distribution", answer=answer, value=value,
                      facts=all_facts, steps=2, verify=vr,
                      evidence={"method": "SQL layer counts + encoded cruise-only definition",
                                "definition": "cruise points only, 15 m layers",
                                "layers": {layer: int(n) for layer, n in rows}})


# --------------------------------------------------------------------------
# Deterministic natural-language entry (no LLM, no randomness)
# --------------------------------------------------------------------------
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[—\-~～至到]\s*(\d{1,2}):(\d{2})")

# v2 resolver.  v1 matched TOPIC NOUNS ("禁飞区" -> conflict tool), which broke
# composite questions: "禁飞区活跃的窗口内共有多少配送订单" asks for ORDERS,
# the zone only qualifies the time window.  v2 scores the ASKED-FOR QUANTITY
# instead, and exposes an explicit ambiguity state instead of guessing.
#
# Upstream principle this follows: local rules resolve what they can; only a
# genuine AMBIGUOUS state should be handed to a planner model.  EXACT /
# AMBIGUOUS / UNRESOLVED are recorded in the eval trace so the hand-off rate is
# measurable rather than implicit.
_ASK_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "no_fly_conflict_count": [
        (r"冲突|conflict|闯入", 4),
        (r"命中点|累计时长|冲突时长|冲突次数", 4),
        (r"禁飞区[^。？?]{0,30}(轨迹点|个点|点数|架次|航迹点)|落入[^。？?]{0,8}禁飞区", 3),
    ],
    "altitude_distribution": [
        (r"高度层|主导层|高度剖面|altitude|垂直层|哪一层|哪层", 4),
        (r"巡航(阶段)?(轨迹)?点|巡航点|cruise|高度", 3),
    ],
    "drone_demand_aggregate": [
        (r"订单|单量|配送量|需求量|需求|起降节点|起降场|demand|派送|(?:多少|几)\s*(?:单|笔)", 3),
        (r"峰值|最忙|最多|最高|总量", 2),
        (r"占比|百分比|比例|占全天|占全部|按小时|按时段|分解|分组", 1),
    ],
}


def resolve_question(q: str) -> dict:
    """Deterministic question -> typed tool call, with an explicit state.

    Returns {tool, params, state, scores}.  `state` is one of EXACT,
    AMBIGUOUS (two asked-for quantities are nearly tied -> hand to a planner)
    or UNRESOLVED (no asked-for quantity detected -> refuse / generic SQL).
    """
    p: dict[str, Any] = {}
    m = _TIME_RE.search(q)
    if m:
        p["from_hhmm"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        p["to_hhmm"] = f"{int(m.group(3)):02d}:{m.group(4)}"
    day = re.search(r"第\s*([0-4])\s*天|day\s*([0-4])", q, re.I)
    if day:
        p["day"] = int(day.group(1) or day.group(2))
    zone = re.search(r"机场|airport|z_airport", q, re.I)
    temp = re.search(r"体育馆|临时|stadium|z_temp", q, re.I)
    if zone:
        p["zone_id"] = "z_airport_szx"
    elif temp:
        p["zone_id"] = "z_temp_baoan_event"
    traj = re.search(r"T-[a-z0-9\-]+", q, re.I)
    if traj:
        p["traj_id"] = traj.group(0)

    scores = {
        tool: sum(weight for pattern, weight in pats if re.search(pattern, q, re.I))
        for tool, pats in _ASK_PATTERNS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, second = ranked[0][1], ranked[1][1]
    if top == 0:
        state, name = "UNRESOLVED", "sql_aggregate"
    elif second > 0 and top - second <= 1:
        state, name = "AMBIGUOUS", ranked[0][0]
    else:
        state, name = "EXACT", ranked[0][0]
    return {"tool": name, "params": p, "state": state, "scores": scores}


# Baseline never gets the specialised tools; it is DOWNGRADED, not blocked.
BASELINE_DOWNGRADE = {
    "no_fly_conflict_count": "bbox_conflict_approx",
    "drone_demand_aggregate": "sql_aggregate",
    "altitude_distribution": "sql_aggregate",
}


def downgrade_plan(plan: dict, con) -> dict:
    """Build an equivalent generic-SQL plan for the original toolchain."""
    name, p = plan["tool"], dict(plan["params"])
    if name == "no_fly_conflict_count":
        return {"tool": "bbox_conflict_approx", "params": p}
    t0 = hhmm_to_min(p.get("from_hhmm", "07:00"))
    t1 = hhmm_to_min(p.get("to_hhmm", "19:00"))
    day = p.get("day")
    if name == "drone_demand_aggregate":
        # ad-hoc: peak group count, joins seq=0 to reach a launch node
        sql = """SELECT MAX(n) FROM (
                   SELECT o.hour_bucket, t.origin_node_id,
                          COUNT(DISTINCT o.order_id) AS n
                   FROM orders o JOIN trajectory_points t
                     ON t.order_id = o.order_id AND t.seq = 0
                   WHERE o.release_min >= ? AND o.release_min < ?"""
        params: list = [t0, t1]
        if day is not None:
            sql += " AND o.day = ?"
            params.append(day)
        sql += " GROUP BY 1, 2)"
        return {"tool": "sql_aggregate", "params": {"sql": sql, "params": params,
                                                     "label": "demand:peak"}}
    # altitude: ad-hoc SQL with NO encoded cruise-only definition
    sql = """SELECT MAX(n) * 100.0 / SUM(n) FROM (
               SELECT altitude_layer, COUNT(*) AS n FROM trajectory_points
               WHERE 1=1"""
    params = []
    if day is not None:
        sql += " AND day = ?"
        params.append(day)
    if p.get("from_hhmm"):
        sql += " AND t_min >= ?"
        params.append(t0)
    if p.get("to_hhmm"):
        sql += " AND t_min < ?"
        params.append(t1)
    sql += " GROUP BY 1)"
    return {"tool": "sql_aggregate", "params": {"sql": sql, "params": params,
                                                 "unit": "percent",
                                                 "label": "altitude:dominant_share"}}


def evidence_digest(result: ToolResult) -> str:
    return hashlib.sha256(json.dumps(
        {"tool": result.tool, "value": result.value, "evidence": result.evidence},
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
