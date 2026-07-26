# Vladimir Oblast (`vl_vladimir`) notes

- OSM oblast relation: `72197` (Overpass area `3600072197`).
- Skip `городской округ Владимир` (relation `389677`) — RDA uses VL-01..03 city districts.
- Geometry: Overpass ids + OSM API `relation/{id}/full.json` (same as Tula).
- **Gus-Khrustalny collision**: city (`VL-06`) and rayon (`VL-16`) normalize to the same stem; forced by OSM display name in `prepare_vl_vladimir.py`.
- **Pokrov** urban okrug → `VL-23` (included in Petushinsky RDA).
- Missing: VL-01..03 (Vladimir city districts), VL-22 (Muromsky — no AL6 polygon; city Murom is VL-09).
