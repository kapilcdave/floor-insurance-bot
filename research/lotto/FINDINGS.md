# Far-OTM earnings lottery tickets, week of 2026-08-31

Verdict: **the structure is negative EV at every distance, and gets monotonically
worse the deeper you go.** Median expectation across 634 far-OTM contracts on 16
names is **−62.7% per dollar** paid at the ask. One contract of 634 reaches
t > 2; pure noise over 634 tests would produce ~14.4.

## Data

| piece | source | note |
|---|---|---|
| earnings calendar | Nasdaq `api/calendar/earnings` | 32 names ≥ $5B reporting Aug 31 – Sep 4 |
| earnings dates + session | SEC EDGAR 8-K **Item 2.02** + acceptance timestamp | authoritative; ≥16:00 ET ⇒ next session reacts |
| price history | Alpaca `v2/stocks/bars`, feed=sip, adjustment=all | back to 2016; 871 pooled reactions |
| chains | Alpaca `v1beta1/options/snapshots` + `v2/options/contracts` | IV, greeks, official close, OI |
| spot | Alpaca snapshots, **feed=delayed_sip** | real-time `sip` is 403 on this tier |

Free tier: no OPRA (`"OPRA agreement is not signed"`), so quotes are the
**indicative** feed — modelled, not necessarily transactable. Every EV here is
computed at that ask, which is the buyer-conservative choice.

## Implied jump vs the name's own history (weekly expiries only)

Median ratio **1.08**; 5 of 16 below 1.0. The market charges slightly more than
the average realized reaction — a modest premium, not a giveaway.

Cheapest by ratio: IOT 0.80, CRDO 0.81, PL 0.84, SNOW 0.95, DOCU 0.97.
Richest: HPE 2.21, AVGO 1.26, MDB 1.18.

## Why the "high IV, big move" premise fails

Standardized log reactions are **essentially Gaussian**: excess kurtosis +0.67,
and sd / mean|·| = 1.273 against 1.2533 for a normal. There is no fat tail to
buy. Black–Scholes at the right vol is close to the right model, so the far
strikes are priced against a roughly correct distribution and the buyer is left
paying the premium with no tail compensation.

EV per dollar at the ask, by distance in multiples of the implied jump:

| distance | n | median EV | mean EV |
|---|---|---|---|
| 1.0–1.5× | 146 | −41.8% | −40.2% |
| 1.5–2.0× | 128 | −48.5% | −46.7% |
| 2.0–3.0× | 190 | −64.8% | −60.2% |
| 3.0×+ | 170 | **−98.6%** | −87.5% |

## Six traps that each flipped a verdict

1. **Stale `close_price`.** GWRE priced a 103.50 straddle on a 69%-IV name — a
   50.3% implied move. Cross-checking the print against
   `0.7979·σ·√T` and rejecting >25% disagreement cut it to 13.1%. TTC too.
2. **A 14-day straddle is not a one-day jump.** Eight names have no weekly after
   earnings; their raw ratios ran to 2.84×. Stripping the diffusive component
   (`jump² = T(σ_impl² − σ_base²)`) brought CPRT 2.84→2.01, GWRE 8.12→1.37.
3. **Signed history bakes in direction.** Sign persistence is **0.501,
   t = +0.07** — a coin flip. Symmetrizing killed 65 of 110 apparent positives.
4. **Trailing mean is biased high.** Pooled `|move| = 0.0247 + 0.723·trailing`,
   **t(b−1) = −4.20**. High-vol names mean-revert, so "implied < history" is
   mostly correct pricing, not mispricing. This is what makes IOT/CRDO/PL screen
   cheap.
5. **Symmetrize in log space, not arithmetic.** Arithmetic skew is +0.461
   (t=+5.56), log skew −0.159. Mirroring arithmetic returns fattens the down tail
   and by itself made **all 76** survivors puts.
6. **Enforce the martingale.** Pooled log drift is +0.49% at t=+1.32 — not
   significant. Leaving it in tilts calls against puts. Final draws satisfy
   E[S_T]/S₀ − 1 = 0.00000.

## The one non-negative observation

