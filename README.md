# FT8CN RDA packs

Offline GeoJSON packs for Russian District Award (RDA) lookup in FT8CN-RN3AOE.

## Catalog

- [`catalog.json`](catalog.json) — pack list with size, SHA-256, download paths
- [`packs/`](packs/) — GeoJSON (`rda_code` per feature)

Built-in APK pack: Moscow + Moscow Oblast (`mo_moscow`). App allows max **3** additional downloads.

Default catalog URL:

`https://cdn.jsdelivr.net/gh/cure76/ft8cn-rda-packs@main/catalog.json`

## Publish

From the FT8CN repo:

```bash
python tools/rda/publish_catalog.py --packs-dir ../ft8cn-rda-packs \
  --base-url 'https://cdn.jsdelivr.net/gh/cure76/ft8cn-rda-packs@main/'
```
