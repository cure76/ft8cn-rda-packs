#!/usr/bin/env python3
"""
Prepare offline RDA GeoJSON pack for Tula Oblast (TL-*).

Uses the same matching helpers as prepare_rda_pack.py.

OSM:
  - admin_level=6 districts / urban okrugs in Tula Oblast
  - Skip "городской округ Тула" (city as whole) — RDA uses TL-01..05 city districts
  - Geometry: Overpass ids (lz4) + OSM API 0.6 full.json (more reliable than out geom)

Usage:
  cd tools
  python prepare_tl_tula.py
  # then: cp out/tl_tula.geojson ../packs/ && python publish_catalog.py
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List

from prepare_rda_pack import (
    HERE,
    attach_rda,
    fetch_relations_geom,
    http_get,
    load_aliases,
    osm_json_to_geojson,
    parse_rda_list,
    RDA_LIST_URL,
)

AREA_TULA_OBLAST = 3600081993  # relation 81993
PACK_ID = "tl_tula"
TULA_CITY_OKRUG_ID = 4775559

OVERPASS_ID_URLS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def overpass_ids(ql: str) -> dict:
    body = ("data=" + urllib.parse.quote(ql)).encode("utf-8")
    last = None
    for url in OVERPASS_ID_URLS:
        try:
            print(f"[info] Overpass ids → {url}")
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "FT8CN-rda-prep/1.0"})
            raw = urllib.request.urlopen(req, timeout=180).read()
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[warn] {url}: {e}")
            last = e
    raise RuntimeError(f"All Overpass id endpoints failed: {last}")


def feature_osm_id(feat: dict):
    props = feat.get("properties") or {}
    tags = props.get("tags") if isinstance(props.get("tags"), dict) else props
    oid = props.get("id") or props.get("@id")
    if isinstance(oid, str) and "/" in oid:
        try:
            return int(oid.split("/")[-1])
        except ValueError:
            return None
    try:
        return int(oid) if oid is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Tula Oblast RDA GeoJSON pack")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out")
    parser.add_argument("--rda-list", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=HERE / "aliases_tl_tula.json")
    parser.add_argument("--simplify", type=float, default=0.0003)
    parser.add_argument("--cache-dir", type=Path, default=HERE / "cache")
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if args.rda_list and args.rda_list.exists():
        rda_text = args.rda_list.read_text(encoding="utf-8", errors="replace")
    else:
        cache_list = args.cache_dir / "rda_eng.txt"
        if cache_list.exists():
            rda_text = cache_list.read_text(encoding="utf-8", errors="replace")
        else:
            rda_text = http_get(RDA_LIST_URL).decode("utf-8", errors="replace")
            cache_list.write_text(rda_text, encoding="utf-8")

    rda_entries = parse_rda_list(rda_text, prefixes=("TL",))
    print(f"[info] Active TL codes: {len(rda_entries)}")
    (out_dir / "rda_tl_codes.json").write_text(
        json.dumps({k: v.name for k, v in sorted(rda_entries.items())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    aliases = load_aliases(args.aliases)
    print(f"[info] Aliases loaded: {len(aliases)} from {args.aliases}")

    cache_al6 = args.cache_dir / "osm_tl_al6.json"
    if cache_al6.exists():
        print(f"[info] Using cached {cache_al6.name}")
        osm_al6 = json.loads(cache_al6.read_text(encoding="utf-8"))
        # Recover wanted ids from cached relation elements with tags+members
        wanted = set()
        for el in osm_al6.get("elements", []):
            if el.get("type") == "relation" and el.get("tags", {}).get("admin_level") == "6":
                if el.get("id") != TULA_CITY_OKRUG_ID:
                    wanted.add(el["id"])
    else:
        ids_doc = overpass_ids(
            f"""
[out:json][timeout:300];
area({AREA_TULA_OBLAST})->.a;
relation["boundary"="administrative"]["admin_level"="6"](area.a);
out ids tags;
"""
        )
        wanted = {
            el["id"]
            for el in ids_doc.get("elements", [])
            if el.get("type") == "relation" and el.get("id") != TULA_CITY_OKRUG_ID
        }
        print(f"[info] Fetching {len(wanted)} relations via OSM API")
        osm_al6 = fetch_relations_geom(sorted(wanted))
        cache_al6.write_text(json.dumps(osm_al6), encoding="utf-8")

    if not wanted:
        # derive from cache relations that look like AL6
        wanted = {
            el["id"]
            for el in osm_al6.get("elements", [])
            if el.get("type") == "relation"
            and (el.get("tags") or {}).get("admin_level") == "6"
            and el.get("id") != TULA_CITY_OKRUG_ID
        }

    gj = osm_json_to_geojson(osm_al6)
    kept = []
    for feat in gj.get("features", []):
        oid = feature_osm_id(feat)
        if oid is None or oid not in wanted:
            continue
        kept.append(feat)
    print(f"[info] AL6 polygons kept: {len(kept)}")

    matched, unmatched, log_al6 = attach_rda(
        {"type": "FeatureCollection", "features": kept},
        rda_entries,
        aliases,
        args.simplify,
        prefix_filter="TL-",
    )
    print(f"[info] TL AL6: matched={len(matched)} unmatched={len(unmatched)}")

    # Keep multiple polygons per code (e.g. Aleksin + Novogurovsky = TL-14)
    seen = {f["properties"]["rda_code"] for f in matched}
    pack = {"type": "FeatureCollection", "name": PACK_ID, "features": matched}
    geojson_path = out_dir / f"{PACK_ID}.geojson"
    geojson_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] Wrote {geojson_path} ({len(matched)} features, {len(seen)} codes)")

    report = {
        "matched_count": len(matched),
        "matched_codes": sorted(seen),
        "missing_rda_codes": sorted(set(rda_entries) - seen),
        "al6_log": log_al6,
        "unmatched_al6": unmatched,
        "note": "TL-01..05 Tula city districts and TL-27 Leninsky not present as OSM AL6 polygons",
    }
    (out_dir / "report_tl.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"Matched features: {len(matched)}",
        f"Unique TL codes: {len(seen)} / {len(rda_entries)}",
        "",
        "=== Missing ===",
        *report["missing_rda_codes"],
        "",
        "=== Unmatched AL6 ===",
        *[f"- {u['osm_name']} (score={u['score']})" for u in unmatched],
    ]
    (out_dir / "report_tl.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] Report: {out_dir / 'report_tl.txt'}")
    print(f"[info] Coverage: {len(seen)}/{len(rda_entries)} = {100 * len(seen) / max(1, len(rda_entries)):.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
