"""Pooled, standardized earnings-reaction distribution.

A single name's 18-42 reactions cannot estimate a tail that only 1-2 of them
reach. Pooling all screened names' events after standardizing each by its own
scale gives 671 draws for the shape, and pooling across names also removes the
per-name direction that sign persistence (0.501) says is noise.

Symmetry is measured here, not assumed: an arithmetic-space mirror fattens the
down tail relative to the lognormal the market prices, which alone makes every
OTM put screen cheap.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import realized as R
import screen as S

HERE = Path(__file__).parent
K = 8


def moments(v: list[float]) -> dict:
    n = len(v)
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5
    sk = (sum(((x - m) / sd) ** 3 for x in v)) * n / ((n - 1) * (n - 2))
    ku = sum(((x - m) / sd) ** 4 for x in v) / n - 3
    se_sk = (6.0 / n) ** 0.5
    return {"n": n, "mean": m, "sd": sd, "skew": sk, "t_skew": sk / se_sk, "xkurt": ku}


def build() -> dict:
    cm = R.cik_map()
    arith, logr, z = [], [], []
    per_name = {}
    for sym in [r["symbol"] for r in S.universe()]:
        cik = cm.get(sym)
        if not cik:
            continue
        try:
            hist = R.reaction_moves(sym, cik)
        except Exception:  # noqa: BLE001
            continue
        if len(hist) < 8:
            continue
        rets = [h["ret"] for h in hist]
        scale = sum(abs(r) for r in rets) / len(rets)
        per_name[sym] = {"n": len(rets), "mean_abs": scale}
        for r in rets:
            arith.append(r)
            logr.append(math.log1p(r))
            if scale > 0:
                z.append(math.log1p(r) / scale)  # standardized log reaction
    return {"arith": arith, "log": logr, "z": z, "per_name": per_name}


def main() -> None:
    d = build()
    print("pooled earnings-reaction moments")
    for label, v in (("arithmetic return", d["arith"]), ("log return", d["log"])):
        m = moments(v)
        print(
            f"  {label:18s} n={m['n']:4d}  mean {m['mean']:+7.3%}  sd {m['sd']:6.2%}  "
            f"skew {m['skew']:+6.3f} (t={m['t_skew']:+5.2f})  xkurt {m['xkurt']:+6.2f}"
        )

    zm = moments(d["z"])
    print(
        f"\n  standardized log z  n={zm['n']}  mean {zm['mean']:+.4f}  sd {zm['sd']:.4f}  "
        f"skew {zm['skew']:+.3f} (t={zm['t_skew']:+.2f})  xkurt {zm['xkurt']:+.2f}"
    )

    zs = sorted(d["z"])
    qs = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    print("\n  z quantiles (units of the name's own mean |move|, log space):")
    print("   " + "  ".join(f"p{int(q*100):02d}={zs[int(q*(len(zs)-1))]:+6.2f}" for q in qs))

    up = [x for x in d["z"] if x > 0]
    dn = [-x for x in d["z"] if x < 0]
    print(f"\n  up tail  n={len(up)} mean {sum(up)/len(up):.3f}  "
          f"p95={sorted(up)[int(0.95*(len(up)-1))]:.2f}")
    print(f"  dn tail  n={len(dn)} mean {sum(dn)/len(dn):.3f}  "
          f"p95={sorted(dn)[int(0.95*(len(dn)-1))]:.2f}")

    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "pooled.json").write_text(json.dumps({"z": d["z"], "per_name": d["per_name"]}))
    print(f"\n  wrote {len(d['z'])} standardized draws -> out/pooled.json")


if __name__ == "__main__":
    main()
