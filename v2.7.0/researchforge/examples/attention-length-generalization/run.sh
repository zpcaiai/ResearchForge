#!/usr/bin/env bash
# Worked example. Usage: ./run.sh [project-dir]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PROJ="${1:-/tmp/rf-example}"
CLI="node $ROOT/packages/cli/dist/cli.js"
PAPER="$ROOT/fixtures/papers/arxiv_1706.03762_abs.html"

# The experiment, manuscript, deck and release stages are paid features. This is a
# demo of the machinery, so it overrides the gate — which is recorded in the run's
# provenance and surfaces in the release manifest, exactly as it should be.
export RESEARCHFORGE_ALLOW_UNLICENSED=1

COMMON=(--project "$PROJ" --model offline
  --set 'hypothesis=Attention entropy drifts toward uniform as sequence length exceeds the trained regime'
  --set 'candidate_method=numpy probe of scaled dot-product attention entropy across sequence lengths'
  --set 'resource_envelope={"gpu_hours":0,"usd":0,"wallclock_hours":4,"seeds":7}'
  --set 'research_objective=characterize length-generalization failure modes of attention'
  --set 'submission_schema={"mechanism_activation":"float","failure_mode_incidence":"float"}')

rm -rf "$PROJ"
echo "── phase 1: to the human gate ──"
$CLI run "$PAPER" "${COMMON[@]}" || true

echo; echo "── phase 2: select a direction, compile and scaffold ──"
$CLI run "$PAPER" "${COMMON[@]}" --select I-001 || true

echo; echo "── phase 3: supply the method under test, then re-run the experiments ──"
for f in "$HERE"/impl/*.py; do
  id="$(basename "$f" .py)"                      # E-001 -> code/e_001/impl.py
  dir="$PROJ/code/$(echo "$id" | tr 'A-Z-' 'a-z_')"
  [ -d "$dir" ] && cp "$f" "$dir/impl.py" && echo "  $id -> $dir/impl.py"
done
$CLI run "$PAPER" "${COMMON[@]}" --select I-001 --redo experiment-runner || true

echo; echo "── result ──"
$CLI status --project "$PROJ"
