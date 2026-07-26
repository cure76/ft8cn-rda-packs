# FT8CN RDA packs

Offline GeoJSON packs for Russian District Award (RDA) lookup in FT8CN-RN3AOE.

## Catalog

- [`catalog.json`](catalog.json) — pack list with size, SHA-256, and download paths
- [`packs/`](packs/) — GeoJSON files (`rda_code` property per feature)

## App usage

FT8CN downloads selected packs over HTTPS, verifies SHA-256, and stores them under app `filesDir/rda/`.
Built-in APK pack: Moscow + Moscow Oblast (`mo_moscow`). Max **3** additional downloaded packs.

Default catalog URL:

`https://raw.githubusercontent.com/cure76/ft8cn-rda-packs/main/catalog.json`

## Publishing

From the FT8CN repo:

```bash
python tools/rda/publish_catalog.py --packs-dir /path/to/ft8cn-rda-packs
```
