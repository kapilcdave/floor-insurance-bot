"""Earnings-week far-OTM screen.

For every optionable name reporting this week: what the market charges for the
move, what the name's own earnings history says the move distribution is, and
what each far strike would have returned over that history.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import chain as C
import realized as R

HERE = Path(__file__).parent
MIN_CAP = 5e9


def realized_vol(bars: list[dict], window: int) -> float | None:
    """Annualized close-to-close vol, excluding the fattest 5% of days so a past
    earnings jump inside the window does not masquerade as base vol."""
    cs = [b["c"] for b in bars[-(window + 1) :] if b["c"]]
    if len(cs) < 20:
        return None
    rets = [cs[i + 1] / cs[i] - 1 for i in range(len(cs) - 1)]
    rets.sort(key=abs)
    keep = rets[: max(10, int(len(rets) * 0.95))]
    m = sum(keep) / len(keep)
    var = sum((r - m) ** 2 for r in keep) / (len(keep) - 1)
    return (var * 252) ** 0.5


def universe() -> list[dict]:
    """Names from the cached Nasdaq calendar days. LOTTO_FROM/LOTTO_TO restrict
    the window; without them every cached day is included, which mixes weeks."""
    lo = os.environ.get("LOTTO_FROM", "")
    hi = os.environ.get("LOTTO_TO", "9999-99-99")
    rows = {}
    for f in sorted(glob.glob(str(HERE / "earn_*.json"))):
        day = re.search(r"earn_(\d{4}-\d\d-\d\d)", f).group(1)
        if not (lo <= day <= hi):
            continue
        doc = json.loads(Path(f).read_text())
        for r in (doc.get("data") or {}).get("rows") or []:
            cap = float(re.sub(r"[^\d.]", "", r.get("marketCap") or "0") or 0)
            sym = (r.get("symbol") or "").strip().upper()
            if cap < MIN_CAP or not sym or "." in sym:
                continue
            rows[sym] = {
                "symbol": sym,
                "name": r.get("name", ""),
                "cap": cap,
                "earnings_date": day,
                "time": r.get("time", ""),
            }
    return sorted(rows.values(), key=lambda r: -r["cap"])


def expected_reaction_date(row: dict) -> str:
    """AH reporters move the next session; BMO reporters move that day."""
    d = datetime.strptime(row["earnings_date"], "%Y-%m-%d").date()
    if row["time"] == "time-pre-market":
        return d.isoformat()
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def run(symbols: list[str] | None = None) -> list[dict]:
    cm = R.cik_map()
    uni = universe()
    if symbols:
        uni = [u for u in uni if u["symbol"] in symbols]
    results = []
    for u in uni:
        sym = u["symbol"]
        try:
            cik = cm.get(sym)
            if not cik:
                print(f"  {sym}: no SEC CIK (foreign filer?), skipped", file=sys.stderr)
                continue
            hist = R.reaction_moves(sym, cik)
            if len(hist) < 8:
                print(f"  {sym}: only {len(hist)} reactions, skipped", file=sys.stderr)
                continue
            react = expected_reaction_date(u)
            exps = C.expiries(sym, react)
            if not exps:
                print(f"  {sym}: no listed options", file=sys.stderr)
                continue
            expiry = exps[0]
            px, px_t = C.spot(sym)
            if not px:
                print(f"  {sym}: no spot", file=sys.stderr)
                continue
            bars = R.daily_bars(sym, "2016-01-01")
            last_session = bars[-1]["d"] if bars else None
            base_vol = realized_vol(bars, 60)
            years = max(
                (
                    datetime.strptime(expiry, "%Y-%m-%d").date()
                    - date.today()
                ).days,
                1,
            ) / 365.0
            ch = C.build_chain(sym, expiry)
            atm = C.atm_straddle(ch, px, years, fresh=last_session)
            jump = (
                C.implied_jump(atm["implied_move"], years, base_vol)
                if atm and base_vol
                else None
            )
            moves = [h["ret"] for h in hist]
            a = sorted(abs(m) for m in moves)
            n = len(a)
            rec = {
                **u,
                "reaction_date": react,
                "expiry": expiry,
                "dte_after_reaction": (
                    datetime.strptime(expiry, "%Y-%m-%d").date()
                    - datetime.strptime(react, "%Y-%m-%d").date()
                ).days,
                "spot": px,
                "spot_asof": px_t,
                "n_hist": n,
                "hist_median_abs": a[n // 2],
                "hist_mean_abs": sum(a) / n,
                "hist_p75_abs": a[int(0.75 * n)],
                "hist_max_abs": a[-1],
                "hist_up_share": sum(1 for m in moves if m > 0) / n,
                "atm": atm,
                "base_vol_60d": base_vol,
                "implied_move": atm["implied_move"] if atm else None,
                # the straddle only measures the earnings jump when the option
                # expires right after it; otherwise strip the diffusive part
                "implied_jump": jump,
                "clean_expiry": (
                    datetime.strptime(expiry, "%Y-%m-%d").date()
                    - datetime.strptime(react, "%Y-%m-%d").date()
                ).days
                <= 2,
                "iv_rv_ratio": (
                    (jump if jump else atm["implied_move"]) / (sum(a) / n)
                    if atm and sum(a)
                    else None
                ),
                "calls": C.empirical_ev(ch, px, moves, "call"),
                "puts": C.empirical_ev(ch, px, moves, "put"),
                "history": hist[-12:],
            }
            results.append(rec)
            im = rec["implied_move"]
            if im:
                jt = f"{jump:6.2%}" if jump else "   n/a"
                print(
                    f"  {sym:6s} spot {px:8.2f} exp {expiry} "
                    f"{'wk' if rec['clean_expiry'] else 'LT'}  "
                    f"straddle {im:6.2%}  jump {jt}  "
                    f"hist |{sum(a)/n:5.2%}|  ratio {rec['iv_rv_ratio']:5.2f}  "
                    f"n={n:2d}  src={atm['source']}"
                )
            else:
                print(f"  {sym:6s} no ATM straddle priced")
        except Exception as exc:  # noqa: BLE001 - keep the sweep going
            print(f"  {sym}: FAILED {type(exc).__name__} {exc}", file=sys.stderr)
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    res = run(args or None)
    p = C.save("screen", res)
    print(f"\n{len(res)} names -> {p}")
