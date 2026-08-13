"""Turning a selected direction into a plan that can be wrong.

Two things happen in this stage, and both are refusals as much as they are
constructions.

The first is that `comparison_mode` stops being advice. Everything upstream of
here spent its effort establishing how much of the source paper was actually
reproduced, and the only place that work can pay off is the moment experiments
are written down: a run that never intended to make a comparative claim cannot
accidentally make one in the manuscript six stages later. So the compiler does
not "prefer" diagnostic experiments under CM_NONE — it is structurally unable to
emit a comparative one, and re-checks its own output before writing it.

The second is falsifiability. An experiment specification that cannot come back
void is not an experiment, it is a plan to produce numbers. Every spec here
carries `invalid_conditions`: the states of the world under which its results
must be discarded rather than interpreted. The compiler refuses to write a spec
without them.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from ..errors import GateBlocked
from ..skill import Context, Skill, SkillResult, register

#: modes under which a comparative performance claim is admissible at all
COMPARATIVE_MODES = ("CM_MEASURED", "CM_RELATIVE", "CM_REPORTED")

#: Claim types. Only "comparative" asserts something about *other people's*
#: systems; the rest are statements about our own artifact and are therefore
#: still admissible when reproduction failed.
CLAIM_COMPARATIVE = "comparative"
CLAIM_DIAGNOSTIC = "diagnostic"
CLAIM_EVALUATION = "evaluation"
CLAIM_ABLATION = "ablation"
NON_COMPARATIVE = (CLAIM_DIAGNOSTIC, CLAIM_EVALUATION, CLAIM_ABLATION)

#: Below this, a between-condition difference has no estimable dispersion, so a
#: comparative claim built on it is not falsifiable — it is a single draw.
MIN_SEEDS_FOR_COMPARATIVE = 3


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@register
class ResearchBlueprintCompiler(Skill):
    """Blueprint + experiment specs + ablations, bounded by the comparison mode.

    Consolidates the v0.1 trio research-blueprint-compiler / experiment-spec-author /
    ablation-and-counterfactual-planner. They were split by document type, not by
    decision, which meant the ablation planner could not see the constraint the
    blueprint was compiled under and duplicated the judgement badly. One skill,
    one place where comparison_mode is enforced.
    """

    name = "research-blueprint-compiler"

    def execute(self, ctx: Context) -> SkillResult:
        cm = ctx.store.read(self.name, "comparison_mode")
        selected = ctx.store.read(self.name, "selected_direction")
        repro = ctx.store.read(self.name, "source_repro_report")
        assets = ctx.store.read(self.name, "baseline_assets")
        mode = cm["mode"]
        warnings: list[str] = []

        # ---- externals that cannot be invented ------------------------
        # The contract does not let this skill read idea_portfolio, only
        # selected_direction — which carries ids, not content. So the hypothesis
        # genuinely is not recoverable from the artifact graph, and guessing one
        # from an idea id would be the exact fabrication this system exists to
        # prevent.
        hypothesis = self._require(ctx, "hypothesis",
                                   "the blueprint's whole job is to make one hypothesis falsifiable; "
                                   "selected_direction carries idea ids, not the hypothesis text",
                                   "pass --set hypothesis='<the claim under test>'")
        method = self._require(ctx, "candidate_method",
                               "an experiment spec needs a candidate condition; with no method "
                               "description there is nothing to specify and nothing to ablate",
                               "pass --set candidate_method='<what the method does>'")
        envelope = self._require(ctx, "resource_envelope",
                                 "ResearchBlueprint requires budgets, and an invented compute/time/"
                                 "cost budget makes the plan unexecutable while looking executable",
                                 "pass --set resource_envelope='{\"gpu_hours\":..,\"wallclock_hours\":"
                                 "..,\"usd\":..,\"seeds\":..}'")
        if not isinstance(envelope, dict):
            raise GateBlocked("external_input", "resource_envelope must be an object",
                              "pass a JSON object with the budget keys you actually have")

        idea_ids = selected.get("selected_idea_ids") or []
        if not idea_ids:
            raise GateBlocked(
                "selection", "selected_direction contains no idea ids; there is no direction to compile",
                "re-run user-feedback-gate and select at least one direction")

        seeds = self._seeds(envelope)
        if mode in COMPARATIVE_MODES and len(seeds) < MIN_SEEDS_FOR_COMPARATIVE:
            # Refusing here rather than emitting the spec with a warning: a
            # comparative spec that is void the moment it runs is worse than no
            # spec, because it consumes the budget before anyone reads the caveat.
            raise GateBlocked(
                "seed_budget",
                f"comparison mode {mode} admits comparative claims, but the resource envelope "
                f"allows only {len(seeds)} seed(s). Below {MIN_SEEDS_FOR_COMPARATIVE} there is no "
                f"dispersion to estimate, so the comparison cannot be falsified.",
                f"raise 'seeds' to >= {MIN_SEEDS_FOR_COMPARATIVE} in resource_envelope, or accept a "
                f"non-comparative (diagnostic/evaluation) plan by re-running the fallback planner")

        mechanisms = self._mechanisms(ctx, method, warnings)
        baseline = self._baseline(mode, repro, assets, warnings)
        disclosure = cm.get("disclosure_required") or {}

        # ---- ablations first: specs reference them, not the reverse ----
        ablations = self._ablations(mechanisms, mode, seeds)
        ctx.store.write(self.name, "ablation_plan", json.dumps({
            "generated_at": time.time(),
            "comparison_mode": mode,
            "mechanisms": mechanisms,
            "ablations": ablations,
            "note": ("Each claimed mechanism gets an isolation test and a matched-compute "
                     "counterfactual. An isolation test is an internal contrast between our own "
                     "variants; it makes no claim about anyone else's system, which is why it "
                     "survives CM_NONE."),
        }, indent=1))
        # JSON is a subset of YAML 1.2, so this is a valid .yaml file and the
        # runtime keeps one fewer dependency it would have to pin.

        # ---- experiment specs -----------------------------------------
        specs = self._specs(mode, hypothesis, method, baseline, seeds, envelope,
                            ablations, repro, assets, disclosure)

        # Guards run before the write, not after: the store would happily accept a
        # schema-valid spec that violates the mode, because the schema does not
        # know what the run was allowed to claim.
        self._assert_mode_respected(specs, mode)
        self._assert_falsifiable(specs)
        ctx.store.write(self.name, "experiment_specs", specs)

        criteria = self._criteria(specs, mode, disclosure, cm)
        ctx.store.write(self.name, "acceptance_criteria",
                        self._criteria_md(specs, mode, disclosure, cm, repro))

        stages = self._stages(specs, mode)
        dag = self._dag(stages, specs)
        self._assert_acyclic(dag)
        ctx.store.write(self.name, "blueprint_dag", dag)

        blueprint = {
            "blueprint_id": f"BP-{ctx.run_id}",
            "selected_idea_id": "+".join(idea_ids),
            "selected_idea_ids": idea_ids,
            "hypothesis": hypothesis,
            "acceptance_criteria": criteria,
            "stages": stages,
            "budgets": self._budgets(envelope, specs, seeds, warnings),
            "human_gates": self._human_gates(mode),
            "comparison_mode": mode,
            "comparison_mode_derived_from_level": cm.get("derived_from_level"),
            "forbidden_claim_patterns": cm.get("forbidden_claim_patterns", []),
            "disclosure_required": disclosure,
            "candidate_method": method,
            "sota_arm": self._sota_arm(assets, {}),
            "experiment_ids": [s["experiment_id"] for s in specs],
            "ablation_ids": [a["ablation_id"] for a in ablations],
            "compiled_at": time.time(),
            "compiled_by_run_id": ctx.run_id,
        }
        ctx.store.write(self.name, "research_blueprint", blueprint)

        if mode == "CM_NONE":
            warnings.append(
                "CM_NONE: every comparative experiment was refused. The plan is diagnostic and "
                "evaluation-methodology work on our own artifact, which is publishable on its own "
                "terms and is the only thing this reproduction level supports.")
        if mode == "CM_REPORTED" and disclosure.get("required"):
            warnings.append(
                "CM_REPORTED: comparisons are against published numbers under a different "
                "environment. The disclosure is an acceptance criterion, not a footnote — an "
                "experiment whose write-up omits it fails acceptance.")
        # The strength of the anchor, stated at plan time. Discovering at review that
        # every effect size in the paper is relative to a method nobody would use is
        # a discovery that costs a submission cycle.
        _sota = blueprint["sota_arm"]
        if not _sota["required"]:
            warnings.append(
                "no state-of-the-art arm is planned: result-reproducer identified no current "
                "strongest method for this task. Every comparison and every ablation below is "
                "therefore anchored to the source paper's baseline, and no claim of "
                "competitiveness can be made from this plan no matter how the runs come out. "
                "Supply candidates with --set sota_methods='[...]' to change that.")
        elif not _sota["established"]:
            warnings.append(
                f"a state-of-the-art arm is planned against {len(_sota['candidates'])} "
                f"candidate(s), none of which has been reproduced here. Until one is, the arm "
                f"contributes a REPORTED number: it makes the comparison visible, not measured.")

        return SkillResult(
            self.name,
            produced=["ablation_plan", "experiment_specs", "acceptance_criteria",
                      "blueprint_dag", "research_blueprint"],
            warnings=warnings, next_state="BLUEPRINT_READY",
            detail={"comparison_mode": mode, "experiments": len(specs),
                    "ablations": len(ablations),
                    "claim_types": sorted({s["claim_type"] for s in specs}),
                    "seeds": len(seeds)})

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------
    def _require(self, ctx: Context, key: str, why: str, how: str) -> Any:
        v = ctx.external(key, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise GateBlocked("external_input", f"missing required external '{key}': {why}", how)
        return v

    def _seeds(self, envelope: dict) -> list[int]:
        """Seeds come from the envelope, never from a default that flatters the plan."""
        s = envelope.get("seeds")
        if isinstance(s, list) and s:
            return [int(x) for x in s]
        if isinstance(s, int) and s > 0:
            return list(range(1, s + 1))
        # One seed is the honest reading of "no seed budget was given": it is the
        # minimum a run can use, and it will trip the comparative-mode gate above
        # rather than silently licensing a three-seed claim nobody funded.
        return [1]

    def _mechanisms(self, ctx: Context, method: Any, warnings: list[str]) -> list[dict]:
        """Claimed mechanisms, from the caller — decomposition is not inferable.

        Splitting a prose method description into the mechanisms it claims is a
        research judgement. Doing it with string heuristics would invent structure
        that the author never asserted, and every ablation downstream would then be
        isolating a fiction. So: take the list if given, otherwise treat the method
        as a single undecomposed mechanism and say so.
        """
        raw = ctx.external("mechanisms", None)
        if isinstance(raw, list) and raw:
            out = []
            for i, m in enumerate(raw):
                if isinstance(m, str):
                    m = {"claim": m}
                out.append({"mechanism_id": m.get("mechanism_id", f"M-{i + 1:03d}"),
                            "claim": m.get("claim", ""),
                            "why_it_should_work": m.get("why_it_should_work"),
                            "decomposed_by": "caller"})
            return out
        warnings.append(
            "no 'mechanisms' external was supplied, so the candidate method is treated as one "
            "undecomposed mechanism. Only one isolation test can be built from that; supply "
            "mechanisms=[{claim:...}] to get per-component ablations.")
        return [{"mechanism_id": "M-001",
                 "claim": method if isinstance(method, str) else json.dumps(method),
                 "why_it_should_work": None,
                 "decomposed_by": "none (caller supplied no decomposition)"}]

    def _baseline(self, mode: str, repro: dict, assets: dict, warnings: list[str]) -> dict:
        """What the candidate is measured against, and how much that thing is worth."""
        level = repro.get("level", "RL0")
        repos = assets.get("repos") or []
        pinned = assets.get("pinned_revision")
        if mode == "CM_MEASURED":
            kind, note = "locally_measured", "baseline numbers measured in this environment"
        elif mode == "CM_RELATIVE":
            kind, note = ("locally_measured_reduced_scale",
                          "reduced-scale local baseline; absolute values are not comparable to "
                          "published ones")
        elif mode == "CM_REPORTED":
            kind, note = ("reported_by_authors",
                          "numbers as published, measured elsewhere, on unknown hardware")
        else:
            kind, note = ("internal_reference_condition",
                          "no external baseline is admissible at this reproduction level; the "
                          "reference is our own unmodified implementation")
        if not repos:
            warnings.append("baseline_assets pins no repository, so 'baseline not established' is "
                            "an invalid condition on every spec rather than a background risk.")
        return {"kind": kind, "note": note, "reproduction_level": level,
                "pinned_revision": pinned,
                "repos": [r.get("url") for r in repos],
                "established": bool(repos and pinned)}

    # ------------------------------------------------------------------
    # ablations
    # ------------------------------------------------------------------
    def _ablations(self, mechanisms: list[dict], mode: str, seeds: list[int]) -> list[dict]:
        out = []
        for i, m in enumerate(mechanisms):
            aid = f"AB-{i + 1:03d}"
            out.append({
                "ablation_id": aid,
                "mechanism_id": m["mechanism_id"],
                "claim": m["claim"],
                "isolation_test": {
                    "kind": "remove_mechanism",
                    "description": f"identical pipeline with {m['mechanism_id']} disabled; every "
                                   f"other component, dataset, seed set and budget held fixed",
                    "holds_fixed": ["data", "seeds", "compute budget", "evaluator version",
                                    "hyperparameter search protocol"],
                },
                "counterfactual_controls": [
                    {"kind": "matched_compute",
                     "description": "mechanism removed and the freed compute returned to the "
                                    "remaining components, so an effect cannot be explained by "
                                    "the mechanism simply costing more"},
                    {"kind": "randomized_mechanism",
                     "description": "mechanism kept structurally but its content randomized "
                                    "(shuffled/permuted), separating 'this mechanism' from 'any "
                                    "intervention of this shape'"},
                ],
                "predicted_if_mechanism_real": "removal degrades the primary metric beyond seed "
                                               "dispersion; the randomized control degrades it too",
                "predicted_if_confound": "removal is within seed dispersion, or the randomized "
                                         "control matches the full method",
                "invalidates_claim_if": f"{m['mechanism_id']} cannot be disabled without changing "
                                        f"another component, i.e. the isolation is not clean",
                "internal_contrast": True,
                "admissible_under_mode": True,
                "seeds": seeds,
            })
        return out

    # ------------------------------------------------------------------
    # specs
    # ------------------------------------------------------------------
    def _specs(self, mode, hypothesis, method, baseline, seeds, envelope, ablations,
               repro, assets, disclosure) -> list[dict]:
        datasets = self._datasets(envelope)
        specs: list[dict] = []
        if mode in COMPARATIVE_MODES:
            specs.append(self._comparative(mode, hypothesis, method, baseline, seeds,
                                           datasets, ablations, disclosure, assets))
        else:
            # CM_NONE. Not a reduced version of the comparative plan — a different
            # plan. Both directions here (explain_diagnose, benchmark_evaluate) are
            # exactly the ones the fallback planner left admissible at RL0.
            specs.append(self._diagnostic(hypothesis, method, baseline, seeds, datasets, ablations))
            specs.append(self._evaluation(hypothesis, method, seeds, datasets))
        # An ablation must measure what the claim it tests rests on. The primary
        # experiment defines that; its ablations inherit it.
        parent = next((sp for sp in specs if sp.get("claim_type") != CLAIM_ABLATION), None)
        for ab in ablations:
            ab.setdefault("parent_experiment_id", parent["experiment_id"] if parent else None)
        specs.extend(self._ablation_specs(mode, hypothesis, method, baseline, seeds,
                                          datasets, ablations,
                                          parent_metrics=(parent or {}).get("metrics"),
                                          parent=parent))
        for s in specs:
            s["invalid_conditions"] = self._invalid_conditions(s, mode, baseline, seeds, repro, assets)
            s["narrowing_conditions"] = self._narrowing_conditions(s, assets)
        return specs

    def _narrowing_conditions(self, spec, assets) -> list[dict]:
        """States that make a result NARROW rather than VOID.

        The distinction is the whole point of having a second list. A comparison
        run without the current strongest method is a perfectly good measurement of
        a smaller question; it is not nothing. Filing it as an `invalid_condition`
        made `evaluate_all` mark the experiment void, so declaring a SOTA candidate
        deleted every measurement the experiment produced — three arms, six
        completed runs including a measured state-of-the-art arm, and
        `best_candidate` reporting "no run produced a measurement".
        """
        out = []
        if spec.get("comparative_claim") and ((assets or {}).get("sota") or {}).get("candidates"):
            out.append({
                "code": "SOTA_ARM_NOT_MEASURED",
                "condition": "no state-of-the-art arm completed with metrics in this experiment",
                "narrows_to": ("a comparison against the source paper's baseline only. The "
                               "measurement stands; the claim it can support does not extend to "
                               "competitiveness."),
                "why": "a comparison that omits the current strongest method answers 'did we beat "
                       "the source paper's baseline', which is not the question a reviewer asks",
                "detect": "a completed 'sota' arm carrying metrics in the experiment ledger",
                "check": {"kind": "ledger_arm_completed", "arm": "sota", "value": 1},
                "enforced_by": ("claim-citation-auditor, which blocks any state-of-the-art claim "
                                "about this work without a measured sota arm")})
        return out

    def _datasets(self, envelope: dict) -> list[dict]:
        ds = envelope.get("datasets")
        if isinstance(ds, list) and ds:
            return [d if isinstance(d, dict) else {"name": d} for d in ds]
        # Named "unresolved" rather than guessed: codebase-scaffolder must fail
        # loudly on an unresolved dataset instead of quietly picking a default.
        return [{"name": "UNRESOLVED", "resolved": False,
                 "note": "no dataset was declared in the resource envelope; the run cannot start "
                         "until one is bound"}]

    def _comparative(self, mode, hypothesis, method, baseline, seeds, datasets,
                     ablations, disclosure, assets=None) -> dict:
        spec = {
            "experiment_id": "E-001",
            "title": "Primary comparison",
            "claim_type": CLAIM_COMPARATIVE,
            "hypothesis": hypothesis,
            "baseline": dict(baseline),
            "candidate": {"description": method, "differs_from_baseline_in":
                          [a["mechanism_id"] for a in ablations]},
            "datasets": datasets,
            "metrics": [
                {"name": "primary", "role": "decision", "direction": "unspecified",
                 "definition_owner": "evaluator-builder",
                 "note": "the metric's operational definition is the evaluator's, not this spec's; "
                         "two definitions of one metric is how a comparison becomes unfalsifiable"},
                {"name": "seed_dispersion", "role": "uncertainty",
                 "definition": "std over seeds of the primary metric, per condition"},
            ],
            "seeds": seeds,
            "success_metric": self._success_metric(mode),
            "comparative_claim": True,
            "ablations": [a["ablation_id"] for a in ablations],
            "preregistered": True,
        }
        # The third condition. Two arms answer "is our method better than the paper
        # we started from"; the reviewer's question is "is it better than what is
        # best now", and that question needs an arm to be answered at all.
        sota = self._sota_arm(assets or {}, spec)
        spec["sota"] = sota
        if sota["required"]:
            spec["success_metric"] += (
                "; and the same contrast is reported against the current strongest method, "
                + ("measured here" if sota["established"] else
                   "whose number is REPORTED by its authors and was not measured here"))
        n_cond = 3 if sota["required"] else 2
        spec["resources"] = {"seeds": len(seeds), "conditions": n_cond,
                             "runs": n_cond * len(seeds)}
        if mode == "CM_REPORTED":
            # The disclosure is carried in the spec *and* in acceptance_criteria.
            # Carrying it only in prose is how it gets lost between here and the
            # manuscript; as an acceptance criterion it can fail the experiment.
            spec["disclosure"] = {
                "required": True,
                "text": disclosure.get("text_template", ""),
                "must_appear_in": disclosure.get("must_appear_in", []),
                "enforced_as": "acceptance_criterion",
            }
        spec["acceptance_criteria"] = self._spec_criteria(spec, mode, disclosure)
        return spec

    def _success_metric(self, mode: str) -> str:
        if mode == "CM_MEASURED":
            return ("candidate beats the locally measured baseline on the primary metric by more "
                    "than the seed dispersion of both conditions combined")
        if mode == "CM_RELATIVE":
            return ("candidate beats the reduced-scale local baseline in relative terms under an "
                    "identical environment and seed set; no absolute comparison to published "
                    "values is made")
        return ("candidate exceeds the authors' reported number by more than this run's seed "
                "dispersion, stated together with the mandatory disclosure that the baseline was "
                "not reproduced here")

    def _diagnostic(self, hypothesis, method, baseline, seeds, datasets, ablations) -> dict:
        spec = {
            "experiment_id": "E-001",
            "title": "Mechanism characterization (diagnostic)",
            "claim_type": CLAIM_DIAGNOSTIC,
            "hypothesis": hypothesis,
            # The reference condition is our own unmodified implementation. That is
            # an internal contrast, so it is not a performance claim against the
            # source paper — which is the only thing CM_NONE forbids.
            "baseline": dict(baseline),
            "candidate": {"description": method,
                          "instrumented": True,
                          "differs_from_baseline_in": [a["mechanism_id"] for a in ablations]},
            "datasets": datasets,
            "metrics": [
                {"name": "mechanism_activation", "role": "descriptive",
                 "definition": "how often and under which inputs the claimed mechanism fires"},
                {"name": "failure_mode_incidence", "role": "descriptive",
                 "definition": "rate of each enumerated failure mode, by input stratum"},
            ],
            "seeds": seeds,
            # two conditions: the instrumented candidate and our own unmodified
            # implementation. It said 1 while declaring a baseline the runner would
            # be asked to execute, so the budget was short by half.
            "resources": {"seeds": len(seeds), "conditions": 2, "runs": 2 * len(seeds)},
            "success_metric": ("the conditions under which the mechanism fires and fails are "
                               "characterized with stated uncertainty; no claim is made about any "
                               "other system's performance"),
            "comparative_claim": False,
            "ablations": [a["ablation_id"] for a in ablations],
            "preregistered": True,
        }
        spec["acceptance_criteria"] = self._spec_criteria(spec, "CM_NONE", {})
        return spec

    def _evaluation(self, hypothesis, method, seeds, datasets) -> dict:
        spec = {
            "experiment_id": "E-002",
            "title": "Measurement validity (evaluation methodology)",
            "claim_type": CLAIM_EVALUATION,
            "hypothesis": ("the metric used to judge this class of method discriminates the "
                           "property it claims to measure: " + str(hypothesis)),
            "baseline": {"kind": "metric_under_test", "note": "the object of study is the "
                                                              "evaluator, not any system"},
            "candidate": {"description": "perturbation battery over submissions: paraphrase, "
                                         "truncation, degenerate-constant, reference echo",
                          "method_under_measurement": method},
            "datasets": datasets,
            "metrics": [
                {"name": "discriminability", "role": "decision",
                 "definition": "score separation between known-good and known-degenerate "
                               "submissions, relative to score noise"},
                {"name": "gaming_sensitivity", "role": "decision",
                 "definition": "score change under perturbations that do not change correctness"},
            ],
            "seeds": seeds,
            "resources": {"seeds": len(seeds), "conditions": 4, "runs": 4 * len(seeds)},
            "success_metric": ("the evaluator separates degenerate from correct submissions by more "
                               "than its own noise; where it does not, that is the reported result"),
            "comparative_claim": False,
            "ablations": [],
            "preregistered": True,
        }
        spec["acceptance_criteria"] = self._spec_criteria(spec, "CM_NONE", {})
        return spec

    def _ablation_specs(self, mode, hypothesis, method, baseline, seeds, datasets,
                        ablations, parent_metrics=None, parent=None) -> list[dict]:
        """One isolation spec per mechanism, measuring what the parent measures.

        These used to declare `primary` and `seed_dispersion` while the experiments
        they ablate declared domain metrics — disjoint sets, so the contrast the
        ablation exists to make could never be constructed from the ledger. An
        ablation that measures something other than the metric the claim rests on
        cannot answer the only question it was created to answer.
        """
        inherited = list(parent_metrics or [])
        # What the ablation's "full method" arm is anchored to. An ablation shows
        # that removing a mechanism costs something; how much that costs matters
        # only if the thing it was removed from is competitive. Take a part out of a
        # method that loses to the current best by a wide margin and you have
        # measured the internals of an also-ran, while the paper reads as if you had
        # measured the internals of a contender.
        parent_sota = ((parent or {}).get("sota") or {})
        anchored = {
            "parent_experiment_id": (parent or {}).get("experiment_id"),
            "parent_has_sota_arm": bool(parent_sota.get("required")),
            "parent_sota_established": bool(parent_sota.get("established")),
        }
        if not anchored["parent_has_sota_arm"]:
            anchored["strawman_risk"] = (
                "the ablated full method has never been placed against the current strongest "
                "method, so the size of every effect measured here is uninterpretable in "
                "absolute terms; report it as an internal contrast only")
        elif not anchored["parent_sota_established"]:
            anchored["strawman_risk"] = (
                "a state-of-the-art arm is declared but was never run here, so the anchor is a "
                "reported number and these effect sizes cannot be placed against it")
        out = []
        for i, ab in enumerate(ablations):
            spec = {
                "experiment_id": f"E-ABL-{i + 1:03d}",
                "title": f"Isolation of {ab['mechanism_id']}",
                "claim_type": CLAIM_ABLATION,
                "hypothesis": f"the effect attributed to {ab['mechanism_id']} survives its removal "
                              f"being the only change: {ab['claim']}",
                # Full method vs. our own ablated variant. Internal contrast only.
                "baseline": {"kind": "own_full_method", "note": "our implementation with the "
                                                                "mechanism enabled",
                             "reproduction_level": baseline.get("reproduction_level")},
                "candidate": {"description": ab["isolation_test"]["description"],
                              "controls": ab["counterfactual_controls"]},
                "datasets": datasets,
                # inherited from the experiment being ablated; the dispersion term is
                # added on top because an ablation is judged against seed noise
                "metrics": (inherited or [
                    {"name": "primary", "role": "decision",
                     "definition_owner": "evaluator-builder"}]) + [
                    {"name": "seed_dispersion", "role": "uncertainty",
                     "definition": "std over seeds, per condition"},
                ],
                "inherits_metrics_from": ab.get("parent_experiment_id"),
                "anchored_to": anchored,
                "seeds": seeds,
                "resources": {"seeds": len(seeds), "conditions": 3,
                              "runs": 3 * len(seeds)},
                "success_metric": ab["predicted_if_mechanism_real"],
                "null_result_is_publishable": True,
                "comparative_claim": False,
                "internal_contrast": True,
                "ablation_ref": ab["ablation_id"],
                "ablations": [ab["ablation_id"]],
                "preregistered": True,
            }
            spec["acceptance_criteria"] = self._spec_criteria(spec, mode, {})
            out.append(spec)
        return out

    def _spec_criteria(self, spec: dict, mode: str, disclosure: dict) -> list[dict]:
        out = [
            {"kind": "success", "statement": spec["success_metric"]},
            {"kind": "failure", "statement": "the predicted effect is absent or within seed "
                                             "dispersion; this is reported, not retried with a "
                                             "different metric"},
            {"kind": "void", "statement": "any invalid_condition of this spec held during the run"},
        ]
        if spec["claim_type"] == CLAIM_COMPARATIVE and mode == "CM_REPORTED":
            out.append({
                "kind": "disclosure",
                "required": True,
                "statement": ("every reported comparison must carry the disclosure that the "
                              "baseline was not reproduced locally: "
                              + (disclosure.get("text_template") or "")),
                "must_appear_in": disclosure.get("must_appear_in", []),
                "failure_if_absent": True,
            })
        return out

    def _sota_arm(self, assets: dict, spec: dict) -> dict:
        """The arm that makes a comparative claim mean something.

        Beating the source paper's own baseline answers "did we beat what they
        beat". Nobody asks that. A comparative experiment without a current-best arm
        produces a number that is true and uninteresting, and an ablation anchored to
        a non-competitive full method is a strawman with extra steps.

        The arm is declared even when the SOTA has not been reproduced: the spec then
        carries SOTA_ARM_NOT_MEASURED as a *narrowing* condition — one that limits
        what the result may claim without voiding the measurement — which makes the
        weakness visible in the plan rather than discovered at review.
        """
        sota = (assets or {}).get("sota") or {}
        cands = sota.get("candidates") or []
        return {
            "required": bool(cands),
            "kind": "current_strongest_reported" if cands else "none_identified",
            "candidates": cands[:5],
            "established": bool(sota.get("established")),
            "comparability": ("measured here" if sota.get("established")
                              else "reported by its authors; not measured here"),
            "note": ("no state-of-the-art candidate was identified, so this comparison is "
                     "against the source paper's baseline only and cannot support a claim "
                     "about competitiveness" if not cands else ""),
        }

    def _invalid_conditions(self, spec, mode, baseline, seeds, repro, assets) -> list[dict]:
        """The states under which this run is void rather than negative.

        A void run and a negative run are different objects: a negative result is
        evidence, a void one is nothing at all. Systems that lack this distinction
        report their voids as findings.
        """
        # Every condition carries a `check` the runtime can actually evaluate.
        # They used to carry only `detect` — prose telling a human what to look at —
        # so not one of them was ever checked on any run and the falsifiability
        # mechanism was decorative. The prose stays for the reader; the predicate is
        # what makes the condition binding. See researchforge.invalid_conditions.
        conds = [
            {"code": "SEEDS_TOO_FEW",
             "condition": f"fewer than {len(seeds)} seeds completed for any condition",
             "why": "the dispersion the success metric is compared against cannot be estimated",
             "detect": "count completed runs per condition in the experiment ledger",
             "check": {"kind": "min_completed_runs_per_condition", "value": len(seeds),
                       # named, so an arm that completed nothing is still counted
                       "conditions": [a for a in ("baseline", "candidate", "sota")
                                      if isinstance(spec.get(a), dict)]}},
            {"code": "EVALUATOR_CHANGED_MID_RUN",
             "condition": "the sha256 of evaluator_code or evaluator_spec differs between the "
                          "first and last run of this experiment",
             "why": "conditions scored by different evaluators are not comparable to each other, "
                    "no matter how carefully they were run",
             "detect": "compare the evaluator digest recorded on each ledger entry",
             "check": {"kind": "field_stable_across_runs", "field": "evaluator_digest"}},
            {"code": "CONDITIONS_NOT_MATCHED",
             "condition": "any factor other than the declared difference varied between "
                          "conditions (data version, hyperparameter budget, hardware class)",
             "why": "the difference measured is then not the difference specified",
             "detect": "diff the run configs recorded in the ledger",
             "check": {"kind": "configs_match_except", "ignore": ["seed", "arm", "run_id"]}},
            {"code": "METRIC_DEFINITION_DRIFT",
             "condition": "the primary metric's operational definition changed after the first run",
             "why": "post-hoc metric choice converts a test into a search",
             "detect": "evaluator_spec digest plus the metric name recorded per run",
             "check": {"kind": "metric_names_stable"}},
        ]

        # Every spec needs its reference condition pinned; what "pinned" means differs.
        # A comparative spec needs an external revision. A diagnostic spec at CM_NONE
        # compares against our OWN unmodified implementation — which still has to hold
        # still, because if our code changes between conditions the difference belongs
        # to the change and not to the mechanism. Scoping this to comparative specs
        # only (as I first did) would have let the internal reference drift unchecked.
        if not baseline.get("established"):
            _external = bool(spec.get("comparative_claim"))
            conds.append({
                "check": ({"kind": "artifact_field_present",
                           "artifact": "baseline_assets", "field": "pinned_revision"}
                          if _external else
                          {"kind": "field_stable_across_runs", "field": "entry_point_sha256"}),
                "code": "BASELINE_NOT_ESTABLISHED",
                "condition": "the baseline condition has no pinned revision/checkpoint at run time "
                             f"(currently: repos={len(assets.get('repos') or [])}, "
                             f"pinned_revision={assets.get('pinned_revision')!r})",
                "why": "an unpinned baseline can silently change between conditions, and every "
                       "difference then belongs to the baseline rather than the candidate",
                "detect": "baseline_assets.pinned_revision must be non-null before the first run"})
        if spec["claim_type"] == CLAIM_COMPARATIVE:
            conds.append({
                "code": "COMPARISON_MODE_DOWNGRADED",
                "condition": f"comparison_mode is no longer {mode} when the run completes",
                "why": "the comparison this spec makes was licensed by that mode alone",
                "detect": "re-read reproduction/comparison_mode.json at analysis time"})
            if mode == "CM_REPORTED":
                conds.append({
                    "code": "DISCLOSURE_ABSENT",
                    "condition": "a reported comparison appears without the mandatory disclosure",
                    "why": "the comparison is against numbers measured elsewhere; without the "
                           "disclosure the reader is misled about what was measured",
                    "detect": "claim-citation-auditor checks the disclosure text is present",
                 "check": {"kind": "text_present_in_artifact", "artifact": "manuscript_draft",
                           "text": ((spec.get("disclosure") or {}).get("text") or "")}})
        if spec["claim_type"] == CLAIM_ABLATION:
            conds.append({
                "code": "ISOLATION_IMPURE",
                "condition": "disabling the mechanism required changing another component",
                "why": "the contrast then isolates two things at once and attributes both to one",
                "detect": "the scaffolder's diff between full and ablated configs touches >1 component"})
        return conds

    # ------------------------------------------------------------------
    # guards
    # ------------------------------------------------------------------
    def _assert_mode_respected(self, specs: list[dict], mode: str) -> None:
        """comparison_mode is a hard constraint. This is where it is hard."""
        if mode == "CM_NONE":
            offenders = [s["experiment_id"] for s in specs
                         if s.get("claim_type") == CLAIM_COMPARATIVE or s.get("comparative_claim")]
            if offenders:
                raise GateBlocked(
                    "comparison_mode",
                    f"comparison mode is CM_NONE — the source result was not reproduced — but "
                    f"experiment(s) {offenders} make a comparative performance claim. No amount of "
                    f"careful running makes that claim supportable.",
                    "remove the comparative experiments (diagnostic and evaluation-methodology "
                    "experiments remain admissible), or raise the reproduction level and re-run "
                    "reproduction-fallback-planner")
        if mode == "CM_REPORTED":
            undisclosed = [s["experiment_id"] for s in specs
                           if s.get("claim_type") == CLAIM_COMPARATIVE
                           and not (s.get("disclosure") or {}).get("required")]
            if undisclosed:
                raise GateBlocked(
                    "comparison_mode",
                    f"comparison mode is CM_REPORTED, so every comparative experiment must carry "
                    f"the disclosure forward; {undisclosed} do not.",
                    "attach disclosure={'required': True, ...} and a disclosure acceptance "
                    "criterion to each comparative spec")

    def _assert_falsifiable(self, specs: list[dict]) -> None:
        bad = [s.get("experiment_id", "<unnamed>") for s in specs
               if not s.get("invalid_conditions")]
        if bad:
            raise GateBlocked(
                "falsifiability",
                f"experiment spec(s) {bad} declare no invalid_conditions. A spec that cannot come "
                f"back void will report every run as a result, including the ones that were void.",
                "state, for each spec, the conditions under which its results must be discarded "
                "(too few seeds, baseline not established, evaluator changed mid-run)")

    def _assert_acyclic(self, dag: dict) -> None:
        edges = {}
        for e in dag["edges"]:
            edges.setdefault(e["from"], []).append(e["to"])
        state: dict[str, int] = {}

        def visit(n: str) -> None:
            if state.get(n) == 1:
                raise GateBlocked("blueprint_dag", f"stage graph contains a cycle through '{n}'",
                                  "a plan whose stages depend on each other cannot be scheduled")
            if state.get(n) == 2:
                return
            state[n] = 1
            for m in edges.get(n, []):
                visit(m)
            state[n] = 2

        for node in [n["id"] for n in dag["nodes"]]:
            visit(node)

    # ------------------------------------------------------------------
    # plan shape
    # ------------------------------------------------------------------
    def _stages(self, specs: list[dict], mode: str) -> list[dict]:
        primary = [s["experiment_id"] for s in specs if s["claim_type"] != CLAIM_ABLATION]
        abl = [s["experiment_id"] for s in specs if s["claim_type"] == CLAIM_ABLATION]
        return [
            {"id": "S1-baseline", "name": "Establish the reference condition",
             "depends_on": [], "experiments": [],
             "exit_criterion": "reference condition pinned to a revision and reproducible twice "
                               "with identical results"},
            {"id": "S2-implement", "name": "Implement the candidate method",
             "depends_on": ["S1-baseline"], "experiments": [],
             "exit_criterion": "candidate runs end to end on the smallest dataset slice"},
            {"id": "S3-primary", "name": "Primary experiments",
             "depends_on": ["S2-implement"], "experiments": primary,
             "exit_criterion": "all seeds completed with no invalid_condition triggered"},
            {"id": "S4-ablations", "name": "Mechanism isolation",
             "depends_on": ["S3-primary"], "experiments": abl,
             "exit_criterion": "each claimed mechanism has an isolation result, including nulls"},
            {"id": "S5-analysis", "name": "Analysis and integrity audit",
             "depends_on": ["S3-primary", "S4-ablations"], "experiments": [],
             "exit_criterion": f"every claim traceable to a ledger entry and admissible under {mode}"},
        ]

    def _dag(self, stages: list[dict], specs: list[dict]) -> dict:
        nodes = [{"id": s["id"], "kind": "stage", "name": s["name"],
                  "experiments": s["experiments"]} for s in stages]
        edges = [{"from": d, "to": s["id"], "kind": "stage_order"}
                 for s in stages for d in s["depends_on"]]
        return {"nodes": nodes, "edges": edges,
                "experiment_index": {s["experiment_id"]: s["claim_type"] for s in specs},
                "note": "stage order only. Runtime branch structure lives in experiment_tree, "
                        "which is the runner's to own; duplicating it here would create a second "
                        "source of truth for what actually ran."}

    def _budgets(self, envelope: dict, specs: list[dict], seeds: list[int],
                 warnings: list[str]) -> dict:
        runs = sum(s["resources"]["runs"] for s in specs)
        # Pass through only what the caller actually declared. A null here reads as
        # "unbudgeted" downstream; a plausible number would read as approved.
        budgets = {
            "gpu_hours": envelope.get("gpu_hours"),
            "wallclock_hours": envelope.get("wallclock_hours"),
            "usd": envelope.get("usd"),
            "seeds_per_condition": len(seeds),
            "planned_runs_total": runs,
            "source": "resource_envelope external input",
        }
        missing = [k for k in ("gpu_hours", "wallclock_hours", "usd") if budgets[k] is None]
        if missing:
            warnings.append(f"resource_envelope declared no {missing}; those budgets are null, not "
                            f"estimated. The runner must treat null as 'unbudgeted' and stop.")
        return budgets

    def _human_gates(self, mode: str) -> list[dict]:
        gates = [
            {"gate": "preregistration_freeze", "before_stage": "S3-primary",
             "question": "are these specs, metrics and invalid_conditions final?",
             "why": "changing them after seeing results converts a test into a search"},
            {"gate": "baseline_accepted", "before_stage": "S3-primary",
             "question": "is the reference condition trustworthy enough to compare against?",
             "why": "every downstream number inherits the baseline's errors"},
        ]
        if mode in ("CM_REPORTED", "CM_RELATIVE"):
            gates.append({"gate": "disclosure_wording", "before_stage": "S5-analysis",
                          "question": "is the mandatory disclosure present and accurate?",
                          "why": f"under {mode} the comparison is only honest with it"})
        return gates

    def _criteria(self, specs, mode, disclosure, cm) -> list[dict]:
        out = [{"experiment_id": s["experiment_id"], "claim_type": s["claim_type"],
                "criteria": s["acceptance_criteria"],
                "invalid_conditions": [c["code"] for c in s["invalid_conditions"]]}
               for s in specs]
        out.append({"experiment_id": "*", "claim_type": "project",
                    "criteria": [
                        {"kind": "mode", "statement": f"no claim outside what {mode} admits; "
                                                      f"forbidden patterns: "
                                                      f"{cm.get('forbidden_claim_patterns', [])}"},
                        {"kind": "provenance",
                         "statement": "every number in the manuscript resolves to a ledger entry"},
                    ] + ([{"kind": "disclosure", "required": True,
                           "statement": disclosure.get("text_template", ""),
                           "must_appear_in": disclosure.get("must_appear_in", [])}]
                         if disclosure.get("required") else []),
                    "invalid_conditions": []})
        return out

    def _criteria_md(self, specs, mode, disclosure, cm, repro) -> str:
        L = ["# Acceptance criteria", "",
             f"Comparison mode: **{mode}** (from reproduction level "
             f"{repro.get('level', 'RL0')}). This bounds what any result below is allowed to "
             f"mean, independently of how it turns out.", ""]
        if mode == "CM_NONE":
            L += ["> No comparative performance claim is admissible in this project. The "
                  "experiments below are diagnostic and evaluation-methodology work on our own "
                  "artifact. An experiment that would compare against the source paper's numbers "
                  "was not compiled — it was refused.", ""]
        if disclosure.get("required"):
            L += ["## Mandatory disclosure", "",
                  "> " + (disclosure.get("text_template") or "").strip(), "",
                  f"Must appear in: {', '.join(disclosure.get('must_appear_in', [])) or 'n/a'}. "
                  f"This is an acceptance criterion: a comparative result written up without it "
                  f"**fails**, it is not merely incomplete.", ""]
        for s in specs:
            L += [f"## {s['experiment_id']} — {s['title']} ({s['claim_type']})", "",
                  f"**Hypothesis.** {s['hypothesis']}", "",
                  f"**Success.** {s['success_metric']}", "",
                  "**Void if any of:**", ""]
            L += [f"- `{c['code']}` — {c['condition']} ({c['why']})" for c in s["invalid_conditions"]]
            L += [""]
            if s.get("disclosure", {}).get("required"):
                L += [f"**Disclosure required** in {', '.join(s['disclosure']['must_appear_in']) or 'the write-up'}.", ""]
        L += ["## Forbidden claim patterns", ""]
        L += [f"- `{p}`" for p in cm.get("forbidden_claim_patterns", [])] or ["- none"]
        L += ["", "A null result on any experiment above is a publishable outcome and is written "
                  "up as one. Nothing here is rerun with a different metric because it came out "
                  "the wrong way."]
        return "\n".join(L)


# ======================================================================
# evaluator-builder
# ======================================================================

#: Guards every evaluator gets regardless of project. These are not guesses about
#: this project's failure modes — they are the ways *any* scored submission can be
#: made to look correct without being correct.
GENERIC_GAMING_PATTERNS = [
    {"id": "self_reported_score",
     "pattern": "the submission includes its own score/reward field",
     "guard": "reject_self_reported_score"},
    {"id": "reference_leakage",
     "pattern": "the submission echoes reference-only fields it could not have derived",
     "guard": "reject_reference_leakage"},
    {"id": "degenerate_constant",
     "pattern": "one constant answer repeated across all items to exploit a majority class",
     "guard": "flag_degenerate_constant"},
    {"id": "non_finite",
     "pattern": "NaN/Inf submitted to break comparisons or aggregate to a favourable value",
     "guard": "reject_non_finite"},
    {"id": "empty_or_missing",
     "pattern": "required fields omitted so the scorer falls back to a default",
     "guard": "reject_missing_fields"},
]

EVALUATOR_SOURCE = r'''#!/usr/bin/env python3
"""Grader-side scoring. Runs OUTSIDE the agent's container.

