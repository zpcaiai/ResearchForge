# Worked example — attention entropy vs sequence length

A complete run, from a paper to a refused release, on a machine with no GPU and no model key.

It reaches RL0 (the fixture is an abstract page, so there is no code to reproduce), which puts the
project in `CM_NONE`: no comparative performance claim is admissible, and only diagnostic and
evaluation-methodology directions remain open. The experiments below are exactly that kind of work,
which is why the run gets anywhere at all.

## Why you have to write `impl/`

`codebase-scaffolder` generates the experiment harness — CLI, seeding, invalid-condition checks,
result emission — and deliberately does **not** generate the method under test. A generator that
writes the algorithm it also measures produces evidence about itself. The generated
`experiment.py` loads a sibling `impl.py` and raises `MethodNotImplemented` if it is absent.

The three files here are that missing piece, written the way a researcher would write them. They
are real numpy computations, not stand-ins: scaled dot-product attention over random inputs at
increasing sequence lengths, a metric-validity probe, and a matched-compute ablation.

## Run it

```bash
./run.sh /tmp/example-project
```

Three phases, because there is a human in the middle and a researcher in the middle:

1. drive to the human selection gate, choose a direction
2. compile the blueprint and scaffold the code — then drop `impl/*.py` into place
3. `--redo experiment-runner` and continue to the release gate

## What you should see

- 21 completed runs (3 experiments × 7 seeds) with real measured metrics
- an integrity audit that recomputes every reported statistic from the raw ledger
- 24 SVG figures, each verified by reading the numbers back off the rendered artists
- a 12-slide `.pptx` with 30 native text frames and 0 pictures
- **`RELEASE REFUSED`**

The refusal is the point. The prose came from `--model offline`, so `synthetic: true` is set on the
draft, and nothing synthetic is releasable. Supply `ANTHROPIC_API_KEY` and drop `--model offline`
to get prose that can be audited on its merits instead.
