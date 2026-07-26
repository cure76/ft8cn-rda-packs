# FT8CN RDA packs

Offline GeoJSON packs for Russian District Award (RDA) lookup in **FT8CN-RN3AOE**.

## For the app

- [`catalog.json`](catalog.json) — pack list (size, SHA-256, paths)
- [`packs/`](packs/) — GeoJSON features with `rda_code`

Catalog URL used by the app:

`https://cdn.jsdelivr.net/gh/cure76/ft8cn-rda-packs@main/catalog.json`

Built-in in APK: Moscow + Moscow Oblast (`mo_moscow`). Max **3** additional downloads.

## For pack authors

Preparation scripts live in [`tools/`](tools/):

```bash
cd tools
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python prepare_sm_smolensk.py
cp out/sm_smolensk.geojson ../packs/
python publish_catalog.py
```

See [`tools/README.md`](tools/README.md).