Objective under evaluation:
  __OBJECTIVE__

Two rules shape everything here:

1. An invalid submission scores `None`, never 0.0. Zero is a legitimate score, so
   returning it for an invalid run launders "this run does not count" into a data
   point that averages in with real ones.
2. The submission never contributes to its own score. Any self-reported score,
   reward or confidence field is grounds for rejection rather than input.

Usage:
    python evaluate.py <submission.json> <reference.json>
Exit codes: 0 = scored, 2 = invalid submission, 3 = usage/IO error.
"""
from __future__ import annotations

import json
import math
import re
import sys
from typing import Any

EVALUATOR_VERSION = "__VERSION__"

#: Field names the submission is allowed to contain, from the declared schema.
SUBMISSION_SCHEMA: dict = json.loads(r"""__SUBMISSION_SCHEMA__""")

#: Patterns this evaluator actively guards against.
GAMING_PATTERNS: list = json.loads(r"""__GAMING_PATTERNS__""")

SELF_SCORE_FIELDS = ("score", "_score", "reward", "confidence", "eval", "grade", "metric")


# ---------------------------------------------------------------- helpers
def _fields(schema: dict) -> list:
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        return sorted(props)
    if isinstance(schema.get("fields"), list):
        return [f if isinstance(f, str) else f.get("name") for f in schema["fields"]]
    return []


def _required(schema: dict) -> list:
    req = schema.get("required")
    if isinstance(req, list):
        return list(req)
    return _fields(schema)


def _norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _numeric_agreement(pred: float, ref: float, tol: float = 1e-9) -> float:
    """1.0 when equal, decaying with relative error. Bounded below at 0."""
    denom = max(abs(float(ref)), tol)
    return max(0.0, 1.0 - abs(float(pred) - float(ref)) / denom)


def _set_f1(pred: Any, ref: Any) -> float:
    p, r = set(map(_norm_text, pred or [])), set(map(_norm_text, ref or []))
    if not p and not r:
        return 1.0
    if not p or not r:
        return 0.0
    inter = len(p & r)
    if inter == 0:
        return 0.0
    prec, rec = inter / len(p), inter / len(r)
    return 2 * prec * rec / (prec + rec)


# ---------------------------------------------------------------- guards
def reject_self_reported_score(submission: dict, reference: dict) -> list:
    hit = [k for k in submission if _norm_text(k) in SELF_SCORE_FIELDS]
    return [{"code": "SELF_REPORTED_SCORE", "detail": f"submission carries {hit}"}] if hit else []


def reject_reference_leakage(submission: dict, reference: dict) -> list:
    allowed = set(_fields(SUBMISSION_SCHEMA))
    if not allowed:
        return []
    leaked = [k for k in submission if k not in allowed and k in reference]
    return ([{"code": "REFERENCE_LEAKAGE",
              "detail": f"submission contains reference-only field(s) {leaked}"}]
            if leaked else [])


def reject_missing_fields(submission: dict, reference: dict) -> list:
    missing = [k for k in _required(SUBMISSION_SCHEMA) if k not in submission]
    return ([{"code": "MISSING_REQUIRED_FIELDS", "detail": f"missing {missing}"}]
            if missing else [])


def reject_non_finite(submission: dict, reference: dict) -> list:
    bad = []
    for k, v in submission.items():
        vals = v if isinstance(v, list) else [v]
        for item in vals:
            if isinstance(item, float) and not math.isfinite(item):
                bad.append(k)
                break
    return [{"code": "NON_FINITE_VALUE", "detail": f"non-finite in {bad}"}] if bad else []


def flag_degenerate_constant(submission: dict, reference: dict) -> list:
    """Not fatal: recorded, because a constant answer can be correct."""
    notes = []
    for k, v in submission.items():
        if isinstance(v, list) and len(v) >= 3 and len({_norm_text(x) for x in v}) == 1:
            notes.append({"code": "DEGENERATE_CONSTANT",
                          "detail": f"field '{k}' is one repeated value across {len(v)} items"})
    return notes


FATAL_GUARDS = (reject_self_reported_score, reject_reference_leakage,
                reject_missing_fields, reject_non_finite)
ADVISORY_GUARDS = (flag_degenerate_constant,)


def validate(submission: Any, reference: Any) -> list:
    if not isinstance(submission, dict):
        return [{"code": "SUBMISSION_NOT_OBJECT", "detail": type(submission).__name__}]
    if not isinstance(reference, dict):
        return [{"code": "REFERENCE_NOT_OBJECT", "detail": type(reference).__name__}]
    out = []
    for g in FATAL_GUARDS:
        out.extend(g(submission, reference))
    return out


# ---------------------------------------------------------------- scoring
def score(submission: Any, reference: Any) -> dict:
    """Score one submission against one reference.

    Returns a dict; `score` is None whenever the submission is invalid. Callers
    that need a number must check `valid` first — there is deliberately no way to
    get a float out of an invalid submission.
    """
    invalidations = validate(submission, reference)
    if invalidations:
        return {"valid": False, "score": None, "components": {}, "notes": [],
                "invalidations": invalidations, "evaluator_version": EVALUATOR_VERSION}

    notes = []
    for g in ADVISORY_GUARDS:
        notes.extend(g(submission, reference))

    components = {}
    for key in _required(SUBMISSION_SCHEMA) or sorted(submission):
        if key not in reference:
            # Unjudgeable rather than wrong: no reference means no verdict.
            components[key] = {"kind": "unjudgeable", "value": None,
                               "why": "no reference value for this field"}
            continue
        pred, ref = submission.get(key), reference.get(key)
        if _finite(pred) and _finite(ref):
            components[key] = {"kind": "numeric", "value": _numeric_agreement(pred, ref)}
        elif isinstance(pred, list) or isinstance(ref, list):
            components[key] = {"kind": "set_f1", "value": _set_f1(pred, ref)}
        else:
            components[key] = {"kind": "exact_normalized",
                               "value": 1.0 if _norm_text(pred) == _norm_text(ref) else 0.0}

    judged = [c["value"] for c in components.values() if c["value"] is not None]
    return {
        "valid": True,
        "score": (sum(judged) / len(judged)) if judged else None,
        "components": components,
        "notes": notes,
        "invalidations": [],
        "judged_fields": len(judged),
        "unjudgeable_fields": len(components) - len(judged),
        "evaluator_version": EVALUATOR_VERSION,
    }


def main(argv: list) -> int:
    if len(argv) != 3:
        print(json.dumps({"error": "usage: evaluate.py <submission.json> <reference.json>"}))
        return 3
    try:
        with open(argv[1], encoding="utf-8") as f:
            submission = json.load(f)
        with open(argv[2], encoding="utf-8") as f:
            reference = json.load(f)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"could not read inputs: {e}"}))
        return 3
    result = score(submission, reference)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


@register
class EvaluatorBuilder(Skill):
    """Scoring definition, runnable scorer, and tests the agent must not see.

    The separation is the point. If the process being evaluated can read the
    decisive tests, the tests measure how well it read them. So the decisive
    material never enters the project tree: what is written here is a manifest
    that says where the grader mounts it from and what it checks, and the scorer
    itself, which is meant to be readable — knowing how you are scored is not
    cheating, knowing the answers is.
    """

    name = "evaluator-builder"

    def execute(self, ctx: Context) -> SkillResult:
        objective = ctx.external("research_objective", None)
        if not objective:
            raise GateBlocked(
                "external_input",
                "no research objective was supplied, and a metric invented without one measures "
                "whatever is easiest to measure",
                "pass --set research_objective='<what a good submission must achieve>'")
        schema = ctx.external("submission_schema", None)
        if not isinstance(schema, dict) or not schema:
            raise GateBlocked(
                "external_input",
                "no submission schema was supplied. Without the shape of an answer, the scorer "
                "cannot distinguish a wrong answer from a malformed one, and would score both.",
                "pass --set submission_schema='{\"properties\":{...},\"required\":[...]}'")

        warnings: list[str] = []
        declared = ctx.external("known_failure_modes", None) or []
        if not declared:
            warnings.append(
                "no project-specific failure modes or gaming patterns were supplied. The evaluator "
                "guards only the generic ones (self-reported score, reference leakage, degenerate "
                "constants, non-finite values, missing fields); anything specific to this task is "
                "unguarded and the spec says so.")
        patterns = list(GENERIC_GAMING_PATTERNS) + [
            {"id": f"project_{i + 1:02d}",
             "pattern": p if isinstance(p, str) else json.dumps(p),
             "guard": None,
             "guarded": False,
             "note": "declared by the caller; no automated guard was generated for it because "
                     "generating a check from a prose description would produce a check that "
                     "passes rather than one that works"}
            for i, p in enumerate(declared)]

        version = _sha(json.dumps([objective, schema, patterns], sort_keys=True))[:16]
        code = (EVALUATOR_SOURCE
                .replace("__OBJECTIVE__", str(objective).replace('"""', "'''"))
                .replace("__VERSION__", version)
                .replace("__SUBMISSION_SCHEMA__", json.dumps(schema, sort_keys=True))
                .replace("__GAMING_PATTERNS__", json.dumps(patterns, sort_keys=True)))
        compile(code, "evaluate.py", "exec")  # a scorer that does not parse is not a scorer
        ctx.store.write(self.name, "evaluator_code", code)
        code_digest = _sha(code)

        manifest = self._hidden_manifest(ctx, patterns, code_digest, version)
        ctx.store.write(self.name, "hidden_tests", manifest)
        ctx.store.write(self.name, "evaluator_spec",
                        self._spec_md(objective, schema, patterns, code_digest, version,
                                      manifest, bool(declared)))
        warnings.append(
            "hidden test payloads were deliberately NOT written into the project. Everything under "
            "the project tree is reachable by the agent under evaluation; the manifest records "
            "what each test decides and where the grader mounts it from.")
        return SkillResult(
            self.name, produced=["evaluator_code", "hidden_tests", "evaluator_spec"],
            warnings=warnings,
            detail={"evaluator_version": version, "guarded_patterns":
                    [p["id"] for p in patterns if p.get("guard")],
                    "unguarded_patterns": [p["id"] for p in patterns if not p.get("guard")]})

    # ------------------------------------------------------------------
    def _hidden_manifest(self, ctx: Context, patterns, code_digest, version) -> dict:
        tests = []
        for i, p in enumerate(patterns):
            tests.append({
                "test_id": f"HT-{i + 1:03d}",
                "targets": p["id"],
                "decides": p["pattern"],
                # The oracle is stated, the payload is not. A digest is enough to
                # detect the oracle being edited without publishing what it expects.
                "oracle": ("score(submission, reference) must return valid=False (score None) for "
                           "a submission exhibiting this pattern"
                           if p.get("guard") else
                           "no automated oracle; a human grader decides this case"),
                "oracle_digest": _sha(f"{p['id']}::{p['pattern']}"),
                "guard": p.get("guard"),
                "automated": bool(p.get("guard")),
                "payload_location": "grader-side only; never materialized in the project tree",
            })
        return {
            "artifact": "hidden_tests",
            "agent_readable": False,
            "visibility": "grader_only",
            "mount_policy": {
                "mount_into_agent_container": False,
                "mount_into_grader_container": True,
                "readable_by": ["grader", "human reviewer"],
                "rationale": ("a decisive test the evaluated process can read stops being decisive: "
                              "it measures reading, not capability"),
            },
            "payloads_written_here": False,
            "why_no_payloads": ("this directory is inside the project tree, which the agent under "
                                "evaluation can read. Only the manifest lives here; expected "
                                "outputs are provisioned by the grader out of band and matched by "
                                "oracle_digest."),
            "evaluator_version": version,
            "evaluator_code_sha256": code_digest,
            "binds_to_evaluator": ("if evaluator_code_sha256 no longer matches evaluation/"
                                   "evaluate.py, the evaluator changed mid-run and every affected "
                                   "experiment is void, not merely re-scored"),
            "tests": tests,
            "generated_at": time.time(),
            "generated_by_run_id": ctx.run_id,
        }

    def _spec_md(self, objective, schema, patterns, code_digest, version, manifest,
                 had_declared) -> str:
        fields = sorted((schema.get("properties") or {})) or schema.get("required") or []
        guarded = [p for p in patterns if p.get("guard")]
        unguarded = [p for p in patterns if not p.get("guard")]
        L = [
            "# Evaluator specification", "",
            f"Version `{version}` · `evaluate.py` sha256 `{code_digest}`", "",
            "## Objective", "", str(objective), "",
            "## What is scored", "",
            f"Submission fields: {', '.join(map(str, fields)) or '(none declared)'}.",
            "",
            "Per field, the scorer picks one comparison and records which one it used:",
            "",
            "- both values finite numbers -> bounded relative agreement, `1 - |p-r|/max(|r|,eps)`",
            "- either value a list -> set F1 over normalized items",
            "- otherwise -> exact match after whitespace/case normalization",
            "",
            "The overall score is the mean over judged fields. A field with no reference value is "
            "`unjudgeable` and is excluded from the mean rather than counted as wrong — the "
            "absence of a reference is the grader's gap, not the submission's error.",
            "",
            "## Validity rules", "",
            "An invalid submission scores `null`, never `0.0`. Zero is a real score; using it for "
            "an invalid run would let a void run average in with real ones. `score()` returns "
            "`valid: false` with the reasons, and `evaluate.py` exits 2.",
            "",
            "| guard | rejects |", "|---|---|",
        ]
        L += [f"| `{p['guard']}` | {p['pattern']} |" for p in guarded]
        L += ["", "## Not guarded", ""]
        if unguarded:
            L += [f"- {p['pattern']} — no automated check was generated. Writing a check from a "
                  f"prose description produces a check that passes, not one that works; this needs "
                  f"a hand-written oracle." for p in unguarded]
        else:
            L += ["- nothing beyond the generic patterns was declared." if not had_declared
                  else "- all declared patterns have guards."]
        L += [
            "",
            "## Hidden tests", "",
            "The decisive tests are **not readable from the agent context** and are not stored in "
            "this project. `evaluation/hidden_tests/_manifest.json` records only what each test "
            "decides and a digest of its oracle; the payloads are mounted by the grader at "
            "scoring time.",
            "",
            "Enforce it in the repository as well as in the container:",
            "",
            "```gitignore",
            "# hidden evaluator material: never committed, never mounted into the agent container",
            "evaluation/hidden_tests/**",
            "!evaluation/hidden_tests/_manifest.json",
            "```",
            "",
            f"Automated oracles: {sum(1 for t in manifest['tests'] if t['automated'])} of "
            f"{len(manifest['tests'])}.",
            "",
            "## Changing this evaluator", "",
            "The manifest pins `evaluator_code_sha256`. If it stops matching `evaluate.py` while an "
            "experiment is in flight, that experiment is **void** (`EVALUATOR_CHANGED_MID_RUN` in "
            "its spec) — conditions scored by two different evaluators were never comparable, and "
            "re-scoring the old runs does not fix the ones that were tuned against the old scorer.",
        ]
        return "\n".join(L)
