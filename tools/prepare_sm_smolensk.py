#!/usr/bin/env python3
"""
Prepare offline RDA GeoJSON pack for Smolensk Oblast (SM-*).

Uses the same matching helpers as prepare_rda_pack.py.

OSM:
  - admin_level=6 municipal / city districts in Smolensk Oblast
  - admin_level=9 city rayons of Smolensk (SM-01..03)
  - Skip "городской округ Смоленск" (city as whole) — RDA uses three city districts

Usage:
  cd tools
  python prepare_sm_smolensk.py
  # then: cp out/sm_smolensk.geojson ../packs/ && python publish_catalog.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from prepare_rda_pack import (
    HERE,
    attach_rda,
    http_get,
    load_aliases,
    load_or_fetch_osm,
    osm_json_to_geojson,
    overpass_query,
    parse_rda_list,
    RDA_LIST_URL,
)

AREA_SMOLENSK_OBLAST = 3600081996  # relation 81996
PACK_ID = "sm_smolensk"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Smolensk Oblast RDA GeoJSON pack")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out")
    parser.add_argument("--rda-list", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=HERE / "aliases_sm_smolensk.json")
    parser.add_argument("--simplify", type=float, default=0.0003)
    parser.add_argument("--cache-dir", type=Path, default=HERE / "cache")
    parser.add_argument(
        "--merge-index",
        type=Path,
        default=None,
        help="Also write pack + merge into index.json at this directory (e.g. app assets/rda)",
    )
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if args.rda_list and args.rda_list.exists():
        rda_text = args.rda_list.read_text(encoding="utf-8", errors="replace")
        print(f"[info] RDA list from {args.rda_list}")
    else:
        cache_list = args.cache_dir / "rda_eng.txt"
        if cache_list.exists():
            rda_text = cache_list.read_text(encoding="utf-8", errors="replace")
            print(f"[info] RDA list from cache {cache_list.name}")
        else:
            print(f"[info] Downloading RDA list: {RDA_LIST_URL}")
            rda_text = http_get(RDA_LIST_URL).decode("utf-8", errors="replace")
            cache_list.write_text(rda_text, encoding="utf-8")

    rda_entries = parse_rda_list(rda_text, prefixes=("SM",))
    print(f"[info] Active SM codes: {len(rda_entries)}")
    (out_dir / "rda_sm_codes.json").write_text(
        json.dumps({k: v.name for k, v in sorted(rda_entries.items())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    aliases = load_aliases(args.aliases)
    print(f"[info] Aliases loaded: {len(aliases)} from {args.aliases}")

    # AL6 municipal districts (+ Desnogorsk); skip whole-city Smolensk urban okrug
    cache_al6 = args.cache_dir / "osm_sm_al6.json"
    osm_al6 = load_or_fetch_osm(
        cache_al6,
        lambda: overpass_query(
            f"""
[out:json][timeout:600];
area({AREA_SMOLENSK_OBLAST})->.a;
relation["boundary"="administrative"]["admin_level"="6"](area.a);
out geom;
"""
        ),
    )
    gj_al6 = osm_json_to_geojson(osm_al6)
    # Drop city-as-whole feature (RDA uses SM-01..03 rayons instead)
    kept = []
    for feat in gj_al6.get("features", []):
        props = feat.get("properties") or {}
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else props
        name = ""
        if isinstance(tags, dict):
            name = (tags.get("name") or "") + " " + (tags.get("name:en") or "")
        low = name.casefold()
        if "смоленск" in low and "городскои" in low.replace("й", "и").replace("ё", "е"):
            # городской округ Смоленск
            if "муниципал" not in low.replace("й", "и"):
                print(f"[info] Skipping city urban okrug: {tags.get('name') if isinstance(tags, dict) else name}")
                continue
        if isinstance(tags, dict) and (tags.get("name") or "") == "городской округ Смоленск":
            print("[info] Skipping городской округ Смоленск")
            continue
        kept.append(feat)
    # More reliable filter by exact name
    kept2 = []
    for feat in kept:
        props = feat.get("properties") or {}
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else props
        n = (tags.get("name") if isinstance(tags, dict) else None) or ""
        if n == "городской округ Смоленск":
            print("[info] Skipping городской округ Смоленск")
            continue
        kept2.append(feat)
    gj_al6 = {"type": "FeatureCollection", "features": kept2}

    matched_al6, unmatched_al6, log_al6 = attach_rda(
        gj_al6, rda_entries, aliases, args.simplify, prefix_filter="SM-"
    )
    print(f"[info] SM AL6: matched={len(matched_al6)} unmatched={len(unmatched_al6)}")

    # City rayons AL9
    cache_al9 = args.cache_dir / "osm_sm_city_al9.json"
    osm_al9 = load_or_fetch_osm(
        cache_al9,
        lambda: overpass_query(
            f"""
