# Architecture

How the system is put together and why. Read this before changing anything.

## The barbell idea

Capital is split into three isolated **silos**, each traded by its own engine.
A silo is a hard spending cap: an engine can never deploy more than its own
allocation, no matter what the broker says is available. Broker margin is
treated as informational only — all position sizing uses the configured silo.

| Engine | Strategy | Cadence | Instruments |
|---|---|---|---|
| `macro_engine` | Donchian breakout + RSI confirmation | Daily, 15:20 IST | Index/commodity ETFs |
| `smallcap_engine` | Volatility-contraction breakout | Daily, 15:15 IST | ~100 liquid smallcaps |
| `option_predator` | Intraday opening-range breakout | Continuous, 09:14–15:05 IST | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY weekly options |
| `regime_eod` | Post-close replay of skipped days | Daily, 15:35 IST | Read-only, never trades |

The engines never import each other. `regime_eod` is the sole exception: it
imports `option_predator` to reuse its pricing helpers for replay.

## Environments

The same image is deployed three times. `APP_ENV` decides two things and
nothing else:

1. **Which database** it connects to.
2. **Whether orders are simulated or real.**

| `APP_ENV` | Orders | Database | Purpose |
|---|---|---|---|
| `dev` | Simulated | `algo_barbell_dev` | Sandbox. Live market data, no money at risk. |
| `preprod` | Simulated | `algo_barbell_preprod` | Production rehearsal: same sizing, same schedule, still no money at risk. |
| `prod` | **Real** | `algo_barbell_prod` | Real orders, real money. |

Real orders require **two** gates: `APP_ENV=prod` *and*
`ALLOW_LIVE_ORDERS=true`. Anything else stays on paper. This is enforced in
`config.py`, so a mis-set variable cannot silently spend money:

```python
PAPER_TRADING = not (IS_PROD and _bool("ALLOW_LIVE_ORDERS", False))
```

Setting `APP_ENV=prod` with `ALLOW_LIVE_ORDERS=false` is a useful final dry
run: production database, production sizing, simulated fills.

## Data model

**One database per environment. One schema for the whole application.**

Previously there were four SQLite files (macro, smallcap, options, paper) that
each carried an *identical* schema — the split bought nothing but drift and
four copies of every query. Now there is a single Postgres schema with an
`engine` discriminator column, and `BotDB` scopes every read and write to its
owning engine, so isolation is preserved.

| Table | Key | Purpose |
|---|---|---|
| `open_positions` | `(engine, symbol)` | Currently-held positions |
| `trade_history` | `id` (BIGSERIAL) | Append-only fill ledger |
| `daily_pnl` | `(engine, date)` | Realised P&L + kill-switch latch |
| `bot_state` | `(engine, key)` | Free-form flags (`position_halt`, sync timestamps) |
| `regime_log` | `(date, underlying)` | Options regime-gate evidence |
| `candles` | `(symbol, interval, timestamp)` | Historical OHLCV cache for the backtester |

There is **one database technology** in the application: PostgreSQL. The
backtester's candle cache was previously a side-car SQLite file; it now lives in
the `candles` table. Refetching that history is expensive (rate-limited, 30-day
chunks), so it belongs somewhere that gets backed up with everything else — and
one technology means one set of SQL idioms to maintain.

`backtest_engine` only ever touches `candles`. It never reads or writes trading
state.

Timestamps are stored as ISO-8601 text, matching the previous behaviour so all
existing time math is unchanged.

`src/database.py` holds the *only* schema definition in the codebase. The
seed utility writes through `BotDB` for exactly this reason — there is no
second copy to drift.

### Why no separate paper database

Paper vs live is now a property of the *environment*, not of a table. Because
dev and preprod each have their own database, simulated trades physically
cannot reach production state. That is a stronger guarantee than the old
"separate file in the same directory" arrangement, and it removes the
duplicated live/paper dashboard tabs that had to be kept in sync.

## Order execution

Every order flows through `src/execution.py`, which makes the paper/live
decision once, centrally:

```
engine → execution.place_market() → ┬→ broker.place_market()   [prod + armed]
                                    └→ simulated fill at LTP   [dev, preprod]
```

Read-only calls (`fetch_ltp`) always hit the real broker in every mode — paper
trading is a rehearsal against genuine market data. Only state-changing order
calls are simulated.

Before this gateway existed, only the options engine had a paper mode
(implemented inline, with `if paper is not None:` branches duplicated at every
order site), and **macro and smallcap would place real orders in any
environment**. Routing all three through one gateway is what makes dev and
preprod safe by construction.

## Safety mechanisms

These are load-bearing. Do not remove them without understanding why they exist.

- **Kill switch** — when an engine's realised daily loss breaches
  `OPTIONS_DAILY_LOSS`, `daily_pnl.kill_switch_hit` latches and the engine
  refuses new entries for the rest of the day. Survives restarts.
- **Position halt** — if the database and the broker disagree about what is
  held, `bot_state.position_halt` latches. The engine continues to *manage and
  exit* existing positions but refuses new entries until an operator clears it.
- **Atomic exits** — `close_position()` and `partial_exit()` wrap their inserts
  and updates in one transaction, so a crash mid-exit can never leave a
  recorded fill alongside a still-"open" position.
- **Regime filter** — the options engine stands down entirely on days when the
  opening range is too narrow. Idle days are normal and intended. `regime_eod`
  then replays what *would* have happened, so the dashboard can show the loss
  that standing down avoided.
- **Hard exit** — all intraday positions are force-closed at
  `OPTIONS_HARD_EXIT_TIME` regardless of P&L.
- **Margin guardrail** — a broker rejection caused by insufficient funds starts
  a cooldown rather than a retry storm, protecting the API rate limit.

## Module map

```
src/
  config.py             environment, credentials, silos, risk — imported by everything
  database.py           the single schema + BotDB (engine-scoped Postgres access)
  execution.py          order gateway: paper ⇆ live decision lives here
  broker.py             raw Angel One order placement (never called directly by engines)
  auth.py               broker login / TOTP / session teardown
  retry.py              API retry decorator + margin-rejection detection
  instrument_master.py  scrip-master download + symbol/token resolution
  nse_calendar.py       trading-day and holiday guard
  smallcap_universe.py  liquid smallcap universe construction
  option_predator.py    options engine
  macro_engine.py       macro engine
  smallcap_engine.py    smallcap engine
  regime_eod.py         post-close regime replay
  backtest_engine.py    offline backtester (reads/writes only the candles table)
  barbell_dashboard.py  Streamlit operator console (read-only)
  flatten_all.py        emergency square-off across all engines
```

Dependency direction is strictly one-way: engines depend on
`execution` / `database` / `config`, never the reverse.
