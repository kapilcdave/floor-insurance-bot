"""Far-OTM call EV under both surviving move forecasts, reported as a bracket.

`final.py` shrank the full-sample mean |move| by b=0.723 — but that coefficient
was fitted against a trailing-8 mean, so the shrinkage was applied to the wrong
regressor. `regime.py` refits both matched and finds them tied on out-of-sample
RMSE (0.0609 full-history vs 0.0608 trailing-8) while disagreeing by up to 2x on
individual names: ORCL reads 1.45x rich on one and 1.08x on the other.

A tie means the per-name forecast is not identified, so quoting one EV number is
false precision. Every contract is therefore scored under both, and what is
reported is the interval. A contract only earns a verdict where both agree.

Shape still comes from the pooled 871 standardized reactions, direction from
nothing (sign persistence 0.501), and the draws are martingale-enforced in log
space -- all as in `final.py`, which those choices were forced by.
"""

from __future__ import annotations

import json
from pathlib import Path

import final as F
import realized as R
import report as RP

HERE = Path(__file__).parent


def main() -> None:
    rows = json.load(open(HERE / "out" / "screen.json"))
    fits = json.load(open(HERE / "out" / "regime.json"))["fits"]
    z = F.z_draws()
    cm = R.cik_map()

    # per-name forecast under each matched (regressor, coefficients) pair
    fc: dict[str, dict[str, float]] = {}
    for r in rows:
        sym = r["symbol"]
        a = [abs(h["ret"]) for h in R.reaction_moves(sym, cm[sym])]
        x = {"full": sum(a) / len(a), "t8": sum(a[-8:]) / len(a[-8:])}
        fc[sym] = {k: fits[k]["a"] + fits[k]["b"] * x[k] for k in ("full", "t8")}

    out = []
    for o in RP.ladder(rows):
        ask = o.get("ask")
        if not ask or ask <= 0 or o["side"] != "call":
            continue
        # move_draws applies the b=0.723 shrinkage internally, so hand it the
        # already-shrunk forecast inverted back onto its own scale
        evs = {}
        for k in ("full", "t8"):
            scale = (fc[o["symbol"]][k] - F.CAL_A) / F.CAL_B
            evs[k] = F.ev(o["strike"], o["spot"], "call", ask,
                          F.move_draws(scale, z))
        lo, hi = sorted((evs["full"]["ev_ratio"], evs["t8"]["ev_ratio"]))
        out.append({**o, "ev_full": evs["full"], "ev_t8": evs["t8"],
                    "ev_lo": lo, "ev_hi": hi,
                    "both_positive": lo > 0,
                    "p_itm": max(evs["full"]["p_itm"], evs["t8"]["p_itm"])})

    out.sort(key=lambda r: -r["ev_hi"])
    print(f"{len(out)} far-OTM calls, priced at the indicative ask\n")
    print(f"  positive under BOTH forecasts : "
          f"{sum(1 for r in out if r['both_positive'])}/{len(out)}")
    print(f"  positive under either         : "
          f"{sum(1 for r in out if r['ev_hi'] > 0)}/{len(out)}")

    print("\n--- EV bracket by distance in implied jumps ---")
    b: dict[str, list] = {}
    for r in out:
        x = r.get("otm_in_jumps")
        if x is None:
            continue
        k = ("1.0-1.5x" if x < 1.5 else "1.5-2.0x" if x < 2.0
             else "2.0-3.0x" if x < 3.0 else "3.0x+")
        b.setdefault(k, []).append(r)
    for k in ["1.0-1.5x", "1.5-2.0x", "2.0-3.0x", "3.0x+"]:
        s = b.get(k) or []
        if not s:
            continue
        med = lambda key: sorted(x[key] for x in s)[len(s) // 2]  # noqa: E731
        print(f"  {k:9s} n={len(s):3d}  median EV {med('ev_lo'):+7.1%} .. "
              f"{med('ev_hi'):+7.1%}   best-case best {max(x['ev_hi'] for x in s):+7.1%}")

    print("\n--- every call in the 1.0-2.0x band, ranked by best-case EV ---")
    hdr = (f"{'sym':5s} {'strike':>7s} {'OTM':>6s} {'xjmp':>5s} {'bid':>5s} {'ask':>5s} "
           f"{'spr':>6s} {'OI':>6s} {'BE':>7s} {'P(ITM)':>7s} {'EV bracket':>19s} {'both+':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for r in out:
        x = r.get("otm_in_jumps")
        if x is None or not (1.0 <= x < 2.0):
            continue
        bid = r.get("bid") or 0.0
        spr = (r["ask"] - bid) / ((r["ask"] + bid) / 2) if bid else float("nan")
        print(f"{r['symbol']:5s} {r['strike']:7.1f} {r['otm_pct']:6.1%} {x:5.2f} "
              f"{bid:5.2f} {r['ask']:5.2f} {spr:6.0%} {(r['oi'] or 0):6d} "
              f"{r['breakeven_move']:+7.1%} {r['p_itm']:7.1%} "
              f"{r['ev_lo']:+8.1%} ..{r['ev_hi']:+8.1%} "
              f"{'yes' if r['both_positive'] else 'no':>6s}")

    (HERE / "out" / "ev_matched.json").write_text(json.dumps(out, indent=1, default=str))
    print("\nwrote out/ev_matched.json")


if __name__ == "__main__":
    main()
