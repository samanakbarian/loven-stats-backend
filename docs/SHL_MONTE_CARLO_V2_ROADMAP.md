# SHL Monte Carlo v2 Roadmap

Last updated: 2026-05-18
Owner: Backend + Product Analytics

## Goal
Replace the current heuristic SHL projection with a calibrated Monte Carlo model that:
- simulates a full season game by game
- produces robust probability distributions
- supports roster scenarios (transfers, injuries, goalie split)

## Scope
In scope:
- match outcome probability model
- season simulation (10k+ runs)
- API outputs for p10/p50/p90, rank distribution, and risk metrics
- model card, calibration, and operations metrics

Out of initial scope:
- live in-game predictions
- real-time player tracking and xG models
- fully automatic transfer-reactive simulation minute by minute

## Architecture Overview
1. Data layer:
- historical SHL games + standings + roster status + special teams
2. Model layer:
- calibrated match model (Poisson/Skellam or logistic baseline)
3. Simulation layer:
- full schedule replay for upcoming season
4. Serving layer:
- precomputed distributions for API/frontend

## Phase Plan
## Phase 1: Data Foundation and Quality (1-2 weeks)
Deliverables:
- unified match table for 5-10 SHL seasons
- feature table per team/match (attack/defense, home/away, special teams, form)
- validation rules for duplicates, missing games, and outliers

Tickets:
1. `MCV2-001` Build historical SHL master table in BigQuery.
2. `MCV2-002` Add dbt tests for schema, uniqueness, and completeness.
3. `MCV2-003` Validate data quality and create incident list for source gaps.

## Phase 2: Match Model and Calibration (1-2 weeks)
Deliverables:
- baseline model for match outcomes
- calibration report (Brier/log-loss/reliability)

Tickets:
1. `MCV2-010` Implement baseline match model.
2. `MCV2-011` Backtest last 3 seasons.
3. `MCV2-012` Calibrate probabilities and document parameters.

## Phase 3: Monte Carlo Engine (1 week)
Deliverables:
- 10k+ season runs
- distribution output per team (rank, points)

Tickets:
1. `MCV2-020` Build simulation engine against upcoming SHL schedule.
2. `MCV2-021` Compute p10/p50/p90 and top6/playin/playout probabilities.
3. `MCV2-022` Precompute and store results in serving table.

## Phase 4: Scenario Layer (1 week)
Deliverables:
- scenario runs for injuries/transfers/goalie split

Tickets:
1. `MCV2-030` Define scenario format (JSON contract).
2. `MCV2-031` Simulate scenario deltas vs baseline.
3. `MCV2-032` Expose scenarios in API contract.

## Phase 5: API/Frontend Integration (1 week)
Deliverables:
- new endpoint (or versioned existing endpoint)
- frontend distribution and uncertainty visualization

Tickets:
1. `MCV2-040` Implement `/api/v1/shl-projection-v2`.
2. `MCV2-041` Add metadata: `model_version`, `trained_at`, `sample_count`.
3. `MCV2-042` Add frontend components for distribution views.

## Phase 6: Operations and Governance (0.5 week)
Deliverables:
- monitoring for model quality and data drift

Tickets:
1. `MCV2-050` Add ops logs and alarms for stale/drift.
2. `MCV2-051` Add weekly backtest job.
3. `MCV2-052` Add incident playbook for model deviations.

## Acceptance KPIs for v2
- better Brier score than v1
- reliability curve within agreed tolerance
- better top6/playout probability hit rate than v1
- <= 1 critical data incident per month

## Risks
- insufficient historical data quality for some seasons
- too much model complexity for ops budget
- incorrect roster inputs can still skew outputs

## Mitigation
- strict dbt quality gates before simulation
- start with simple calibrated model and iterate
- separate baseline and scenario inputs with validation

## Estimate
- MVP (without advanced scenario layer): 3-4 weeks
- full v2 as above: 6-8 weeks
