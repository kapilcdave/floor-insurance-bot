"""Does a name's trailing earnings-move history predict its next reaction?

Every "implied move is cheap vs history" ranking rests on this. If the trailing
mean is a biased-high predictor of the next |move|, then names whose implied
jump sits below their history are correctly priced, not mispriced, and the whole
screen is measuring mean reversion instead of edge.

Pooled OLS across all screened names:  |move_i| = a + b * trailing_mean_k + e
Null for a genuine predictor is b = 1 with a = 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import realized as R
import screen as S

HERE = Path(__file__).parent
K = 8  # trailing events used to form the forecast


def ols(xs: list[float], ys: list[float]) -> dict:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se_b = (s2 / sxx) ** 0.5
    return {
        "n": n,
        "a": a,
        "b": b,
        "se_b": se_b,
        "t_b_vs_0": b / se_b,
        "t_b_vs_1": (b - 1.0) / se_b,
        "r2": 1 - sum(r * r for r in resid) / sum((y - my) ** 2 for y in ys),
    }


def main() -> None:
    cm = R.cik_map()
    syms = [r["symbol"] for r in S.universe()]
    xs, ys, rows = [], [], []
    for sym in syms:
        cik = cm.get(sym)
        if not cik:
            continue
        try:
            hist = R.reaction_moves(sym, cik)
        except Exception:  # noqa: BLE001
            continue
        a = [abs(h["ret"]) for h in hist]
        for i in range(K, len(a)):
            trail = sum(a[i - K : i]) / K
            xs.append(trail)
            ys.append(a[i])
            rows.append({"sym": sym, "date": hist[i]["reaction_date"],
                         "trail": trail, "actual": a[i]})

    fit = ols(xs, ys)
    print(f"pooled events: {fit['n']}  (K={K} trailing, {len(set(r['sym'] for r in rows))} names)")
    print(f"  |move| = {fit['a']:+.4f} + {fit['b']:.3f} * trailing_mean")
    print(f"  b = {fit['b']:.3f} +/- {fit['se_b']:.3f}   "
          f"t(b>0) = {fit['t_b_vs_0']:+.2f}   t(b-1) = {fit['t_b_vs_1']:+.2f}")
    print(f"  R^2 = {fit['r2']:.3f}")

    mt, ma = sum(xs) / len(xs), sum(ys) / len(ys)
    print(f"\n  mean trailing forecast {mt:.2%}  vs  mean realized {ma:.2%}  "
          f"(forecast/realized = {mt/ma:.3f})")

    # does the *sign* of the last reaction predict the next one?
    same = tot = 0
    for sym in syms:
        cik = cm.get(sym)
        if not cik:
            continue
        try:
            hist = R.reaction_moves(sym, cik)
        except Exception:  # noqa: BLE001
            continue
        for i in range(1, len(hist)):
            if hist[i - 1]["ret"] == 0 or hist[i]["ret"] == 0:
                continue
            tot += 1
            same += (hist[i - 1]["ret"] > 0) == (hist[i]["ret"] > 0)
    p = same / tot
    se = (0.25 / tot) ** 0.5
    print(f"\n  sign persistence: {same}/{tot} = {p:.3f}  "
          f"(t vs 0.5 = {(p-0.5)/se:+.2f})")

    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "persistence.json").write_text(
        json.dumps({"fit": fit, "rows": rows}, indent=1)
    )


if __name__ == "__main__":
    main()
