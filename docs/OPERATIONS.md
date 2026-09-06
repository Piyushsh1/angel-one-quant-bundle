# Operations runbook

Day-to-day running, monitoring, and what to do when something looks wrong.

## Daily commands

```bash
scripts/barbell.sh status  prod        # what's running
scripts/barbell.sh logs    prod        # tail everything
scripts/barbell.sh logs    options prod # one service
scripts/barbell.sh verify  prod        # config + database health
```

Log files also land in `logs/` on the host, one per engine per day
(`logs/options_2026-09-05.log`).

## Reading the boot log

Every engine prints its mode on startup. Check this first, always:

```
EXECUTION: PAPER (simulated) | env=preprod — no orders will reach the broker
EXECUTION: LIVE (real money) | env=prod — orders WILL be transmitted to Angel One
```

If a production engine says PAPER, either `APP_ENV` isn't `prod` or
`ALLOW_LIVE_ORDERS` isn't `true`.

## What normal looks like

**Idle days are expected.** The options engine stands down entirely when the
opening range is too narrow to justify a trade. A week with few or no options
trades is the regime filter working, not a bug. The Regime filter tab shows
each day's decision and — via the post-close replay — the loss that standing
down avoided.

Macro and smallcap are daily-cadence swing strategies. Days with no new entries
are normal; they only act when a setup completes.

## Dashboard

`http://localhost:<DASHBOARD_PORT>` — 8501/8502/8503 for dev/preprod/prod by
default. Password is `DASHBOARD_PASSWORD`.

The footer shows `env`, `mode` and `db` so you can always tell which
environment you're looking at.

Tabs:

| Tab | Shows |
|---|---|
| Action center | Broker cash, silo deployment, quick controls |
| Live activity | Recent log lines in plain English |
| Regime filter | Per-day gate decisions and avoided-loss evidence |
| Macro / Smallcap / Options | Open positions, trade history, daily P&L per engine |
| Tunables | Editable risk settings |
| Emergency | Flatten controls |
| Procedures | Operator checklists |

The dashboard is **read-only** with respect to trading state. It never places
orders; it reads the database and shows process status.

## Emergency: close everything

```bash
scripts/barbell.sh flatten      prod   # DRY RUN — prints what it would do
scripts/barbell.sh flatten-exec prod   # actually closes (asks for confirmation)
```

In dev/preprod nothing was ever sent to the broker, so flatten simply clears
the rows. In prod with orders armed it transmits real SELL MARKET orders.

If flatten reports a failure, the position is still open at the broker. Close it
manually in the Angel One app — do not assume the bot recovered.

## Troubleshooting

### Dashboard won't start or shows a database error

```bash
scripts/barbell.sh status prod          # is postgres healthy?
scripts/barbell.sh logs dashboard prod
scripts/barbell.sh verify prod
```

Most common causes:

- **Postgres not ready yet.** The health check gates the dashboard, but on a
  cold start give it ~15s. Engines call `wait_for_database()` and retry on
  their own.
- **Wrong credentials.** `DB_USER`/`DB_PASSWORD` in the env file must match what
  Postgres was *first initialised* with. Changing them later has no effect on an
  existing volume — Postgres only reads `POSTGRES_*` when creating a new data
  directory. Either set them back, or recreate the volume (destroys data):
  ```bash
  scripts/barbell.sh down prod
  docker volume rm algo-barbell-prod_pgdata
  scripts/barbell.sh up prod
  ```
- **Port already in use.** Change `DASHBOARD_PORT` in the env file.

### Engine exits immediately

Check the trading-day guard first — the engines refuse to run on weekends and
NSE holidays, which is correct behaviour:

```
Trading-day guard: SKIP (weekend)
```

Use `--force` to override for testing:

```bash
APP_ENV=dev docker compose -p algo-barbell-dev run --rm options \
  python src/option_predator.py --force --scan-only
```

### Broker login fails

```bash
scripts/barbell.sh verify-full prod
```

In order of likelihood:

1. **IP not allowlisted.** The host's public IP must be registered in the
   SmartAPI portal. This is the most common cause on a fresh EC2 instance or
   after an IP change.
