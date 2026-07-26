# RDA pack preparation tools

Build GeoJSON region packs for FT8CN-RN3AOE and publish `catalog.json`.

## Setup

```bash
cd tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Packs

| Pack | Codes | Script |
|------|-------|--------|
| `mo_moscow` | MA-*, MO-* | `prepare_rda_pack.py` |
| `sm_smolensk` | SM-* | `prepare_sm_smolensk.py` |
| `kg_kaluga` | KG-* | `prepare_kg_kaluga.py` |
| `tl_tula` | TL-* | `prepare_tl_tula.py` |
| `vl_vladimir` | VL-* | `prepare_vl_vladimir.py` |

## Workflow

```bash
# 1) Build (writes tools/out/*.geojson + report)
python prepare_rda_pack.py
python prepare_sm_smolensk.py
python prepare_kg_kaluga.py
python prepare_tl_tula.py
python prepare_vl_vladimir.py

# 2) Install into repo packs/ and refresh catalog
cp out/mo_moscow.geojson out/sm_smolensk.geojson ../packs/
python publish_catalog.py

# 3) Commit & push this repository
```

`publish_catalog.py` defaults `--packs-dir` to the repository root.

Builtin APK pack (`mo_moscow`) must also be copied into FT8CN  
`ft8cn/app/src/main/assets/rda/` when you intentionally bump the embedded pack.

Aliases: `aliases_mo_moscow.json`, `aliases_sm_smolensk.json`.  
Cache/out/venv are gitignored.
