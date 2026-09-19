"""Rank the far-OTM earnings strikes by empirical expectation.

Only names whose front expiry lands within two sessions of the reaction are
scored: for a longer-dated option the payoff calculation ignores the time value
still left after the print, so it understates the option and the ranking would
be measuring DTE rather than edge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
MIN_OTM = 0.10  # "deep" starts here
MIN_OI = 20


def load() -> list[dict]:
    return json.load(open(HERE / "out" / "screen.json"))


def ladder(rows: list[dict], only_clean: bool = True) -> list[dict]:
    out = []
    for r in rows:
        if only_clean and not r.get("clean_expiry"):
            continue
        if not r.get("implied_move"):
            continue
        for side in ("calls", "puts"):
            for o in r[side]:
                if o["otm_pct"] < MIN_OTM or (o["oi"] or 0) < MIN_OI:
                    continue
                if o["cost"] < 0.02:
                    continue
                out.append(
                    {
                        "symbol": r["symbol"],
                        "side": side[:-1],
                        "expiry": r["expiry"],
                        "spot": r["spot"],
                        "implied_jump": r.get("implied_jump"),
                        "hist_mean": r["hist_mean_abs"],
                        "ratio": r["iv_rv_ratio"],
                        "otm_in_jumps": (
                            o["otm_pct"] / r["implied_jump"]
                            if r.get("implied_jump")
                            else None
                        ),
                        **o,
                    }
                )
    return out


def fmt(rows: list[dict], n: int = 22) -> str:
    hdr = (
        f"{'sym':6s} {'sd':4s} {'strike':>8s} {'OTM%':>6s} {'x jump':>6s} "
        f"{'cost':>6s} {'src':>5s} {'ask':>6s} {'OI':>7s} {'BE move':>8s} "
        f"{'hit':>7s} {'win':>7s} {'EV/$':>7s} {'EV-1':>7s} {'n':>3s}"
    )
    lines = [hdr, "-" * len(hdr)]
    for o in rows[:n]:
        lines.append(
            f"{o['symbol']:6s} {o['side']:4s} {o['strike']:8.1f} "
            f"{o['otm_pct']:6.1%} "
            f"{(o['otm_in_jumps'] or 0):6.2f} "
            f"{o['cost']:6.2f} {o['cost_src']:>5s} "
            f"{(o['ask'] if o['ask'] is not None else float('nan')):6.2f} "
            f"{(o['oi'] or 0):7d} {o['breakeven_move']:+8.1%} "
            f"{o['hist_itm_count']:2d}/{o['n']:<4d} "
            f"{o['hist_profit_count']:2d}/{o['n']:<4d} "
            f"{o['ev_ratio']:+7.1%} "
            f"{(o['ev_ratio_drop1'] if o['ev_ratio_drop1'] is not None else 0):+7.1%} "
            f"{(o['ev_ratio_sym'] if o['ev_ratio_sym'] is not None else 0):+7.1%} "
            f"{o['n']:3d}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    rows = load()
    lad = ladder(rows)
    print(f"{len(lad)} far-OTM contracts (OTM>={MIN_OTM:.0%}, OI>={MIN_OI}) "
          f"across {len({l['symbol'] for l in lad})} weekly-expiry names\n")

    pos = [o for o in lad if o["ev_ratio"] > 0]
    print(f"positive empirical EV at the official close: {len(pos)}/{len(lad)}")
    surv = [o for o in pos if not o["one_event_driven"]]
    print(f"  ... surviving the drop-one-event jackknife: {len(surv)}")
    ask_pos = [o for o in surv if (o.get("ev_ratio_at_ask") or -1) > 0]
    print(f"  ... still positive paying the indicative ask: {len(ask_pos)}")
    sym_pos = [o for o in ask_pos if (o.get("ev_ratio_sym") or -1) > 0]
    print(f"  ... surviving the sign-symmetry control:      {len(sym_pos)}")
    print(f"      (killed by direction alone: "
          f"{sum(1 for o in lad if o.get('sign_driven'))} of {len(pos)} positives)\n")

    print("=== ranked by empirical EV per dollar (close-based) ===")
    print(fmt(sorted(lad, key=lambda o: -o["ev_ratio"])))

    if surv:
        print("\n=== jackknife survivors ===")
        print(fmt(sorted(surv, key=lambda o: -o["ev_ratio"])))

    dist = sorted(o["ev_ratio"] for o in lad)
    m = dist[len(dist) // 2]
    print(f"\nmedian EV per dollar across all {len(dist)} far strikes: {m:+.1%}")
    print(f"mean: {sum(dist)/len(dist):+.1%}   best {dist[-1]:+.1%}  worst {dist[0]:+.1%}")
