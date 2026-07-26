#!/usr/bin/env python3
"""
Prepare offline RDA GeoJSON pack for FT8CN-RN3AOE (pilot: Moscow + Moscow Oblast).

Pipeline:
  1) Download official RDA list (rdaward.org) and keep active MA-* / MO-* codes
  2) Download OSM administrative polygons via Overpass
     - Moscow city: admin_level=5 (administrative okrugs → MA-*)
     - Moscow Oblast: admin_level=6 (municipal / city districts → MO-*)
  3) Match OSM names to RDA codes (aliases + fuzzy match)
  4) Write GeoJSON with rda_code, index.json, and match report

Usage:
  cd tools
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  python prepare_rda_pack.py
  # then: cp out/mo_moscow.geojson ../packs/ && python publish_catalog.py

Notes:
  - Matching is never perfect; review report.txt and extend aliases_mo_moscow.json
  - Deleted RDA codes are skipped
  - Network required for first run (RDA list + Overpass); responses are cached
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import osm2geojson
except ImportError:  # pragma: no cover
    osm2geojson = None

try:
    from shapely.geometry import mapping, shape
    from shapely.validation import make_valid
except ImportError:  # pragma: no cover
    shape = None
    mapping = None
    make_valid = None


RDA_LIST_URL = "https://rdaward.org/rda_eng.txt"
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

# OSM area ids = relation_id + 3600000000
AREA_MOSCOW = 3600102269
AREA_MOSCOW_OBLAST = 3600051490

HERE = Path(__file__).resolve().parent


@dataclass
class RdaEntry:
    code: str
    name: str


def http_get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FT8CN-RN3AOE-rda-prep/1.0 (amateur radio; local tool)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_post(url: str, data: bytes, timeout: int = 600) -> bytes:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "FT8CN-RN3AOE-rda-prep/1.0 (amateur radio; local tool)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ы": "y",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def normalize(text: str) -> str:
    """Normalize names for matching (RU/EN RDA and OSM)."""
    if not text:
        return ""
    s = text.casefold()
    repl = {
        "ё": "е",
        "й": "и",
        "ъ": "",
        "ь": "",
        "–": " ",
        "—": " ",
        "/": " ",
        ",": " ",
        ".": " ",
        "(": " ",
        ")": " ",
        "-": " ",
        "_": " ",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    stop = {
        "city",
        "district",
        "area",
        "okrug",
        "urban",
        "administrative",
        "municipal",
        "of",
        "the",
        "incl",
        "including",
        "and",
        "zato",
        "город",
        "городской",
        "городскои",  # after й→и
        "округ",
        "район",
        "раион",  # after й→и
        "муниципальный",
        "муниципалныи",  # after й→и
        "административный",
        "административныи",
        "ао",
        "москва",
        "moscow",
        "star",  # "Star City" alone is too generic
    }
    tokens = [t for t in re.split(r"\s+", s) if t and t not in stop]
    return " ".join(tokens)


def cyr_to_latin(text: str) -> str:
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text)


def spelling_variants(latin: str) -> List[str]:
    """Common RU↔EN RDA spelling variants on already-latin text."""
    variants = {latin}
    variants.add(latin.replace("ay", "ai"))
    variants.add(latin.replace("ai", "ay"))
    # yegoryevsk ↔ egoryevsk (only ye→e; reverse is too noisy)
    variants.add(re.sub(r"\bye", "e", latin))
    variants.add(latin.replace("j", "y"))
    variants.add(latin.replace("ii", "y"))
    variants.add(latin.replace("yi", "y"))
    variants.add(latin.replace("tsky", "tsy"))
    # shchyolkovsky / shchelkovo
    variants.add(latin.replace("shchyo", "shche"))
    variants.add(latin.replace("shchy", "shch"))
    return [v for v in variants if v]


def match_keys(text: str) -> List[str]:
    """All normalized forms used when matching one OSM/RDA name."""
    n = normalize(text)
    if not n:
        return []
    keys = {n}
    latin = cyr_to_latin(n)
    keys.update(spelling_variants(latin))
    return list(keys)


def _stem_close(a: str, b: str) -> bool:
    """volokolamsky ≈ volokolamsk, zaraysky ≈ zaraysk."""
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 6:
        return False
    return longer.startswith(shorter)


def parse_rda_list(text: str, prefixes: Iterable[str]) -> Dict[str, RdaEntry]:
    """Parse rdaward.org list; skip deleted entries."""
    out: Dict[str, RdaEntry] = {}
    prefix_re = re.compile(r"^(" + "|".join(re.escape(p) for p in prefixes) + r")-\d+")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) < 2:
            m = re.match(r"^([A-Z]{2}-\d+)\s+(.+)$", line)
            if not m:
                continue
            code, name = m.group(1), m.group(2).strip()
        else:
            code, name = parts[0].strip(), parts[1].strip()

        if not prefix_re.match(code):
            continue
        if "deleted" in name.casefold():
            continue
        name = re.sub(r"\s+", " ", name).strip()
        out[code] = RdaEntry(code=code, name=name)
    return out


def overpass_query(ql: str) -> dict:
    last_err: Optional[Exception] = None
    body = ("data=" + urllib.parse.quote(ql)).encode("utf-8")
    for url in OVERPASS_URLS:
        try:
            print(f"[info] Overpass query → {url}")
            raw = http_post(url, body)
            return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            print(f"[warn] Overpass failed at {url}: {e}", file=sys.stderr)
            time.sleep(2)
    raise RuntimeError(f"All Overpass endpoints failed: {last_err}")


def osm_json_to_geojson(osm_json: dict) -> dict:
    if osm2geojson is None:
        raise RuntimeError("osm2geojson is required. pip install -r requirements.txt")
    return osm2geojson.json2geojson(osm_json)


def feature_names(props: dict) -> List[str]:
    names = []
    for key in ("name", "name:en", "name:ru", "official_name", "alt_name"):
        v = props.get(key)
        if isinstance(v, str) and v.strip():
            names.append(v.strip())
    return names


def load_aliases(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        for mk in match_keys(k):
            out[mk] = v
        # keep raw normalized key too
        out[normalize(k)] = v
    return out


def best_rda_match(
    names: List[str],
    rda_by_code: Dict[str, RdaEntry],
    aliases: Dict[str, str],
    min_ratio: float = 0.72,
) -> Tuple[Optional[str], float, str]:
    """Return (code, score, reason). Picks the best candidate across all codes."""
    osm_keys: List[str] = []
    for name in names:
        for k in match_keys(name):
            if k not in osm_keys:
                osm_keys.append(k)

    for n in osm_keys:
        if n in aliases:
            return aliases[n], 1.0, f"alias:{n}"
        first = n.split()[0] if n else ""
        if len(first) >= 4 and first in aliases:
            return aliases[first], 1.0, f"alias:{first}"

    rda_keys: Dict[str, List[str]] = {
        code: match_keys(e.name) for code, e in rda_by_code.items()
    }

    best_code: Optional[str] = None
    best_score = 0.0
    best_reason = ""

    def consider(code: str, score: float, reason: str) -> None:
        nonlocal best_code, best_score, best_reason
        if score > best_score:
            best_code = code
            best_score = score
            best_reason = reason

    for n in osm_keys:
        n_toks = [t for t in n.split() if len(t) >= 4]
        for code, rks in rda_keys.items():
            for rn in rks:
                if not n or not rn:
                    continue
                if n == rn:
                    consider(code, 1.0, "exact")
                    continue
                if (n in rn or rn in n) and min(len(n), len(rn)) >= 5:
                    consider(code, 0.92, "contains")
                    continue
                if _stem_close(n, rn) or any(
                    _stem_close(a, b) for a in n.split() for b in rn.split()
                ):
                    consider(code, 0.9, f"stem:{n}~{rn}")
                    continue
                r_toks = [t for t in rn.split() if len(t) >= 4]
                # Require first OSM token in RDA to avoid posadsky collisions
                if n_toks and r_toks and n_toks[0] in r_toks:
                    overlap = len(set(n_toks) & set(r_toks))
                    consider(code, 0.85 + 0.03 * overlap, f"token:{n}~{rn}")
                    continue
                score = SequenceMatcher(None, n, rn).ratio()
                consider(code, score, f"fuzzy:{n}~{rn}")

    if best_code and best_score >= min_ratio:
        return best_code, best_score, best_reason
    return None, best_score, best_reason or "no-match"


# Extra admin/place polygons not covered by bulk AL5/AL6 area queries
# (Zelenograd AO sits awkwardly vs Moscow area filter; Dzerzhinsky is a town
# inside Lyubertsy urban okrug in current OSM admin tree).
EXTRA_RELATIONS = [
    {"cache": "osm_zelenograd.json", "osm_ids": [1320358], "force_code": "MA-03"},
    {"cache": "osm_dzerzhinsky.json", "osm_ids": [184003], "force_code": "MO-06"},
]


def fetch_relations_geom(osm_ids: List[int]) -> dict:
    """Fetch relation geometry; prefer OSM API 0.6 (more reliable than Overpass)."""
    # OSM API supports one relation/full at a time; merge elements.
    merged: dict = {"elements": []}
    seen = set()
    for oid in osm_ids:
        url = f"https://api.openstreetmap.org/api/0.6/relation/{oid}/full.json"
        try:
            print(f"[info] OSM API fetch relation/{oid}/full.json")
            raw = http_get(url, timeout=180)
            data = json.loads(raw.decode("utf-8"))
            for el in data.get("elements", []):
                key = (el.get("type"), el.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                merged["elements"].append(el)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[warn] OSM API failed for {oid}: {e}", file=sys.stderr)

    if merged["elements"]:
        return merged

    ids = "".join(f"relation({i});" for i in osm_ids)
    return overpass_query(
        f"""