2. **Wrong `TOTP_SECRET`.** Must be the base32 secret from enrolment, not a
   six-digit code.
3. **Outside login hours.** Angel One restricts session creation overnight.
4. **Wrong `PASSWORD`.** This is the mPIN, not the web password.

### API throttling / rate-limit rejections

Symptoms: orders returning `None`, repeated retries, `exceeding access rate`
in logs.

The engines pace their calls (`API_PACING_SECONDS`) and back off on failure, but
running all three engines at once still concentrates load — smallcap alone scans
~100 symbols. Mitigations:

- Keep the staggered schedule (smallcap 15:15, macro 15:20). Do not collapse them.
- Reduce `SMALLCAP_UNIVERSE_SIZE` from 250 to 100.
- Raise `API_PACING_SECONDS`.

A margin-related rejection is handled differently: it starts a cooldown for that
symbol rather than retrying, to avoid a retry storm. The position stays open and
is logged as `[MARGIN GUARDRAIL]`. Fund the account, then flatten manually.

### Kill switch tripped

```
KILL SWITCH TRIGGERED | engine=OPTIONS date=2026-09-05
```

The engine has hit `OPTIONS_DAILY_LOSS` and will refuse new entries for the rest
of the day. Existing positions are still managed and exited. This is a safety
feature — it resets automatically the next trading day. Investigate the losses
before overriding.

### Position halt latched

```
POSITION HALT LATCHED | engine=OPTIONS reason='...'
```

The database and the broker disagree about what is held. The engine keeps
managing and exiting existing positions but refuses new entries. Reconcile
manually:

```bash
scripts/barbell.sh psql prod
```
```sql
SELECT * FROM open_positions WHERE engine = 'OPTIONS';
SELECT * FROM bot_state WHERE engine = 'OPTIONS';
```

Compare against the broker's position book. Once they agree, clear the halt:

```sql
UPDATE bot_state SET value = '0' WHERE engine = 'OPTIONS' AND key = 'position_halt';
```

Do not clear it without reconciling — the halt exists to stop the engine trading
on a wrong view of reality.

## Useful queries

```bash
scripts/barbell.sh psql prod
```

```sql
-- Open positions across all engines
SELECT engine, symbol, quantity, entry_price, stop_loss, entry_time
FROM open_positions ORDER BY engine, entry_time;

-- Today's P&L per engine
SELECT engine, realised_pnl, kill_switch_hit
FROM daily_pnl WHERE date = CURRENT_DATE::text;

-- Last 20 fills
SELECT engine, timestamp, symbol, side, quantity, price, pnl
FROM trade_history ORDER BY id DESC LIMIT 20;

-- Cumulative realised P&L per engine
SELECT engine, SUM(pnl) AS net FROM trade_history
WHERE pnl IS NOT NULL GROUP BY engine;

-- Regime decisions this month
SELECT date, underlying, decision, or_width_pct, threshold_pct, hypo_pnl
FROM regime_log ORDER BY date DESC, underlying;
```

## Backup and restore

```bash
# Backup
docker compose -p algo-barbell-prod exec postgres \
  pg_dump -U algo algo_barbell_prod | gzip > backup-$(date +%F).sql.gz

# Restore into a fresh database
gunzip -c backup-2026-09-05.sql.gz | \
  docker compose -p algo-barbell-prod exec -T postgres \
  psql -U algo -d algo_barbell_prod
```

Back up before any schema change or upgrade. The Postgres volume is the only
place trading history lives.

## Maintenance

**Rebuild after code changes:**
```bash
scripts/barbell.sh build prod
scripts/barbell.sh down  prod
scripts/barbell.sh up    prod
```

**Log rotation:** engines rotate their own files at 10 MB, keeping 3 backups.
Container logs are Docker's responsibility — cap them in `/etc/docker/daemon.json`
if disk becomes an issue.

**Instrument cache:** the scrip master is re-downloaded daily on first use and
cached in the `app-data` volume. Delete `data/instrument_cache.json` inside the
volume to force a refresh.

**Candle cache:** backtest history lives in the `candles` table. To reclaim
space or force a refetch:

```sql
DELETE FROM candles WHERE interval = 'ONE_MINUTE' AND timestamp < '2026-01-01';
```
