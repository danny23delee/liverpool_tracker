## External model hook

Save your model's pre-match probabilities as `data/external/model_probs.csv` (this exact name) and rebuild
(`python build.py`). The Market Lens page then scores it with the same Brier score and log loss as the market
and the xG simulation.

Columns: `match_id` (the Understat match id) **or** `date` (yyyy-mm-dd, local match date) + `home_team` + `away_team`
(Understat club names), plus `p_home`, `p_draw`, `p_away`. Every probability must be in [0, 1] and each row must sum
to 1 (within 0.001), otherwise the build stops. Matches without a row are simply not scored.

`model_probs.example.csv` shows the format (it is not loaded).

## External style KPIs (optional)

Save `data/external/style_kpis.csv` to replace or add style-of-play KPI values, for example real pass-level data from a
paid provider. If the file is absent nothing happens.

```csv
season,club,kpi_id,value
2024-25,Liverpool,def_ppda,8.9
2024-25,Arsenal,def_ppda,10.4
```

- `season` like `2024-25`; `club` is the Understat club name.
- `kpi_id` is any KPI id in `config/style.json`, including the external-only ones (`att_header_share`, `att_cross_share`, ...).
  An unavailable axis becomes scorable for a season once at least two of its KPIs have values for 10 or more clubs.
- Rows replace the proxy value for that season and club. An axis that uses any external value shows "External data" instead of "Proxy".
- Malformed files (wrong columns, unknown KPI ids, duplicates, non-finite values, clubs not in the data) stop the build.
