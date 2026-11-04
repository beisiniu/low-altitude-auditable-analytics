"""Deterministic ingestion for the Shenzhen Qianhai low-altitude logistics case.

WHY THIS FILE EXISTS
--------------------
The IEEE DataPort record (DOI 10.21227/3gxb-mj63, CC BY 4.0) ships its main
53.1 KB dataset archive behind an IEEE SAML login.  Its *dataset scripts*
archive, however, is a public signed link, and contains ``build_case.py``:
24 KB of Python that embeds every geographic and stochastic parameter of the
case (27 real WGS-84 nodes, two time-activated no-fly-zone polygons, the
piecewise Poisson order process, the three fixed seeds, the congestion table
and the weather Markov chain).

The companion ``dagcd`` package (linked from the DataPort page) is a dead link
(HTTP 404), so ``build_case.py`` cannot be executed as-is.  This script instead
**reads the constants straight out of the archived public file via AST** and
reproduces its documented data-generating process with the standard library.
Nothing is invented: coordinates, polygons, windows, lambdas and seeds are the
published values.  The only derived artefacts are explicitly labelled.

    python lowalt/data/build_dataset.py --out lowalt/data/out

Outputs CSV tables plus ``manifest.json`` (row counts, provenance, sha256).
Re-running with the same arguments is byte-identical.

What is REAL vs DERIVED (also stated in SOURCES.md):
    REAL (published)     27 WGS-84 node coordinates, 2 NFZ polygons and their
                         activation windows, order-release process + seeds,
                         07:00-19:00 horizon, congestion table, weather chain.
    DERIVED (by us)      order-level rows sampled from that published process;
                         delivery trajectories interpolated between the
                         published vertiports and customer nodes; cruise
                         altitude layers.  These are reproducible synthetic
                         constructs, NOT measured drone flights.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_SCRIPT = HERE / "ieee_build_case.py"

# Published on the DataPort abstract: circuity = 1.724 +/- 0.537 fitted from
# 45 AMap driving-distance queries.  Kept for provenance only; spatial analysis
# in this project uses true WGS-84 / local metric metres, NOT the road-equivalent
# frame that the dispatch benchmark scales its coordinates by.
CIRCUITY_MEAN = 1.724
CIRCUITY_STD = 0.537

ALTITUDE_LAYERS_M = (45.0, 60.0, 75.0, 90.0)  # GEMMA-style 15 m vertical layers
DAY_START_HOUR = 7
DAY_SEEDS = (2026092302, 1, 2, 3, 4)  # day0 = archived seed; day1..4 = --order-seed variants
WX_SEEDS = (2026092303, 1, 2, 3, 4)


# --------------------------------------------------------------------------
# 1. Read published constants from the archived public script (AST, not exec)
# --------------------------------------------------------------------------
def published_constants(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict = {}

    def literal(node: ast.AST):
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return None

    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            value = literal(node.value)
            if value is not None:
                out[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
            # e.g. LAT0, LON0 = 22.55, 113.93 and the DRONE_* block
            names = [e.id for e in target.elts if isinstance(e, ast.Name)]
            values = [literal(e) for e in node.value.elts]
            if len(names) == len(values):
                out.update({n: v for n, v in zip(names, values) if v is not None})
        # expressions (e.g. M_PER_DEG_LAT = math.pi * R_EARTH / 180) are recomputed below
    return out


# --------------------------------------------------------------------------
# 2. Geometry helpers
# --------------------------------------------------------------------------
def build_projection(K: dict) -> tuple[float, float]:
    r_earth = 6378137.0
    m_per_deg_lat = math.pi * r_earth / 180.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(K["LAT0"]))
    return m_per_deg_lat, m_per_deg_lon


def to_local(lat: float, lon: float, K: dict, proj: tuple[float, float]) -> tuple[float, float]:
    """Equirectangular local metric frame, exactly as build_case.to_local."""
    m_lat, m_lon = proj
    return ((lon - K["LON0"]) * m_lon, (lat - K["LAT0"]) * m_lat)


def min_to_hhmm(t: float) -> str:
    total = DAY_START_HOUR * 60 + int(round(t))
    return f"{total // 60:02d}:{total % 60:02d}"


def hour_bucket(t: float) -> str:
    h = DAY_START_HOUR + int(t // 60)
    return f"{h:02d}:00-{h + 1:02d}:00"


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------
# 3. Tables
# --------------------------------------------------------------------------
def build_nodes(K: dict, proj: tuple[float, float]) -> list[dict]:
    rows = []
    for nid, (_key, name, lat, lon) in enumerate(K["COORDS"]):
        x, y = to_local(lat, lon, K, proj)
        kind = "depot" if nid == 0 else "vertiport" if nid <= 3 else "customer"
        rows.append({
            "node_id": nid,
            "name": name,
            "kind": kind,
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "local_x_m": f"{x:.3f}",
            "local_y_m": f"{y:.3f}",
        })
    return rows


def build_nfz(K: dict, proj: tuple[float, float]) -> list[dict]:
    """Two published NFZs as WGS-84 polygons.

    NOTE ON FRAME: the dispatch benchmark scales every coordinate by the
    circuity scalar into a road-equivalent frame, and builds the temporary
    square with an unscaled 120 m half-side.  We do spatial analysis in true
    WGS-84 instead, so the temporary square is defined here as +/-120 real
    metres around the published centre.  This is documented, deliberate, and
    affects only the small temporary zone.
    """
    rows: list[dict] = []
    m_lat, m_lon = proj
    zones = []

    airport = [(lat, lon) for lat, lon in K["AIRPORT_NFZ_LATLON"]]
    zones.append(("z_airport_szx", "深圳宝安机场禁飞区(5顶点简化)", airport,
                  K["AIRPORT_NFZ_WINDOW"]))

    c_lat, c_lon = K["TEMP_NFZ_CENTER"]
    half_m = K["TEMP_NFZ_HALF_M"]
    d_lat = half_m / m_lat
    d_lon = half_m / m_lon
    temp = [
        (c_lat - d_lat, c_lon - d_lon),
        (c_lat + d_lat, c_lon - d_lon),
        (c_lat + d_lat, c_lon + d_lon),
        (c_lat - d_lat, c_lon + d_lon),
    ]
    zones.append(("z_temp_baoan_event", "宝安体育馆临时禁飞区(方形)", temp,
                  K["TEMP_NFZ_WINDOW"]))

    for zone_id, zone_name, poly, (t0, t1) in zones:
        for seq, (lat, lon) in enumerate(poly):
            rows.append({
                "zone_id": zone_id,
                "zone_name": zone_name,
                "vertex_seq": seq,
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "active_from_min": t0,
                "active_to_min": t1,
                "active_from_hhmm": min_to_hhmm(t0),
                "active_to_hhmm": min_to_hhmm(t1),
            })
    return rows


def build_orders(K: dict, days: int) -> list[dict]:
    """Reproduce build_case.build_orders verbatim (piecewise-homogeneous
    Poisson by multiplicative wait, then uniform placement inside the segment).
    """
    customer_ids = [nid for nid in range(len(K["COORDS"])) if nid > 3]
    rows: list[dict] = []
    for day in range(days):
        rng = random.Random(DAY_SEEDS[day])
        releases: list[float] = []
        for a, b, lam in K["RELEASE_SEGMENTS"]:
            n = 0
            limit = math.exp(-lam * (b - a))
            p = 1.0
            while p > limit:
                n += 1
                p *= rng.random()
            n -= 1
            releases.extend(a + (b - a) * rng.random() for _ in range(n))
        releases.sort()
        for k, t in enumerate(releases, start=1):
            node = customer_ids[rng.randrange(len(customer_ids))]
            weight = rng.uniform(0.5, 0.9 * K["DRONE_PAYLOAD"])
            service = rng.uniform(1.0, 3.0)
            e = t + rng.uniform(0.0, 15.0)
            l = e + rng.uniform(30.0, 90.0)
            rows.append({
                "order_id": f"d{day}-o{k}",
                "day": day,
                "release_min": f"{t:.3f}",
                "release_hhmm": min_to_hhmm(t),
                "hour_bucket": hour_bucket(t),
                "customer_node_id": node,
                "weight_kg": f"{weight:.4f}",
                "service_min": f"{service:.4f}",
                "earliest_min": f"{e:.3f}",
                "latest_min": f"{l:.3f}",
            })
    return rows


def build_trajectories(K: dict, proj, nodes: list[dict], orders: list[dict]) -> list[dict]:
    """DERIVED: deterministic 4D trajectories.

    Rule (fixed, documented, seeded by content hash -- no RNG):
      * launch from the nearest of the 3 published vertiports (true metres);
      * cruise layer chosen from {45,60,75,90} m by sha256(day, order_id);
      * 9 sampled points, linear in plan position and in time, from
        release+2 min over ceil(distance / DRONE_CRUISE) minutes (min 2);
      * vertical profile: climb 0 -> layer, hold, descend layer -> 0.
    These are NOT measured flights.  They exist so that a spatial/vertical
    analysis tool has a reproducible, auditable input.
    """
    xy = {int(n["node_id"]): (float(n["local_x_m"]), float(n["local_y_m"])) for n in nodes}
    ll = {int(n["node_id"]): (float(n["lat"]), float(n["lon"])) for n in nodes}
    vertiports = [int(n["node_id"]) for n in nodes if n["kind"] == "vertiport"]
    rows: list[dict] = []
    for o in orders:
        cust = int(o["customer_node_id"])
        origin = min(vertiports, key=lambda v: distance(xy[v], xy[cust]))
        leg_m = distance(xy[origin], xy[cust])
        flight_min = max(2.0, math.ceil(leg_m / K["DRONE_CRUISE"]))
        digest = hashlib.sha256(f"{o['day']}|{o['order_id']}".encode()).digest()
        layer = ALTITUDE_LAYERS_M[digest[0] % len(ALTITUDE_LAYERS_M)]
        depart = float(o["release_min"]) + 2.0
        traj_id = f"T-{o['order_id']}"
        n_pts = 9
        for seq in range(n_pts):
            u = seq / (n_pts - 1)
            lat = ll[origin][0] + (ll[cust][0] - ll[origin][0]) * u
            lon = ll[origin][1] + (ll[cust][1] - ll[origin][1]) * u
            t = depart + flight_min * u
            if seq == 0 or seq == n_pts - 1:
                alt, phase = 0.0, "ground"
            elif seq in (1, n_pts - 2):
                alt, phase = layer / 2.0, "climb" if seq == 1 else "descend"
            else:
                alt, phase = layer, "cruise"
            rows.append({
                "traj_id": traj_id,
                "order_id": o["order_id"],
                "day": o["day"],
                "seq": seq,
                "t_min": f"{t:.3f}",
                "t_hhmm": min_to_hhmm(t),
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "altitude_m": f"{alt:.1f}",
                "altitude_layer": f"{int(layer):03d}-{int(layer) + 15:03d}m",
                "phase": phase,
                "origin_node_id": origin,
                "customer_node_id": cust,
                "leg_m": f"{leg_m:.1f}",
            })
    return rows


def build_congestion(K: dict) -> list[dict]:
    c = K["CONG_LEVEL"]
    c_peak, c_sh = 1.0 - 0.2 * c, 1.0 - 0.4 * c
    segs = [(0.0, 30.0, c_sh), (30.0, 150.0, c_peak), (150.0, 210.0, c_sh),
            (210.0, 570.0, 1.0), (570.0, 630.0, c_sh), (630.0, 720.0, c_peak)]
    return [{"seg_from_min": a, "seg_to_min": b, "speed_factor": round(f, 4),
             "from_hhmm": min_to_hhmm(a), "to_hhmm": min_to_hhmm(b)} for a, b, f in segs]


# --------------------------------------------------------------------------
# 4. Write
# --------------------------------------------------------------------------
def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError(f"refusing to write empty table: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--days", type=int, default=5)
    args = ap.parse_args()

    K = published_constants(SOURCE_SCRIPT)
    required = {"COORDS", "AIRPORT_NFZ_LATLON", "TEMP_NFZ_CENTER", "TEMP_NFZ_HALF_M",
                "AIRPORT_NFZ_WINDOW", "TEMP_NFZ_WINDOW", "RELEASE_SEGMENTS",
                "ORDER_SEED", "SIM_END", "DRONE_PAYLOAD", "DRONE_CRUISE",
                "CONG_LEVEL", "WX_CHAIN_RAINY", "MEAN_EVENT_MIN", "LAT0", "LON0"}
    missing = required - K.keys()
    if missing:
        raise RuntimeError(f"published script is missing expected constants: {sorted(missing)}")

    proj = build_projection(K)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    nodes = build_nodes(K, proj)
    nfz = build_nfz(K, proj)
    orders = build_orders(K, args.days)
    traj = build_trajectories(K, proj, nodes, orders)
    cong = build_congestion(K)

    tables = {
        "nodes.csv": nodes,
        "no_fly_zones.csv": nfz,
        "orders.csv": orders,
        "trajectory_points.csv": traj,
        "congestion.csv": cong,
    }
    for name, rows in tables.items():
        write_csv(out / name, rows)

    src_sha = hashlib.sha256(SOURCE_SCRIPT.read_bytes()).hexdigest()
    manifest = {
        "dataset": "Shenzhen Qianhai truck-drone low-altitude logistics case",
        "source_record": "IEEE DataPort DOI 10.21227/3gxb-mj63",
        "license": "CC BY 4.0",
        "source_artifact": "public DATASET SCRIPTS archive (build_case_script.zip)",
        "source_script": "lowalt/data/ieee_build_case.py",
        "source_script_sha256": src_sha,
        "access_note": (
            "Main 53.1 KB scenario archive requires IEEE SAML login; the public "
            "scripts archive embeds all geographic and stochastic parameters. "
            "Companion dagcd GitHub repo linked on the record returns HTTP 404, "
            "so the published generating process is reproduced here with the "
            "standard library."
        ),
        "published_parameters_used": {
            "n_nodes": len(K["COORDS"]),
            "n_no_fly_zones": 2,
            "release_segments": K["RELEASE_SEGMENTS"],
            "order_seeds": list(DAY_SEEDS[:args.days]),
            "weather_chain": K["WX_CHAIN_RAINY"],
            "mean_event_min": K["MEAN_EVENT_MIN"],
            "sim_end_min": K["SIM_END"],
            "circuity_mean_documented": CIRCUITY_MEAN,
            "circuity_std_documented": CIRCUITY_STD,
            "circuity_applied_to_coordinates": False,
        },
        "derived_notice": (
            "orders rows are sampled from the published seeded process; "
            "trajectory_points.csv is a DERIVED deterministic interpolation "
            "between published vertiports and customer nodes, not measured "
            "flight data. See lowalt/data/SOURCES.md."
        ),
        "frames": {
            "spatial": "WGS-84 lat/lon + equirectangular local metres anchored at 22.55N 113.93E",
            "time": "minutes since 07:00 local; 07:00-19:00 horizon",
        },
        "tables": {},
    }
    for name in tables:
        p = out / name
        manifest["tables"][name] = {
            "rows": len(tables[name]),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for name, meta in manifest["tables"].items():
        print(f"  {name:26s} {meta['rows']:6d} rows  sha256={meta['sha256'][:12]}")
    print(f"\n{sum(m['rows'] for m in manifest['tables'].values())} rows -> {out}")


if __name__ == "__main__":
    main()
