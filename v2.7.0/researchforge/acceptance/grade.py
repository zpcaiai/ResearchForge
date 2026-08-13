#!/usr/bin/env python3
"""The acceptance grader: does a finished ResearchForge project contain acceptable research?

This runs after a full run, on a real key and real hardware, and answers one
question with a machine-checkable answer. It is deliberately NOT a second opinion
on the questions the pipeline already answers. `claim-citation-auditor` decides
whether a claim is fabricated; `integrity-auditor` decides whether the statistics
hold; `release-gate` decides whether an artifact may ship. Re-deriving any of
those verdicts here would create a second threshold for the same question, and the
lower of two thresholds is the one that governs — which means the careful one
upstream would silently stop mattering. So those verdicts are CONSUMED.

What this file adds is the set of quantities nothing upstream computes, because
every upstream check is per-item and this one is per-project: coverage and ratio.
The gate refuses one fabricated claim; it has nothing to say about a manuscript
where 40% of the claims are only partially supported. The figure factory refuses
one figure bound to an unknown claim; it has nothing to say about a claim with no
figure. The blueprint compiler writes an ablation per mechanism; nothing checks
that the ablation ever ran.

The other rule this file keeps is that a dimension it could not measure is
NOT_MEASURED, which is a distinct outcome from PASS and blocks acceptance exactly
as a FAIL does. A grader that scores an absent artifact as a pass is the failure
mode the whole system is built against, reappearing in the grader.

Usage:
    python3 grade.py PROJECT_DIR [--second-run DIR] [--out DIR] [--quiet]

Exit codes: 0 ACCEPTED, 1 REJECTED, 2 INCOMPLETE (something was not measurable),
3 REFUSED (synthetic project, or the grader could not start).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUBRIC_VERSION = "1.0.0"

PASS, FAIL, NOT_MEASURED, NOT_APPLICABLE = "PASS", "FAIL", "NOT_MEASURED", "NOT_APPLICABLE"

# --------------------------------------------------------------------------
# Thresholds that already exist somewhere in the runtime.
#
# These are copies, and a copy that drifts is worse than no copy at all: the
# grader would then hold the project to a number the project never agreed to. So
# `_assert_constants_agree` re-reads them from the package when the package is
# importable and refuses to grade on a disagreement rather than quietly using the
# stale value. Every one of them is sourced, not chosen.
# --------------------------------------------------------------------------
#: python/researchforge/skills/planning.py::MIN_SEEDS_FOR_COMPARATIVE
#: python/researchforge/skills/analysis.py::MIN_SEEDS_FOR_INFERENCE
MIN_SEEDS_INFERENTIAL = 3
#: analysis.py::_summary refuses an interval below n=2 ("a single observation
#: carries no dispersion information"), so 2 is the floor for any claim that
#: reports a spread at all, even a purely descriptive one.
MIN_SEEDS_DESCRIPTIVE = 2
#: analysis.py::DEFAULT_ALPHA — overridden per project by stats_audit["alpha"]
DEFAULT_ALPHA = 0.05
#: analysis.py::IntegrityAuditor.execute, criteria["max_failure_rate"] default
MAX_FAILURE_RATE = 0.34

#: artifacts_out.py::SYNTHETIC_KEYS — the same markers the release gate refuses on
SYNTHETIC_KEYS = ("synthetic", "_synthetic", "is_synthetic")

#: Baseline kinds planning.py::_baseline emits per comparison mode. A spec whose
#: baseline kind belongs to another mode is making a comparison the mode does not
#: license, whatever its prose says.
BASELINE_KIND_FOR_MODE = {
    "CM_MEASURED": {"locally_measured"},
    "CM_RELATIVE": {"locally_measured_reduced_scale"},
    "CM_REPORTED": {"reported_by_authors"},
    "CM_NONE": {"internal_reference_condition"},
}
#: Ablation and evaluation specs are internal contrasts against our own artifact,
#: so their baseline is not drawn from the mode's external-baseline vocabulary.
INTERNAL_BASELINE_KINDS = {"own_full_method", "metric_under_test",
                           "internal_reference_condition"}

#: planning.py claim types that assert a between-condition difference. These are
#: the ones that need dispersion, i.e. MIN_SEEDS_INFERENTIAL.
INFERENTIAL_CLAIM_TYPES = {"comparative", "ablation"}


class GraderRefusal(Exception):
    """The grader will not produce a score. Not a low score — no score."""


# --------------------------------------------------------------------------
# results model
# --------------------------------------------------------------------------
@dataclass
class Check:
    id: str
    outcome: str
    detail: str
    measured: Any = None
    threshold: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "outcome": self.outcome, "measured": self.measured,
                "threshold": self.threshold, "detail": self.detail}


@dataclass
class Dimension:
    id: str
    title: str
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    @property
    def outcome(self) -> str:
        outs = [c.outcome for c in self.checks]
        if not outs:
            return NOT_MEASURED
        # FAIL outranks NOT_MEASURED: a dimension with one proven defect is failed
        # whether or not some other part of it was measurable. Both block
        # acceptance, so the ordering only affects what the operator is told to fix
        # first, and a proven defect is the more actionable of the two.
        if FAIL in outs:
            return FAIL
        if NOT_MEASURED in outs:
            return NOT_MEASURED
        if all(o == NOT_APPLICABLE for o in outs):
            return NOT_APPLICABLE
        return PASS

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "outcome": self.outcome,
                "checks": [c.as_dict() for c in self.checks]}


def ok(cid: str, detail: str, measured: Any = None, threshold: Any = None) -> Check:
    return Check(cid, PASS, detail, measured, threshold)


def bad(cid: str, detail: str, measured: Any = None, threshold: Any = None) -> Check:
    return Check(cid, FAIL, detail, measured, threshold)


def unmeasured(cid: str, detail: str) -> Check:
    return Check(cid, NOT_MEASURED, detail)


def na(cid: str, detail: str) -> Check:
    return Check(cid, NOT_APPLICABLE, detail)


def gate(cid: str, condition: bool, pass_detail: str, fail_detail: str,
         measured: Any = None, threshold: Any = None) -> Check:
    return (ok(cid, pass_detail, measured, threshold) if condition
            else bad(cid, fail_detail, measured, threshold))


# --------------------------------------------------------------------------
# project loading
# --------------------------------------------------------------------------
class Project:
    """Artifact access by contract id, never by hardcoded path.

    Paths come from manifests/artifact-graph.json because that file is the only
    source of truth for where an artifact lives (docs/CONTRACT.md). A grader with
    its own copy of the layout would grade a project the runtime does not produce.
    """

    def __init__(self, project: Path, repo_root: Path) -> None:
        self.project = Path(project).resolve()
        self.repo_root = Path(repo_root).resolve()
        graph_path = self.repo_root / "manifests" / "artifact-graph.json"
        if not graph_path.exists():
            raise GraderRefusal(
                f"cannot read the artifact contract at {graph_path}. The grader resolves every "
                f"artifact through the contract and will not guess paths; pass --repo-root or set "
                f"RESEARCHFORGE_ROOT to the repository that produced this project.")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        self.specs_by_id: dict[str, dict[str, Any]] = dict(graph.get("artifacts") or {})
        for owner, internals in (graph.get("internal_artifacts") or {}).items():
            for aid, spec in internals.items():
                # Internal artifacts leave the public contract but not the disk
                # (CONTRACT.md rule 5). The grader is not a skill and is not bound by
                # the read rules, but it must still find them where they actually are.
                self.specs_by_id.setdefault(aid, {**spec, "producer": owner, "internal": True})
        self._cache: dict[str, Any] = {}
        self.missing: set[str] = set()

    # -- paths ----------------------------------------------------------
    def rel(self, aid: str) -> str:
        spec = self.specs_by_id.get(aid)
        if spec is None:
            raise GraderRefusal(f"'{aid}' is not in the artifact contract")
        return str(spec["path"]).split("|")[0]

    def path(self, aid: str) -> Path:
        return self.project / self.rel(aid)

    def is_dir_artifact(self, aid: str) -> bool:
        return self.rel(aid).endswith("/")

    def exists(self, aid: str) -> bool:
        p = self.path(aid)
        return (p / "_manifest.json").exists() if self.is_dir_artifact(aid) else p.exists()

    # -- reads ----------------------------------------------------------
    def get(self, aid: str, default: Any = None) -> Any:
        """Read an artifact, or record that it was absent and return `default`.

        Every absence is remembered. A dimension that consumed a missing artifact
        must report NOT_MEASURED, and it can only know to do that if the loader
        keeps the list.
        """
        if aid in self._cache:
            return self._cache[aid]
        p = self.path(aid)
        if self.is_dir_artifact(aid):
            p = p / "_manifest.json"
        if not p.exists():
            self.missing.add(aid)
            return default
        try:
            if p.suffix == ".jsonl":
                value: Any = [json.loads(line) for line in
                              p.read_text(encoding="utf-8").splitlines() if line.strip()]
            elif p.suffix == ".json":
                value = json.loads(p.read_text(encoding="utf-8"))
            else:
                value = p.read_text(encoding="utf-8", errors="replace")
        except (json.JSONDecodeError, OSError):
            # An artifact that exists but cannot be parsed is not a pass and is not
            # an absence: it is a corrupt input, and the dimension that needed it
            # must say so rather than fall through to a default.
            self.missing.add(aid)
            return default
        self._cache[aid] = value
        return value

    def experiment_specs(self) -> list[dict[str, Any]]:
        """Resolve the `experiments/*.yaml` glob the way execution.py resolves it.

        Same rule as `_load_experiment_specs`: a file in `experiments/` without an
        `experiment_id` is not an experiment. `ablation_plan.yaml` lives there too,
        and counting it as a spec would invent a planned run.
        """
        root = self.project / "experiments"
        out: list[dict[str, Any]] = []
        if not root.is_dir():
            self.missing.add("experiment_specs")
            return out
        for p in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # planning.py writes JSON into a .yaml file on purpose (JSON is a
                # subset of YAML 1.2). Anything this cannot parse is not something
                # the compiler wrote, so it is skipped rather than guessed at.
                continue
            for item in (obj if isinstance(obj, list) else [obj]):
                if isinstance(item, dict) and item.get("experiment_id"):
                    out.append(item)
        if not out:
            self.missing.add("experiment_specs")
        return out


# --------------------------------------------------------------------------
# small shared readings of the ledger — deliberately the same conventions the
# analysis plane uses, so the grader and the auditor are looking at one dataset
# --------------------------------------------------------------------------
def entry_ok(entry: dict) -> bool:
    """analysis.py::_ok — the statuses that count as a measurement."""
    return str(entry.get("status", "")).lower() in (
        "ok", "success", "succeeded", "completed", "pass")


def entry_seed(entry: dict) -> Any:
    if entry.get("seed") is not None:
        return entry["seed"]
    return (entry.get("provenance") or {}).get("seed")


def metric_names(spec: dict) -> list[str]:
    out = []
    for m in spec.get("metrics") or []:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict) and m.get("name"):
            out.append(str(m["name"]))
    return out


def numeric_metrics(entry: dict) -> dict[str, float]:
    out = {}
    for k, v in (entry.get("metrics") or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[k] = float(v)
    return out


def synthetic_hits(label: str, payload: Any, path: str = "") -> list[str]:
    """artifacts_out.py::_synthetic_hits, applied to the same markers.

    Only `is True` counts. `"_synthetic": false` is the runtime saying the opposite
    of what this looks for, and treating a present-but-false flag as a hit would
    make the grader refuse every honest project.
    """
    hits: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            here = f"{path}.{k}" if path else k
            if k in SYNTHETIC_KEYS and v is True:
                hits.append(f"{label}:{here}")
            hits += synthetic_hits(label, v, here)
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            hits += synthetic_hits(label, v, f"{path}[{i}]")
    return hits


# --------------------------------------------------------------------------
# D1 — experiment engineering
# --------------------------------------------------------------------------
def dim_experiments(p: Project) -> Dimension:
    d = Dimension("experiment_engineering", "Experiment engineering")
    specs = p.experiment_specs()
    ledger = p.get("experiment_ledger", default=None)
    if not specs:
        d.add(unmeasured("E1.specs_present",
                         "no ExperimentSpec is on disk under experiments/, so nothing about the "
                         "experiments can be measured — not their metrics, seeds or conditions"))
        return d
    if ledger is None:
        d.add(unmeasured("E1.ledger_present",
                         "experiment_ledger.jsonl is absent, so whether the specs ever ran is "
                         "unknown"))
        return d

    by_experiment: dict[str, list[dict]] = {}
    for e in ledger:
        by_experiment.setdefault(str(e.get("experiment_id")), []).append(e)

    # -- E1.1 every spec produced at least one measured run --------------
    never_ran = [s["experiment_id"] for s in specs
                 if not any(entry_ok(e) and numeric_metrics(e)
                            for e in by_experiment.get(str(s["experiment_id"]), []))]
    d.add(gate("E1.1_every_spec_ran",
               not never_ran,
               f"all {len(specs)} specs produced at least one completed run carrying metrics",
               f"specs that never produced a measured run: {never_ran}. A spec that did not run "
               f"is a plan, and the paper cannot cite a plan.",
               measured=len(specs) - len(never_ran), threshold=len(specs)))

    # -- E1.2 run success rate -------------------------------------------
    total = len(ledger)
    completed = sum(1 for e in ledger if entry_ok(e))
    rate = completed / total if total else 0.0
    floor = 1.0 - MAX_FAILURE_RATE
    d.add(gate("E1.2_completion_rate",
               rate >= floor - 1e-9,
               f"{completed}/{total} planned runs completed ({rate:.0%})",
               f"only {completed}/{total} planned runs completed ({rate:.0%}); the project's own "
               f"max_failure_rate of {MAX_FAILURE_RATE} puts the floor at {floor:.0%}. Above that "
               f"failure rate the surviving runs are a filtered sample, not the experiment.",
               measured=round(rate, 4), threshold=round(floor, 4)))

    # -- E1.3 every metric declared in its spec ---------------------------
    undeclared: list[str] = []
    for s in specs:
        declared = set(metric_names(s))
        for e in by_experiment.get(str(s["experiment_id"]), []):
            if not entry_ok(e):
                continue
            for m in numeric_metrics(e):
                if m not in declared:
                    undeclared.append(f"{s['experiment_id']}:{m}")
    d.add(gate("E1.3_metrics_declared",
               not undeclared,
               "every metric in every completed run is declared by its spec",
               f"metrics recorded but never declared: {sorted(set(undeclared))}. A metric that "
               f"appears only after the run is a metric chosen with the data in view; there is no "
               f"acceptable fraction of that, so the threshold is zero.",
               measured=len(set(undeclared)), threshold=0))

    # -- E1.4 seed adequacy per claim type --------------------------------
    seed_rows, inadequate = [], []
    for s in specs:
        claim_type = str(s.get("claim_type") or "unspecified")
        required = (MIN_SEEDS_INFERENTIAL if claim_type in INFERENTIAL_CLAIM_TYPES
                    else MIN_SEEDS_DESCRIPTIVE)
        # Declared seeds are an intention. The seeds that completed are the sample
        # size the claim actually rests on, so that is what is graded.
        got = len({str(entry_seed(e)) for e in by_experiment.get(str(s["experiment_id"]), [])
                   if entry_ok(e) and numeric_metrics(e) and entry_seed(e) is not None})
        seed_rows.append({"experiment_id": s["experiment_id"], "claim_type": claim_type,
                          "seeds_declared": len(s.get("seeds") or []),
                          "seeds_completed": got, "seeds_required": required})
        if got < required:
            inadequate.append(f"{s['experiment_id']} ({claim_type}): {got} < {required}")
    d.add(gate("E1.4_seed_adequacy",
               not inadequate,
               f"every spec completed at least the seeds its claim type requires "
               f"({MIN_SEEDS_INFERENTIAL} for a between-condition claim, "
               f"{MIN_SEEDS_DESCRIPTIVE} otherwise)",
               f"under-seeded specs: {inadequate}. Below the floor the dispersion the claim is "
               f"compared against cannot be estimated, so the claim is a single draw.",
               measured=seed_rows, threshold={"inferential": MIN_SEEDS_INFERENTIAL,
                                              "descriptive": MIN_SEEDS_DESCRIPTIVE}))

    # -- E1.5 invalid_conditions: declared, machine-evaluable, and executed
    without = [s["experiment_id"] for s in specs if not (s.get("invalid_conditions") or [])]
    d.add(gate("E1.5a_invalid_conditions_declared",
               not without,
               "every spec declares the conditions under which its result is void",
               f"specs with no invalid_conditions: {without}. An experiment that cannot come back "
               f"void is not an experiment, it is a plan to produce numbers.",
               measured=len(specs) - len(without), threshold=len(specs)))

    def evaluable(cond: Any) -> bool:
        # rf_runtime.check_invalid_conditions can only evaluate a condition shaped
        # {metric, op, value}; anything else it returns as *unchecked*. A condition
        # it cannot evaluate is documentation, not a runtime guard.
        return (isinstance(cond, dict) and cond.get("metric") is not None
                and cond.get("op") in (">", ">=", "<", "<=", "==", "!="))

    no_evaluable = [s["experiment_id"] for s in specs
                    if not any(evaluable(c) for c in (s.get("invalid_conditions") or []))]
    d.add(gate("E1.5b_invalid_conditions_machine_evaluable",
               not no_evaluable,
               "every spec carries at least one invalid_condition the runtime can evaluate",
               f"specs whose invalid_conditions are all prose: {no_evaluable}. "
               f"rf_runtime.check_invalid_conditions returns a prose condition as *unchecked*, "
               f"never as satisfied, so these runs were never guarded at runtime by anything.",
               measured=len(specs) - len(no_evaluable), threshold=len(specs)))

    unverifiable, unguarded = [], []
    for e in ledger:
        if not (entry_ok(e) and numeric_metrics(e)):
            continue
        prov = e.get("provenance") or {}
        rel = prov.get("entry_point")
        if not rel:
            unverifiable.append(f"{e.get('experiment_id')}:seed={entry_seed(e)} (no entry_point)")
            continue
        f = p.project / str(rel)
        if not f.exists():
            unverifiable.append(f"{rel} (recorded but not on disk)")
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        if "check_invalid_conditions(" not in src or "INVALID_CONDITIONS" not in src:
            unguarded.append(str(rel))
    if unverifiable:
        d.add(unmeasured("E1.5c_invalid_conditions_checked_at_runtime",
                         f"the executed entry points cannot be inspected: {sorted(set(unverifiable))[:5]}. "
                         f"Whether the void conditions were evaluated during the run is therefore "
                         f"unknown, and unknown is not a pass."))
    else:
        d.add(gate("E1.5c_invalid_conditions_checked_at_runtime",
                   not unguarded,
                   "every executed entry point calls check_invalid_conditions with its spec's "
                   "conditions",
                   f"entry points that never evaluate their invalid_conditions: "
                   f"{sorted(set(unguarded))}. The conditions were written down and then not used.",
                   measured=len(set(unguarded)), threshold=0))
    return d


# --------------------------------------------------------------------------
# D2 — baselines
# --------------------------------------------------------------------------
def dim_baselines(p: Project) -> Dimension:
    d = Dimension("baselines", "Baselines")
    specs = p.experiment_specs()
    cm = p.get("comparison_mode")
    if not specs:
        d.add(unmeasured("E2.specs_present", "no ExperimentSpec on disk; no baseline to grade"))
        return d
    if not isinstance(cm, dict) or not cm.get("mode"):
        d.add(unmeasured("E2.mode_present",
                         "reproduction/comparison_mode.json is absent, so what comparison the "
                         "project was licensed to make is unknown"))
        return d
    mode = str(cm["mode"])

    no_baseline = [s["experiment_id"] for s in specs
                   if not isinstance(s.get("baseline"), dict) or not s["baseline"].get("kind")]
    d.add(gate("E2.1_baseline_condition_exists",
               not no_baseline,
               f"all {len(specs)} specs name a baseline condition",
               f"specs with no baseline condition: {no_baseline}. Without a referent, a number is "
               f"a measurement of nothing in particular.",
               measured=len(specs) - len(no_baseline), threshold=len(specs)))

    # -- E2.2 established, or explicitly declared unestablished ----------
    silent: list[str] = []
    for s in specs:
        b = s.get("baseline") or {}
        if b.get("kind") in INTERNAL_BASELINE_KINDS or b.get("established") is True:
            continue
        codes = {str(c.get("code")) for c in (s.get("invalid_conditions") or [])
                 if isinstance(c, dict)}
        # An unpinned baseline is acceptable; an unpinned baseline nobody wrote
        # down is not. The compiler's own remedy is BASELINE_NOT_ESTABLISHED as an
        # invalid condition, so its presence is what "explicitly declared" means.
        if "BASELINE_NOT_ESTABLISHED" not in codes:
            silent.append(str(s["experiment_id"]))
    d.add(gate("E2.2_baseline_pinned_or_declared_unpinned",
               not silent,
               "every external baseline is either pinned or carries BASELINE_NOT_ESTABLISHED as an "
               "invalid condition",
               f"specs with an unpinned baseline and no BASELINE_NOT_ESTABLISHED condition: "
               f"{silent}. An unpinned baseline can change between conditions, and every "
               f"difference then belongs to the baseline.",
               measured=len(specs) - len(silent), threshold=len(specs)))

    # -- E2.3 the mode permits the comparison actually made --------------
    allowed = BASELINE_KIND_FOR_MODE.get(mode, set()) | INTERNAL_BASELINE_KINDS
    wrong = [f"{s['experiment_id']}:{(s.get('baseline') or {}).get('kind')}" for s in specs
             if (s.get("baseline") or {}).get("kind") not in allowed]
    comparative_under_none = [s["experiment_id"] for s in specs
                              if mode == "CM_NONE" and (s.get("claim_type") == "comparative"
                                                        or s.get("comparative_claim") is True)]
    fail_detail = (
        f"under {mode} these baselines are not admissible: {wrong}; comparative specs under "
        f"CM_NONE: {comparative_under_none}. The comparison mode is what licenses the comparison; "
        f"a spec outside it is comparing against something the project never established.")
    d.add(gate("E2.3_comparison_permitted_by_mode",
               not wrong and not comparative_under_none,
               f"every baseline kind is admissible under {mode}",
               fail_detail,
               measured={"mode": mode, "offending_specs": len(wrong) + len(comparative_under_none)},
               threshold=0))
    return d


# --------------------------------------------------------------------------
# D3 — ablations
# --------------------------------------------------------------------------
def dim_ablations(p: Project) -> Dimension:
    d = Dimension("ablations", "Ablations")
    plan = p.get("ablation_plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            plan = None
    specs = p.experiment_specs()
    ledger = p.get("experiment_ledger", default=None)
    if not isinstance(plan, dict):
        d.add(unmeasured("E3.plan_present",
                         "experiments/ablation_plan.yaml is absent or unreadable, so the set of "
                         "claimed mechanisms is unknown and coverage over it cannot be computed"))
        return d
    if ledger is None:
        d.add(unmeasured("E3.ledger_present",
                         "experiment_ledger.jsonl is absent; whether the ablations ran is unknown"))
        return d

    ablations = list(plan.get("ablations") or [])
    mechanisms = {str(a.get("mechanism_id")) for a in ablations if a.get("mechanism_id")}
    if not mechanisms:
        d.add(na("E3.mechanisms_declared",
                 "the ablation plan claims no mechanism, so there is nothing to isolate"))
        return d

    spec_by_ablation = {str(s.get("ablation_ref")): s for s in specs if s.get("ablation_ref")}
    measured_ids = {str(e.get("experiment_id")) for e in ledger
                    if entry_ok(e) and numeric_metrics(e)}

    # -- E3.1 an isolation test per claimed mechanism, that ran ----------
    uncovered = []
    for a in ablations:
        s = spec_by_ablation.get(str(a.get("ablation_id")))
        if s is None:
            uncovered.append(f"{a.get('mechanism_id')} (no spec compiles {a.get('ablation_id')})")
        elif str(s["experiment_id"]) not in measured_ids:
            uncovered.append(f"{a.get('mechanism_id')} ({s['experiment_id']} produced no "
                             f"measured run)")
    coverage = (len(mechanisms) - len(uncovered)) / len(mechanisms)
    d.add(gate("E3.1_isolation_test_per_mechanism",
               not uncovered,
               f"all {len(mechanisms)} claimed mechanism(s) have an isolation test that produced "
               f"measurements",
               f"mechanisms with no executed isolation test: {uncovered}. An uncovered mechanism "
               f"is an unfalsified mechanism, and a partial threshold would license claiming "
               f"exactly the ones nobody tested.",
               measured=round(coverage, 4), threshold=1.0))

    # -- E3.2 compute matched, in the plan and in the runs ---------------
    unmatched = []
    primary_seed_counts = {len(s.get("seeds") or []) for s in specs
                           if s.get("claim_type") not in ("ablation",)}
    for a in ablations:
        controls = {str(c.get("kind")) for c in (a.get("counterfactual_controls") or [])
                    if isinstance(c, dict)}
        if "matched_compute" not in controls:
            unmatched.append(f"{a.get('ablation_id')}: no matched_compute control in the plan")
            continue
        s = spec_by_ablation.get(str(a.get("ablation_id")))
        if s is not None and primary_seed_counts and len(s.get("seeds") or []) not in primary_seed_counts:
            # Matched compute that exists only in the plan is not matched compute.
            # The seed budget is the part of it the ledger can actually witness.
            unmatched.append(f"{a.get('ablation_id')}: {len(s.get('seeds') or [])} seeds against "
                             f"{sorted(primary_seed_counts)} for the non-ablation specs")
    d.add(gate("E3.2_compute_matched",
               not unmatched,
               "every ablation declares a matched-compute counterfactual and runs on the same seed "
               "budget as the primary specs",
               f"ablations whose compute is not matched: {unmatched}. Without it, a degradation on "
               f"removal is explained just as well by the mechanism having cost more.",
               measured=len(ablations) - len(unmatched), threshold=len(ablations)))

    # -- E3.3 a null result is reported, not re-metered ------------------
    primary_metrics = {m for s in specs if s.get("claim_type") != "ablation"
                       for m in metric_names(s)}
    disjoint = []
    for a in ablations:
        s = spec_by_ablation.get(str(a.get("ablation_id")))
        if s is None or not primary_metrics:
            continue
        shared = [m for m in metric_names(s) if m in primary_metrics]
        if not shared:
            disjoint.append(f"{s['experiment_id']}: {metric_names(s)} vs "
                            f"{sorted(primary_metrics)}")
    d.add(gate("E3.3a_ablation_shares_a_metric_with_its_primary_experiment",
               not disjoint,
               "every ablation measures at least one metric its primary experiments also measure",
               f"ablations whose metric set is disjoint from the primary experiments': {disjoint}. "
               f"Nothing can be concluded from such an isolation: analysis groups the ledger by "
               f"(branch, metric), so a disjoint metric set means no contrast between the ablation "
               f"and the experiment it is supposed to isolate a mechanism for can ever be "
               f"computed. It is also the shape a null result rescued by a new metric takes.",
               measured=len(disjoint), threshold=0))

    stats = p.get("stats_audit")
    negatives = p.get("negative_findings", default=None)
    findings = p.get("findings", default=[])
    if not isinstance(stats, dict) or negatives is None:
        d.add(unmeasured("E3.3b_null_results_reported",
                         "stats_audit.json or findings/negative_findings.jsonl is absent, so "
                         "whether a null ablation was written down or quietly dropped cannot be "
                         "determined"))
        return d

    recorded = json.dumps(list(negatives) + list(findings or []), default=str)
    null_ablations, unreported = [], []
    for t in stats.get("tests") or []:
        branches = {str(t.get("branch_a")), str(t.get("branch_b"))}
        for a in ablations:
            s = spec_by_ablation.get(str(a.get("ablation_id")))
            if s is None or str(s["experiment_id"]) not in branches:
                continue
            # Significant-after-correction is a positive result and needs nothing
            # here. Everything else — non-significant, or refused for want of data
            # — is a null the project owes the reader.
            if t.get("significant_corrected") is True:
                continue
            null_ablations.append(str(s["experiment_id"]))
            if (str(s["experiment_id"]) not in recorded
                    and str(a.get("ablation_id")) not in recorded
                    and str(a.get("mechanism_id")) not in recorded):
                unreported.append(str(s["experiment_id"]))
    if not null_ablations:
        d.add(ok("E3.3b_null_results_reported",
                 "no ablation produced a null result, so there is none to report",
                 measured=0, threshold=0))
    else:
        d.add(gate("E3.3b_null_results_reported",
                   not unreported,
                   f"all {len(set(null_ablations))} null ablation(s) appear in the findings",
                   f"null ablations that appear in no finding: {sorted(set(unreported))}. A null "
                   f"result that is not written down is indistinguishable from one that was "
                   f"retried until it stopped being null.",
                   measured=len(set(null_ablations)) - len(set(unreported)),
                   threshold=len(set(null_ablations))))
    return d


# --------------------------------------------------------------------------
# D4 — evidence support
# --------------------------------------------------------------------------
def dim_evidence(p: Project) -> Dimension:
    d = Dimension("evidence_support", "Evidence support")
    audit = p.get("claim_audit", default=None)
    gate_art = p.get("integrity_gate")
    if audit is None:
        d.add(unmeasured("E4.claim_audit_present",
                         "review/claim_audit.jsonl is absent. Claim support is the one thing this "
                         "grader will not estimate: without the auditor's verdicts there is no "
                         "measurement to report."))
        return d
    if not isinstance(gate_art, dict):
        d.add(unmeasured("E4.integrity_gate_present",
                         "review/integrity_gate.json is absent, so the auditor's own tally cannot "
                         "be consumed and this grader will not re-derive it"))
        return d

    # The counts are the auditor's, read as given. Nothing here re-grades a claim.
    counts = dict(gate_art.get("counts") or {})
    if not counts:
        counts = {}
        for rec in audit:
            counts[str(rec.get("verdict"))] = counts.get(str(rec.get("verdict")), 0) + 1

    fabricated = int(counts.get("FABRICATED", 0))
    d.add(gate("E4.1_zero_fabricated",
               fabricated == 0,
               "the auditor found no fabricated claim",
               f"{fabricated} claim(s) are FABRICATED: a number or a citation in the manuscript "
               f"corresponds to nothing that exists. One is disqualifying; there is no rate at "
               f"which fabrication is acceptable.",
               measured=fabricated, threshold=0))

    graded = [rec for rec in audit
              if str(rec.get("verdict")) in ("SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED",
                                             "FABRICATED", "SCOPE_MISMATCH")
              and rec.get("kind") != "disclosure"]
    supported = sum(1 for rec in graded if str(rec.get("verdict")) == "SUPPORTED")
    ratio = supported / len(graded) if graded else None

    stats = p.get("stats_audit")
    alpha = float(stats.get("alpha", DEFAULT_ALPHA)) if isinstance(stats, dict) else DEFAULT_ALPHA
    floor = 1.0 - alpha
    if ratio is None:
        d.add(unmeasured("E4.2_support_ratio",
                         "the claim audit graded no claim, so a support ratio has no denominator"))
    else:
        d.add(gate("E4.2_support_ratio",
                   ratio >= floor - 1e-9,
                   f"{supported}/{len(graded)} graded claims are fully SUPPORTED ({ratio:.0%})",
                   f"only {supported}/{len(graded)} graded claims are fully SUPPORTED ({ratio:.0%}); "
                   f"the floor is {floor:.0%}. The integrity gate blocks FABRICATED, NOT_SUPPORTED "
                   f"and SCOPE_MISMATCH individually but lets PARTIALLY_SUPPORTED through in any "
                   f"quantity, so this ratio is the only thing standing between a passing gate and "
                   f"a manuscript that overstates its evidence throughout.",
                   measured=round(ratio, 4), threshold=round(floor, 4)))

    # -- E4.3 every quantitative claim traces to a run -------------------
    quantitative = [rec for rec in audit if rec.get("quantitative")]
    untraced = [f"{rec.get('claim_id')}@{rec.get('locator')}" for rec in quantitative
                if not rec.get("number_checks")
                or not all(c.get("matched") for c in rec["number_checks"])]
    if not quantitative:
        d.add(na("E4.3_quantitative_claims_traced",
                 "the manuscript states no quantitative claim, so there is no number to trace"))
    else:
        d.add(gate("E4.3_quantitative_claims_traced",
                   not untraced,
                   f"all {len(quantitative)} quantitative claims trace to a recorded ledger value",
                   f"quantitative claims with an untraced number: {untraced[:8]}. A number with no "
                   f"run behind it is fabricated regardless of how it got there.",
                   measured=len(quantitative) - len(untraced), threshold=len(quantitative)))
    return d


# --------------------------------------------------------------------------
# D5 — statistical validity
# --------------------------------------------------------------------------
def dim_statistics(p: Project) -> Dimension:
    d = Dimension("statistical_validity", "Statistical validity")
    stats = p.get("stats_audit")
    if not isinstance(stats, dict):
        d.add(unmeasured("E5.stats_audit_present",
                         "analysis/stats_audit.json is absent; the statistics were never audited, "
                         "and this grader does not compute them itself"))
        return d

    lock = stats.get("evidence_lock") or {}
    d.add(gate("E5.1_evidence_lock_clear",
               lock.get("blocked") is False,
               "the integrity auditor locked the evidence: no BLOCKER or HIGH finding survives",
               f"the integrity auditor refused evidence lock ({lock.get('blocked_by')}). This "
               f"verdict is consumed, not recomputed: a claim whose statistics did not survive "
               f"that audit cannot be written as a result.",
               measured=lock.get("blocked"), threshold=False))

    tests = list(stats.get("tests") or [])
    live = [t for t in tests if t.get("p_raw") is not None]
    if not tests:
        d.add(na("E5.2_effect_sizes_with_intervals",
                 "the audit ran no between-condition test, so there is no effect size to report"))
        d.add(na("E5.3_correction_named", "no test family exists to correct over"))
        d.add(na("E5.4_no_claim_on_fewer_than_min_seeds", "no test to check the arm sizes of"))
        return d

    missing_ci = [t.get("test_id") for t in live
                  if not ((t.get("effect_size") or {}).get("ci95")
                          or (t.get("effect_size") or {}).get("refused"))]
    d.add(gate("E5.2_effect_sizes_with_intervals",
               not missing_ci,
               f"all {len(live)} live tests carry an effect size with an interval (or an explicit "
               f"refusal to compute one)",
               f"tests reporting an effect with no interval: {missing_ci}. A point estimate with "
               f"no interval is the format in which noise gets published.",
               measured=len(live) - len(missing_ci), threshold=len(live)))

    corr = stats.get("multiple_comparison_correction") or {}
    family = int(corr.get("family_size") or 0)
    method = str(corr.get("method") or "")
    named = bool(method) and method != "none"
    d.add(gate("E5.3_correction_named",
               named or family <= 1,
               (f"family of {family}; correction: {method}" if family > 1 else
                f"family of {family}: a single test needs no correction, and the audit says so "
                f"explicitly rather than silently omitting one"),
               f"a family of {family} simultaneous comparisons was corrected by "
               f"{method or 'nothing named'}. An uncorrected family of that size has roughly a "
               f"{1 - (1 - DEFAULT_ALPHA) ** max(family, 1):.0%} chance of at least one false "
               f"positive, and a correction that is not named cannot be checked.",
               measured={"family_size": family, "method": method}, threshold="named when n>1"))

    min_seeds = int(stats.get("min_seeds_required") or MIN_SEEDS_INFERENTIAL)
    thin = [t.get("test_id") for t in tests
            if (int(t.get("n_a") or 0) < min_seeds or int(t.get("n_b") or 0) < min_seeds)
            and not t.get("refusal")]
    d.add(gate("E5.4_no_claim_on_fewer_than_min_seeds",
               not thin,
               f"no test below {min_seeds} seeds per arm produced a p-value; the thin ones were "
               f"refused rather than reported weakly",
               f"tests reporting a result on fewer than {min_seeds} seeds per arm without a "
               f"refusal: {thin}. Below that there is not enough data to say anything, and saying "
               f"it weakly is still saying it.",
               measured=len(thin), threshold=0))
    return d


# --------------------------------------------------------------------------
# D6 — figures
# --------------------------------------------------------------------------
def _svg_is_vector(path: Path) -> tuple[bool, str]:
    """A .svg extension is not vector. What is inside it is.

    An embedded raster is the way a bitmap ships inside a file everyone reads as
    a vector, and it is exactly what a reviewer cannot zoom into.
    """
    try:
        raw = path.read_bytes()
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except (OSError, ET.ParseError, UnicodeDecodeError) as e:
        return False, f"not parseable as SVG ({type(e).__name__})"
    if not root.tag.endswith("svg"):
        return False, f"root element is {root.tag}, not <svg>"
    for el in root.iter():
        if el.tag.endswith("image"):
            return False, "contains an <image> element: a raster embedded in a vector wrapper"
    return True, "vector"


def dim_figures(p: Project) -> Dimension:
    d = Dimension("figures", "Figures")
    selected = p.get("selected_figure")
    element_map = p.get("svg_element_map")
    results = p.get("analysis_results")
    spine = p.get("manuscript_spine")
    if not isinstance(selected, dict):
        plan = p.get("figure_plan")
        if isinstance(plan, dict) and not (plan.get("figures") or []):
            d.add(na("E6.figures_planned",
                     "the figure plan declares no figure, so there is none to grade"))
            return d
        d.add(unmeasured("E6.selected_figure_present",
                         "figures/selected/_manifest.json is absent, so what would ship is unknown"))
        return d
    figures = list(selected.get("figures") or [])
    if not figures:
        d.add(na("E6.figures_produced", "no figure was selected, so there is none to grade"))
        return d

    # -- E6.1 vector, not raster -----------------------------------------
    problems = []
    root_dir = p.path("selected_figure")
    for f in figures:
        fp = root_dir / str(f.get("file"))
        if not fp.exists():
            problems.append(f"{f.get('figure_id')}: {f.get('file')} is not on disk")
            continue
        vector, why = _svg_is_vector(fp)
        if not vector:
            problems.append(f"{f.get('figure_id')}: {why}")
    if selected.get("rasterized") is True:
        problems.append("selected_figure declares rasterized=true")
    d.add(gate("E6.1_vector_not_raster",
               not problems,
               f"all {len(figures)} shipped figures parse as vector SVG with no embedded raster",
               f"figures that are not vector: {problems}. A raster figure cannot be zoomed into, "
               f"corrected, or read back against its data.",
               measured=len(figures) - len(problems), threshold=len(figures)))

    # -- E6.2 every figure bound to a claim -------------------------------
    if not isinstance(spine, dict):
        d.add(unmeasured("E6.2_figure_claim_binding",
                         "paper/manuscript_spine.json is absent, so a figure's claim_id cannot be "
                         "checked against the argument it is supposed to support"))
    else:
        known = {str(c.get("claim_id")) for c in (spine.get("claims") or [])}
        unbound = [str(f.get("figure_id")) for f in figures
                   if not f.get("claim_id") or str(f["claim_id"]) not in known]
        bound_ratio = (len(figures) - len(unbound)) / len(figures)
        d.add(gate("E6.2_figure_claim_binding",
                   not unbound,
                   f"all {len(figures)} figures name a claim that is in the spine",
                   f"figures bound to no spine claim: {unbound}. A figure that argues for nothing "
                   f"in the paper is a picture; the reader will still read it as evidence.",
                   measured=round(bound_ratio, 4), threshold=1.0))

    # -- E6.3 the numbers on the figure are the numbers in the analysis ---
    if not isinstance(element_map, dict) or not isinstance(results, dict):
        d.add(unmeasured("E6.3_figure_numbers_match_analysis",
                         "figures/element_map.json or analysis/analysis_results.json is absent, so "
                         "the values drawn cannot be compared with the values analysed"))
        return d
    groups = {str(g.get("group_id")): g for g in (results.get("groups") or [])}
    drift, uncheckable = [], 0
    for fid, entry in (element_map.get("figures") or {}).items():
        for el in entry.get("elements") or []:
            binds = el.get("binds_to") or {}
            gid, stat = str(binds.get("group_id")), str(binds.get("statistic"))
            g = groups.get(gid)
            if g is None or stat in ("None", "", "values"):
                if binds.get("artifact") == "analysis_results":
                    uncheckable += 1
                continue
            drawn = [float(v) for v in (el.get("values") or [])]
            if stat == "mean":
                expected = [float(g["mean"])] if isinstance(g.get("mean"), (int, float)) else None
            elif stat == "ci95":
                expected = [float(v) for v in (g.get("ci95") or [])] or None
            elif stat == "n":
                expected = [float(g.get("n") or 0)]
            else:
                expected = None
            if expected is None:
                uncheckable += 1
                continue
            if len(drawn) != len(expected) or any(
                    abs(a - b) > 1e-9 + 1e-6 * max(abs(a), abs(b))
                    for a, b in zip(drawn, expected)):
                drift.append(f"{fid}/{el.get('element_id')}: drew {drawn} for {stat}={expected}")
    d.add(gate("E6.3_figure_numbers_match_analysis",
               not drift,
               f"every bound element matches analysis_results ({uncheckable} element(s) carried no "
               f"comparable statistic and were not counted either way)",
               f"figure elements that disagree with the analysis: {drift[:6]}. A figure is believed "
               f"faster than a sentence and checked later.",
               measured=len(drift), threshold=0))
    return d


# --------------------------------------------------------------------------
# D7 — writing / argumentation
# --------------------------------------------------------------------------
def dim_writing(p: Project) -> Dimension:
    d = Dimension("writing", "Writing and argumentation")
    manifest = p.get("draft_manifest")
    spine = p.get("manuscript_spine")
    draft = p.get("manuscript_draft")
    cm = p.get("comparison_mode")
    if not isinstance(manifest, dict) or not isinstance(spine, dict):
        d.add(unmeasured("E7.draft_present",
                         "paper/draft_manifest.json or paper/manuscript_spine.json is absent, so "
                         "the draft cannot be compared with the argument it was built from"))
        return d

    paragraphs = list(manifest.get("paragraphs") or [])
    if not paragraphs:
        d.add(unmeasured("E7.paragraphs_present",
                         "the draft manifest records no paragraph; there is no prose to grade"))
        return d
    bound = sum(1 for r in paragraphs if r.get("claim_id"))
    ratio = bound / len(paragraphs)
    d.add(gate("E7.1_paragraph_claim_coverage",
               bound == len(paragraphs) and manifest.get("every_paragraph_bound_to_a_claim") is True,
               f"all {len(paragraphs)} paragraphs name a claim id",
               f"{len(paragraphs) - bound} paragraph(s) name no claim ({ratio:.0%} bound). A "
               f"paragraph that cannot name its claim has no evidence behind it, and the builder "
               f"drops those — so a shortfall here means the manifest disagrees with itself.",
               measured=round(ratio, 4), threshold=1.0))

    spine_ids = {str(c.get("claim_id")) for c in (spine.get("claims") or [])}
    draft_ids = {str(r.get("claim_id")) for r in paragraphs if r.get("claim_id")}
    # MC-DISCLOSURE is inserted by the renderer, not by the spine. It is a required
    # sentence about what was not established, not a claim, so it is not an orphan.
    extra = sorted(draft_ids - spine_ids - {"MC-DISCLOSURE"})
    unwritten = sorted(set(manifest.get("unwritten_claims") or []) | (spine_ids - draft_ids))
    d.add(gate("E7.2_spine_claims_are_the_drafts_claims",
               not extra and not unwritten,
               f"the draft argues exactly the spine's {len(spine_ids)} claims",
               f"claims in the draft but not the spine: {extra}; spine claims never written: "
               f"{unwritten}. A spine claim with no paragraph is an argument the paper promised "
               f"and did not make; a paragraph claim outside the spine was never audited.",
               measured={"only_in_draft": len(extra), "only_in_spine": len(unwritten)},
               threshold=0))

    if not isinstance(cm, dict):
        d.add(unmeasured("E7.3_disclosure_present",
                         "comparison_mode.json is absent, so whether a disclosure was required is "
                         "unknown"))
        d.add(unmeasured("E7.4_no_forbidden_claim_pattern",
                         "comparison_mode.json is absent, so the forbidden patterns for this mode "
                         "are unknown"))
        return d

    disclosure = manifest.get("disclosure") or {}
    required = bool((cm.get("disclosure_required") or {}).get("required"))
    if not required:
        d.add(na("E7.3_disclosure_present",
                 f"{cm.get('mode')} requires no comparison-mode disclosure"))
    else:
        missing = list(disclosure.get("missing_sections") or [])
        where = missing or "every section that requires it"
        d.add(gate("E7.3_disclosure_present",
                   not missing and bool(disclosure.get("inserted_in")),
                   f"the disclosure required by {cm.get('mode')} appears in "
                   f"{disclosure.get('inserted_in')}",
                   f"the disclosure required by {cm.get('mode')} is missing from {where}. Under a "
                   f"degraded comparison mode that sentence is what makes every other number in "
                   f"the section readable.",
                   measured=len(disclosure.get("inserted_in") or []),
                   threshold=len(disclosure.get("must_appear_in") or [])))

    if not isinstance(draft, str) or not draft.strip():
        d.add(unmeasured("E7.4_no_forbidden_claim_pattern",
                         "the manuscript draft is absent, so the bytes that would ship cannot be "
                         "scanned"))
        return d
    # manuscript-builder filters forbidden language out of the paragraphs it was
    # given; nothing re-reads the rendered file afterwards. This is that read, on
    # the artifact that actually ships rather than on the inputs it was made from.
    hits = []
    for pattern in (cm.get("forbidden_claim_patterns") or []):
        if re.search(re.escape(str(pattern)), draft, re.I):
            hits.append(str(pattern))
    d.add(gate("E7.4_no_forbidden_claim_pattern",
               not hits,
               f"the rendered draft uses none of the {len(cm.get('forbidden_claim_patterns') or [])} "
               f"claim patterns {cm.get('mode')} forbids",
               f"the rendered draft contains claim language forbidden under {cm.get('mode')}: "
               f"{hits}. The filter runs on the paragraphs; this reads the file that ships.",
               measured=len(hits), threshold=0))
    return d


# --------------------------------------------------------------------------
# D8 — reproducibility of the run itself
# --------------------------------------------------------------------------
def _state_fingerprint(p: Project) -> dict[str, Any] | None:
    """What "the same state" means, reduced to something two runs can be compared on.

    Not a digest of the project directory: timestamps, run ids and absolute paths
    differ between two correct runs and would make every comparison fail. What must
    match is the state the machine reached and the measurements it recorded.
    """
    state = p.get("research_state") or p.get("progress_state")
    ledger = p.get("experiment_ledger", default=None)
    if ledger is None:
        return None
    runs = sorted(
        (str(e.get("experiment_id")), str(entry_seed(e)), str(e.get("status")),
         sorted((k, round(v, 12)) for k, v in numeric_metrics(e).items()))
        for e in ledger)
    release = p.get("release_manifest") or {}
    return {
        "state": (state or {}).get("state") if isinstance(state, dict) else None,
        "runs": runs,
        "release_status": release.get("release_status"),
        "released": release.get("released"),
    }


def dim_reproducibility(p: Project, second: Project | None) -> Dimension:
    d = Dimension("reproducibility", "Reproducibility of the run itself")
    release = p.get("release_manifest")
    if not isinstance(release, dict):
        d.add(unmeasured("E8.release_manifest_present",
                         "release/release_manifest.json is absent, so what was released and "
                         "whether it had lineage cannot be determined"))
    else:
        # release-gate already blocked on any artifact without provenance. The
        # verdict is consumed; what is added is the count, so a manifest that is
        # complete only because it listed nothing cannot pass silently.
        artifacts = list(release.get("artifacts") or [])
        incomplete = [a.get("artifact_id") for a in artifacts
                      if a.get("released") and not a.get("provenance_complete")]
        released_n = sum(1 for a in artifacts if a.get("released"))
        if not artifacts:
            d.add(unmeasured("E8.1_provenance_for_every_released_artifact",
                             "the release manifest lists no artifact at all"))
        elif released_n == 0:
            d.add(bad("E8.1_provenance_for_every_released_artifact",
                      f"the release gate released nothing (status "
                      f"{release.get('release_status')!r}). A refused release is a correct outcome "
                      f"for the run, and it is not an accepted one.",
                      measured=0, threshold=">0"))
        else:
            d.add(gate("E8.1_provenance_for_every_released_artifact",
                       not incomplete and release.get("provenance_complete") is not False,
                       f"all {released_n} released artifacts carry a provenance write event and a "
                       f"digest that still matches the file on disk",
                       f"released artifacts without complete provenance: {incomplete}. An artifact "
                       f"with no lineage cannot be shown to have come from anything.",
                       measured=released_n - len(incomplete), threshold=released_n))

    env = p.get("environment_lock")
    sandbox = p.get("sandbox_manifest")
    if env is None or not isinstance(sandbox, dict):
        d.add(unmeasured("E8.2_environment_captured",
                         "experiments/environment.lock or experiments/sandbox_manifest.json is "
                         "absent, so the environment the numbers came from was not captured"))
    else:
        pinned = [l for l in str(env).splitlines() if "==" in l]
        isolated = sandbox.get("untrusted_code_execution_allowed") is True
        d.add(gate("E8.2_environment_captured",
                   bool(pinned) and isolated,
                   f"{len(pinned)} pinned dependencies and isolation="
                   f"{sandbox.get('isolation')!r}",
                   f"environment capture is incomplete: {len(pinned)} pinned dependency line(s), "
                   f"isolation={sandbox.get('isolation')!r}, untrusted execution allowed="
                   f"{sandbox.get('untrusted_code_execution_allowed')!r}. A freeze with no `==` "
                   f"lines is not a lock, and numbers produced outside a sandbox were produced in "
                   f"an environment nobody recorded.",
                   measured={"pinned": len(pinned), "isolation": sandbox.get("isolation")},
                   threshold={"pinned": ">0", "untrusted_code_execution_allowed": True}))

    if second is None:
        d.add(unmeasured("E8.3_second_run_reaches_the_same_state",
                         "no second run was supplied (--second-run DIR). Determinism cannot be "
                         "inferred from a single run, and this grader will not assume it: "
                         "run_acceptance.sh performs the second run and passes it here."))
        return d
    a, b = _state_fingerprint(p), _state_fingerprint(second)
    if a is None or b is None:
        d.add(unmeasured("E8.3_second_run_reaches_the_same_state",
                         "one of the two runs has no experiment ledger, so the two cannot be "
                         "compared"))
        return d
    same_state = a["state"] == b["state"]
    same_runs = a["runs"] == b["runs"]
    same_release = (a["release_status"], a["released"]) == (b["release_status"], b["released"])
    differing = [f"{x[0]}:seed={x[1]}" for x, y in zip(a["runs"], b["runs"]) if x != y][:8]
    d.add(gate("E8.3_second_run_reaches_the_same_state",
               same_state and same_runs and same_release,
               f"a second run from the same inputs reached state {a['state']!r} with identical "
               f"metrics for all {len(a['runs'])} runs",
               f"the second run diverged — state {a['state']!r} vs {b['state']!r}, release "
               f"{a['release_status']!r} vs {b['release_status']!r}, "
               f"{'runs differ at ' + str(differing) if not same_runs else 'runs match'}. "
               f"Every interval in the analysis is computed from seed dispersion; if the seeds do "
               f"not reproduce, that dispersion is measuring the machine, not the method.",
               measured={"state_match": same_state, "runs_match": same_runs,
                         "release_match": same_release},
               threshold=True))
    return d


# --------------------------------------------------------------------------
# synthetic refusal
# --------------------------------------------------------------------------
SYNTHETIC_SCAN = ("draft_manifest", "integrity_gate", "review_report", "stats_audit",
                  "claim_audit", "findings", "negative_findings", "artifact_manifest",
                  "comparison_mode", "source_repro_report", "release_manifest",
                  "analysis_results", "manuscript_spine", "finding_memory_graph")


def find_synthetic(p: Project) -> list[str]:
    """Refuse to grade output the runtime already marked as not research.

    `--model offline` stamps every artifact it touches. Scoring such a project
    would produce a number that looks like a research result and is a test of the
    machinery, and the number would outlive the caveat.
    """
    hits: list[str] = []
    for aid in SYNTHETIC_SCAN:
        payload = p.get(aid)
        if payload is not None:
            hits += synthetic_hits(aid, payload)
    for event in (p.get("provenance_log", default=[]) or []):
        if isinstance(event, dict) and event.get("kind") == "skill_end" \
                and (event.get("detail") or {}).get("synthetic") is True:
            hits.append(f"provenance:{event.get('skill')}")
    return sorted(set(hits))


# --------------------------------------------------------------------------
# constant drift guard
# --------------------------------------------------------------------------
def assert_constants_agree() -> list[str]:
    """If the runtime is importable, its numbers win.

    A grader holding a stale copy of a threshold grades the project against a rule
    it never agreed to. When the package is not importable — grading a bundle on
    another machine — the copies above stand, and the report says so.
    """
    try:
        from researchforge.skills import analysis as _a
        from researchforge.skills import planning as _pl
    except Exception:
        return []
    drift = []
    for name, mine, theirs in (
            ("MIN_SEEDS_INFERENTIAL/planning.MIN_SEEDS_FOR_COMPARATIVE",
             MIN_SEEDS_INFERENTIAL, _pl.MIN_SEEDS_FOR_COMPARATIVE),
            ("MIN_SEEDS_INFERENTIAL/analysis.MIN_SEEDS_FOR_INFERENCE",
             MIN_SEEDS_INFERENTIAL, _a.MIN_SEEDS_FOR_INFERENCE),
            ("DEFAULT_ALPHA", DEFAULT_ALPHA, _a.DEFAULT_ALPHA)):
        if mine != theirs:
            drift.append(f"{name}: grader has {mine}, runtime has {theirs}")
    return drift


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------
def grade(project_dir: Path, repo_root: Path,
          second_run: Path | None = None) -> dict[str, Any]:
    p = Project(project_dir, repo_root)
    drift = assert_constants_agree()
    if drift:
        raise GraderRefusal(
            "the grader's copies of the runtime's thresholds have drifted: " + "; ".join(drift)
            + ". Two thresholds for one question means the lower one governs; fix the copies in "
              "acceptance/grade.py and RUBRIC.md before grading anything.")

    synthetic = find_synthetic(p)
    if synthetic:
        return {
            "acceptance_version": RUBRIC_VERSION,
            "graded_at": time.time(),
            "project": str(p.project),
            "verdict": "REFUSED_SYNTHETIC",
            "verdict_reason": (
                f"{len(synthetic)} artifact field(s) are marked synthetic: {synthetic[:10]}. "
                f"Offline-stub output is not research and will not be scored; a score attached to "
                f"it would outlive the caveat attached to it."),
            "synthetic_markers": synthetic,
            "dimensions": [], "failed_dimensions": [], "not_measured_dimensions": [],
        }

    second = Project(second_run, repo_root) if second_run else None
    dims = [
        dim_experiments(p),
        dim_baselines(p),
        dim_ablations(p),
        dim_evidence(p),
        dim_statistics(p),
        dim_figures(p),
        dim_writing(p),
        dim_reproducibility(p, second),
    ]
    failed = [d.id for d in dims if d.outcome == FAIL]
    not_measured = [d.id for d in dims if d.outcome == NOT_MEASURED]
    if failed:
        verdict = "REJECTED"
        reason = f"{len(failed)} dimension(s) failed: {failed}"
    elif not_measured:
        # Not a pass with an asterisk. The harness could not see part of the
        # project, so it does not know whether that part is acceptable.
        verdict = "INCOMPLETE"
        reason = (f"no dimension failed, but {len(not_measured)} could not be measured: "
                  f"{not_measured}. An unmeasured dimension is not a passing one.")
    else:
        verdict = "ACCEPTED"
        reason = "every dimension was measured and every dimension passed."

    return {
        "acceptance_version": RUBRIC_VERSION,
        "graded_at": time.time(),
        "project": str(p.project),
        "second_run": str(second.project) if second else None,
        "verdict": verdict,
        "verdict_reason": reason,
        "dimensions": [d.as_dict() for d in dims],
        "failed_dimensions": failed,
        "not_measured_dimensions": not_measured,
        "missing_artifacts": sorted(p.missing),
        "consumed_audits": {
            "integrity_gate": (p.get("integrity_gate") or {}).get("verdict"),
            "stats_audit_evidence_lock_blocked":
                ((p.get("stats_audit") or {}).get("evidence_lock") or {}).get("blocked"),
            "review_report_recommendation": (p.get("review_report") or {}).get("recommendation"),
            "release_manifest_status": (p.get("release_manifest") or {}).get("release_status"),
            "note": "these verdicts are read, never recomputed. Re-deriving them here would "
                    "create a second threshold for a question that already has one.",
        },
        "thresholds_in_force": {
            "min_seeds_inferential": MIN_SEEDS_INFERENTIAL,
            "min_seeds_descriptive": MIN_SEEDS_DESCRIPTIVE,
            "max_failure_rate": MAX_FAILURE_RATE,
            "claim_support_ratio_floor": round(
                1.0 - float((p.get("stats_audit") or {}).get("alpha", DEFAULT_ALPHA)), 4),
            "runtime_constants_verified": bool(assert_constants_agree() == []),
        },
    }


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
_SYMBOL = {PASS: "PASS", FAIL: "FAIL", NOT_MEASURED: "NOT_MEASURED",
           NOT_APPLICABLE: "N/A"}


def render_report(result: dict[str, Any]) -> str:
    L = [f"# Acceptance report — {result['verdict']}", "",
         f"- rubric: `RUBRIC.md` v{result['acceptance_version']}",
         f"- project: `{result['project']}`",
         f"- graded: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(result['graded_at']))}", ""]
    if result.get("second_run"):
        L.append(f"- second run: `{result['second_run']}`")
    L += ["", f"**{result['verdict_reason']}**", ""]

    if result["verdict"] == "REFUSED_SYNTHETIC":
        L += ["## Refused", "",
              "This project carries artifacts the runtime itself marked synthetic. The grader does "
              "not score them.", ""]
        L += [f"- `{h}`" for h in result.get("synthetic_markers", [])[:40]]
        return "\n".join(L) + "\n"

    L += ["## Dimensions", "", "| dimension | outcome |", "|---|---|"]
    for d in result["dimensions"]:
        L.append(f"| {d['title']} (`{d['id']}`) | **{_SYMBOL[d['outcome']]}** |")
    L += ["", "A dimension is ACCEPTED-eligible only when it was measured. NOT_MEASURED is not a "
              "pass: it is the grader saying it could not see that part of the project, and it "
              "blocks acceptance exactly as a failure does.", ""]

    for d in result["dimensions"]:
        L += [f"## {d['title']} — {_SYMBOL[d['outcome']]}", ""]
        for c in d["checks"]:
            L += [f"### `{c['id']}` — {_SYMBOL[c['outcome']]}", "",
                  f"{c['detail']}", ""]
            if c["measured"] is not None or c["threshold"] is not None:
                L += [f"- measured: `{json.dumps(c['measured'], default=str)}`",
                      f"- threshold: `{json.dumps(c['threshold'], default=str)}`", ""]

    consumed = result.get("consumed_audits", {})
    L += ["## Audits consumed, not recomputed", "",
          "| audit | verdict as recorded |", "|---|---|"]
    for k, v in consumed.items():
        if k == "note":
            continue
        L.append(f"| `{k}` | `{v}` |")
    L += ["", consumed.get("note", ""), ""]

    if result.get("missing_artifacts"):
        L += ["## Artifacts the grader looked for and did not find", ""]
        L += [f"- `{a}`" for a in result["missing_artifacts"]]
        L += [""]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grade a finished ResearchForge project.")
    ap.add_argument("project", type=Path)
    ap.add_argument("--second-run", type=Path, default=None,
                    help="a second project produced from the same inputs; without it the "
                         "reproducibility dimension reports NOT_MEASURED")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parents[1],
                    help="repository holding manifests/artifact-graph.json")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write acceptance_report.md and acceptance_result.json "
                         "(default: PROJECT/acceptance)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    try:
        result = grade(a.project, a.repo_root, a.second_run)
    except GraderRefusal as e:
        print(f"acceptance: REFUSED — {e}", file=sys.stderr)
        return 3

    out_dir = a.out or (a.project / "acceptance")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "acceptance_result.json").write_text(
        json.dumps(result, indent=1, sort_keys=True, default=str), encoding="utf-8")
    report = render_report(result)
    (out_dir / "acceptance_report.md").write_text(report, encoding="utf-8")
    if not a.quiet:
        print(report)
    print(f"acceptance: {result['verdict']} — {result['verdict_reason']}", file=sys.stderr)
    print(f"acceptance: wrote {out_dir}/acceptance_report.md and acceptance_result.json",
          file=sys.stderr)
    return {"ACCEPTED": 0, "REJECTED": 1, "INCOMPLETE": 2}.get(result["verdict"], 3)


if __name__ == "__main__":
    raise SystemExit(main())
