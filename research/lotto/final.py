"""Final far-OTM earnings EV, built to inject no artifact of its own.

Construction, in the order the measurements forced it:
  * shape      - pooled 871 standardized log reactions, not one name's 18-42, so
                 the tail is estimated from more than the 1-2 events that reach it
  * magnitude  - each name's trailing mean shrunk by the fitted b=0.723, because
                 trailing mean is biased high for exactly the high-vol names that
                 screen "cheap" (t(b-1) = -4.20)
  * direction  - none: sign persistence is 0.501 (t=+0.07)
  * drift      - martingale-enforced, E[S_T] = S_0. Pooled log drift is +0.49%
                 at t=+1.32, and leaving any drift in tilts calls vs puts
  * space      - log, because arithmetic skew is +0.46 (t=+5.56) while log skew
                 is -0.16; mirroring arithmetic returns fattens the down tail and
                 alone makes every OTM put screen cheap
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import report as RP

HERE = Path(__file__).parent
CAL_A, CAL_B = 0.0247, 0.723


def z_draws() -> list[float]:
    """Standardized log reactions, recentred to zero mean."""
    raw = json.load(open(HERE / "out" / "pooled.json"))["z"]
    m = sum(raw) / len(raw)
    return [x - m for x in raw]


def move_draws(mean_abs: float, z: list[float]) -> list[float]:
    """Arithmetic reaction draws for a name whose history averages mean_abs.

    The scale is the shrunk forecast, and a constant is removed in log space so
    the resulting price distribution is a martingale.
    """
    fc = CAL_A + CAL_B * mean_abs  # calibrated mean |move|
    zbar = sum(abs(x) for x in z) / len(z)
    s = fc / zbar  # so that mean|log move| == fc
    logs = [x * s for x in z]
    drift = math.log(sum(math.exp(l) for l in logs) / len(logs))
    return [math.exp(l - drift) - 1.0 for l in logs]


def ev(strike: float, spot: float, side: str, cost: float, draws: list[float]) -> dict:
    pays = [
        max(0.0, spot * (1 + m) - strike) if side == "call" else max(0.0, strike - spot * (1 + m))
        for m in draws
    ]
    n = len(pays)
    gross = sum(pays) / n
    hits = sum(1 for p in pays if p > 0)
    wins = sum(1 for p in pays if p > cost)
    # bootstrap-free se of the mean payoff, then propagate to EV per dollar
    var = sum((p - gross) ** 2 for p in pays) / (n - 1)
    se = (var / n) ** 0.5
    return {
        "gross": gross,
        "ev": gross - cost,
        "ev_ratio": (gross - cost) / cost,
        "se_ratio": se / cost,
        "t": (gross - cost) / se if se > 0 else 0.0,
        "p_itm": hits / n,
        "p_profit": wins / n,
    }


def main() -> None:
    rows = json.load(open(HERE / "out" / "screen.json"))
    z = z_draws()
    lad = RP.ladder(rows)
    out = []
    for o in lad:
        ask = o.get("ask")
        if not ask or ask <= 0:
            continue
        draws = move_draws(o["hist_mean"], z)
        e = ev(o["strike"], o["spot"], o["side"], ask, draws)
        out.append({**o, **{f"f_{k}": v for k, v in e.items()}})

    out.sort(key=lambda r: -r["f_ev_ratio"])
    v = sorted(r["f_ev_ratio"] for r in out)
    pos = [r for r in out if r["f_ev_ratio"] > 0]
    sig = [r for r in pos if r["f_t"] > 2.0]

    print(f"{len(out)} far-OTM contracts, priced at the ask, "
          f"scored on 871 pooled reactions\n")
    print(f"  median EV per dollar : {v[len(v)//2]:+7.1%}")
    print(f"  mean                 : {sum(v)/len(v):+7.1%}")
    print(f"  positive EV          : {len(pos):3d}/{len(out)}")
    print(f"  positive at t > 2    : {len(sig):3d}/{len(out)}")

    print("\n--- EV by tail distance (multiples of the implied jump) ---")
    b: dict[str, list[float]] = {}
    for r in out:
        x = r.get("otm_in_jumps")
        if x is None:
            continue
        k = ("1.0-1.5x" if x < 1.5 else "1.5-2.0x" if x < 2.0
             else "2.0-3.0x" if x < 3.0 else "3.0x+")
        b.setdefault(k, []).append(r["f_ev_ratio"])
    for k in ["1.0-1.5x", "1.5-2.0x", "2.0-3.0x", "3.0x+"]:
        s = sorted(b.get(k, []))
        if s:
            print(f"  {k:9s} n={len(s):3d}  median {s[len(s)//2]:+7.1%}  "
                  f"mean {sum(s)/len(s):+7.1%}  best {s[-1]:+7.1%}")

    print("\n--- calls vs puts (checks the estimator is not tilted) ---")
    for side in ("call", "put"):
        s = sorted(r["f_ev_ratio"] for r in out if r["side"] == side)
        print(f"  {side:5s} n={len(s):3d}  median {s[len(s)//2]:+7.1%}  "
              f"mean {sum(s)/len(s):+7.1%}  positive "
              f"{sum(1 for x in s if x>0)}/{len(s)}")

    print("\n--- best 15 by EV per dollar ---")
    hdr = (f"{'sym':5s} {'sd':5s} {'strike':>8s} {'OTM':>6s} {'xjump':>6s} "
           f"{'ask':>6s} {'OI':>6s} {'P(ITM)':>7s} {'EV/$':>8s} {'t':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for r in out[:15]:
        print(f"{r['symbol']:5s} {r['side']:5s} {r['strike']:8.1f} {r['otm_pct']:6.1%} "
              f"{(r['otm_in_jumps'] or 0):6.2f} {r['ask']:6.2f} {(r['oi'] or 0):6d} "
              f"{r['f_p_itm']:7.1%} {r['f_ev_ratio']:+8.1%} {r['f_t']:+6.2f}")

    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "final.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nwrote out/final.json")


if __name__ == "__main__":
    main()
