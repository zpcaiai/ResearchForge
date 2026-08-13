"""Behavioural tests for the execution stage — mostly tests of what it refuses.

The interesting assertions here are negative ones. Anyone can check that a runner
writes a ledger; the thing that matters is that when the runner cannot honestly run
anything, the ledger contains no number, the best candidate is null, and the reason
is named. Those are the tests that would fail if someone "helpfully" made the
pipeline produce plausible output on a machine with no sandbox.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from researchforge import skills as _skills  # noqa: F401  registers implementations
from researchforge.artifacts import ArtifactStore
from researchforge.errors import GateBlocked
from researchforge.provenance import ProvenanceLog
from researchforge.skill import Context, get

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"

SPEC = {
    "experiment_id": "E-001",
    "hypothesis": "the candidate beats the baseline on accuracy",
    "baseline": {"name": "baseline-a"},
    "candidate": {"name": "candidate-a"},
    "datasets": ["synthetic-tiny"],
    "metrics": [{"name": "accuracy", "direction": "maximize"}],
    "seeds": [1, 2],
    "invalid_conditions": [{"metric": "accuracy", "op": ">", "value": 0.999}],
}
#: The arms SPEC declares, in run order. Every count below is arms x seeds.
ARMS = ["baseline", "candidate"]


# ---------------------------------------------------------------------------
def make_ctx(project: Path, run_id: str = "r1", config: dict | None = None) -> Context:
    prov = ProvenanceLog(project)
    store = ArtifactStore(project, SCHEMAS, run_id, prov)
    return Context(project=project, run_id=run_id, mode="auto", store=store, prov=prov,
                   quota=None, model=None, scholarly=[], config=config or {})


def seed_project(project: Path, *, isolation: bool = False, evaluator: bool = False,
                 spec: dict | None = None) -> Path:
    """Stand in for the upstream skills that this batch does not implement."""
    ctx = make_ctx(project)
    s = ctx.store
    s.write("sandbox-provisioner", "sandbox_manifest", {
        "profile": "no-network-untrusted-code",
        "isolation": "container" if isolation else "none",
        "untrusted_code_execution_allowed": isolation,
        "host": {"platform": "test", "python": sys.version.split()[0]},
        "warnings": [] if isolation else ["no container engine"],
    })
    s.write("sandbox-provisioner", "sandbox_container_config", {
        "engine": "docker" if isolation else "none",
        "limits": {"cpus": 2, "memory": "2g", "network": "none", "pids": 256,
                   "timeout_seconds": 60},
    })
    s.write("sandbox-provisioner", "environment_lock", "pytest==8.0.0\n")
    s.write("research-blueprint-compiler", "research_blueprint", {
        "blueprint_id": "B-1", "selected_idea_id": "I-001",
        "hypothesis": SPEC["hypothesis"], "acceptance_criteria": ["accuracy improves"],
        "stages": [{"id": "run", "depends_on": []}], "budgets": {"gpu_hours": 0},
    })
    s.write("research-blueprint-compiler", "acceptance_criteria", "# Acceptance\n- accuracy improves\n")
    s.write("research-blueprint-compiler", "blueprint_dag", {"stages": [{"id": "run"}]})
    s.write("result-reproducer", "baseline_assets", {"repos": [], "checkpoints": [], "datasets": []})
    if evaluator:
        s.write("evaluator-builder", "evaluator_code", "def evaluate(pred, gold):\n    return {}\n")
        s.write("evaluator-builder", "evaluator_spec", "# Evaluator\naccuracy over the tiny split\n")
    (project / "experiments").mkdir(parents=True, exist_ok=True)
    (project / "experiments" / "E-001.yaml").write_text(
        json.dumps(spec or SPEC, indent=1), encoding="utf-8")
    return project


def run_skill(project: Path, name: str, run_id: str = "r1", config: dict | None = None):
    return get(name)(make_ctx(project, run_id, config))


def ledger(project: Path) -> list[dict]:
    p = project / "experiment_ledger.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


@pytest.fixture
def project(tmp_path):
    return seed_project(tmp_path)


# ===========================================================================
#  the central refusal: no isolation means no numbers
# ===========================================================================
def test_without_isolation_nothing_runs_and_no_metric_is_invented(project):
    run_skill(project, "codebase-scaffolder")
    res = run_skill(project, "experiment-runner")

    rows = ledger(project)
    # arms x seeds. The runner used to invoke the entry point with no --arm and so
    # measured the candidate twice while calling one of them a baseline; a spec that
    # declares two conditions has to produce two conditions' worth of rows.
    assert len(rows) == len(ARMS) * len(SPEC["seeds"]), \
        "every planned run of every declared arm must be recorded, not just the run ones"
    assert {r["arm"] for r in rows} == set(ARMS)
    for r in rows:
        assert r["status"] == "NOT_RUN"
        assert r["metrics"] == {}, "a NOT_RUN entry with a metric is the failure mode this prevents"
        assert r["not_run_reason"] == "NO_ISOLATION"
        assert r["provenance"]["executed"] is False
        assert "not a security boundary" in r["provenance"]["reason_detail"]
    assert any("untrusted_code_execution_allowed is False" in w for w in res.warnings)
    assert res.detail["executed"] == 0 and res.detail["measured_runs"] == 0


def test_without_isolation_no_number_appears_anywhere_in_the_ledger(project):
    run_skill(project, "codebase-scaffolder")
    run_skill(project, "experiment-runner")
    for r in ledger(project):
        assert not r["metrics"]
        # nothing shaped like a score may hide in the entry either
        assert "accuracy" not in json.dumps(r["metrics"])


def test_nothing_measured_means_no_best_candidate_and_no_ranking(project):
    run_skill(project, "codebase-scaffolder")
    run_skill(project, "experiment-runner")

    best = json.loads((project / "experiments/best_candidate.json").read_text())
    assert best["selected"] is None
    assert "no run produced a measurement" in best["reason"]
    assert "NO_ISOLATION" in best["not_run_reasons"]

    ranked = json.loads((project / "experiments/ranked_branches.json").read_text())
    assert ranked["ranking_possible"] is False
    assert all(b["rank"] is None for b in ranked["branches"])


def test_terminal_codebase_blocks_execution_even_with_isolation(tmp_path):
    seed_project(tmp_path, isolation=True, evaluator=True)
    run_skill(tmp_path, "codebase-scaffolder")
    # simulate debug-and-repair having given up
    st = json.loads((tmp_path / "experiments/terminal_status.json").read_text())
    st["terminal"] = True
    st["stop_reason"] = "repair_attempt_cap_reached"
    (tmp_path / "experiments/terminal_status.json").write_text(json.dumps(st))

    run_skill(tmp_path, "experiment-runner")
    rows = ledger(tmp_path)
    assert rows and all(r["status"] == "NOT_RUN" for r in rows)
    assert all(r["not_run_reason"] == "TERMINAL_CODE_DEFECT" for r in rows)
    assert all(r["metrics"] == {} for r in rows)


def test_missing_evaluator_means_nothing_is_scored(tmp_path):
    seed_project(tmp_path, isolation=True, evaluator=False)
    run_skill(tmp_path, "codebase-scaffolder")
    run_skill(tmp_path, "experiment-runner")
    rows = ledger(tmp_path)
    assert all(r["not_run_reason"] == "EVALUATOR_MISSING" and r["metrics"] == {} for r in rows)


# ===========================================================================
#  the ledger is append-only
# ===========================================================================
def test_ledger_is_append_only_across_runs(project):
    run_skill(project, "codebase-scaffolder")
    run_skill(project, "experiment-runner", run_id="run-a")
    p = project / "experiment_ledger.jsonl"
    first = p.read_bytes()
    n_first = len(ledger(project))

    run_skill(project, "experiment-runner", run_id="run-b")
    second = p.read_bytes()

    assert second.startswith(first), "an earlier ledger entry was rewritten or reordered"
    assert len(ledger(project)) == 2 * n_first
    assert {r["run_id"] for r in ledger(project)} == {"run-a", "run-b"}


def test_ledger_entries_validate_against_the_schema(project):
    from jsonschema import Draft202012Validator

    run_skill(project, "codebase-scaffolder")
    run_skill(project, "experiment-runner")
    v = Draft202012Validator(json.loads((SCHEMAS / "ExperimentResult.schema.json").read_text()))
    for r in ledger(project):
        v.validate(r)


# ===========================================================================
#  provenance is consolidated, not clobbered
# ===========================================================================
def test_provenance_log_is_appended_not_overwritten(project):
    run_skill(project, "codebase-scaffolder")
    before = (project / "provenance.jsonl").read_bytes()
    res = run_skill(project, "experiment-runner")
    after = (project / "provenance.jsonl").read_bytes()

    assert after.startswith(before), "the runtime provenance log was rewritten"
    assert res.detail["provenance_log"] == "appended_summary_to_runtime_log"
    # the merged file must still parse as the runtime's own event stream
    events = ProvenanceLog(project).read()
    summaries = [e for e in events if e.kind == "provenance_summary"]
    assert len(summaries) == 1
    assert summaries[0].detail["ledger_statuses"]["NOT_RUN"] == len(ARMS) * len(SPEC["seeds"])


def test_artifact_manifest_hashes_real_files_and_names_lineage(project):
    run_skill(project, "codebase-scaffolder")
    run_skill(project, "experiment-runner")
    man = json.loads((project / "artifact_manifest.json").read_text())
    by_path = {a["path"]: a for a in man["artifacts"]}
    assert "experiment_ledger.jsonl" in by_path
    assert by_path["experiment_ledger.jsonl"]["artifact_id"] == "experiment_ledger"
    assert all(len(a["sha256"]) == 64 for a in man["artifacts"])
    # the generated code is on disk and hashed, not merely described
    assert any(p.startswith("code/e_001/") for p in by_path)
    assert "code_worktree" in by_path["experiment_ledger.jsonl"]["parents"]


# ===========================================================================
#  the scaffolder generates real code
# ===========================================================================
def test_scaffolder_writes_real_modules_and_tests(project):
    res = run_skill(project, "codebase-scaffolder")
    entry = project / "code/e_001/experiment.py"
    assert entry.exists() and (project / "code/tests/test_e_001.py").exists()
    assert (project / "code/rf_runtime.py").exists()
    wt = json.loads((project / "code/_manifest.json").read_text())
    assert wt["entry_points"]["E-001"] == "code/e_001/experiment.py"
    assert wt["executed_by_this_skill"] is False
    assert "code/e_001/experiment.py" in (project / "code/implementation_plan.md").read_text()
    assert res.detail["files"] >= 5


def test_generated_tests_pass(project):
    run_skill(project, "codebase-scaffolder")
    r = subprocess.run([sys.executable, "-m", "pytest", "code/tests", "-q", "-p", "no:cacheprovider"],
                       cwd=project, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_generated_entry_point_refuses_to_invent_a_metric(project):
    run_skill(project, "codebase-scaffolder")
    r = subprocess.run([sys.executable, "code/e_001/experiment.py", "--seed", "1"],
                       cwd=project, capture_output=True, text=True)
    assert r.returncode == 3
    payload = json.loads(r.stdout.split("RF_RESULT ", 1)[1])
    assert payload["status"] == "NOT_IMPLEMENTED" and payload["metrics"] == {}
    assert "refuses to synthesize" in payload["error"]


# ===========================================================================
#  repair is capped
# ===========================================================================
def _append_failure(project: Path, run_id: str, cls: str = "SYNTAX_ERROR") -> None:
    ctx = make_ctx(project, run_id)
    ctx.store.append_jsonl("experiment-runner", "experiment_ledger", [{
        "run_id": run_id, "experiment_id": "E-001", "status": "FAILED",
        "metrics": {}, "artifacts": [], "failure_class": cls,
        "provenance": {"skill": "experiment-runner", "executed": True},
    }])


def test_repair_attempts_are_capped_across_invocations(project):
    cap = get("codebase-scaffolder").REPAIR_ATTEMPT_CAP
    for i in range(cap + 4):
        _append_failure(project, f"run-{i}")
        run_skill(project, "codebase-scaffolder", run_id=f"run-{i}")

    commits = [json.loads(l) for l
               in (project / "code/.git/repair_commits").read_text().splitlines() if l.strip()]
    assert len(commits) == cap, f"repair loop ran {len(commits)} times against a cap of {cap}"
    assert [c["attempt"] for c in commits] == list(range(1, cap + 1))
    assert all(c["cap"] == cap for c in commits)

    st = json.loads((project / "experiments/terminal_status.json").read_text())
    assert st["terminal"] is True
    assert st["stop_reason"] == "repair_attempt_cap_reached"
    assert st["repair_attempt_cap"] == cap
    assert st["experiments_at_cap"] == ["E-001"]

    diag = json.loads((project / "experiments/failure_diagnosis.json").read_text())
    d = next(d for d in diag["diagnoses"] if d["experiment_id"] == "E-001")
    assert d["terminal"] is True and d["next_action_owner"] == "human"


def test_unrepairable_failures_are_terminal_on_the_first_look(project):
    _append_failure(project, "run-0", cls="METHOD_NOT_IMPLEMENTED")
    run_skill(project, "codebase-scaffolder")
    assert (project / "code/.git/repair_commits").read_text().strip() == "", \
        "a failure codegen cannot fix must not consume a repair attempt"
    st = json.loads((project / "experiments/terminal_status.json").read_text())
    assert st["terminal"] is True and st["stop_reason"] == "terminal_classification"
    d = json.loads((project / "experiments/failure_diagnosis.json").read_text())["diagnoses"][0]
    assert "must not invent the algorithm" in d["root_cause"]


def test_a_terminal_run_stage_does_not_reset_the_repair_counter(project):
    """A cap that resets on re-invocation is a slower unbounded loop, not a cap."""
    cap = get("codebase-scaffolder").REPAIR_ATTEMPT_CAP
    for i in range(cap):
        _append_failure(project, f"a-{i}")
        run_skill(project, "codebase-scaffolder", run_id=f"a-{i}")
    n = len((project / "code/.git/repair_commits").read_text().strip().splitlines())
    _append_failure(project, "b-0")
    run_skill(project, "codebase-scaffolder", run_id="b-0")
    assert len((project / "code/.git/repair_commits").read_text().strip().splitlines()) == n == cap


# ===========================================================================
#  when isolation IS available, the numbers are the ones the code produced
# ===========================================================================
IMPL = '''
def candidate(seed, config):
    return {"accuracy": 0.31415}


def baseline(seed, config):
    return {"accuracy": 0.2}
'''


def test_with_isolation_the_metric_is_the_one_the_code_returned(tmp_path):
    seed_project(tmp_path, isolation=True, evaluator=True)
    run_skill(tmp_path, "codebase-scaffolder")
    (tmp_path / "code/e_001/impl.py").write_text(IMPL, encoding="utf-8")

    res = run_skill(tmp_path, "experiment-runner")
    rows = ledger(tmp_path)
    assert len(rows) == 4 and all(r["status"] == "COMPLETED" for r in rows), rows
    # each arm reports its own number, and they are different numbers
    assert {r["metrics"]["accuracy"] for r in rows if r["arm"] == "candidate"} == {0.31415}
    assert {r["metrics"]["accuracy"] for r in rows if r["arm"] == "baseline"} == {0.2}
    assert all(r["provenance"]["executed"] is True for r in rows)
    assert all(r["provenance"]["exit_code"] == 0 for r in rows)
    assert all(r["provenance"]["limits"]["memory"] == "2g" for r in rows)
    assert all(r["provenance"]["timeout_seconds"] for r in rows)
    assert res.detail["measured_runs"] == 4

    best = json.loads((tmp_path / "experiments/best_candidate.json").read_text())
    # the branch is scored by its candidate arm, not by the pool. A pooled mean here
    # would be 0.257 — the average of the method and the thing it is compared to,
    # which describes no condition that was run.
    assert best["selected"] == "E-001" and best["value"] == pytest.approx(0.31415)
    assert best["significance_tested"] is False

    ranked = json.loads((tmp_path / "experiments/ranked_branches.json").read_text())
    branch = ranked["branches"][0]
    assert branch["scored_arm"] == "candidate"
    assert branch["arms"]["baseline"]["accuracy"]["mean"] == pytest.approx(0.2)
    contrast = branch["contrasts"]["candidate_vs_baseline"]["metrics"]["accuracy"]
    assert contrast["difference"] == pytest.approx(0.31415 - 0.2)
    assert branch["contrasts"]["candidate_vs_baseline"]["significance_tested"] is False


def test_a_run_returning_an_undeclared_metric_records_no_metric(tmp_path):
    seed_project(tmp_path, isolation=True, evaluator=True)
    run_skill(tmp_path, "codebase-scaffolder")
    (tmp_path / "code/e_001/impl.py").write_text(
        "def candidate(seed, config):\n    return {'made_up_score': 0.99}\n", encoding="utf-8")

    run_skill(tmp_path, "experiment-runner")
    rows = ledger(tmp_path)
    assert all(r["status"] == "FAILED" and r["metrics"] == {} for r in rows)
    cand = [r for r in rows if r["arm"] == "candidate"]
    assert cand and all("not declared" in r.get("error", "") for r in cand)
    # the baseline arm has no impl at all, and says so rather than borrowing one
    base = [r for r in rows if r["arm"] == "baseline"]
    assert base and all(r["failure_class"] == "METHOD_NOT_IMPLEMENTED" for r in base)


def test_metrics_without_a_declared_direction_are_not_ranked(tmp_path):
    spec = dict(SPEC, metrics=["accuracy"])   # a bare name says nothing about better
    seed_project(tmp_path, isolation=True, evaluator=True, spec=spec)
    run_skill(tmp_path, "codebase-scaffolder")
    (tmp_path / "code/e_001/impl.py").write_text(IMPL, encoding="utf-8")

    res = run_skill(tmp_path, "experiment-runner")
    ranked = json.loads((tmp_path / "experiments/ranked_branches.json").read_text())
    assert ranked["ranking_possible"] is False
    assert "no metric declares a direction" in ranked["reason"]
    best = json.loads((tmp_path / "experiments/best_candidate.json").read_text())
    assert best["selected"] is None
    assert any("refusing to rank" in w for w in res.warnings)


def test_a_run_that_trips_an_invalid_condition_is_not_a_result(tmp_path):
    seed_project(tmp_path, isolation=True, evaluator=True)
    run_skill(tmp_path, "codebase-scaffolder")
    (tmp_path / "code/e_001/impl.py").write_text(
        "def candidate(seed, config):\n    return {'accuracy': 1.0}\n", encoding="utf-8")

    run_skill(tmp_path, "experiment-runner")
    rows = [r for r in ledger(tmp_path) if r["arm"] == "candidate"]
    assert rows and all(r["status"] == "INVALID" and r["metrics"] == {} for r in rows)
    best = json.loads((tmp_path / "experiments/best_candidate.json").read_text())
    assert best["selected"] is None


# ===========================================================================
#  gates
# ===========================================================================
def test_runner_without_a_sandbox_manifest_is_a_gate_not_a_guess(tmp_path):
    seed_project(tmp_path)
    (tmp_path / "experiments/sandbox_manifest.json").unlink()
    with pytest.raises(GateBlocked) as e:
        run_skill(tmp_path, "experiment-runner")
    assert e.value.gate == "sandbox_unknown"
    assert "must never" in e.value.reason
    assert not (tmp_path / "experiment_ledger.jsonl").exists()


def test_no_specs_blocks_both_skills(tmp_path):
    seed_project(tmp_path)
    (tmp_path / "experiments/E-001.yaml").unlink()
    for skill in ("codebase-scaffolder", "experiment-runner"):
        with pytest.raises(GateBlocked) as e:
            run_skill(tmp_path, skill)
        assert e.value.gate == "no_experiment_specs" and e.value.remediation


def test_the_ablation_plan_is_not_mistaken_for_an_experiment(project):
    (project / "experiments/ablation_plan.yaml").write_text(
        json.dumps({"ablations": [{"name": "drop-x"}]}), encoding="utf-8")
    run_skill(project, "codebase-scaffolder")
    res = run_skill(project, "experiment-runner")
    assert res.detail["planned_runs"] == len(ARMS) * len(SPEC["seeds"])
    assert {r["experiment_id"] for r in ledger(project)} == {"E-001"}


def test_specs_written_as_one_globbed_file_are_all_found(tmp_path):
    """research-blueprint-compiler writes the whole list to the literal `*.yaml` path.

    The glob in the contract is a path pattern, not a promise about file layout, so
    both layouts have to resolve to the same set of planned runs.
    """
    seed_project(tmp_path)
    (tmp_path / "experiments/E-001.yaml").unlink()
    second = dict(SPEC, experiment_id="E-002", seeds=[7])
    (tmp_path / "experiments" / "*.yaml").write_text(json.dumps([SPEC, second]), encoding="utf-8")

    run_skill(tmp_path, "codebase-scaffolder")
    res = run_skill(tmp_path, "experiment-runner")
    assert res.detail["planned_runs"] == len(ARMS) * 3
    assert {r["experiment_id"] for r in ledger(tmp_path)} == {"E-001", "E-002"}
    assert all(r["status"] == "NOT_RUN" and r["metrics"] == {} for r in ledger(tmp_path))
    assert (tmp_path / "code/e_002/experiment.py").exists()


def test_unspecified_direction_from_the_real_compiler_blocks_ranking(tmp_path):
    """The blueprint compiler emits `direction: "unspecified"` on purpose."""
    spec = dict(SPEC, metrics=[{"name": "accuracy", "direction": "unspecified"}])
    seed_project(tmp_path, isolation=True, evaluator=True, spec=spec)
    run_skill(tmp_path, "codebase-scaffolder")
    (tmp_path / "code/e_001/impl.py").write_text(IMPL, encoding="utf-8")
    run_skill(tmp_path, "experiment-runner")
    assert json.loads((tmp_path / "experiments/ranked_branches.json").read_text())["ranking_possible"] is False