[out:json][timeout:300];
area({AREA_SMOLENSK_OBLAST})->.a;
relation["boundary"="administrative"]["admin_level"="9"](area.a);
out geom;
"""
        ),
    )
    gj_al9 = osm_json_to_geojson(osm_al9)
    matched_al9, unmatched_al9, log_al9 = attach_rda(
        gj_al9, rda_entries, aliases, args.simplify, prefix_filter="SM-"
    )
    print(f"[info] SM city AL9: matched={len(matched_al9)} unmatched={len(unmatched_al9)}")

    # Smaller city rayons first
    all_features: List[dict] = matched_al9 + matched_al6
    seen = set()
    deduped = []
    for feat in all_features:
        code = feat["properties"]["rda_code"]
        if code in seen:
            print(f"[warn] Duplicate skipped: {code} ({feat['properties'].get('osm_name')})")
            continue
        seen.add(code)
        deduped.append(feat)
    all_features = deduped

    pack = {"type": "FeatureCollection", "name": PACK_ID, "features": all_features}
    geojson_path = out_dir / f"{PACK_ID}.geojson"
    geojson_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] Wrote {geojson_path} ({len(all_features)} features)")

    report = {
        "matched_count": len(all_features),
        "matched_codes": sorted(seen),
        "missing_rda_codes": sorted(set(rda_entries) - seen),
        "al6_log": log_al6,
        "al9_log": log_al9,
        "unmatched_al6": unmatched_al6,
        "unmatched_al9": unmatched_al9,
    }
    (out_dir / "report_sm.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"Matched features: {len(all_features)}",
        f"Unique SM codes: {len(seen)} / {len(rda_entries)}",
        "",
        "=== Missing ===",
        *report["missing_rda_codes"],
        "",
        "=== Unmatched AL6 ===",
        *[f"- {u['osm_name']} (score={u['score']})" for u in unmatched_al6],
        "",
        "=== Unmatched AL9 ===",
        *[f"- {u['osm_name']} (score={u['score']})" for u in unmatched_al9],
    ]
    (out_dir / "report_sm.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] Report: {out_dir / 'report_sm.txt'}")
    print(f"[info] Coverage: {len(seen)}/{len(rda_entries)} = {100 * len(seen) / max(1, len(rda_entries)):.1f}%")

    # Update / merge index.json
    def write_index(target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        index_path = target_dir / "index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        else:
            index = {"version": 1, "packs": []}
        packs = index.setdefault("packs", [])
        # upsert
        found = False
        for p in packs:
            if p.get("id") == PACK_ID:
                p["file"] = f"{PACK_ID}.geojson"
                p["enabled"] = True
                found = True
                break
        if not found:
            packs.append({"id": PACK_ID, "file": f"{PACK_ID}.geojson", "enabled": True})
        # ensure mo_moscow stays if present in out
        if any(p.get("id") == "mo_moscow" for p in packs) is False and (target_dir / "mo_moscow.geojson").exists():
            packs.insert(0, {"id": "mo_moscow", "file": "mo_moscow.geojson", "enabled": True})
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] Index: {index_path}")

    write_index(out_dir)

    if args.merge_index:
        # copy geojson + merge index
        dest = args.merge_index
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{PACK_ID}.geojson").write_text(geojson_path.read_text(encoding="utf-8"), encoding="utf-8")
        write_index(dest)
        print(f"[ok] Copied pack to {dest}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
