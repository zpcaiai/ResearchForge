# B arm — the human ceiling, ready to run

The A arm measured what a coding agent achieves on 20 papers under a 4-hour timebox. On its own
that number is uninterpretable. A 15% reproduction rate is damning if a skilled engineer gets 80%
on the same papers and unremarkable if they get 20%. **The B arm is the denominator**, and it is
the one number in this study nobody has.

Everything here is built except the part that needs a person.

## Before you spend a week: what n=8 can and cannot resolve

Both arms attempt the **same papers**, so this is a paired design and the right instrument is an
exact McNemar test on the discordant pairs — the papers where exactly one arm reproduced. Papers
both arms managed, or neither did, say nothing about which arm is better.

What that costs depends on the **discordance rate**, not on either arm's success rate:

| difference to detect | pairs needed at p_d=0.25 | at p_d=0.50 | at p_d=0.75 |
|---:|---:|---:|---:|
| 0.50 | — (δ² ≥ p_d) | 14 | 22 |
| 0.40 | 10 | 23 | 35 |
| 0.30 | 20 | 42 | 63 |
| 0.20 | 47 | 96 | 145 |

At n=8 and a discordance of 0.25, the smallest detectable difference is about **0.42**.

**This corrects an earlier version of this file**, which reported 0.70 at n=8 and a required n of
99 for a 20-point difference. Those numbers came from the two-independent-sample formula, which is
wrong here: two arms attempting the same papers are positively correlated by paper difficulty, and
using the independent formula on such a design inflates the sample requirement — by **2.7×** at a
20-point difference. The instrument now uses the exact paired test, and the old figures were an
overstatement of how bad the protocol's n=8 is.

n=8 is still thin. Five discordant pairs all favouring the human is p=0.0625, which does not
reject at 0.05; four is p=0.125. **Budget n≈20–25 if you want a usable answer at a plausible
effect size.** If you can only afford 8, run it knowing you are most likely collecting
failure-code distributions and qualitative notes rather than a gap — those are worth having, and
`analyse.py` reports them, but do not call the result a ceiling.

## Running it

```
# 1. draw the subset and build the packet
python3 docs/study/b-arm/build_packet.py --a-arm docs/study/all_results.jsonl \
    --n 8 --seed 20260812 --packet b_packet.json --key b_key.json

# 2. give b_packet.json to the engineer. Do NOT give them b_key.json.

# 3. when the worksheets come back
python3 docs/study/b-arm/analyse.py --packet b_packet_returned.json \
    --key b_key.json --a-arm docs/study/all_results.jsonl --out b_arm_result.json
```

The subset is **drawn, not chosen** — a seeded shuffle whose seed is recorded in the packet.
Picking eight by hand would select for papers whose difficulty someone already had an opinion
about, and that opinion comes from the A-arm results this arm is supposed to be blind to.

## The blind is the design

The packet carries the paper, the repository and the claim targets. It withholds every A-arm
outcome — level, failure codes, timings, notes — and lists what it withheld so the withholding is
auditable. If the engineer reads the A-arm log first, what gets measured is *"can a human fix an
agent's dead end"*, which is a much easier question because the agent has already paid the search
cost.

The builder refuses to run if the A-arm records contain a field it does not classify as either
carried or withheld. A field added later shows up as an error rather than leaking into the packet.

Each worksheet asks `saw_a_arm_material` after the fact. Answer honestly: a `yes` does not waste
the worksheet, it moves it out of the ceiling estimate and into a separate sample. A `yes` recorded
as a `no` destroys the comparison and nothing downstream can detect it.

## What the analysis refuses to do

1. **Report a gap over different papers.** The returned packet must hash to its key. A swapped
   paper, or a packet rebuilt with a different seed, gives two rates over two samples — and the
   difference of those is not a gap.
2. **Fold in a run that broke its timebox.** A worksheet that ran past 8 hours executed a different
   design. Reported separately, never merged.
3. **Count an unattempted paper as a failure.** Unfinished is `NOT_RUN`. Counting it as RL0 makes
   the human arm look worse and the agent look better — the exact direction of error this study
   exists to avoid.
   The same applies to an *unanswered* question. Both `saw_a_arm_material` and
   `wall_clock_hours_used` ship as `null`, and an earlier version read `null` as "no": an engineer
   who skipped both fields produced a clean 8/8 ceiling that had passed neither refusal.
   Unanswered now excludes the worksheet and says which question went unanswered.
4. **Call RL1 a reproduction.** Success is RL3+, meaning at least one main result inside the
   tolerance that step 0's seed-variance measurement set. RL1 is "it ran without erroring" and RL2
   is "right direction at reduced scale"; folding them in is the most common way a study of this
   kind reports a rate several times too high.

## Step 0 is not optional

Every worksheet carries it: repeat the main experiment with three different seeds and record the
standard deviation before comparing anything. Tolerance is `max(the paper's reported variance,
2 × your measured 3-seed sd, 2% relative)`. `docs/study/step0_results.json` is why — on the worked
example, **8 of 12 metrics needed a tolerance wider than the 2% floor, one of them 67× wider**. A
fixed ±5% turns a high-variance task's own noise into a failed reproduction.
