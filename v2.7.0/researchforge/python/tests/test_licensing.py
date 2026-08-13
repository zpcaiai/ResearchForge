"""The paywall must actually gate.

Before this existed, `verify()` computed the restricted feature list correctly and
the CLI printed it — and then ran every gated stage anyway. A paywall that only
prints is not a paywall, and invoicing for a feature the customer already has is
not a business.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from researchforge.licensing import COMMUNITY_FEATURES, FEATURE_OF_SKILL, check, load

ROOT = Path(__file__).resolve().parents[2]


def run_skill(project, skill, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "python")}
    env.pop("RESEARCHFORGE_ALLOW_UNLICENSED", None)
    env.update(env_extra or {})
    p = subprocess.run(
        [sys.executable, "-m", "researchforge.runner", "run", "--skill", skill,
         "--project", str(project), "--model", "offline", "--offline",
         "--schemas", str(ROOT / "schemas")],
        input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
    return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode


def test_community_covers_everything_up_to_the_human_gate():
    free = {"paper-ingest", "paper-model-builder", "literature-search", "citation-resolver",
            "result-reproducer", "reproduction-fallback-planner", "idea-seed-miner",
            "idea-portfolio-generator", "idea-evaluator", "idea-ranker", "user-feedback-gate"}
    for s in free:
        assert check(s) is None, f"{s} must be free: it is where the differentiator is visible"


def test_paid_skills_are_actually_refused(tmp_path):
    for skill in ("experiment-runner", "manuscript-builder", "deck-factory", "release-gate"):
        gate = check(skill)
        assert gate is not None and gate["permitted"] is False, skill
        out, code = run_skill(tmp_path, skill)
        assert code == 14, (skill, out)
        assert out["error"]["kind"] == "licence_required"
        # the refusal has to name the feature and the tier, or the buyer cannot act on it
        assert out["error"]["feature"] == FEATURE_OF_SKILL[skill]
        assert "community" in out["error"]["message"]


def test_the_override_is_explicit_and_leaves_a_record(tmp_path):
    out, code = run_skill(tmp_path, "manuscript-builder",
                          {"RESEARCHFORGE_ALLOW_UNLICENSED": "1"})
    # it gets past the gate and fails for its own reasons, which is the point
    assert code != 14, out
    events = [json.loads(l) for l in (tmp_path / "provenance.jsonl").read_text().splitlines() if l]
    overrides = [e for e in events if e["detail"].get("kind") == "licence_override"]
    assert overrides, "an override that leaves no record is indistinguishable from a patch"
    assert overrides[0]["detail"]["feature"] == "manuscript"


def test_an_unverifiable_licence_file_is_treated_as_community(tmp_path):
    lic = tmp_path / "license.json"
    lic.write_text(json.dumps({
        "license": {"licensee": "attacker", "edition": "site", "expires": None,
                    "features": ["experiment-engine", "manuscript", "deck", "release-gate"],
                    "issued": "2026-01-01"},
        "signature": "bm90LWEtc2lnbmF0dXJl"}))
    os.environ["RESEARCHFORGE_LICENSE"] = str(lic)
    try:
        st = load()
        # a self-written licence must not unlock anything
        assert st.edition == "community"
        assert not st.allows("manuscript")
        assert check("manuscript-builder")["permitted"] is False
    finally:
        os.environ.pop("RESEARCHFORGE_LICENSE", None)


def test_a_corrupt_licence_file_degrades_rather_than_crashing(tmp_path):
    lic = tmp_path / "license.json"
    lic.write_text("{not json")
    os.environ["RESEARCHFORGE_LICENSE"] = str(lic)
    try:
        st = load()
        assert st.edition == "community" and st.valid
        assert "unreadable" in st.reason
    finally:
        os.environ.pop("RESEARCHFORGE_LICENSE", None)


def test_every_gated_skill_maps_to_a_real_feature():
    from researchforge.generated import SKILLS
    for skill, feature in FEATURE_OF_SKILL.items():
        assert skill in SKILLS, skill
        assert feature not in COMMUNITY_FEATURES, f"{feature} cannot be both free and paid"
