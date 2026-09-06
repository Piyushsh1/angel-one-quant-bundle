"""
================================================================================
metrics.py  ▸  Performance measurement over recorded trades
================================================================================
WHY THIS EXISTS
───────────────
"Is this strategy working?" cannot be answered by looking at total P&L. A
system can be up money and still be a bad bet (one lucky trade carrying fifty
losers), or down money over a sample too small to mean anything.

This module computes the statistics that actually distinguish an edge from
noise, straight from ``trade_history`` — which, since the cost model landed,
holds P&L NET of brokerage, STT, exchange fees, GST, stamp duty and slippage.
So these numbers are comparable to a real brokerage statement rather than to an
idealised simulation.

THE NUMBER THAT MATTERS MOST
────────────────────────────
Expectancy — average profit per trade:

    expectancy = (win_rate × avg_win) − (loss_rate × avg_loss)

A high win rate is NOT the goal and can actively mislead. Taking tiny profits
while holding losers produces an 80% win rate and a negative expectancy. The
question is always: does the average trade make money after costs?

HOW TO READ THE OUTPUT HONESTLY
───────────────────────────────
  · Under ~30 trades, treat every figure as provisional. Small samples produce
    impressive-looking numbers by luck.
  · max_drawdown is the number to check against your own risk tolerance. It is
    the worst peak-to-trough fall the strategy actually put you through.
  · A profit factor near 1.0 means gross wins barely cover gross losses — the
    strategy is effectively a coin flip once costs are counted.
  · Past results do not establish future results. This measures what HAS
    happened, which is the only thing measurable.

USAGE
─────
    import metrics
    s = metrics.summary("OPTIONS")            # one engine
    s = metrics.summary()                     # all engines combined
    print(metrics.format_summary(s))
================================================================================
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import config
import database

log = logging.getLogger("metrics")

#: Below this many closed trades, results are statistically meaningless.
MIN_MEANINGFUL_SAMPLE = 30


@dataclass
class Summary:
    """Performance statistics over a set of closed trades."""

    engine: str = "ALL"
    trades: int = 0                 # closed (P&L-bearing) trades
    wins: int = 0
    losses: int = 0
    scratches: int = 0              # exactly zero P&L

    win_rate: float = 0.0           # %
    gross_profit: float = 0.0       # sum of winners
    gross_loss: float = 0.0         # sum of losers (positive number)
    net_pnl: float = 0.0            # after all charges

    avg_win: float = 0.0
    avg_loss: float = 0.0           # positive number
    largest_win: float = 0.0
    largest_loss: float = 0.0       # positive number

    expectancy: float = 0.0         # ₹ per trade
    profit_factor: float = 0.0      # gross_profit / gross_loss
    reward_risk: float = 0.0        # avg_win / avg_loss

    max_drawdown: float = 0.0       # worst peak-to-trough on the equity curve
    max_win_streak: int = 0
    max_loss_streak: int = 0

    total_charges: float = 0.0      # what costs took out
    gross_pnl_before_costs: float = 0.0

    exit_reasons: dict = field(default_factory=dict)
    trading_days: int = 0
    trades_per_day: float = 0.0
    equity_curve: list = field(default_factory=list)

    @property
    def is_meaningful(self) -> bool:
        """False when the sample is too small to draw conclusions from."""
        return self.trades >= MIN_MEANINGFUL_SAMPLE

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_meaningful"] = self.is_meaningful
        return d


# ════════════════════════════════════════════════════════════════════════════
#  DATA ACCESS
# ════════════════════════════════════════════════════════════════════════════
def closed_trades(engine: Optional[str] = None,
                  since: Optional[str] = None) -> list[dict[str, Any]]:
    """Return P&L-bearing rows from ``trade_history``, oldest first.

    Entry legs carry ``pnl IS NULL``, so filtering on non-null P&L isolates
    completed round trips (including partial scale-outs, which realise P&L on
    the lots they close).
    """
    sql = ["SELECT engine, timestamp, symbol, side, quantity, price, pnl,",
           "       meta_json",
           "FROM trade_history",
           "WHERE pnl IS NOT NULL"]
    args: list[Any] = []
    if engine:
        sql.append("AND engine = %s")
        args.append(engine.upper())
    if since:
        sql.append("AND timestamp >= %s")
        args.append(since)
    sql.append("ORDER BY timestamp ASC, id ASC")

    pool = database._pool_instance()
    with pool.connection() as c:
        rows = c.execute(" ".join(sql), args).fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
#  COMPUTATION
# ════════════════════════════════════════════════════════════════════════════
def _max_drawdown(curve: list[float]) -> float:
    """Worst peak-to-trough decline on a cumulative-P&L curve.

    Returned as a positive number. This is the loss the strategy actually put
    the account through, which is usually a harder constraint than total P&L.
    """
    peak = 0.0
    worst = 0.0
    for equity in curve:
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return round(worst, 2)


def _streaks(pnls: list[float]) -> tuple[int, int]:
    """Return ``(longest_win_streak, longest_loss_streak)``."""
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
        elif p < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def summary(engine: Optional[str] = None,
            since: Optional[str] = None) -> Summary:
    """Compute performance statistics for one engine, or all engines combined."""
    rows = closed_trades(engine, since)
    s = Summary(engine=(engine or "ALL").upper())
    if not rows:
        return s

    pnls: list[float] = []
    equity = 0.0
    days: set[str] = set()

    for r in rows:
        pnl = float(r["pnl"] or 0.0)
        pnls.append(pnl)
        equity += pnl
        s.equity_curve.append(round(equity, 2))

        ts = str(r["timestamp"] or "")
        if ts:
            days.add(ts[:10])

        # meta_json carries the cost breakdown and the exit reason.
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        s.total_charges += float(meta.get("charges") or 0.0)
        s.gross_pnl_before_costs += float(meta.get("gross_pnl") or pnl)
        reason = str(meta.get("reason") or "unknown")
        s.exit_reasons[reason] = s.exit_reasons.get(reason, 0) + 1

    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]      # stored positive

    s.trades = len(pnls)
    s.wins = len(wins)
    s.losses = len(losses)
    s.scratches = s.trades - s.wins - s.losses

    s.net_pnl = round(sum(pnls), 2)
    s.gross_profit = round(sum(wins), 2)
    s.gross_loss = round(sum(losses), 2)

    s.win_rate = round(s.wins / s.trades * 100.0, 2) if s.trades else 0.0
    s.avg_win = round(s.gross_profit / s.wins, 2) if s.wins else 0.0
    s.avg_loss = round(s.gross_loss / s.losses, 2) if s.losses else 0.0
    s.largest_win = round(max(wins), 2) if wins else 0.0
    s.largest_loss = round(max(losses), 2) if losses else 0.0

    # Expectancy — the headline figure. Average rupees per trade.
    s.expectancy = round(s.net_pnl / s.trades, 2) if s.trades else 0.0

    # Profit factor: >1 means gross wins exceed gross losses. inf when no losses
    # yet, which is itself a sign the sample is too young to trust.
    s.profit_factor = (
        round(s.gross_profit / s.gross_loss, 2) if s.gross_loss > 0
        else (float("inf") if s.gross_profit > 0 else 0.0)
    )
    s.reward_risk = round(s.avg_win / s.avg_loss, 2) if s.avg_loss > 0 else 0.0

    s.max_drawdown = _max_drawdown(s.equity_curve)
    s.max_win_streak, s.max_loss_streak = _streaks(pnls)

    s.total_charges = round(s.total_charges, 2)
    s.gross_pnl_before_costs = round(s.gross_pnl_before_costs, 2)
    s.trading_days = len(days)
    s.trades_per_day = (
        round(s.trades / s.trading_days, 2) if s.trading_days else 0.0
    )
    return s


# ════════════════════════════════════════════════════════════════════════════
#  PRESENTATION
# ════════════════════════════════════════════════════════════════════════════
def format_summary(s: Summary) -> str:
    """Render a Summary as an operator-readable block of text."""
    if s.trades == 0:
        return (
            f"  {s.engine}: no closed trades yet — nothing to measure.\n"
            f"  Run paper/preprod through some sessions first."
        )

    pf = "∞" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
    lines = [
        f"  ENGINE            : {s.engine}",
        f"  Closed trades     : {s.trades}  "
        f"({s.trading_days} session(s), {s.trades_per_day:.2f}/day)",
        "",
        f"  NET P&L           : ₹{s.net_pnl:+,.2f}   (after all charges)",
        f"  Gross before cost : ₹{s.gross_pnl_before_costs:+,.2f}",
        f"  Charges paid      : ₹{s.total_charges:,.2f}",
        "",
        f"  EXPECTANCY/trade  : ₹{s.expectancy:+,.2f}   ← the number that matters",
        f"  Win rate          : {s.win_rate:.1f}%  "
        f"({s.wins}W / {s.losses}L / {s.scratches} flat)",
        f"  Avg win           : ₹{s.avg_win:,.2f}",
        f"  Avg loss          : ₹{s.avg_loss:,.2f}",
        f"  Reward : risk     : {s.reward_risk:.2f}",
        f"  Profit factor     : {pf}",
        "",
        f"  MAX DRAWDOWN      : ₹{s.max_drawdown:,.2f}   ← could you sit through this?",
        f"  Largest win       : ₹{s.largest_win:,.2f}",
        f"  Largest loss      : ₹{s.largest_loss:,.2f}",
        f"  Longest win run   : {s.max_win_streak}",
        f"  Longest loss run  : {s.max_loss_streak}",
    ]
    if s.exit_reasons:
        lines.append("")
        lines.append("  Exit reasons      :")
        for reason, n in sorted(
            s.exit_reasons.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"      {reason:<28} {n}")

    lines.append("")
    if not s.is_meaningful:
        lines.append(
            f"  ⚠️  SAMPLE TOO SMALL — {s.trades} trades. Below "
            f"{MIN_MEANINGFUL_SAMPLE} these figures are mostly luck."
        )
        lines.append(
            "     Keep running preprod before drawing any conclusion."
        )
    elif s.expectancy > 0:
        lines.append(
            f"  Positive expectancy over {s.trades} trades. That is evidence, "
            f"not proof —"
        )
        lines.append(
            "     check it holds across different market conditions before "
            "risking capital."
        )
    else:
        lines.append(
            f"  Negative expectancy over {s.trades} trades: this configuration "
            f"loses ₹{abs(s.expectancy):,.2f} per trade after costs."
        )
        lines.append(
            "     Do NOT enable live orders on this configuration."
        )
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
#  CLI  (python src/metrics.py [ENGINE])
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _log = logging.getLogger("metrics")

    _target = sys.argv[1].upper() if len(sys.argv) > 1 else None
    database.wait_for_database()

    _log.info("=" * 70)
    _log.info("  PERFORMANCE — env=%s db=%s", config.APP_ENV, config.DB_NAME)
    _log.info("  mode=%s (paper results include modelled costs)",
              config.TRADING_MODE)
    _log.info("=" * 70)

    _targets = [_target] if _target else list(database.ENGINES) + [None]
    for _t in _targets:
        _log.info("")
        for _line in format_summary(summary(_t)).splitlines():
            _log.info(_line)
        _log.info("  " + "─" * 66)

    database.close_pool()