Puts score ~14 points better than calls (median −53.3% vs −67.8%). A zero-skew
placebo **flips** it (−64.6% put vs −55.7% call), so the whole gap is the
measured log skew of −0.250 (t = −3.02) exceeding what the chains price. That is
a *relative* statement — long put skew against call skew — not a lottery ticket.
Both sides remain firmly negative outright.

## Files

- `realized.py` — EDGAR earnings dates → reaction sessions → realized moves
- `chain.py` — chain assembly, validated straddle, jump extraction, EV
- `screen.py` — per-name driver, base vol, universe (`LOTTO_FROM`/`LOTTO_TO` pick the week)
- `persistence.py` — the b=0.723 / sign-0.501 regressions
- `pooled.py` — 871 standardized reactions, symmetry and kurtosis
- `final.py` — artifact-controlled EV; writes `out/final.json`
- `regime.py` — trailing vs full-history horse race; writes `out/regime.json`
- `ev_matched.py` — call EV as a two-forecast bracket; writes `out/ev_matched.json`

## Reproduce

```bash
cd /Users/kapil/proj/options && set -a && . ./.env && set +a && cd research/lotto
python3 screen.py && python3 persistence.py && python3 pooled.py && python3 final.py
# forward week, calls only, bracketed EV:
export LOTTO_FROM=2026-09-08 LOTTO_TO=2026-09-18
python3 screen.py && python3 regime.py && python3 ev_matched.py
```

---

# Second week, 2026-09-08 – 2026-09-18: replicates, plus trap 7

20 names ≥ $5B on the Nasdaq calendar, 12 with ≥ 8 SEC-dated reactions, 8 with an
expiry within two sessions of the print. Verdict is unchanged and now
**0 of 105 far-OTM calls are positive under both surviving forecasts**; 2 are
positive under one. Best case anywhere in the buyable band is ORCL 177.5C at
**−34%** per dollar, ADBE 295C at **−43%**.

| distance | n | median EV bracket | best case |
|---|---|---|---|
| 1.0–1.5× | 10 | −77.4% .. −45.8% | −34.0% |
| 1.5–2.0× | 16 | −85.8% .. −55.9% | −3.3% |
| 2.0–3.0× | 30 | −91.3% .. −66.8% | +23.9% |
| 3.0×+ | 49 | −100.0% .. −99.8% | −39.8% |

## Trap 7: the shrinkage was applied to the wrong regressor

`final.py` shrinks a name's mean |move| by `0.0247 + 0.723·x` — but
`persistence.py` fitted that on a **trailing-8** mean while `final.py` passes the
**full-sample** mean. The two differ by up to 2× on exactly the names where the
reaction regime moved, and the error is signed per name, not uniform:

| name | implied jump | full-hist mean | trailing 8 | ratio, full | ratio, trailing |
|---|---|---|---|---|---|
| ORCL | 10.97% | 7.06% | 12.38% | 1.45 (rich) | **1.08** (fair) |
| GME | 7.07% | 10.96% | 5.27% | **0.67** (cheap) | 1.20 (rich) |
| M | 7.92% | 7.35% | 3.91% | 1.02 | 1.55 |
| ADBE | 7.09% | 5.71% | 7.27% | 1.08 | 1.00 |

Refitting both matched, clustered by name over 407 causal panel rows, they are
**tied**: RMSE 0.0609 full-history vs 0.0608 trailing-8 (trailing-4 is worse at
0.0617, and a joint fit reaches only 0.0606 with corr(full, t8) = 0.874). Both
still reject `b = 1` (t = −2.16 and −5.39), so the *level* result — trailing
history is biased high — survives. What does not survive is the **per-name
cheap/rich ranking**: it is not identified, so any "implied is below its own
history" screen is reporting an estimator choice. GME was the whole reason to
look at this week and it flips sign.

Fixing the mismatch moved ORCL's ladder from −71%/−82% to −34%/−51%. Nothing
crossed zero, so the conclusion is robust to the bug in the direction that
matters.

## Quote quality gates the cheap end

The tight strikes are the liquid ones and vice versa. ORCL quotes 1–5% wide with
3.7k–6.9k OI; AVAV, M and CHWY quote 29–167% wide, and AVAV 167.5C prints
1.02 bid / 4.93 ask. On the indicative feed those wide asks are modelled, not
transactable, so the deep names' EV is not even measurable at the ask — another
reason the ranking collapses onto the two liquid names.
