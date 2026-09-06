# algo-barbell

An automated trading system for Angel One SmartAPI. Capital is split into three
isolated silos, each traded by its own engine, with a Streamlit operator console
on top.

Runs in three environments from one image: **dev** and **preprod** simulate
orders against live market data; **prod** trades real money. The environment
decides which database is used and whether orders are real.

## Quick start

```bash
cp env/dev.env.example env/dev.env
$EDITOR env/dev.env                # add your Angel One credentials

scripts/barbell.sh build
scripts/barbell.sh verify dev      # preflight: config, database, imports
scripts/barbell.sh up dev          # postgres + dashboard
scripts/barbell.sh seed dev        # optional: demo rows to look at
```

Dashboard: `http://localhost:8501` (password from `DASHBOARD_PASSWORD`).

Run an engine:

```bash
scripts/barbell.sh run options dev
```

## Environments

| `APP_ENV` | Orders | Database | Purpose |
|---|---|---|---|
| `dev` | Simulated | `algo_barbell_dev` | Sandbox. Live data, no money at risk. |
| `preprod` | Simulated | `algo_barbell_preprod` | Production rehearsal: real sizing and schedule, still no money at risk. |
| `prod` | **Real** | `algo_barbell_prod` | Real orders, real money. |

Real orders need **both** `APP_ENV=prod` and `ALLOW_LIVE_ORDERS=true`. Anything
else stays on paper, so a mis-set variable cannot silently spend money.

Every engine prints its mode at startup — check this first:

```
EXECUTION: PAPER (simulated) | env=dev — no orders will reach the broker
```

## Engines

| Engine | Strategy | Schedule (IST) |
|---|---|---|
| `option_predator` | Intraday opening-range breakout on index weeklies | 09:14–15:05 |
| `smallcap_engine` | Volatility-contraction breakout, ~100 liquid smallcaps | 15:15 daily |
| `macro_engine` | Donchian breakout + RSI on index/commodity ETFs | 15:20 daily |
| `regime_eod` | Replays skipped days to show avoided losses (never trades) | 15:35 daily |

Idle days are normal. The options engine deliberately stands down when the
opening range is too narrow — the Regime filter tab shows each decision and what
standing down saved.

## Commands

```bash
scripts/barbell.sh up|down|status|logs   [env]
scripts/barbell.sh verify | verify-full  [env]
scripts/barbell.sh run <engine>          [env]
scripts/barbell.sh seed                  [env]
scripts/barbell.sh flatten | flatten-exec [env]
scripts/barbell.sh psql                  [env]
scripts/barbell.sh build                 [env]
```

`env` defaults to `dev`. Run `scripts/barbell.sh help` for details.

## Layout

```
src/            application code (engines, dashboard, data layer, execution gateway)
scripts/        barbell.sh control script + demo data seeder
verify/         single preflight checker
env/            per-environment config templates
docs/           architecture, deployment, operations
data/ logs/     local cache and log output (trading state lives in Postgres)
```

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how it works: silos, data
  model, execution gateway, safety mechanisms. Read before changing code.
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — setup, scheduling, EC2, and the
  promotion path to real money.
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — daily runbook, troubleshooting,
  emergency procedures, useful queries.

## Safety

This software places real financial orders when configured to do so. Before
enabling `ALLOW_LIVE_ORDERS`:

- Run preprod through several complete sessions and review the results.
- Confirm `scripts/barbell.sh verify-full prod` passes cleanly.
- Set `OPTIONS_DAILY_LOSS` to a loss you can accept in a single day.
- Know how to run `scripts/barbell.sh flatten-exec prod`.
- Keep the dashboard off the public internet; reach it over an SSH tunnel.

The kill switch, position halt, hard exit, and regime filter are load-bearing
safety mechanisms. Understand what they do before changing them — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

No trading system is risk-free. Losses are possible even when everything works
as designed.
