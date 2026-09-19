"""Which history predicts the next earnings reaction: all of it, or the recent part?

`final.py` shrinks a name's mean |move| by the fitted b=0.723 before pricing the
tail, but that coefficient came out of `persistence.py` regressed on a *trailing
8-event* mean while `final.py` feeds it the *full-sample* mean. The two differ
whenever a name's reaction regime moved, which is precisely the interesting case:
ORCL's full-sample mean is 7.06% against 12.50% over its last twelve prints.

So run the horse race on the pooled panel, strictly causally: at event i, form
each candidate forecast from events < i only, and score it against |move_i|.
Standard errors are clustered by name, because one name contributes many rows and
its regime shifts are shared across them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import realized as R
import screen as S

HERE = Path(__file__).parent
MIN_PRIOR = 8  # need this many past events before a forecast is defined


def ols_clustered(xs: list[float], ys: list[float], gs: list[str]) -> dict:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    # cluster-robust meat: sum over groups of (sum_i x~_i e_i)^2
    meat: dict[str, float] = {}
    for x, e, g in zip(xs, resid, gs):
        meat[g] = meat.get(g, 0.0) + (x - mx) * e
    se_b = (sum(v * v for v in meat.values()) / sxx**2) ** 0.5
    rmse = (sum(r * r for r in resid) / n) ** 0.5
    return {
        "n": n,
        "n_clusters": len(set(gs)),
        "a": a,
        "b": b,
        "se_b": se_b,
        "t_b_vs_1": (b - 1.0) / se_b if se_b else 0.0,
        "r2": 1 - sum(r * r for r in resid) / sum((y - my) ** 2 for y in ys),
        "rmse": rmse,
    }


def panel() -> dict[str, list]:
    """Causal forecast/outcome rows for every screened name."""
    cm = R.cik_map()
    cols: dict[str, list] = {k: [] for k in ("sym", "y", "full", "t8", "t4", "t12")}
    for sym in [r["symbol"] for r in S.universe()]:
        cik = cm.get(sym)
        if not cik:
            continue
        try:
            hist = R.reaction_moves(sym, cik)
        except Exception:  # noqa: BLE001 - a missing name must not stop the panel
            continue
        a = [abs(h["ret"]) for h in hist]
        for i in range(MIN_PRIOR, len(a)):
            past = a[:i]
            cols["sym"].append(sym)
            cols["y"].append(a[i])
            cols["full"].append(sum(past) / len(past))
            for k, w in (("t4", 4), ("t8", 8), ("t12", 12)):
                win = past[-w:]
                cols[k].append(sum(win) / len(win))
    return cols


def main() -> None:
    c = panel()
    print(f"pooled panel: {len(c['y'])} events over {len(set(c['sym']))} names, "
          f"each forecast built from prior events only\n")

    hdr = f"{'forecast':10s} {'b':>7s} {'se':>6s} {'t(b-1)':>7s} {'a':>8s} {'R2':>7s} {'RMSE':>7s}"
    print(hdr)
    print("-" * len(hdr))
    fits = {}
    for k, label in (("full", "full-hist"), ("t12", "trailing12"),
                     ("t8", "trailing8"), ("t4", "trailing4")):
        f = ols_clustered(c[k], c["y"], c["sym"])
        fits[k] = f
        print(f"{label:10s} {f['b']:7.3f} {f['se_b']:6.3f} {f['t_b_vs_1']:+7.2f} "
              f"{f['a']:8.4f} {f['r2']:7.4f} {f['rmse']:7.4f}")

    best = min(fits, key=lambda k: fits[k]["rmse"])
    print(f"\nlowest RMSE: {best}")

    # Does adding the recent window to the full history help? Two-regressor fit
    # by hand: regress y on [full, t8] via the normal equations.
    y, x1, x2 = c["y"], c["full"], c["t8"]
    n = len(y)
    m1, m2, my = sum(x1) / n, sum(x2) / n, sum(y) / n
    s11 = sum((a - m1) ** 2 for a in x1)
    s22 = sum((b - m2) ** 2 for b in x2)
    s12 = sum((a - m1) * (b - m2) for a, b in zip(x1, x2))
    s1y = sum((a - m1) * (v - my) for a, v in zip(x1, y))
    s2y = sum((b - m2) * (v - my) for b, v in zip(x2, y))
    det = s11 * s22 - s12 * s12
    b1 = (s22 * s1y - s12 * s2y) / det
    b2 = (s11 * s2y - s12 * s1y) / det
    a0 = my - b1 * m1 - b2 * m2
    res = [v - (a0 + b1 * p + b2 * q) for v, p, q in zip(y, x1, x2)]
    rmse2 = (sum(r * r for r in res) / n) ** 0.5
    corr = s12 / math.sqrt(s11 * s22)
    print(f"\njoint fit  y = {a0:.4f} + {b1:.3f}*full + {b2:.3f}*trail8   "
          f"RMSE {rmse2:.4f}   corr(full,t8) = {corr:.3f}")

    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "regime.json").write_text(
        json.dumps({"fits": fits, "joint": {"a": a0, "b_full": b1, "b_t8": b2,
                                            "rmse": rmse2, "corr": corr}}, indent=1)
    )

    print("\n--- what each forecast says about this week's names ---")
    rows = json.load(open(HERE / "out" / "screen.json"))
    f_full, f_t8 = fits["full"], fits[best]
    h2 = (f"{'sym':6s} {'implied':>8s} {'fullmean':>9s} {'trail8':>8s} "
          f"{'fc(full)':>9s} {'fc(best)':>9s} {'ratio_old':>10s} {'ratio_new':>10s}")
    print(h2)
    print("-" * len(h2))
    cm = R.cik_map()
    for r in sorted(rows, key=lambda r: r["symbol"]):
        jump = r.get("implied_jump")
        if not jump:
            continue
        a = [abs(h["ret"]) for h in R.reaction_moves(r["symbol"], cm[r["symbol"]])]
        full = sum(a) / len(a)
        t8 = sum(a[-8:]) / len(a[-8:])
        xb = {"full": full, "t12": sum(a[-12:]) / len(a[-12:]),
              "t8": t8, "t4": sum(a[-4:]) / len(a[-4:])}[best]
        fc_old = f_full["a"] + f_full["b"] * full
        fc_new = f_t8["a"] + f_t8["b"] * xb
        print(f"{r['symbol']:6s} {jump:8.2%} {full:9.2%} {t8:8.2%} "
              f"{fc_old:9.2%} {fc_new:9.2%} {jump/fc_old:10.2f} {jump/fc_new:10.2f}")


if __name__ == "__main__":
    main()
