"""Option-chain pricing for the earnings-week lottery-ticket screen.

Two numbers matter per name: what the market charges for the move (the ATM
straddle) and what each far strike would have paid across that same name's own
realized earnings reactions. The second is an empirical expectation over ~35-40
historical events, so it is quoted with its sample size, never alone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode

from realized import ALPACA_HDRS, HERE, get_json

CONTRACTS = "https://paper-api.alpaca.markets/v2/options/contracts"
SNAPSHOTS = "https://data.alpaca.markets/v1beta1/options/snapshots"
STOCK_SNAP = "https://data.alpaca.markets/v2/stocks/snapshots"
FEED = os.environ.get("OPTIONS_FEED", "indicative")


def spot(symbol: str) -> tuple[float, str]:
    """Free tier has no real-time SIP; delayed_sip is the whole tape, 15m back."""
    doc = get_json(f"{STOCK_SNAP}?symbols={symbol}&feed=delayed_sip", ALPACA_HDRS)
    s = doc.get(symbol) or {}
    bar = s.get("dailyBar") or s.get("prevDailyBar") or {}
    trade = s.get("latestTrade") or {}
    if trade.get("p"):
        return float(trade["p"]), trade.get("t", "")[:19]
    return float(bar.get("c", 0.0)), (bar.get("t", "")[:10])


def expiries(symbol: str, on_or_after: str, limit: int = 400) -> list[str]:
    q = {
        "underlying_symbols": symbol,
        "expiration_date_gte": on_or_after,
        "limit": str(limit),
        "type": "call",
    }
    doc = get_json(f"{CONTRACTS}?{urlencode(q)}", ALPACA_HDRS)
    return sorted({c["expiration_date"] for c in doc.get("option_contracts", [])})


def contracts_for(symbol: str, expiry: str) -> dict[str, dict]:
    """Official prior close + open interest, keyed by OCC symbol."""
    out: dict[str, dict] = {}
    token = None
    while True:
        q = {
            "underlying_symbols": symbol,
            "expiration_date": expiry,
            "limit": "1000",
        }
        if token:
            q["page_token"] = token
        doc = get_json(f"{CONTRACTS}?{urlencode(q)}", ALPACA_HDRS)
        for c in doc.get("option_contracts", []):
            out[c["symbol"]] = {
                "strike": float(c["strike_price"]),
                "type": c["type"],
                "oi": int(c["open_interest"] or 0),
                "oi_date": c.get("open_interest_date"),
                "close": float(c["close_price"]) if c.get("close_price") else None,
                "close_date": c.get("close_price_date"),
            }
        token = doc.get("next_page_token")
        if not token:
            break
    return out


def quotes_for(symbol: str, expiry: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    token = None
    while True:
        q = {"feed": FEED, "expiration_date": expiry, "limit": "1000"}
        if token:
            q["page_token"] = token
        doc = get_json(f"{SNAPSHOTS}/{symbol}?{urlencode(q)}", ALPACA_HDRS)
        for k, v in (doc.get("snapshots") or {}).items():
            lq = v.get("latestQuote") or {}
            out[k] = {
                "bid": lq.get("bp"),
                "ask": lq.get("ap"),
                "bid_sz": lq.get("bs"),
                "ask_sz": lq.get("as"),
                "iv": v.get("impliedVolatility"),
                "delta": (v.get("greeks") or {}).get("delta"),
                "quote_t": lq.get("t", "")[:19],
            }
        token = doc.get("next_page_token")
        if not token:
            break
    return out


def build_chain(symbol: str, expiry: str) -> list[dict]:
    con, qt = contracts_for(symbol, expiry), quotes_for(symbol, expiry)
    rows = []
    for occ, c in con.items():
        q = qt.get(occ, {})
        bid, ask = q.get("bid"), q.get("ask")
        mid = (bid + ask) / 2 if (bid is not None and ask) else None
        rows.append({"occ": occ, **c, **q, "mid": mid})
    return sorted(rows, key=lambda r: (r["type"], r["strike"]))


STRADDLE_K = 0.79788456  # E|Z| for a standard normal; straddle = K*S*sigma*sqrt(T)


def atm_straddle(
    chain: list[dict], spot_px: float, years: float, fresh: str | None = None
) -> dict | None:
    """Implied move at the strike nearest spot, priced three independent ways.

    close_price can be stale by weeks (GWRE priced a 103.50 straddle on a 69%-IV
    name), so the analytic IV figure is the anchor and the print-based ones are
    only trusted when they agree with it.
    """
    calls = {r["strike"]: r for r in chain if r["type"] == "call"}
    puts = {r["strike"]: r for r in chain if r["type"] == "put"}
    common = sorted(set(calls) & set(puts), key=lambda k: abs(k - spot_px))
    for k in common:
        c, p = calls[k], puts[k]
        civ, piv = c.get("iv"), p.get("iv")
        if not (civ and piv):
            continue
        iv = (civ + piv) / 2
        analytic = STRADDLE_K * iv * (years**0.5)

        cc, pc = c.get("close"), p.get("close")
        stale = fresh is not None and fresh not in (
            c.get("close_date") or "",
            p.get("close_date") or "",
        )
        from_close = (cc + pc) / spot_px if (cc and pc and not stale) else None

        cm, pm = c.get("mid"), p.get("mid")
        from_mid = (cm + pm) / spot_px if (cm and pm) else None

        # accept a print only when it lands within 25% of the analytic value
        def ok(v):
            return v is not None and abs(v - analytic) / analytic < 0.25

        if ok(from_close):
            src, im = "close", from_close
        elif ok(from_mid):
            src, im = "mid", from_mid
        else:
            src, im = "analytic_iv", analytic

        return {
            "strike": k,
            "source": src,
            "implied_move": im,
            "im_analytic": analytic,
            "im_close": from_close,
            "im_mid": from_mid,
            "close_stale": stale,
            "atm_iv": iv,
            "call_iv": civ,
            "put_iv": piv,
        }
    return None


def implied_jump(im_total: float, years: float, base_vol: float) -> float | None:
    """Strip the diffusive component so a 14-day straddle is comparable to a
    one-session earnings move. base_vol is annualized; realized vol understates
    forward base vol, which overstates the jump -- conservative for a buyer."""
    tot_var = (im_total / STRADDLE_K) ** 2  # = sigma_total^2 * T
    base_var = (base_vol**2) * years
    if tot_var <= base_var:
        return None
    return STRADDLE_K * ((tot_var - base_var) ** 0.5)


    # pooled fit from persistence.py over 671 events, 25 names
CAL_A, CAL_B = 0.0247, 0.723


def calibrated_moves(moves: list[float]) -> list[float]:
    """Shrink the raw history toward the pooled forecast and drop the sign.

    Trailing mean |move| regresses on the next |move| with b=0.723 (t(b-1)=-4.2),
    so a high-vol name's own history overstates its next reaction; sign
    persistence is 0.501 (t=+0.07), so direction carries nothing. The honest
    forecast distribution is therefore the magnitudes rescaled to the fitted
    mean and mirrored around zero.
    """
    n = len(moves)
    mean_abs = sum(abs(m) for m in moves) / n
    if mean_abs <= 0:
        return list(moves)
    scale = (CAL_A + CAL_B * mean_abs) / mean_abs
    return [m * scale for m in moves] + [-m * scale for m in moves]


def empirical_ev(
    rows: list[dict], spot_px: float, moves: list[float], side: str
) -> list[dict]:
    """Payoff of each OTM strike averaged over the name's own reaction history."""
    n = len(moves)
    out = []
    for r in rows:
        if r["type"] != side:
            continue
        k = r["strike"]
        otm = (k / spot_px - 1) if side == "call" else (1 - k / spot_px)
        if otm <= 0.005:
            continue
        # a real print vs a synthetic ask: EV is reported against both, because a
        # buyer pays the ask and the indicative feed's ask may not be transactable
        c_close, c_ask = r.get("close"), r.get("ask")
        cost = c_close or c_ask or r.get("mid")
        if not cost or cost <= 0:
            continue
        def payoff(m: float) -> float:
            s1 = spot_px * (1 + m)
            return max(0.0, s1 - k) if side == "call" else max(0.0, k - s1)

        pays = [payoff(m) for m in moves]
        # Sign control: reusing the signed history bets that the DIRECTION of past
        # reactions repeats. Nothing supports that, so the same magnitudes are
        # re-scored with both signs; edge that dies here was a directional artifact.
        sym = [payoff(m) for m in moves] + [payoff(-m) for m in moves]
        ev_sym = (sum(sym) / len(sym)) - cost
        # the estimator that actually respects both measured facts
        cal = [payoff(m) for m in calibrated_moves(moves)]
        gross_cal = sum(cal) / len(cal)
        ev_cal = gross_cal - cost
        ev_cal_ask = (gross_cal - c_ask) if c_ask else None
        hits = sum(1 for p in pays if p > 0)
        wins = sum(1 for p in pays if p > cost)
        gross = sum(pays) / n
        ev = gross - cost
        ev_at_ask = (gross - c_ask) if c_ask else None
        # a far-strike EV is usually carried by one or two events; if dropping the
        # single largest payoff flips the sign, the edge is that event, not a rate
        drop1 = (
            ((sum(pays) - max(pays)) / (n - 1)) - cost if n > 1 else None
        )
        out.append(
            {
                "occ": r["occ"],
                "strike": k,
                "otm_pct": otm,
                "cost": cost,
                "cost_src": "close" if c_close else ("ask" if c_ask else "mid"),
                "gross_ev": gross,
                "ev_ratio_at_ask": (ev_at_ask / c_ask) if ev_at_ask is not None else None,
                "ev_ratio_sym": ev_sym / cost,
                "sign_driven": ev > 0 and ev_sym <= 0,
                "ev_ratio_cal": ev_cal / cost,
                "ev_ratio_cal_ask": (
                    ev_cal_ask / c_ask if ev_cal_ask is not None else None
                ),
                "bid": r.get("bid"),
                "ask": r.get("ask"),
                "close": r.get("close"),
                "oi": r.get("oi"),
                "iv": r.get("iv"),
                "delta": r.get("delta"),
                "breakeven_move": (
                    (k + cost) / spot_px - 1 if side == "call" else 1 - (k - cost) / spot_px
                ),
                "hist_itm": hits / n,
                "hist_itm_count": hits,
                "hist_profit": wins / n,
                "hist_profit_count": wins,
                "ev_per_contract": ev * 100,
                "ev_ratio": ev / cost,
                "ev_ratio_drop1": (drop1 / cost) if drop1 is not None else None,
                "one_event_driven": (
                    drop1 is not None and ev > 0 and drop1 <= 0
                ),
                "n": n,
            }
        )
    return out


def save(name: str, obj) -> Path:
    p = HERE / "out" / f"{name}.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=str))
    return p
