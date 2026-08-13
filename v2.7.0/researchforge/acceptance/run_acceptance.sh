#!/usr/bin/env bash
# The acceptance run: one paper, a real model key, real hardware, then a grade.
#
# This script exists for the one thing the development container cannot do. Every
# other path through ResearchForge degrades gracefully when a key or a GPU is
# missing — `--model offline` produces a complete, clearly-stamped, worthless run,
# and that is the correct behaviour for a demo. It is the wrong behaviour here.
# An acceptance run that quietly fell back to the offline provider would produce
# an acceptance report, and the report would be the artifact people remember.
#
# So this script has no fallback. It checks for everything it needs, names each
# missing thing individually, and exits before touching the project directory.
#
# Usage:
#   ./run_acceptance.sh PAPER PROJECT_DIR [options]
#
#   --impl DIR        directory of <EXPERIMENT_ID>.py files supplying the method
#                     under test (see examples/attention-length-generalization)
#   --select IDS      idea ids to select at the human gate (default: I-001)
#   --sets FILE       file of key=value lines, one per --set external
#   --model NAME      model provider (default: anthropic). 'offline' is refused.
#   --no-second-run   skip the determinism run. The reproducibility dimension will
#                     then report NOT_MEASURED and acceptance cannot be reached.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

PAPER=""; PROJ=""; IMPL=""; SELECT="I-001"; SETS_FILE=""; MODEL="anthropic"
SECOND_RUN=1
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --impl) IMPL="${2:?--impl needs a directory}"; shift 2 ;;
    --select) SELECT="${2:?--select needs ids}"; shift 2 ;;
    --sets) SETS_FILE="${2:?--sets needs a file}"; shift 2 ;;
    --model) MODEL="${2:?--model needs a name}"; shift 2 ;;
    --no-second-run) SECOND_RUN=0; shift ;;
    --) shift; EXTRA+=("$@"); break ;;
    -*) EXTRA+=("$1"); shift ;;
    *) if [ -z "$PAPER" ]; then PAPER="$1"; elif [ -z "$PROJ" ]; then PROJ="$1";
       else EXTRA+=("$1"); fi; shift ;;
  esac
done

# --------------------------------------------------------------------------
# Preflight. Every missing precondition is collected and reported together:
# discovering them one exit code at a time wastes an operator's afternoon, and
# the point of this block is that it is cheaper than the run it guards.
# --------------------------------------------------------------------------
MISSING=()
note() { MISSING+=("$1"); }

[ -n "$PAPER" ] || note "argument 1 (PAPER): a paper URL, PDF or HTML file to run on"
[ -z "$PAPER" ] || [ -f "$PAPER" ] || [[ "$PAPER" == http* ]] || \
  note "PAPER '$PAPER' is neither an existing file nor an http(s) URL"
[ -n "$PROJ" ] || note "argument 2 (PROJECT_DIR): where the run will be built"

# -- 1. a real model key ---------------------------------------------------
# Checked by name, because "no key" and "wrong key" produce very different
# failures four hours in, and only the first one is this script's business.
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  note "ANTHROPIC_API_KEY or OPENAI_API_KEY: neither is set. The acceptance run needs a real
      model provider. Export one:
          export ANTHROPIC_API_KEY=sk-ant-...
      This script will not fall back to --model offline: offline output is stamped
      synthetic, and acceptance/grade.py refuses to grade a synthetic project."
fi
if [ "$MODEL" = "offline" ] || [ "${RESEARCHFORGE_OFFLINE:-}" = "1" ]; then
  note "--model offline (or RESEARCHFORGE_OFFLINE=1) was requested. Refused: an acceptance
      run in offline mode grades the machinery, not the research."
fi

# -- 2. a GPU ---------------------------------------------------------------
# Deliberately has no override flag. An 'I know what I am doing' escape hatch on
# this check is the door through which an acceptance report gets produced on a
# laptop, and that report is indistinguishable from a real one afterwards.
GPU_DESC=""
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_DESC="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -4 || true)"
fi
if [ -z "$GPU_DESC" ] && command -v rocm-smi >/dev/null 2>&1; then
  GPU_DESC="$(rocm-smi --showproductname 2>/dev/null | head -4 || true)"
fi
if [ -z "$GPU_DESC" ]; then
  GPU_DESC="$(python3 - <<'PY' 2>/dev/null || true
try:
    import torch
    if torch.cuda.is_available():
        print("; ".join(torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())))
except Exception:
    pass
PY
)"
fi
if [ -z "$GPU_DESC" ]; then
  note "a visible GPU: nvidia-smi reports none, rocm-smi reports none, and torch.cuda
      reports none. The acceptance run executes generated training code; without an
      accelerator the runs either do not finish inside the timebox or finish at a
      scale whose numbers say nothing about the method."
fi

# -- 3. a sandbox -----------------------------------------------------------
# sandbox-provisioner sets untrusted_code_execution_allowed only when docker is
# present, and experiment-runner records every planned run as NOT_RUN when it is
# not. Without this the run completes and measures nothing.
if ! command -v docker >/dev/null 2>&1; then
  note "docker: not on PATH. sandbox-provisioner will set
      untrusted_code_execution_allowed=false and experiment-runner will record every
      planned run as NOT_RUN. The run would finish with an empty ledger."
fi

