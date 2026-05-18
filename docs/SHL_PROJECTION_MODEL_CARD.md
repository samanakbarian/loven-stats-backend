# SHL Projection Model Card

## Scope
This model generates a preseason SHL table projection for decision support.  
It is not a match-by-match simulator and should be treated as a heuristic planning model.

## Data Inputs
- `raw_sports.swehockey_seasons`
- `raw_sports.swehockey_standings`
- Analytics-derived roster readiness modules:
  - `shl_transition`
  - `age_curve`
  - `special_teams`
- Silly-season baseline counts:
  - `confirmed_signings`
  - `confirmed_departures`
  - `expiring_contracts`

## Core Logic
### 1) SHL baseline per team
For latest SHL regular season rows with:
- `games_played >= 40`
- `points > 0`

Compute:
- `ppg = points / games_played`
- `ppg_seed = ppg * 52`
- `rank_seed = max(42, 100 - (rank - 1) * 4)`
- `base_points = round(0.75 * ppg_seed + 0.25 * rank_seed)`

### 2) Upcoming-league team set
- Remove relegated teams via token filter (`modo`, `leksand`, `leksands`)
- Add promoted team (`IF Björklöven`) with seed if missing

### 3) Björklöven custom projection
Start with `58.0` points and adjust:
- Skater readiness: `(avg_sk_adj - 0.38) * 80.0`
- Goalie readiness: `(avg_g_adj - 89.5) * 2.4`
- Special teams: `(special_teams_index - 95.0) * 0.35`
- Silly dynamics:
  - `+ signings_count * 1.8`
  - `- departures_count * 0.5`
  - `- expiring_count * 0.9`

Clamp:
- `max(46.0, min(96.0, bjk_points_model))`

### 4) Ranking and uncertainty bands
Sort by projected points descending and assign rank.

Bands:
- Points: `p10 = pts - 12`, `p90 = pts + 12` (bounded)
- Rank spread:
  - top 4: `±2`
  - top 10: `±3`
  - lower: `±4`

## Outputs
- `projected_rank`
- `projected_points`
- `projected_rank_p10/p50/p90`
- `projected_points_p10/p50/p90`
- `tier`
- `top6_chance_pct`
- `playout_risk_pct`

## Known Weaknesses
- Not a full Monte Carlo simulator.
- Fixed coefficients; no automated seasonal re-calibration.
- Sensitive to upstream data quality errors.
- Does not include explicit schedule-strength pathing.
- Limited injury/availability treatment.

## Why no full Monte Carlo (current architecture)
- Requires larger parameter surface:
  - Team attack/defense distributions
  - Home/away effects
  - Schedule path simulation
  - Correlated uncertainty per roster state
- Requires robust calibration and backtesting loops (historical seasons).
- Current API latency and simplicity goals favored deterministic heuristics.

## Upgrade Path to Monte Carlo v2
1. Build calibrated team-strength priors from multiple seasons.
2. Simulate full schedule 10k+ runs.
3. Inject roster scenarios (injuries, transfers, goalie split).
4. Store full distributions and confidence diagnostics.
5. Add reliability tracking vs actual outcomes.

