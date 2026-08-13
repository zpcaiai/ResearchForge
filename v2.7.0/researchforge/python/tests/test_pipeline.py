"""Behavioural tests for the parts that are supposed to refuse."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/papers/arxiv_1706.03762_abs.html"


def run_skill(project, skill, config=None, mode="guided"):
    p = subprocess.run(
        [sys.executable, "-m", "researchforge.runner", "run", "--skill", skill,
         "--project", str(project), "--mode", mode, "--model", "offline", "--offline",
         "--schemas", str(ROOT / "schemas")],
        input=json.dumps({"config": config or {}}), capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "python"),
             # these tests exercise skills, not the paywall; the licence gate has its own test
             "RESEARCHFORGE_ALLOW_UNLICENSED": "1"}, cwd=ROOT)
    return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode


@pytest.fixture
def project(tmp_path):
    run_skill(tmp_path, "project-repo-manager", {"paper_locator": str(FIXTURE)})
    run_skill(tmp_path, "sandbox-provisioner")
    run_skill(tmp_path, "paper-ingest", {"paper_locator": str(FIXTURE)})
    run_skill(tmp_path, "paper-model-builder")
    return tmp_path


def test_an_abstract_page_is_reported_as_insufficient(project):
    warns = json.loads((project / "source/layout_warnings.md").read_text()) \
        if False else (project / "source/layout_warnings.md").read_text()
    assert "abstract page, not a paper" in warns


def test_drafting_before_evidence_lock_is_refused(project):
    """Was a stub test in v0.3.0; manuscript-builder is now implemented.

    The behaviour that matters did not change: asking for a manuscript before the
    evidence is locked must refuse and name what is unlocked. It now refuses as a
    gate rather than as an unbuilt stub, which is a stronger guarantee.
    """
    out, code = run_skill(project, "manuscript-builder")
    assert out["ok"] is False
    assert code in (11, 13), out
    msg = json.dumps(out["error"]).lower()
    # It refuses for the earliest true reason and names the producer that has not
    # run — a more useful refusal than a generic "not ready".
    assert "has not been produced" in msg or ("evidence" in msg and "lock" in msg)
    assert "reproduction-fallback-planner" in msg or "evidence_graph" in msg
    assert not (project / "paper/main.tex").exists()


def test_coverage_starts_unknown_and_names_blind_spots(project):
    out, _ = run_skill(project, "literature-provider-manager")
    cov = json.loads((project / "literature/coverage_report.json").read_text())
    assert cov["status"] == "UNKNOWN_COVERAGE"
    assert cov["measured"] is False and cov["score"] is None
    assert "no full-text search" in cov["named_blind_spots"]


def test_zero_results_do_not_become_evidence_of_novelty(project):
    run_skill(project, "literature-provider-manager")
    out, _ = run_skill(project, "literature-search")
    assert any("NOT evidence" in w for w in out["warnings"])
    cands = (project / "literature/literature_candidates.jsonl")
    assert cands.exists() and cands.read_text().strip() == ""


def _to_repro(project):
    for s in ("literature-provider-manager", "literature-search", "citation-resolver",
              "claim-evidence-graph", "result-reproducer", "reproduction-fallback-planner"):
        run_skill(project, s, {"paper_locator": str(FIXTURE)})


def test_rl0_produces_cm_none_but_does_not_halt(project):
    _to_repro(project)
    cm = json.loads((project / "reproduction/comparison_mode.json").read_text())
    assert cm["derived_from_level"] == "RL0"
    assert cm["mode"] == "CM_NONE"
    # the point: the project narrows, it does not stop
    assert cm["admissible_idea_modes"] == ["explain_diagnose", "benchmark_evaluate"]
    assert cm["disclosure_required"]["required"] is True
    assert "No comparative performance claim" in cm["disclosure_required"]["text_template"]


def test_rl0_portfolio_is_non_empty_and_mode_constrained(project):
    _to_repro(project)
    run_skill(project, "idea-seed-miner")
    run_skill(project, "idea-portfolio-generator")
    ideas = json.loads((project / "ideas/idea_portfolio.json").read_text())
    assert len(ideas) > 0, "RL0 must still yield directions; halting is the old broken behaviour"
    allowed = {"explain_diagnose", "benchmark_evaluate"}
    assert {i["mode"] for i in ideas} <= allowed


def test_novel_enough_is_blocked_under_unknown_coverage(project):
    _to_repro(project)
    run_skill(project, "idea-seed-miner")
    run_skill(project, "idea-portfolio-generator")
    run_skill(project, "idea-evaluator")
    nov = json.loads((project / "ideas/novelty_report.json").read_text())
    assert nov["verdicts"], "no verdicts produced"
    assert all(v["novelty_status"] == "UNKNOWN_COVERAGE" for v in nov["verdicts"])
    assert all(v["blocked_from_novel_enough"] for v in nov["verdicts"])


def test_guided_mode_stops_for_a_human(project):
    _to_repro(project)
    for s in ("idea-seed-miner", "idea-portfolio-generator", "idea-evaluator", "idea-ranker"):
        run_skill(project, s)
    out, code = run_skill(project, "user-feedback-gate")
    assert code == 10 and out.get("needs_human") is True
    assert not (project / "ideas/selected_direction.json").exists()


def test_selection_retains_rejected_candidates(project):
    _to_repro(project)
    for s in ("idea-seed-miner", "idea-portfolio-generator", "idea-evaluator", "idea-ranker"):
        run_skill(project, s)
    out, code = run_skill(project, "user-feedback-gate",
                          {"user_feedback": {"selected": ["I-001"]}})
    assert code == 0, out
    sel = json.loads((project / "ideas/selected_direction.json").read_text())
    assert sel["selected_idea_ids"] == ["I-001"]
    assert len(sel["rejected_but_retained"]) > 0


def test_unknown_idea_id_is_a_gate_not_a_silent_default(project):
    _to_repro(project)
    for s in ("idea-seed-miner", "idea-portfolio-generator", "idea-evaluator", "idea-ranker"):
        run_skill(project, s)
    out, code = run_skill(project, "user-feedback-gate",
                          {"user_feedback": {"selected": ["I-999"]}})
    assert code == 11 and out["error"]["kind"] == "gate_blocked"


def test_offline_output_is_marked_synthetic(project):
    _to_repro(project)
    out, _ = run_skill(project, "idea-seed-miner")
    assert out["synthetic"] is True
    assert any("OFFLINE" in w for w in out["warnings"])


def test_every_artifact_write_is_provenanced(project):
    events = [json.loads(l) for l in (project / "provenance.jsonl").read_text().splitlines() if l]
    writes = [e for e in events if e["kind"] == "artifact_write"]
    assert writes and all(e["digest"] and e["path"] for e in writes)