# -- 4. the runtime itself --------------------------------------------------
CLI="$ROOT/packages/cli/dist/cli.js"
command -v node >/dev/null 2>&1 || note "node: not on PATH (the orchestrator is TypeScript)"
[ -f "$CLI" ] || note "$CLI: not built. Run 'npm install && npm run build' in $ROOT."
python3 -c "import researchforge" 2>/dev/null || \
  note "the researchforge python package is not importable. Run 'pip install -e $ROOT/python'."

# -- 5. inputs the run needs to reach the end -------------------------------
[ -z "$SETS_FILE" ] || [ -f "$SETS_FILE" ] || note "--sets file '$SETS_FILE' does not exist"
[ -z "$IMPL" ] || [ -d "$IMPL" ] || note "--impl directory '$IMPL' does not exist"
if [ -z "$IMPL" ]; then
  note "--impl DIR: no implementation directory was given. The scaffolder generates the
      experiment harness but never the method under test; without impl/<id>.py every run
      fails with METHOD_NOT_IMPLEMENTED and the ledger carries no measurement."
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "" >&2
  echo "acceptance: REFUSING TO START — ${#MISSING[@]} precondition(s) missing." >&2
  echo "" >&2
  for m in "${MISSING[@]}"; do
    echo "  ✖ $m" >&2
    echo "" >&2
  done
  echo "Nothing was written to '${PROJ:-<unset>}'. This script has no degraded mode: its whole" >&2
  echo "purpose is the run that cannot be performed without the things listed above." >&2
  exit 78   # EX_CONFIG
fi

if [ -z "${RESEARCHFORGE_CONTACT_EMAIL:-}" ]; then
  # A warning, not a refusal: the run works without it, but the scholarly APIs
  # throttle an anonymous caller instead of applying polite-pool limits, and the
  # coverage report will name the resulting blind spots.
  echo "acceptance: warning — RESEARCHFORGE_CONTACT_EMAIL is unset; scholarly providers will" >&2
  echo "            rate-limit this run rather than apply polite-pool limits." >&2
fi

# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
COMMON=(--model "$MODEL")
if [ -n "$SETS_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    COMMON+=(--set "$line")
  done < "$SETS_FILE"
fi
COMMON+=("${EXTRA[@]+"${EXTRA[@]}"}")

echo "acceptance: GPU — ${GPU_DESC//$'\n'/ | }"
echo "acceptance: paper=$PAPER project=$PROJ model=$MODEL select=$SELECT"

drive_run() {
  # One full traversal: to the human gate, through the gate, then again with the
  # method under test in place. The third pass is not a retry — the scaffolder
  # cannot produce impl.py, so the experiments genuinely cannot run before it
  # exists, and --redo experiment-runner is the documented way back into the
  # execution stage.
  local proj="$1"
  rm -rf "$proj"
  mkdir -p "$proj"

  echo ""; echo "── $proj phase 1: to the human gate ──"
  node "$CLI" run "$PAPER" --project "$proj" "${COMMON[@]}" || true

  echo ""; echo "── $proj phase 2: select a direction, compile and scaffold ──"
  node "$CLI" run "$PAPER" --project "$proj" "${COMMON[@]}" --select "$SELECT" || true

  if [ -n "$IMPL" ]; then
    echo ""; echo "── $proj phase 3: install the method under test ──"
    for f in "$IMPL"/*.py; do
      [ -e "$f" ] || continue
      local id dir
      id="$(basename "$f" .py)"
      dir="$proj/code/$(echo "$id" | tr 'A-Z-' 'a-z_')"
      if [ -d "$dir" ]; then cp "$f" "$dir/impl.py"; echo "  $id -> $dir/impl.py"
      else echo "  ! $id: no scaffolded directory at $dir — the blueprint compiled no such experiment" >&2
      fi
    done
    echo ""; echo "── $proj phase 4: re-run the experiments and everything downstream ──"
    node "$CLI" run "$PAPER" --project "$proj" "${COMMON[@]}" --select "$SELECT" \
      --redo experiment-runner || true
  fi

  node "$CLI" status --project "$proj" || true
}

drive_run "$PROJ"

SECOND_ARG=()
if [ "$SECOND_RUN" -eq 1 ]; then
  PROJ2="${PROJ%/}-second"
  echo ""; echo "══ determinism: a second run from the same inputs into $PROJ2 ══"
  # Same paper, same externals, same impl. If the two disagree on a metric, every
  # confidence interval in the manuscript is measuring the machine.
  drive_run "$PROJ2"
  SECOND_ARG=(--second-run "$PROJ2")
else
  echo ""
  echo "acceptance: --no-second-run given. The reproducibility dimension will report" >&2
  echo "            NOT_MEASURED and the overall verdict cannot be ACCEPTED." >&2
fi

# --------------------------------------------------------------------------
# the grade
# --------------------------------------------------------------------------
echo ""; echo "══ grading ══"
set +e
python3 "$HERE/grade.py" "$PROJ" --repo-root "$ROOT" ${SECOND_ARG[@]+"${SECOND_ARG[@]}"} --quiet
RC=$?
set -e
echo ""
echo "acceptance: report  -> $PROJ/acceptance/acceptance_report.md"
echo "acceptance: machine -> $PROJ/acceptance/acceptance_result.json"
exit $RC
