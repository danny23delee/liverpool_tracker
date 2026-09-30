# External style KPIs (optional)

Drop a file called `style_kpis.csv` in this folder to replace or add style-of-play KPI values, for example real
pass-level data from a paid provider. If the file is absent nothing happens.

```csv
season,club,kpi_id,value
2024-25,Liverpool,def_ppda,8.9
2024-25,Arsenal,def_ppda,10.4
```

- `season` like `2024-25`; `club` is the Understat club name (as in `data/processed/style_raw.parquet`).
- `kpi_id` is any KPI id in `config/style.json`, including the external-only ones (`att_header_share`, `att_cross_share`, ...).
- Rows replace the proxy value for that season and club. An axis that uses any external value shows "External data" instead of "Proxy".
- Malformed files (wrong columns, unknown KPI ids, duplicates, non-finite values, clubs not in the data) fail the build loudly.
