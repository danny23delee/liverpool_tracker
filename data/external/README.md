# External model hook

Save your model's pre-match probabilities as `data/external/model_probs.csv` (this exact name) and rebuild
(`python build.py`). The Market Lens page then scores it with the same Brier score and log loss as the market
and the xG simulation.

Columns: `match_id` (the Understat match id) **or** `date` (yyyy-mm-dd, local match date) + `home_team` + `away_team`
(Understat club names), plus `p_home`, `p_draw`, `p_away`. Every probability must be in [0, 1] and each row must sum
to 1 (within 0.001), otherwise the build stops. Matches without a row are simply not scored.

`model_probs.example.csv` shows the format (it is not loaded).