[out:json][timeout:180];
(
  {ids}
);
out geom;
"""
    )


def merge_forced_features(
    geojson: dict,
    rda_by_code: Dict[str, RdaEntry],
    force_code: str,
    simplify_tol: float,
) -> List[dict]:
    """Attach a fixed RDA code to every feature in geojson."""
    out: List[dict] = []
    entry = rda_by_code.get(force_code)
    if not entry:
        return out
    for feat in geojson.get("features", []):
        props = dict(feat.get("properties") or {})
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else props
        names = feature_names(tags if isinstance(tags, dict) else props)
        display = names[0] if names else force_code
        geom = feat.get("geometry")
        if not geom:
            continue
        geom = simplify_geometry(geom, simplify_tol)
        out.append(
            {
                "type": "Feature",
                "properties": {
                    "rda_code": force_code,
                    "rda_name": entry.name,
                    "osm_name": display,
                    "match_score": 1.0,
                    "match_reason": "forced-extra",
                },
                "geometry": geom,
            }
        )
    return out


def load_or_fetch_osm(cache_path: Path, fetcher) -> dict:
    if cache_path.exists():
        print(f"[info] Using cached {cache_path.name}")
        return json.loads(cache_path.read_text(encoding="utf-8"))
    data = fetcher()
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def simplify_geometry(geom: dict, tolerance: float) -> dict:
    if shape is None or tolerance <= 0:
        return geom
    try:
        g = shape(geom)
        if make_valid is not None:
            g = make_valid(g)
        g = g.simplify(tolerance, preserve_topology=True)
        if g.is_empty:
            return geom
        return mapping(g)
    except Exception:
        return geom


def attach_rda(
    geojson: dict,
    rda_by_code: Dict[str, RdaEntry],
    aliases: Dict[str, str],
    simplify_tol: float,
    prefix_filter: Optional[str] = None,
) -> Tuple[List[dict], List[dict], List[dict]]:
    matched: List[dict] = []
    unmatched: List[dict] = []
    log: List[dict] = []

    candidates = rda_by_code
    if prefix_filter:
        candidates = {k: v for k, v in rda_by_code.items() if k.startswith(prefix_filter)}

    for feat in geojson.get("features", []):
        props = dict(feat.get("properties") or {})
        # osm2geojson may nest tags
        tags = props.get("tags") if isinstance(props.get("tags"), dict) else props
        names = feature_names(tags if isinstance(tags, dict) else props)
        code, score, reason = best_rda_match(names, candidates, aliases)
        display = names[0] if names else "(no name)"
        entry = {
            "osm_name": display,
            "all_names": names,
            "rda_code": code,
            "score": round(score, 3),
            "reason": reason,
        }
        log.append(entry)

        geom = feat.get("geometry")
        if not geom:
            unmatched.append(entry)
            continue
        geom = simplify_geometry(geom, simplify_tol)

        if code:
            matched.append(
                {
                    "type": "Feature",
                    "properties": {
                        "rda_code": code,
                        "rda_name": rda_by_code[code].name,
                        "osm_name": display,
                        "match_score": round(score, 3),
                        "match_reason": reason,
                    },
                    "geometry": geom,
                }
            )
        else:
            unmatched.append(entry)

    return matched, unmatched, log


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Moscow+MO RDA GeoJSON pack")
    parser.add_argument("--out-dir", type=Path, default=HERE / "out")
    parser.add_argument("--rda-list", type=Path, default=None)
    parser.add_argument("--aliases", type=Path, default=HERE / "aliases_mo_moscow.json")
    parser.add_argument("--simplify", type=float, default=0.0003)
    parser.add_argument("--skip-osm", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=HERE / "cache")
    args = parser.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if args.rda_list and args.rda_list.exists():
        rda_text = args.rda_list.read_text(encoding="utf-8", errors="replace")
        print(f"[info] RDA list from {args.rda_list}")
    else:
        print(f"[info] Downloading RDA list: {RDA_LIST_URL}")
        rda_text = http_get(RDA_LIST_URL).decode("utf-8", errors="replace")
        (args.cache_dir / "rda_eng.txt").write_text(rda_text, encoding="utf-8")

    rda_entries = parse_rda_list(rda_text, prefixes=("MA", "MO"))
    print(
        f"[info] Active RDA codes: "
        f"MA={sum(1 for k in rda_entries if k.startswith('MA-'))}, "
        f"MO={sum(1 for k in rda_entries if k.startswith('MO-'))}"
    )
    (out_dir / "rda_ma_mo_codes.json").write_text(
        json.dumps({k: v.name for k, v in sorted(rda_entries.items())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    aliases = load_aliases(args.aliases)
    print(f"[info] Aliases loaded: {len(aliases)} from {args.aliases}")

    if args.skip_osm:
        print("[info] --skip-osm set; done after parsing codes")
        return 0

    if osm2geojson is None:
        print("[error] Install deps: pip install -r requirements.txt", file=sys.stderr)
        return 1

    # Moscow MA
    cache_ma = args.cache_dir / "osm_moscow_al5.json"
    if cache_ma.exists():
        print(f"[info] Using cached {cache_ma.name}")
        osm_ma = json.loads(cache_ma.read_text(encoding="utf-8"))
    else:
        print("[info] Fetching Moscow admin_level=5 (okrugs)…")
        osm_ma = overpass_query(
            f"""
