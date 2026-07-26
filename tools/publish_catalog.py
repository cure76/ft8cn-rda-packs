#!/usr/bin/env python3
"""Rebuild catalog.json for RDA packs directory (sha256, sizes, feature counts)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_META = {
    "mo_moscow": {
        "name": "Moscow + Moscow Oblast",
        "codes_prefix": ["MA", "MO"],
        "builtin_in_apk": True,
    },
    "sm_smolensk": {
        "name": "Smolensk Oblast",
        "codes_prefix": ["SM"],
        "builtin_in_apk": False,
    },
    "kg_kaluga": {
        "name": "Kaluga Oblast",
        "codes_prefix": ["KG"],
        "builtin_in_apk": False,
    },
    "tl_tula": {
        "name": "Tula Oblast",
        "codes_prefix": ["TL"],
        "builtin_in_apk": False,
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--packs-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Root of packs repo (contains packs/). Default: repository root.",
    )
    ap.add_argument(
        "--base-url",
        default="https://cdn.jsdelivr.net/gh/cure76/ft8cn-rda-packs@main/",
        help="HTTPS base URL ending with /",
    )
    ap.add_argument("--max-downloaded", type=int, default=3)
    args = ap.parse_args()

    packs_root = args.packs_dir / "packs"
    if not packs_root.is_dir():
        raise SystemExit(f"missing {packs_root}")

    packs = []
    for path in sorted(packs_root.glob("*.geojson")):
        pid = path.stem
        data = path.read_bytes()
        geo = json.loads(data)
        meta = DEFAULT_META.get(pid, {})
        packs.append(
            {
                "id": pid,
                "name": meta.get("name", pid),
                "file": f"packs/{path.name}",
                "codes_prefix": meta.get("codes_prefix", []),
                "feature_count": len(geo.get("features", [])),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "pack_version": 1,
                "builtin_in_apk": bool(meta.get("builtin_in_apk", False)),
            }
        )

    catalog = {
        "version": 1,
        "base_url": args.base_url if args.base_url.endswith("/") else args.base_url + "/",
        "max_downloaded_packs": args.max_downloaded,
        "packs": packs,
    }
    out = args.packs_dir / "catalog.json"
    out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(packs)} packs)")


if __name__ == "__main__":
    main()
