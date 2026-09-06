#!/usr/bin/env bash
# ==============================================================================
#  barbell.sh  ▸  Single control script for the whole stack
# ==============================================================================
#  Replaces the twelve previous shell scripts (install.sh, setup.sh,
#  setup_auto_trading.sh, auto_daily_trading.sh, start_paper_trading.sh,
#  stop_paper_trading.sh, start_options_only.sh, start_dashboard.sh,
#  start_dashboard_simple.sh, run_dashboard_persistent.sh,
#  check_paper_trading_status.sh, watch_launch.sh) — most of which were three
#  different ways to do the same thing.
#
#  Everything runs through Docker Compose, so there is one code path per action
#  and no local Python environment to keep in sync.
#
#  USAGE
#      scripts/barbell.sh <command> [env]
#
#  ENV defaults to dev. Valid: dev | preprod | prod
#
#  COMMANDS
#      up               start postgres
#      down             stop everything (data volumes are preserved)
#      status           show container status
#      logs [svc]       tail logs (all services, or one)
#      verify           run the preflight checker
#      verify-full      preflight incl. broker login + live-readiness gates
#      run <engine>     run one engine now: options|macro|smallcap|regime-eod
#      metrics [engine] performance report: expectancy, win rate, drawdown
#      seed             seed demo data (refused in prod)
#      flatten          DRY-RUN emergency flatten
#      flatten-exec     EXECUTE emergency flatten (asks for confirmation)
#      psql             open a psql shell on the environment's database
#      build            rebuild the application image
#
#  EXAMPLES
#      scripts/barbell.sh up dev
#      scripts/barbell.sh run options preprod
#      scripts/barbell.sh metrics OPTIONS preprod
#      scripts/barbell.sh logs options prod
# ==============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CMD="${1:-help}"

# The env argument position varies by command, so resolve it per command below.
_resolve_env() {
  local candidate="${1:-dev}"
  case "$candidate" in
    dev | preprod | prod) echo "$candidate" ;;
    *)
      echo "ERROR: invalid environment '$candidate' (use dev|preprod|prod)" >&2
      exit 1
      ;;
  esac
}

c_info() { printf '\033[1;36m%s\033[0m\n' "$*"; }
c_ok()   { printf '\033[1;32m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
c_err()  { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

# ── Preconditions ────────────────────────────────────────────────────────────
require_env_file() {
  local env_name="$1"
  local f="env/${env_name}.env"
  if [[ ! -f "$f" ]]; then
    c_err "Missing $f"
    echo "  Create it from the template:" >&2
    echo "      cp env/${env_name}.env.example $f" >&2
    echo "  Then fill in your Angel One credentials." >&2
    exit 1
  fi
}

compose() {
  local env_name="$1"; shift
  APP_ENV="$env_name" docker compose -p "algo-barbell-${env_name}" "$@"
}

# ── Commands ─────────────────────────────────────────────────────────────────
case "$CMD" in
  up)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    c_info "Starting algo-barbell [$ENV_NAME] — postgres"
    compose "$ENV_NAME" up -d postgres
    echo
    compose "$ENV_NAME" ps
    c_ok "Database up. Engines are invoked per run: $0 run <engine> $ENV_NAME"
    c_ok "Read performance with:                    $0 metrics $ENV_NAME"
    if [[ "$ENV_NAME" == "prod" ]]; then
      c_warn "PROD environment. Real orders only fire if ALLOW_LIVE_ORDERS=true."
    fi
    ;;

  metrics)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    compose "$ENV_NAME" up -d postgres
    # Optional third arg limits the report to one engine.
    if [[ -n "${3:-}" ]]; then
      compose "$ENV_NAME" run --rm metrics python src/metrics.py "$3"
    else
      compose "$ENV_NAME" run --rm metrics
    fi
    ;;

  down)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    c_info "Stopping algo-barbell [$ENV_NAME] (data volumes preserved)"
    compose "$ENV_NAME" down
    ;;

  status)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    compose "$ENV_NAME" ps
    ;;

  logs)
    # Second arg may be a service name or an environment.
    if [[ "${2:-}" =~ ^(dev|preprod|prod)$ ]]; then
      ENV_NAME="$(_resolve_env "${2}")"; SVC=""
    else
      SVC="${2:-}"; ENV_NAME="$(_resolve_env "${3:-dev}")"
    fi
    # shellcheck disable=SC2086
    compose "$ENV_NAME" logs -f --tail=200 $SVC
    ;;

  verify)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    compose "$ENV_NAME" up -d postgres
    compose "$ENV_NAME" run --rm verify
    ;;

  verify-full)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    compose "$ENV_NAME" up -d postgres
    compose "$ENV_NAME" run --rm verify python verify/verify.py --full
    ;;

  run)
    ENGINE="${2:-}"
    ENV_NAME="$(_resolve_env "${3:-dev}")"
    case "$ENGINE" in
      options | macro | smallcap | regime-eod) ;;
      *)
        c_err "Usage: $0 run <options|macro|smallcap|regime-eod> [env]"
        exit 1
        ;;
    esac
    require_env_file "$ENV_NAME"
    c_info "Running $ENGINE [$ENV_NAME]"
    compose "$ENV_NAME" up -d postgres
    compose "$ENV_NAME" run --rm "$ENGINE"
    ;;

  seed)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    if [[ "$ENV_NAME" == "prod" ]]; then
      c_err "Refusing to seed demo data into prod."
      exit 1
    fi
    require_env_file "$ENV_NAME"
    compose "$ENV_NAME" up -d postgres
    compose "$ENV_NAME" run --rm verify \
      python scripts/seed_demo_data.py --reset --trades 40 --open 2
    ;;

  flatten)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    c_info "Emergency flatten — DRY RUN [$ENV_NAME]"
    compose "$ENV_NAME" run --rm flatten
    ;;

  flatten-exec)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    c_warn "This will CLOSE EVERY OPEN POSITION in [$ENV_NAME]."
    if [[ "$ENV_NAME" == "prod" ]]; then
      c_warn "PROD: real SELL orders will be transmitted to the broker."
    fi
    read -r -p "Type FLATTEN to confirm: " confirm
    if [[ "$confirm" != "FLATTEN" ]]; then
      echo "Aborted."
      exit 1
    fi
    compose "$ENV_NAME" run --rm flatten python src/flatten_all.py --execute
    ;;

  psql)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    require_env_file "$ENV_NAME"
    DB_NAME="$(grep -E '^DB_NAME=' "env/${ENV_NAME}.env" | cut -d= -f2)"
    DB_USER="$(grep -E '^DB_USER=' "env/${ENV_NAME}.env" | cut -d= -f2)"
    compose "$ENV_NAME" exec postgres psql -U "$DB_USER" -d "$DB_NAME"
    ;;

  build)
    ENV_NAME="$(_resolve_env "${2:-dev}")"
    compose "$ENV_NAME" build
    ;;

  help | --help | -h)
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;

  *)
    c_err "Unknown command: $CMD"
    echo "Run '$0 help' for usage." >&2
    exit 1
    ;;
esac
