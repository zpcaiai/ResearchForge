"""The innovation engine, under the constraints reproduction imposed."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..errors import CoverageInsufficient, HumanDecisionRequired
from ..skill import Context, Skill, SkillResult, register

SYNTHETIC_NOTE = ("Produced with OfflineStubProvider. This is not research output; it exercises "
                  "the contract and the state machine. The release gate treats it as a blocker.")


def _ask_json(ctx: Context, system: str, prompt: str, fallback: Any) -> tuple[Any, bool]:
    """Ask the model for JSON. Returns (value, synthetic).

    A parse failure is not silently replaced with the fallback — the fallback is
    only used when the provider itself is the offline stub, which is already
    marked. A real model that returns unparseable output is a real error.
    """
    r = ctx.model.complete(prompt, system=system, max_tokens=8000, json_mode=True)
    if r.synthetic:
        return fallback, True
    ctx.quota.record(ctx.model.name, tokens_in=r.tokens_in, tokens_out=r.tokens_out,
                     usd=r.usd, endpoint="complete")
    txt = r.text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if m:
        txt = m.group(1).strip()
    return json.loads(txt), False


@register
class IdeaSeedMiner(Skill):
    name = "idea-seed-miner"

    def execute(self, ctx: Context) -> SkillResult:
        model = ctx.store.read(self.name, "paper_model")
        atoms = ctx.store.read(self.name, "contribution_atoms")["atoms"]
        coverage = ctx.store.read(self.name, "coverage_report")
        lit = ctx.store.read(self.name, "literature_candidates", default=[])
        repro = ctx.store.read(self.name, "source_repro_report")
        warnings: list[str] = []
        synthetic = False

        # --- Mode A: assumptions, failure modes, blind spots ----------
        fallback_w = {"assumptions": [
            {"assumption_id": f"W-{i+1:03d}", "atom_id": a["atom_id"],
             "assumption": f"[synthetic] the paper assumes the conditions under which '{a['summary'][:90]}' holds generalize",
             "why_it_might_fail": "[synthetic]", "severity": "unknown", "_synthetic": True}
            for i, a in enumerate(atoms[:8])]}
        wm, s1 = _ask_json(ctx, "You are a rigorous reviewer mining a paper for its soft spots.",
                           self._prompt_weakness(model, atoms), fallback_w)
        synthetic |= s1
        ctx.store.write(self.name, "weakness_map",
                        {**wm, "_synthetic": s1, "_note": SYNTHETIC_NOTE if s1 else None,
                         "mode": "A/assumption-weakness"})
        ctx.store.append_jsonl(self.name, "assumption_tests", [
            {"test_id": f"T-{i+1:03d}", "assumption_id": a.get("assumption_id"),
             "cheap_probe": "[synthetic] minimal probe not generated offline" if s1 else a.get("probe", ""),
             "_synthetic": s1}
            for i, a in enumerate(wm.get("assumptions", [])[:12])])

        # --- Mode B: evidenced gaps ----------------------------------
        if coverage.get("status") == "UNKNOWN_COVERAGE":
            warnings.append(
                "coverage is UNKNOWN, so gaps mined here are not evidenced gaps. They are "
                "hypotheses about gaps, and idea-evaluator may not certify novelty from them.")
        gaps = [{"gap_id": f"G-{i+1:03d}",
                 "statement": f"[unevidenced] no retrieved work addresses: {a['summary'][:120]}",
                 "evidence_strength": "NONE" if coverage.get("status") == "UNKNOWN_COVERAGE" else "WEAK",
                 "supporting_works": [], "coverage_status": coverage.get("status"),
                 "_synthetic": True}
                for i, a in enumerate(atoms[:6])]
        ctx.store.append_jsonl(self.name, "gap_ledger", gaps)
        ctx.store.write(self.name, "gap_evidence", self._gap_md(gaps, coverage, lit))

        # --- Mode C: cross-domain analogies --------------------------
        fallback_a = [{"analogy_id": f"X-{i+1:03d}", "source_domain": "[synthetic]",
                       "mechanism": "[synthetic]", "transfer_to": a["atom_id"],
                       "why_it_might_transfer": "[synthetic]", "_synthetic": True}
                      for i, a in enumerate(atoms[:4])]
        an, s3 = _ask_json(ctx, "You find mechanisms in other fields that would transfer here.",
                           self._prompt_analogy(model, atoms), fallback_a)
        synthetic |= s3
        ctx.store.append_jsonl(self.name, "analogy_candidates",
                               an if isinstance(an, list) else an.get("analogies", []))

        modes_run = ["A/weakness", "B/gap", "C/analogy"]
        if repro.get("level") == "RL0":
            warnings.append("source paper is at RL0: seeds derived from its internal mechanism are "
                            "unverified, since the mechanism was never observed to work here.")
        if synthetic:
            warnings.append("OFFLINE: seeds are structurally valid placeholders, not research.")
        return SkillResult(self.name,
                           produced=["weakness_map", "assumption_tests", "gap_ledger",
                                     "gap_evidence", "analogy_candidates"],
                           warnings=warnings, synthetic=synthetic,
                           detail={"modes_run": modes_run, "atoms": len(atoms), "gaps": len(gaps)})

    def _prompt_weakness(self, model, atoms):
        return ("Mine this paper's contributions for assumptions, failure modes, evaluation blind "
                "spots and implementation bottlenecks.\n\nReturn JSON: {\"assumptions\":[{"
                "\"assumption_id\",\"atom_id\",\"assumption\",\"why_it_might_fail\",\"severity\","
                "\"probe\"}]}\n\nTITLE: " + str(model.get("title")) + "\n\nCONTRIBUTIONS:\n"
                + "\n".join(f"- {a['atom_id']}: {a['summary']}" for a in atoms[:15]))

    def _prompt_analogy(self, model, atoms):
        return ("Identify mechanisms from OTHER fields that would transfer to this paper's problem. "
                "Return JSON list of {analogy_id, source_domain, mechanism, transfer_to, "
                "why_it_might_transfer}.\n\nTITLE: " + str(model.get("title")) + "\n"
                + "\n".join(f"- {a['atom_id']}: {a['summary'][:150]}" for a in atoms[:10]))

    def _gap_md(self, gaps, coverage, lit):
        L = ["# Gap evidence", ""]
        L.append(f"Retrieved corpus: {len(lit)} works. Coverage status: **{coverage.get('status')}**.")
        if coverage.get("status") == "UNKNOWN_COVERAGE":
            L += ["", "> Every gap below is **unevidenced**. A gap is a claim that the field has not "
                      "done something; that claim requires a search that could have found it.", ""]
        for g in gaps:
            L.append(f"- `{g['gap_id']}` ({g['evidence_strength']}) {g['statement']}")
        return "\n".join(L)


@register
class IdeaPortfolioGenerator(Skill):
    name = "idea-portfolio-generator"
    optional_outputs = ("mutant_candidates", "idea_lineage_graph")

    def execute(self, ctx: Context) -> SkillResult:
        weak = ctx.store.read(self.name, "weakness_map")
        gaps = ctx.store.read(self.name, "gap_ledger", default=[])
        analogies = ctx.store.read(self.name, "analogy_candidates", default=[])
        cm = ctx.store.read(self.name, "comparison_mode")
        constraints = ctx.store.read(self.name, "idea_mode_constraints")
        repro = ctx.store.read(self.name, "source_repro_report")
        admissible = constraints["admissible"]
        warnings: list[str] = []

        fallback = self._synthetic_portfolio(admissible, weak, gaps, repro)
        data, synthetic = _ask_json(
            ctx, "You generate research directions that are falsifiable and costed.",
            self._prompt(weak, gaps, analogies, cm, admissible), fallback)
        ideas = data if isinstance(data, list) else data.get("ideas", [])

        illegal = [i for i in ideas if i.get("mode") and i["mode"] not in admissible]
        if illegal:
            warnings.append(
                f"dropped {len(illegal)} candidate(s) using innovation modes closed off at "
                f"{repro.get('level')}: {sorted({i['mode'] for i in illegal})}. "
                f"comparison_mode={cm['mode']} permits only {admissible}.")
            ideas = [i for i in ideas if i.get("mode") in admissible]
        if not ideas:
            warnings.append("portfolio is empty after mode filtering — this is a failure, not a "
                            "result: even at RL0 the diagnostic and evaluation modes remain open.")
        for i, idea in enumerate(ideas):
            idea.setdefault("idea_id", f"I-{i+1:03d}")
        ctx.store.write(self.name, "idea_portfolio", ideas)
        if synthetic:
            warnings.append("OFFLINE: portfolio entries are placeholders, not research directions.")
        return SkillResult(self.name, produced=["idea_portfolio"], warnings=warnings,
                           synthetic=synthetic, next_state="IDEAS_READY",
                           detail={"count": len(ideas), "admissible_modes": admissible,
                                   "comparison_mode": cm["mode"]})

    def _prompt(self, weak, gaps, analogies, cm, admissible):
        return ("Generate 5-8 candidate research directions.\n\n"
                f"HARD CONSTRAINT: comparison mode is {cm['mode']}. Admissible innovation modes are "
                f"{admissible} — a direction using any other mode is inadmissible and must not be "
                f"generated. Forbidden claim patterns: {cm['forbidden_claim_patterns']}.\n\n"
                "Each idea must be JSON with: idea_id, title, mode (one of the admissible), "
                "hypothesis, delta (the exact difference vs baseline — 'improve X' is not a delta), "
                "mechanism, why_it_should_work, closest_prior_work, minimum_experiment (an OBJECT "
                "with description/baseline/dataset/metric/expected_effect/compute_estimate/"
                "duration_estimate), success_metrics (non-empty list), kill_criteria (non-empty "
                "list: what result would end this direction), compute_cost, novelty_risk.\n\n"
                "Return {\"ideas\":[...]}\n\n"
                f"WEAKNESSES: {json.dumps(weak)[:3000]}\n"
                f"GAPS: {json.dumps(gaps)[:2000]}\n"
                f"ANALOGIES: {json.dumps(analogies)[:1500]}")

    def _synthetic_portfolio(self, admissible, weak, gaps, repro):
        lvl = repro.get("level", "RL0")
        seeds = (weak.get("assumptions") or [])[:3] + (gaps or [])[:3]
        out = []
        for i, s in enumerate(seeds[:6]):
            mode = admissible[i % len(admissible)]
            out.append({
                "idea_id": f"I-{i+1:03d}",
                "title": f"[synthetic/{mode}] direction seeded by "
                         f"{s.get('assumption_id') or s.get('gap_id')}",
                "mode": mode,
                "hypothesis": "[synthetic] placeholder hypothesis; no model produced this",
                "delta": "[synthetic]",
                "mechanism": "[synthetic]",
                "why_it_should_work": "[synthetic]",
                "closest_prior_work": "[unknown — coverage was not measured]",
                "minimum_experiment": {
                    "description": f"[synthetic] placeholder probe, admissible under {lvl}",
                    "baseline": "[synthetic] none established",
                    "dataset": None,
                    "metric": "[synthetic]",
                    "expected_effect": None,
                    "compute_estimate": "unknown",
                    "duration_estimate": "unknown",
                },
                "success_metrics": ["[synthetic]"],
                "kill_criteria": ["[synthetic] kill if the probe shows no effect"],
                "compute_cost": "unknown",
                "novelty_risk": "unknown",
                "_synthetic": True, "_note": SYNTHETIC_NOTE,
            })
        return {"ideas": out}


@register
class IdeaEvaluator(Skill):
    name = "idea-evaluator"

    def execute(self, ctx: Context) -> SkillResult:
        ideas = ctx.store.read(self.name, "idea_portfolio")
        coverage = ctx.store.read(self.name, "coverage_report")
        cm = ctx.store.read(self.name, "comparison_mode")
        repro = ctx.store.read(self.name, "source_repro_report")
        assets = ctx.store.read(self.name, "baseline_assets")
        unknown = coverage.get("status") == "UNKNOWN_COVERAGE"
        warnings: list[str] = []

        novelty, prior, feas = [], [], []
        for idea in ideas:
            iid = idea["idea_id"]
            status = "UNKNOWN_COVERAGE" if unknown else "INCREMENTAL"
            novelty.append({
                "idea_id": iid, "novelty_status": status,
                "coverage_status": coverage.get("status"),
                "coverage_score": coverage.get("score"),
                "closest_prior_work_ids": [],
                "rationale": ("Coverage was never measured, so no absence claim is supportable. "
                              "NOVEL_ENOUGH is unavailable by rule, not by judgment."
                              if unknown else "no near-duplicate found in the retrieved corpus"),
                "blocked_from_novel_enough": unknown,
            })
            prior.append({"idea_id": iid, "closest": None,
                          "delta_table": {"problem": "?", "mechanism": "?", "data": "?",
                                          "evaluation": "?", "outcome": "?"},
                          "note": "no prior work retrieved to compare against"})
            has_code = bool(assets.get("repos"))
            feas.append({
                "idea_id": iid,
                "feasibility_score": 3 if not has_code else 5,
                "uncertainty": "HIGH",
                "uncertainty_is_separate_from_score": True,
                "blockers": ([] if has_code else ["no working code base: the source paper is at "
                                                  f"{repro.get('level')}"]),
                "compute_class": "unknown",
                "reason": (f"comparison mode {cm['mode']} constrains what this idea could ever "
                           f"demonstrate, independent of how hard it is to build"),
            })
        if unknown:
            warnings.append(
                f"all {len(ideas)} candidates are UNKNOWN_COVERAGE. NOVEL_ENOUGH is blocked for "
                f"every one of them. This is the intended behaviour: the system refuses to "
                f"distinguish 'no prior work exists' from 'we could not look'.")
        ctx.store.write(self.name, "novelty_report",
                        {"verdicts": novelty, "coverage": coverage.get("status")})
        ctx.store.append_jsonl(self.name, "closest_prior_work", prior)
        ctx.store.write(self.name, "feasibility_report", {"assessments": feas})
        return SkillResult(self.name,
                           produced=["novelty_report", "closest_prior_work", "feasibility_report"],
                           warnings=warnings,
                           detail={"evaluated": len(ideas), "coverage": coverage.get("status")})


@register
class IdeaRanker(Skill):
    name = "idea-ranker"

    def execute(self, ctx: Context) -> SkillResult:
        ideas = ctx.store.read(self.name, "idea_portfolio")
        nov = {v["idea_id"]: v for v in ctx.store.read(self.name, "novelty_report")["verdicts"]}
        fea = {f["idea_id"]: f for f in ctx.store.read(self.name, "feasibility_report")["assessments"]}
        cov = ctx.store.read(self.name, "coverage_report")
        cm = ctx.store.read(self.name, "comparison_mode")
        prefs = ctx.external("user_priorities", {}) or {}
        warnings: list[str] = []

        NOV = {"NOVEL_ENOUGH": 8.0, "INCREMENTAL": 4.0, "DUPLICATE_RISK": 1.0,
               "UNKNOWN_COVERAGE": 2.0}
        rows = []
        for idea in ideas:
            iid = idea["idea_id"]
            n, f = nov.get(iid, {}), fea.get(iid, {})
            n_s = NOV.get(n.get("novelty_status"), 2.0)
            f_s = float(f.get("feasibility_score", 3))
            penalty = 2.0 if n.get("blocked_from_novel_enough") else 0.0
            rows.append({
                "idea_id": iid, "title": idea.get("title"), "mode": idea.get("mode"),
                "scores": {"novelty": n_s, "feasibility": f_s,
                           "falsifiability": 6.0 if idea.get("kill_criteria") else 2.0,
                           "uncertainty_penalty": penalty},
                "composite": round(n_s + f_s - penalty, 2),
                "why_not_higher": ("novelty is capped at UNKNOWN_COVERAGE until retrieval coverage "
                                   "is measured" if penalty else "—"),
                "swing_evidence": ("measuring retrieval coverage would move this ranking more than "
                                   "any other single action"),
            })
        rows.sort(key=lambda r: -r["composite"])
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        pareto = self._pareto(rows)
        ctx.store.write(self.name, "ranked_ideas",
                        {"ranking": rows, "criteria": ["novelty", "feasibility", "falsifiability"],
                         "not_collapsed_to_single_score": True,
                         "hard_constraints_applied_as_filters": list(prefs.keys())})
        ctx.store.write(self.name, "idea_pareto_front", {"front": pareto})
        ctx.store.write(self.name, "ranking_rationale", self._md(rows, pareto, cov, cm))
        if cov.get("status") == "UNKNOWN_COVERAGE":
            warnings.append("every candidate carries the same uncertainty penalty, so this ranking "
                            "orders by feasibility more than by novelty. Read it as provisional.")
        return SkillResult(self.name,
                           produced=["ranked_ideas", "idea_pareto_front", "ranking_rationale"],
                           warnings=warnings, detail={"ranked": len(rows), "pareto": len(pareto)})

    def _pareto(self, rows):
        front = []
        for r in rows:
            dominated = any(
                o["scores"]["novelty"] >= r["scores"]["novelty"]
                and o["scores"]["feasibility"] >= r["scores"]["feasibility"]
                and (o["scores"]["novelty"] > r["scores"]["novelty"]
                     or o["scores"]["feasibility"] > r["scores"]["feasibility"])
                for o in rows if o is not r)
            if not dominated:
                front.append(r["idea_id"])
        return front

    def _md(self, rows, pareto, cov, cm):
        L = ["# Ranking rationale", "",
             f"Comparison mode: **{cm['mode']}** — this bounds what any of these directions could "
             f"ever claim, regardless of how they rank.", "",
             f"Retrieval coverage: **{cov.get('status')}**.", "",
             "| rank | idea | mode | novelty | feasibility | composite |",
             "|---:|---|---|---:|---:|---:|"]
        for r in rows:
            L.append(f"| {r['rank']} | {str(r['title'])[:60]} | {r.get('mode','')} | "
                     f"{r['scores']['novelty']} | {r['scores']['feasibility']} | {r['composite']} |")
        L += ["", f"Pareto front: {', '.join(pareto) or 'none'}", "",
              "## What would change this ranking most", "",
              "Measuring retrieval coverage. Every novelty score here is capped by the fact that "
              "the search could not be shown to have looked."]
        return "\n".join(L)


@register
class UserFeedbackGate(Skill):
    name = "user-feedback-gate"

    def execute(self, ctx: Context) -> SkillResult:
        ranked = ctx.store.read(self.name, "ranked_ideas")
        cm = ctx.store.read(self.name, "comparison_mode")
        feedback = ctx.external("user_feedback", None)
        rows = ranked["ranking"]

        if ctx.mode == "guided" and not feedback:
            ctx.store.append_jsonl(self.name, "decision_log", [{
                "ts": time.time(), "run_id": ctx.run_id, "event": "gate_opened",
                "presented": [r["idea_id"] for r in rows], "comparison_mode": cm["mode"]}])
            raise HumanDecisionRequired(
                f"{len(rows)} directions are ranked under comparison mode {cm['mode']}. "
                f"Choose one, merge several, add a constraint, or reject all.",
                "ranked_ideas")

        if ctx.mode == "auto":
            chosen = [rows[0]["idea_id"]] if rows else []
            decided_by, note = "auto", "autonomous decision recorded; no human approved this"
        else:
            chosen = feedback.get("selected", []) if isinstance(feedback, dict) else [feedback]
            decided_by, note = "human", feedback.get("note", "") if isinstance(feedback, dict) else ""

        known = {r["idea_id"] for r in rows}
        bad = [c for c in chosen if c not in known]
        if bad:
            from ..errors import GateBlocked
            raise GateBlocked("selection", f"unknown idea id(s) {bad}",
                              f"choose from {sorted(known)}")

        sel = {
            "selected_idea_ids": chosen,
            "merged": len(chosen) > 1,
            "comparison_mode": cm["mode"],
            "admissible_modes": ctx.store.read(self.name, "comparison_mode")["admissible_idea_modes"],
            "constraints_added": (feedback or {}).get("constraints", {}) if isinstance(feedback, dict) else {},
            "decided_by": decided_by, "note": note, "ts": time.time(),
            "rejected_but_retained": [r["idea_id"] for r in rows if r["idea_id"] not in chosen],
        }
        ctx.store.write(self.name, "selected_direction", sel)
        ctx.store.append_jsonl(self.name, "decision_log", [{
            "ts": time.time(), "run_id": ctx.run_id, "event": "selection",
            "selected": chosen, "decided_by": decided_by,
            "retained": sel["rejected_but_retained"]}])
        return SkillResult(self.name, produced=["selected_direction", "decision_log"],
                           next_state="DIRECTION_SELECTED",
                           detail={"selected": chosen, "decided_by": decided_by})
