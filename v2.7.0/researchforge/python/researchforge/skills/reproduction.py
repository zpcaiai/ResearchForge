"""Reproduction before ideation, and the degradation path when it falls short.

This is the highest-risk stage in the system and the one whose real-world success
rate the project's viability depends on. Under BYOK-without-GPU the reproducer can
honestly reach RL0 and RL1 — it can clone, resolve dependencies and run an entry
point on CPU — and must refuse to claim RL2 or above, because those require
matching numbers the available compute cannot produce.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..skill import Context, Skill, SkillResult, register

GITHUB_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)", re.I)
FAILURE_CODES = ("NO_CODE", "DEPENDENCY_UNRESOLVABLE", "HARDWARE_UNAVAILABLE", "DATA_UNAVAILABLE",
                 "DATA_ACCESS_GATED", "CHECKPOINT_MISSING", "UNDOCUMENTED_PREPROCESSING",
                 "CONFIG_AMBIGUOUS", "NONDETERMINISM", "METRIC_DEFINITION_MISMATCH",
                 "NUMBERS_DIVERGE", "TIMEBOX_EXCEEDED", "LICENSE_BLOCKED")


@register
class ResultReproducer(Skill):
    name = "result-reproducer"
    # comparison-baseline outputs only exist once a direction has been selected
    optional_outputs = ("reproduction_report", "baseline_metrics", "baseline_deviation_log")

    def execute(self, ctx: Context) -> SkillResult:
        model = ctx.store.read(self.name, "paper_model")
        atoms = ctx.store.read(self.name, "contribution_atoms")
        sandbox = ctx.store.read(self.name, "sandbox_manifest")
        timebox = float(ctx.external("repro_timebox_seconds", 4 * 3600))
        # A 30-seed probe of the worked example (docs/study/step0_results.json)
        # found 8 of 12 metrics need a WIDER tolerance than this floor, one of them
        # 67x wider. The floor is therefore a floor and nothing more: step 0 must
        # measure the metric's own dispersion before any RL3 verdict, or the run
        # will classify its own noise as a failed reproduction.
        tol_floor = float(ctx.external("tolerance_relative_floor", 0.02))
        repos = ctx.external("code_urls", []) or self._discover(model)
        started = time.time()
        codes: list[str] = []
        warnings: list[str] = []

        # ---- A. artifact discovery -----------------------------------
        rankings = []
        for i, url in enumerate(repos):
            m = GITHUB_RE.search(url)
            rankings.append({"rank": i + 1, "url": url,
                             "owner": m.group(1) if m else None,
                             "repo": m.group(2) if m else None,
                             "kind": "official" if i == 0 else "candidate",
                             "prior_features": {}})
        ctx.store.write(self.name, "baseline_repo_rankings", {"candidates": rankings})

        if not repos:
            codes.append("NO_CODE")
            warnings.append("no code repository found for this paper. RL0 by definition; the "
                            "degradation path decides what remains possible.")
        assets = {"repos": rankings, "checkpoints": [], "datasets": [],
                  "resolved_at": time.time(), "pinned_revision": None}

        # ---- B. plan --------------------------------------------------
        headline = [a for a in atoms.get("atoms", []) if a.get("kind") == "empirical"][:5]
        plan = {"target_kind": "source_paper", "timebox_seconds": timebox,
                "tolerance_policy": {"kind": "max(reported_variance, 2x measured_std, relative floor)",
                                     "relative_floor": tol_floor,
                                     "measured_std": None,
                                     "note": "seed-variance probe required before any RL3 verdict"},
                "targets": [{"atom_id": a["atom_id"], "claim_ids": a["claim_ids"],
                             "summary": a["summary"][:160]} for a in headline]}
        ctx.store.write(self.name, "source_repro_plan", json.dumps(plan, indent=1))

        # ---- C. tiered attempt ---------------------------------------
        level = "RL0"
        env_digest = None
        detail: dict[str, Any] = {}
        if repos and not ctx.offline:
            level, codes2, detail = self._attempt(repos[0], timebox, ctx)
            codes += codes2
            env_digest = detail.get("env_digest")
        elif repos and ctx.offline:
            codes.append("TIMEBOX_EXCEEDED")
            warnings.append("offline mode: repository clone and dependency resolution were not "
                            "attempted. RL0 here means 'not attempted', not 'not reproducible'.")

        if not sandbox.get("untrusted_code_execution_allowed"):
            codes.append("HARDWARE_UNAVAILABLE")
            warnings.append(
                "no container isolation available, so the paper's entry point was not executed. "
                "RL1 requires running untrusted code and this host is not a security boundary.")
        if level in ("RL3", "RL4"):
            warnings.append("RL3+ requires matched numbers on full-scale runs; downgrading because "
                            "no compute budget was configured")
            level = "RL2"

        ctx.store.write(self.name, "source_repro_env", env_digest or
                        f"# environment not captured\n# python={sys.version.split()[0]}\n")
        ctx.store.write(self.name, "source_repro_metrics",
                        {"comparisons": [],
                         "note": "no paired reported-vs-measured record was produced. Any level "
                                 "above RL1 requires at least one, so none was claimed."})
        ctx.store.append_jsonl(self.name, "repro_failure_taxonomy",
                               [{"code": c, "target": "source_paper", "ts": time.time()}
                                for c in dict.fromkeys(codes)])
        report = {
            "target_paper_id": model.get("paper_id", "unknown"),
            "target_kind": "source_paper",
            "level": level,
            "level_rationale": detail.get("rationale",
                "No execution was attempted or no paired numeric comparison exists."),
            "assessed_at_run_id": ctx.run_id,
            "reduced_scale": True,
            "claim_comparisons": [],
            "failure_codes": sorted(dict.fromkeys(codes)),
            "remediation_candidates": self._remediation(codes),
            "environment_digest": env_digest or "not-captured",
            "timebox_seconds": timebox,
            "timebox_exhausted": (time.time() - started) >= timebox,
            "human_reviewed": False,
        }
        ctx.store.write(self.name, "source_repro_report", report)
        # --- D. who is currently strongest on this task ------------------
        # A comparison against the source paper's own baseline answers "did we beat
        # what they beat". That is not the question a reviewer asks. An ablation
        # anchored to a non-competitive full method is a strawman with extra steps,
        # so the SOTA candidates are discovered HERE, graded by the same RL ladder,
        # and carried in baseline_assets where the blueprint compiler will require them.
        bench = ctx.store.read(self.name, "benchmark_matrix", default="")
        lit = ctx.store.read(self.name, "literature_candidates", default=[])
        declared = ctx.external("sota_methods", []) or []
        sota = self._sota(model, bench, lit, declared)
        assets["sota"] = sota
        # Flat mirror of sota.established. The invalid-condition checker reads one
        # level of an artifact and has no path syntax, deliberately — a check that
        # can address arbitrary nesting is a check nobody can audit by reading it.
        # Keeping the flat key here is cheaper than teaching every checker to walk.
        assets["sota_established"] = bool(sota["established"])
        if not sota["candidates"]:
            warnings.append(
                "no state-of-the-art candidate could be identified for this task. Retrieval "
                "coverage is the usual cause, and the consequence is concrete: any later "
                "comparative claim would be against the source paper's baseline only, which "
                "answers 'did we beat what they beat' rather than 'is this competitive'. "
                "Supply them with --set sota_methods='[...]' if you know them.")
        elif not sota["established"]:
            warnings.append(
                f"{len(sota['candidates'])} SOTA candidate(s) identified but none established "
                f"(none reproduced locally). Comparative claims against them may only be made "
                f"under the disclosure the comparison mode requires.")

        ctx.store.write(self.name, "baseline_assets", assets)
        ctx.store.write(self.name, "baseline_license_risk", self._license(rankings))
        return SkillResult(self.name,
                           produced=["baseline_repo_rankings", "source_repro_plan", "source_repro_env",
                                     "source_repro_metrics", "repro_failure_taxonomy",
                                     "source_repro_report", "baseline_assets", "baseline_license_risk"],
                           warnings=warnings, next_state="REPRO_LEVEL_ESTABLISHED",
                           detail={"level": level, "failure_codes": report["failure_codes"]})

    # ------------------------------------------------------------------
    def _discover(self, model) -> list[str]:
        return []

    def _sota(self, model, bench, lit, declared) -> dict:
        """Current strongest methods on this paper's benchmarks.

        Deliberately conservative about what counts as evidence. A number scraped
        from a benchmark table is a REPORTED number, not a measured one, and this
        records it as such — `established` stays False until something in the
        reproduction ladder actually ran it. The alternative, treating a table cell
        as a baseline, is how a project ends up comparing its measured result
        against someone else's best-case reported one.
        """
        cands: list[dict] = []
        for d in declared:
            if isinstance(d, dict):
                cands.append({**d, "source": "declared_by_user", "reported_only": True})
            elif isinstance(d, str):
                cands.append({"name": d, "source": "declared_by_user", "reported_only": True})

        # benchmark_matrix is CSV written by literature-search: benchmark,reported_by,value,source
        for line in str(bench or "").splitlines()[1:]:
            cols = [c.strip().strip('"') for c in line.split(",")]
            if len(cols) >= 4 and cols[0] and cols[0].lower() != "benchmark":
                cands.append({"name": cols[1] or "unknown", "benchmark": cols[0],
                              "reported_value": cols[2], "source": "benchmark_matrix",
                              "citation": cols[3], "reported_only": True})

        metrics = {str(m).lower() for m in (model.get("metrics") or [])}
        for w in (lit or [])[:80]:
            title = str(w.get("title") or "")
            if any(k in title.lower() for k in ("state-of-the-art", "sota", "outperform")):
                cands.append({"name": title[:120], "source": "literature_candidates",
                              "provider": w.get("_provider"), "reported_only": True})

        seen, uniq = set(), []
        for c in cands:
            k = str(c.get("name", "")).lower()[:80]
            if k and k not in seen:
                seen.add(k); uniq.append(c)
        return {
            "candidates": uniq[:20],
            "established": False,
            "established_ids": [],
            "relevant_metrics": sorted(metrics)[:12],
            "note": ("Reported numbers only. A candidate becomes `established` when it has been "
                     "run here and graded RL3+, which is the only state in which our measured "
                     "number and its number are comparable."),
        }

    def _attempt(self, url: str, timebox: float, ctx: Context):
        codes: list[str] = []
        tmp = Path(tempfile.mkdtemp(prefix="rf-repro-"))
        try:
            r = subprocess.run(["git", "clone", "--depth", "1", url, str(tmp / "repo")],
                               capture_output=True, text=True, timeout=min(600, timebox))
            if r.returncode != 0:
                codes.append("NO_CODE")
                return "RL0", codes, {"rationale": f"clone failed: {r.stderr[-200:]}"}
            repo = tmp / "repo"
            rev = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            reqs = [p for p in ("requirements.txt", "pyproject.toml", "environment.yml", "setup.py")
                    if (repo / p).exists()]
            if not reqs:
                codes.append("CONFIG_AMBIGUOUS")
            readme = next((p for p in repo.iterdir()
                           if p.name.lower().startswith("readme")), None)
            has_repro_section = bool(readme and re.search(
                r"^#+.*(reproduc|replicat|results)", readme.read_text(errors="replace"), re.I | re.M))
            codes.append("HARDWARE_UNAVAILABLE")
            return "RL0", codes, {
                "rationale": ("repository cloned and inspected; execution not attempted because no "
                              "isolated runtime was available. RL0 = not reproduced, not "
                              "'not reproducible'."),
                "env_digest": f"# repo={url}\n# revision={rev}\n# dep_files={reqs}\n",
                "revision": rev, "dependency_files": reqs,
                "readme_has_reproduction_section": has_repro_section}
        except subprocess.TimeoutExpired:
            codes.append("TIMEBOX_EXCEEDED")
            return "RL0", codes, {"rationale": "clone exceeded the time box"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _remediation(self, codes):
        # Priorities here are not guesses. A 20-paper probe (docs/study/FINDINGS.md,
        # 2026-08-11, seed 20260811) found DEPENDENCY_UNRESOLVABLE to be the single
        # largest addressable failure class, and within it pinned torch/CUDA wheels
        # absent from PyPI dominated. That is why the dependency remediations below
        # are specific about *which* index to reach for rather than saying "fix the
        # dependencies". NO_CODE and CONFIG_AMBIGUOUS together accounted for 40% of
        # that sample and have no engineering remedy at all — they are what the
        # degradation path exists for.
        table = {
            "CHECKPOINT_MISSING": ("locate a weight mirror or contact the authors", 0.0, 2.0),
            "HARDWARE_UNAVAILABLE": ("rent a GPU instance, or run the paper's reduced setting", 20.0, 1.0),
            "DATA_ACCESS_GATED": ("submit the dataset access application", 0.0, 72.0),
            # Corrected 2026-08-11. The A' arm (docs/study/APRIME_FINDINGS.md) built
            # and tested the "dependency time machine" this table used to recommend:
            # a date-pinned index snapshot and a multi-version interpreter pool. On the
            # same 20 papers it produced ZERO real lift. The date snapshot helped one
            # paper; the interpreter pool helped none; the apparent gain from switching
            # resolvers evaporated under a real install, because a dry-run does not run
            # setup.py. What actually blocks these repos is a CUDA build toolchain —
            # flash-attn and its class import torch at build time and then compile.
            "DEPENDENCY_UNRESOLVABLE": (
                "the blocker is almost always a CUDA extension package (flash-attn, "
                "xformers, deepspeed) that compiles at install time: install torch first, "
                "retry with --no-build-isolation, and above all supply a prebuilt wheel "
                "matched to the torch/CUDA/Python triple. A date-pinned index does NOT "
                "fix this — that was tested and it did not work", 0.0, 2.0),
            "CONFIG_AMBIGUOUS": (
                "no environment specification exists anywhere in the tree; this is not "
                "fixable by tooling. Contact the authors or accept the degraded mode",
                0.0, 4.0),
            "NO_CODE": ("search for a credible third-party reimplementation", 0.0, 2.0),
        }
        out = []
        for c in dict.fromkeys(codes):
            if c in table:
                a, usd, hrs = table[c]
                out.append({"failure_code": c, "action": a, "est_cost_usd": usd, "est_hours": hrs})
        return out

    def _license(self, rankings):
        lines = ["# License and reuse risk", ""]
        if not rankings:
            lines.append("- no repository located; nothing to assess")
        for r in rankings:
            lines.append(f"- {r['url']}: license NOT inspected. Inspect LICENSE at the exact commit "
                         f"before vendoring, and record commit + license in a dependency lock.")
        lines += ["", "Permissive, non-commercial, share-alike and custom responsible-use terms are "
                      "materially different. Treat every upstream asset as reference-only until a "
                      "project-specific review is complete."]
        return "\n".join(lines)


@register
class ReproductionFallbackPlanner(Skill):
    name = "reproduction-fallback-planner"

    MODE = {"RL4": "CM_MEASURED", "RL3": "CM_MEASURED", "RL2": "CM_RELATIVE",
            "RL1": "CM_REPORTED", "RL0": "CM_NONE"}
    MODES = {
        "RL4": ["extend_generalize", "replace_simplify", "combine_transfer", "explain_diagnose",
                "benchmark_evaluate", "systemize"],
        "RL3": ["extend_generalize", "replace_simplify", "combine_transfer", "explain_diagnose",
                "benchmark_evaluate", "systemize"],
        "RL2": ["extend_generalize", "replace_simplify", "combine_transfer", "explain_diagnose",
                "benchmark_evaluate", "systemize"],
        "RL1": ["explain_diagnose", "benchmark_evaluate", "systemize", "combine_transfer"],
        "RL0": ["explain_diagnose", "benchmark_evaluate"],
    }
    DISCLOSURE = {
        "CM_REPORTED": ("The comparison baseline was not reproduced locally (level {lvl}). All "
                        "comparisons are against numbers as reported by the original authors, under "
                        "a different environment. Observed shortfalls: {codes}."),
        "CM_RELATIVE": ("Only reduced-scale reproduction was achieved (level {lvl}). Comparisons are "
                        "relative, under identical environment and seeds; published absolute values "
                        "are not comparable to these runs."),
        "CM_NONE": ("The source artifact could not be reproduced (level {lvl}; {codes}). No "
                    "comparative performance claim is made in this work."),
    }

    def execute(self, ctx: Context) -> SkillResult:
        report = ctx.store.read(self.name, "source_repro_report")
        codes = ctx.store.read(self.name, "repro_failure_taxonomy", default=[])
        lvl = report.get("level", "RL0")
        mode = self.MODE[lvl]
        modes = self.MODES[lvl]
        code_list = ", ".join(sorted({c["code"] for c in codes})) or "none recorded"

        remediations = report.get("remediation_candidates", [])
        warnings = []
        if remediations:
            warnings.append(
                "recoverable causes were identified before accepting the degraded mode: "
                + "; ".join(f"{r['failure_code']} -> {r['action']} (~{r['est_hours']}h, "
                            f"${r['est_cost_usd']:.0f})" for r in remediations)
                + ". Accepting a lower comparison mode while a cheap remediation exists is a choice, "
                  "and it is recorded as one.")
        if mode == "CM_NONE":
            warnings.append(
                "RL0 is not a terminal state. Comparative performance claims are closed off, but "
                "diagnostic and evaluation-methodology directions remain open — and the failure "
                "taxonomy just produced is itself evidence for them.")

        disclosure = self.DISCLOSURE.get(mode)
        cm = {
            "mode": mode,
            "derived_from_level": lvl,
            "derived_from_report_id": report.get("assessed_at_run_id"),
            "admissible_idea_modes": modes,
            "forbidden_claim_patterns": self._forbidden(mode),
            "disclosure_required": {
                "required": mode != "CM_MEASURED",
                "text_template": (disclosure.format(lvl=lvl, codes=code_list) if disclosure else ""),
                "must_appear_in": ["experimental setup", "limitations"] if disclosure else [],
            },
            "substitute_baseline": None,
            "approved_by": None,
            "autonomous_decision_id": f"auto-{ctx.run_id}" if ctx.mode == "auto" else None,
        }
        ctx.store.write(self.name, "comparison_mode", cm)
        ctx.store.write(self.name, "idea_mode_constraints", {
            "level": lvl, "admissible": modes,
            "closed_off": [m for m in self.MODES["RL4"] if m not in modes],
            "rationale": f"derived from reproduction level {lvl}",
        })
        ctx.store.append_jsonl(self.name, "fallback_decision_log", [{
            "ts": time.time(), "run_id": ctx.run_id, "level": lvl, "mode": mode,
            "closed_modes": [m for m in self.MODES["RL4"] if m not in modes],
            "failure_codes": sorted({c["code"] for c in codes}),
            "remediations_offered": remediations,
            "decided_by": "auto" if ctx.mode == "auto" else "pending_human_ack",
        }])
        return SkillResult(self.name,
                           produced=["comparison_mode", "idea_mode_constraints", "fallback_decision_log"],
                           warnings=warnings, next_state="REPRO_LEVEL_ESTABLISHED",
                           detail={"level": lvl, "comparison_mode": mode, "admissible_modes": modes})

    def _forbidden(self, mode):
        if mode == "CM_NONE":
            return ["outperforms", "improves over", "achieves higher", "state-of-the-art",
                    "beats the baseline", "reduces error by"]
        if mode == "CM_REPORTED":
            return ["we reproduce", "our measured baseline", "under identical conditions"]
        if mode == "CM_RELATIVE":
            return ["absolute comparison with published values", "state-of-the-art"]
        return []