[out:json][timeout:180];
area({AREA_MOSCOW})->.a;
(
  relation["boundary"="administrative"]["admin_level"="5"](area.a);
);
out body;
>;
out skel qt;
"""
        )
        cache_ma.write_text(json.dumps(osm_ma), encoding="utf-8")

    gj_ma = osm_json_to_geojson(osm_ma)
    matched_ma, unmatched_ma, log_ma = attach_rda(
        gj_ma, rda_entries, aliases, args.simplify, prefix_filter="MA-"
    )
    print(f"[info] Moscow: matched={len(matched_ma)} unmatched={len(unmatched_ma)}")

    # Moscow Oblast MO
    cache_mo = args.cache_dir / "osm_mo_al6.json"
    if cache_mo.exists():
        print(f"[info] Using cached {cache_mo.name}")
        osm_mo = json.loads(cache_mo.read_text(encoding="utf-8"))
    else:
        print("[info] Fetching Moscow Oblast admin_level=6 …")
        # out geom is often more reliable than body+skel for large oblasts
        osm_mo = overpass_query(
            f"""
[out:json][timeout:600];
area({AREA_MOSCOW_OBLAST})->.a;
relation["boundary"="administrative"]["admin_level"="6"](area.a);
out geom;
"""
        )
        cache_mo.write_text(json.dumps(osm_mo), encoding="utf-8")

    gj_mo = osm_json_to_geojson(osm_mo)
    matched_mo, unmatched_mo, log_mo = attach_rda(
        gj_mo, rda_entries, aliases, args.simplify, prefix_filter="MO-"
    )
    print(f"[info] Moscow Oblast: matched={len(matched_mo)} unmatched={len(unmatched_mo)}")

    # Extra polygons missing from bulk AL5/AL6 dumps
    matched_extra: List[dict] = []
    for extra in EXTRA_RELATIONS:
        cache_extra = args.cache_dir / extra["cache"]
        osm_ids = list(extra["osm_ids"])
        force_code = str(extra["force_code"])
        osm_extra = load_or_fetch_osm(
            cache_extra, lambda ids=osm_ids: fetch_relations_geom(ids)
        )
        gj_extra = osm_json_to_geojson(osm_extra)
        feats = merge_forced_features(gj_extra, rda_entries, force_code, args.simplify)
        print(f"[info] Extra {force_code}: features={len(feats)} from {extra['cache']}")
        matched_extra.extend(feats)

    all_features = matched_extra + matched_ma + matched_mo
    # Prefer smaller extras first (e.g. Dzerzhinsky before Lyubertsy).
    # Also sort remaining by rough bbox area ascending inside RdaLookup.

    pack_id = "mo_moscow"
    pack = {"type": "FeatureCollection", "name": pack_id, "features": all_features}

    geojson_path = out_dir / f"{pack_id}.geojson"
    geojson_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] Wrote {geojson_path} ({len(all_features)} features)")

    index = {
        "version": 1,
        "packs": [{"id": pack_id, "file": f"{pack_id}.geojson", "enabled": True}],
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "matched_count": len(all_features),
        "matched_codes": sorted({f["properties"]["rda_code"] for f in all_features}),
        "missing_rda_codes": sorted(set(rda_entries) - {f["properties"]["rda_code"] for f in all_features}),
        "moscow_log": log_ma,
        "oblast_log": log_mo,
        "unmatched_moscow": unmatched_ma,
        "unmatched_oblast": unmatched_mo,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"Matched features: {len(all_features)}",
        f"Unique RDA codes in pack: {len(report['matched_codes'])} / {len(rda_entries)}",
        "",
        "=== Missing RDA codes (no OSM polygon matched) ===",
        *report["missing_rda_codes"],
        "",
        "=== Unmatched OSM features (Moscow) ===",
        *[f"- {u['osm_name']} (score={u['score']})" for u in unmatched_ma],
        "",
        "=== Unmatched OSM features (Oblast) ===",
        *[f"- {u['osm_name']} (score={u['score']})" for u in unmatched_mo],
        "",
        "Next: extend aliases_mo_moscow.json for unmatched names, re-run.",
        f"Then: cp {geojson_path.name} ../packs/ && python publish_catalog.py\n  (For APK builtin: also copy into FT8CN ft8cn/app/src/main/assets/rda/)",
    ]
    (out_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] Report: {out_dir / 'report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
