# Deployment

Covers local setup, the three environments, scheduling, and promotion to real
money.

## Prerequisites

- Docker with Compose v2 (`docker compose version`)
- Angel One SmartAPI credentials: `API_KEY`, `CLIENT_ID`, `PASSWORD` (mPIN),
  `TOTP_SECRET`
- For live trading: the deployment host's static/elastic IP allowlisted in the
  SmartAPI portal, or orders and market data will be refused

## First run

```bash
cp env/dev.env.example env/dev.env
$EDITOR env/dev.env          # fill in credentials, set DB_PASSWORD

scripts/barbell.sh build
scripts/barbell.sh verify dev     # config + database + imports
scripts/barbell.sh up dev         # postgres + dashboard
```

The dashboard is then on `http://localhost:8501` (port comes from
`DASHBOARD_PORT` in the env file). The schema is created automatically on first
connection — there is no migration step to run.

Optionally load demo rows so the dashboard has something to show:

```bash
scripts/barbell.sh seed dev
```

## Running engines

Engines are one-shot containers, not always-on services. The dashboard and
Postgres are the only long-running pieces.

```bash
scripts/barbell.sh run options  dev
scripts/barbell.sh run macro    dev
scripts/barbell.sh run smallcap dev
scripts/barbell.sh run regime-eod dev
```

`options` is long-running once started: it holds the intraday loop open until
its hard exit time, then exits on its own.

## Scheduling

Compose has no cron. Use the host's scheduler to trigger the daily runs. IST
times, weekdays only — add these to `crontab -e` on the deployment host:

```cron
# ── algo-barbell (times are IST; set CRON_TZ or run the host in IST) ────────
CRON_TZ=Asia/Kolkata

14 9  * * 1-5  cd /opt/algo-barbell && scripts/barbell.sh run options    prod
15 15 * * 1-5  cd /opt/algo-barbell && scripts/barbell.sh run smallcap   prod
20 15 * * 1-5  cd /opt/algo-barbell && scripts/barbell.sh run macro      prod
35 15 * * 1-5  cd /opt/algo-barbell && scripts/barbell.sh run regime-eod prod
```

Smallcap runs five minutes before macro because it scans ~100 symbols and needs
the headroom. Regime-EOD runs after the options hard exit so it replays a
completed session.

Verify the schedule fired by checking container history:

```bash
scripts/barbell.sh status prod
scripts/barbell.sh logs prod
```

## Running several environments on one host

Each environment gets its own Compose project name, network and volumes, so
they coexist without interfering:

```bash
scripts/barbell.sh up dev       # dashboard :8501, db algo_barbell_dev
scripts/barbell.sh up preprod   # dashboard :8502, db algo_barbell_preprod
scripts/barbell.sh up prod      # dashboard :8503, db algo_barbell_prod
```

Ports come from `DASHBOARD_PORT` in each env file; the examples ship with
8501/8502/8503 already distinct.

## Promotion path to real money

Do not skip steps. Each one catches a different class of failure.

**1. dev** — confirm the plumbing works.
```bash
scripts/barbell.sh verify dev
scripts/barbell.sh run options dev
```
Look for `EXECUTION: PAPER (simulated)` in the logs and trades appearing on the
dashboard.

**2. preprod** — rehearse production for several sessions.
```bash
cp env/preprod.env.example env/preprod.env   # mirror prod capital and risk
scripts/barbell.sh verify preprod
scripts/barbell.sh up preprod
```
Run the full cron schedule here. Watch for: entries firing when expected, stops
trailing correctly, the regime filter standing down on quiet days, and the
kill switch latching if a loss limit is hit. Orders are still simulated.

**3. prod with orders still simulated** — validate against the production
database and credentials without spending anything.
```bash
cp env/prod.env.example env/prod.env
chmod 600 env/prod.env
# keep ALLOW_LIVE_ORDERS=false
scripts/barbell.sh verify-full prod
```
`verify-full` performs a real broker login, so this also proves the IP
allowlist and TOTP are correct.

**4. prod, armed** — only after the above are all clean.
```bash
$EDITOR env/prod.env      # ALLOW_LIVE_ORDERS=true
scripts/barbell.sh verify-full prod
scripts/barbell.sh up prod
```

