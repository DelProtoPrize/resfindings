# Capability Roadmap & Scope Triage

The target capability set, triaged by how well the **available data** supports it.
The point of this triage: a sports-analytics panel rewards rigor and is skeptical
of finance buzzwords used loosely. Everything below is buildable — but some items
are flagship-strong and a couple need to be framed carefully or they read as gimmicks.

## Valuation sources, and the TE-premium decision (DECIDED)

Two market sources, two different epistemologies — which is *why* they disagree:
- **FantasyPros (via DynastyProcess)** = a model-transformed **expert ranking**.
  DP scrapes FantasyPros Dynasty ECR (ordinal) and converts to a cardinal value via
  `Value = 10500 * e^(ECR * -0.0235)`; Superflex is a LOESS 1QB→2QB ADP remap.
  Consequence: it is **not** TE-premium aware and **not** age-aware by construction.
  (`fp_ecr_2qb` = the rank; `fp_value_2qb` = the exponential value derived from it.)
- **FantasyCalc** = a crowd-sourced **market price** from real trades. Better reflects
  live demand, BUT the public `values/current` endpoint does **not** expose a working
  TE-premium parameter (tested: tePremium/teBonus/tep all ignored). So what we pull is
  also effectively non-TEP-segmented.

**Decision: do NOT apply a flat TE-premium multiplier.** TEP pays *per reception*, so
its value is `projected_receptions × tep_bonus` → value. A flat % overpays
touchdown-dependent TEs and underpays high-target-share TEs — inverting what TEP
rewards. Therefore **TE-premium is computed downstream of the projection model**
(target share → receptions → bonus → incremental value), not as a standalone fudge.
Until projections exist, FP/FC values are served **as-is and labeled non-TEP**; the
large TE arbitrage deltas are interpreted as genuine market-vs-expert signal.

## A. Grounded with data we already have (MVP — build first)
These run off the current warehouse with SQL + light compute. Interview-ready.
- **League breakdown** — aggregates over dims/fact.
- **Roster distributions** — positional value allocation (`v_roster_assets`).
- **Roster valuations** — done (FantasyPros primary, FantasyCalc secondary).
- **Arbitrage delta (Buy/Sell)** — done (FP vs FC disagreement).
- **Percentile ranks** — `PERCENT_RANK() OVER (PARTITION BY league)`.
- **Concentration / "due diligence"** — HHI (`SUM(share^2)`, float-cast!).
- **Power rankings** — composite of roster value + recent points + wins.
- **Full team diagnostics** — a per-team panel composed of the above.
- **Trade calculator / trade abilities** — value-based trade evaluation (players + picks).
- **Acquisition diagnosis & potential targets** — positional-gap analysis vs league,
  then surface available/tradeable players who fill the gap.

## B. Grounded, but needs a small ETL addition (build second)
- **Player volatility & stability** — weekly per-player points exist in Sleeper
  matchup `players_points`; pull them → stdev / coefficient of variation, boom-bust rates.
- **Alpha** (manager skill) & **trade ROI** — wire the transactions fact (adds/drops/trades
  with timestamps) and value assets at the snapshot nearest each transaction.
- **Delta (Δ)** — value momentum = change in value over time. Needs accruing snapshots
  (and `fc_trend_30day` as a head start).
- **Gamma (Γ)** — acceleration of value change (2nd derivative). Needs several snapshots; noisy early.
- **Beta (β)** — sensitivity of a player's/roster's value moves to the market
  (league/position index): `cov(asset Δ, market Δ) / var(market Δ)`. Strong once history accrues.
- **Power rankings & team trajectory (short term)** — recent scoring trend + schedule.

## C. Advanced modeling — strong IF defined rigorously (build third, Python-side)
Keep the heavy math in Python (pandas/numpy), materialize outputs to tables, let
Node serve them. Plays to SQL+Python strengths instead of reimplementing math in JS.
- **DCF (flagship).** A player is an asset producing future "cash flows" = projected
  points/value over N seasons, discounted by a rate reflecting age, injury, and
  volatility risk, plus a terminal (decline) value. Dynasty value *is* the market's
  implicit DCF — so an explicit one yields a defensible intrinsic value vs. market price.
  This is the centerpiece of the "financial modeling" angle. Requires a projection input (below).
- **Team trajectory (long term)** — age-curve + young-asset value + pick capital → contend/rebuild window.
- **Risk models** — volatility, drawdown, VaR/CVaR on roster value or weekly points;
  a Sharpe-like return-per-unit-risk for players/rosters.

## D. Proceed with caution — framing risk
- **Offensive projection models** — meaningful projections need richer data than
  Sleeper/FP/FC provide (target share, snaps, EPA). **Recommended: add nflverse /
  `nfl_data_py`** (free, standard in NFL analytics) — feeds projections, volatility,
  and DCF at once. Worthwhile but a real data-source expansion.
- **LBO model.** Honest caveat: fantasy has no debt/leverage/interest, so a literal
  LBO doesn't map. A finance-literate panel may push back. Defensible *reframe*: a
  "win-now leverage ratio" = (future assets committed: picks/young players/FAAB) /
  (present value acquired), analyzing risk-adjusted return on win-now pushes. Use as
  a clearly-labeled analogy, not a literal LBO, or skip it.

## Recommended build order
1. **Repo + MVP (Bucket A)** on the Node/SQL/HTML spine — a working, demoable dashboard.
2. **ETL additions (Bucket B)** — players_points + transactions + accruing snapshots.
3. **Advanced models (Bucket C)** + nflverse integration; DCF as the showpiece.
4. **LBO/leverage** only if time allows, framed as analysis not a literal model.

---

## Roster construction layer (the "scarcity / cornering" question)

Decision: this sits **on top of** per-player value, never inside it. A player's value
is a property of the player + NFL + league *settings*, not of whose roster he's on —
keep it mark-to-market so two managers' valuations of the same asset stay comparable
(required for trade analysis). Static league structure (3 FLEX, SF, 14 teams) already
lives in the projection/VORP replacement math. Dynamic roster construction is the layer
above. Three tiers:

1. **Asset price (built):** projection -> VORP -> value. Context-free. `v_player_value`.
2. **Portfolio construction (next):** per roster, SOLVE the optimal lineup, don't count
   slots. Slot-eligibility matrix derived from `roster_positions`:
     QB{QB} RB{RB} WR{WR} TE{TE} FLEX{RB,WR,TE} SUPER_FLEX{QB,RB,WR,TE}
   Assign players -> slots maximizing projected points (tier-nested, so fill
   most-restrictive -> least-restrictive with best remaining eligible; tiny max-weight
   assignment). Outputs: **Optimal Starting Lineup value** (deployable points — an elite
   RB lands in FLEX; SF takes QB2 *or* an RB if it outprojects), and **true surplus**
   (only players who LOSE the assignment = injury/bye insurance + trade capital).
   This is where the "4th RB1 as sell-high capital" signal actually lives.
3. **Endogenous replacement / cornering (the novel one):** replacement level is not the
   global pool — it's value-over-next-AVAILABLE (VONA) given what's actually held. When
   one manager corners a position, everyone else's effective replacement drops. Surface
   via positional HHI across teams + a diagnostic ("RB1s held: 4 / remaining 8 across 13").
4. **Marginal win-equity (later):** convert lineup distribution -> weekly win prob;
   high-floor concentration wins more H2H than raw points imply. Bridge to championship-
   equity / DCF-of-wins.