The boot log must now read `EXECUTION: LIVE (real money)`. If it still says
PAPER, one of the two gates is unset.

### Pre-live checklist

- [ ] `verify-full prod` passes with no failures
- [ ] Broker login succeeds from the deployment host (IP allowlisted)
- [ ] Account funded above the sum of the three silos plus margin cushion
- [ ] `OPTIONS_DAILY_LOSS` set to a loss you are willing to take in one day
- [ ] `DASHBOARD_PASSWORD` is a long random value, not a default
- [ ] `env/prod.env` is `chmod 600` and never committed
- [ ] Preprod ran the full schedule cleanly for several sessions
- [ ] You know how to run `scripts/barbell.sh flatten-exec prod`

## Rolling back to paper

Fastest way to stop real-money trading without losing state:

```bash
$EDITOR env/prod.env      # ALLOW_LIVE_ORDERS=false
scripts/barbell.sh down prod
scripts/barbell.sh up prod
```

Any positions already open at the broker are **not** closed by this. Flatten
them first if that is what you want:

```bash
scripts/barbell.sh flatten prod        # dry run — see what it would do
scripts/barbell.sh flatten-exec prod   # actually close
```

## Deploying to AWS EC2

A `t3.small` (2 vCPU / 2 GB) is sufficient. Ubuntu 22.04 or newer.

```bash
# 1. Install Docker
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER" && newgrp docker

# 2. Copy the bundle
scp -r angel-one-quant-bundle ubuntu@<EC2_IP>:/opt/algo-barbell
ssh ubuntu@<EC2_IP> && cd /opt/algo-barbell

# 3. Configure
cp env/prod.env.example env/prod.env
$EDITOR env/prod.env && chmod 600 env/prod.env

# 4. Verify, then start
scripts/barbell.sh build
scripts/barbell.sh verify-full prod
scripts/barbell.sh up prod

# 5. Schedule the engines
crontab -e     # see the Scheduling section above
```

Then:

- **Allowlist the instance's Elastic IP** in the SmartAPI portal. Use an
  Elastic IP so it survives restarts — a changed IP means refused orders.
- **Do not expose the dashboard to the internet.** Its only protection is a
  password. Keep the security group closed and reach it over an SSH tunnel:
  ```bash
  ssh -L 8503:localhost:8503 ubuntu@<EC2_IP>
  ```
- **Back up the database.** The Postgres volume holds all trading history:
  ```bash
  docker compose -p algo-barbell-prod exec postgres \
    pg_dump -U algo algo_barbell_prod | gzip > backup-$(date +%F).sql.gz
  ```

## Configuration reference

Environment-selecting variables:

| Variable | Values | Effect |
|---|---|---|
| `APP_ENV` | `dev` `preprod` `prod` | Picks the database and default trading mode |
| `ALLOW_LIVE_ORDERS` | `true` `false` | Second gate for real money; ignored unless `APP_ENV=prod` |

Database:

| Variable | Default | Notes |
|---|---|---|
| `DB_HOST` | `localhost` | `postgres` inside Compose |
| `DB_PORT` | `5432` | |
| `DB_NAME` | `algo_barbell_<APP_ENV>` | One database per environment |
| `DB_USER` / `DB_PASSWORD` | `algo` / `algo` | Change for preprod and prod |
| `DATABASE_URL` | derived | Set to override all of the above |
| `DB_STATEMENT_TIMEOUT_MS` | `15000` | Stops a wedged query pinning an engine |

Capital and risk (see `.env.example` for the full list):
`MACRO_MAX_CAPITAL`, `SMALLCAP_MAX_CAPITAL`, `OPTIONS_MAX_CAPITAL`,
`MARGIN_CUSHION_PCT`, `MACRO_RISK_PCT`, `SMALLCAP_RISK_PCT`,
`OPTIONS_DAILY_LOSS`, `OPTIONS_HARD_EXIT_TIME`.

## Running without Docker

Supported but not recommended — Docker exists so the runtime matches across
environments.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env && $EDITOR .env    # set DB_HOST=localhost
export PYTHONPATH=src

python verify/verify.py
streamlit run src/barbell_dashboard.py
python src/option_predator.py
```

You must supply your own PostgreSQL instance and point `DB_*` at it.
