"""Meta plane: the offline maintenance entry point.

These three skills are not part of a research run. They are the second entry
point of the package — the loop that measures the system and changes it — and
they operate on the system, not on a paper. Nothing here may be reached from a
run in flight, because a system that edits itself while being measured has
destroyed the measurement.

The whole plane exists to answer one question honestly: *is the new version
better?* Everything that could make that answer flattering is refused rather than
approximated:

* A retrospective benchmark whose contamination floor is unmeasured cannot gate
  promotion. The model under test has very likely read the follow-up papers, so
  recall@k may be measuring memory. An unmeasured floor is not a low floor.
* A benchmark that changes after a system has been scored against it is not the
  same benchmark, and every score already measured against it is void.
* Two scores are comparable only when they came from the same system: same
  contract digest, same task suite, same evaluator. A version delta is legitimate
  only as a declared A/B arm pair, never as a silent re-baselining.
* A skill is promoted only when held-out performance improves and no guardrail
  regresses. Training-set improvement is the signal this module exists to
  disbelieve.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from ..errors import GateBlocked
from ..generated import ARTIFACTS, CONTRACT_DIGEST, INTERNAL_ARTIFACT_SPECS, SKILLS
from ..skill import REGISTRY, Context, Skill, SkillResult, register

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
#: Relations that make a citing work a genuine follow-up. A paper that cites the
#: seed in its related-work paragraph is not a follow-up, and counting it as one
#: inflates the gold set with work the seed did not cause.
#: Compared after `_norm`, so punctuation and separators in a corpus's vocabulary
#: ("related_work_mention", "related work mention") do not silently fall through
#: into the "not adjudicated" bucket and lose the reason for the exclusion.
MATERIAL_RELATIONS = frozenset({"extends", "replaces", "supersedes", "diagnoses",
                                "refutes", "improves", "generalizes"})
INCIDENTAL_RELATIONS = frozenset({"incidental", "related work mention", "background",
                                  "cites only", "motivational"})
#: A direction descriptor must be in the same vocabulary the innovation engine
#: emits, or the comparison is between a sentence and a paper.
DESCRIPTOR_FIELDS = ("problem_delta", "method_delta", "mechanism", "demonstrating_experiment")

REQUIRED_FRONTMATTER = ("name", "description", "version", "stage", "artifact_kind",
                        "implementation_status")
REQUIRED_SECTIONS = ("Objective", "Inputs", "Outputs", "Depends on", "Procedure",
                     "Hard gates", "Verification / tests", "Evidence contract",
                     "Failure behavior")
#: Frontmatter values that assert the package ships running code for a skill.
CLAIMS_RUNTIME = frozenset({"implemented", "runtime-complete", "production", "shipped",
                            "implementation-complete"})

_PKG_ROOT = Path(__file__).resolve().parents[3]   # .../forge


def _sha(obj: Any) -> str:
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(obj).hexdigest()
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode("utf-8")).hexdigest()
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return round(sum(xs) / len(xs), 6) if xs else None


# --------------------------------------------------------------------------
# comparability
# --------------------------------------------------------------------------
#: The four things that must be identical before two numbers may be subtracted.
COMPARABILITY_KEYS = ("contract_digest", "suite_hash", "evaluator_digest", "skill_version")
#: What an A/B arm pair is allowed to differ in — exactly one thing.
AB_SHARED_KEYS = ("contract_digest", "suite_hash", "evaluator_digest")


def comparability_key(sc: dict) -> dict:
    return {k: sc.get(k) for k in COMPARABILITY_KEYS}


def assert_same_system(a: dict, b: dict, *, label_a: str = "baseline",
                       label_b: str = "current") -> None:
    """Refuse to treat two scorecards as measurements of the same system.

    A regression check subtracts one score from another. That subtraction is only
    meaningful if the two runs differ in nothing but time. A changed contract
    digest means the artifact graph itself changed; a changed skill version means
    a different system answered the questions. Either way the difference is not a
    regression or an improvement — it is a category error, and reporting it as a
    delta is how a rewrite gets credited with a gain it did not produce.
    """
    ka, kb = comparability_key(a), comparability_key(b)
    unknown = [k for k in COMPARABILITY_KEYS if ka[k] in (None, "") or kb[k] in (None, "")]
    if unknown:
        raise GateBlocked(
            "score_comparability",
            f"scorecards '{label_a}' and '{label_b}' cannot be placed on a common axis: "
            f"{sorted(unknown)} is missing from at least one of them. An unstamped score has "
            f"no system attached to it.",
            "re-run research-eval-harness so both scorecards carry contract_digest, "
            "suite_hash, evaluator_digest and skill_version")
    diff = {k: [ka[k], kb[k]] for k in COMPARABILITY_KEYS if ka[k] != kb[k]}
    if diff:
        raise GateBlocked(
            "score_comparability",
            f"refusing to compare '{label_a}' with '{label_b}': they differ in "
            f"{sorted(diff)} ({json.dumps(diff, sort_keys=True)}). These are not the same "
            f"system, so the difference between their scores is not a regression or an "
            f"improvement.",
            "re-run both arms under one contract digest, one frozen suite and one evaluator; "
            "if the versions are meant to differ, declare them as an A/B arm pair "
            "(skill-evolution-manager) instead of comparing them as one system over time")


def assert_ab_arms(baseline: dict, candidate: dict) -> None:
    """Allow exactly one difference: the skill version under evaluation.

    This is the only legitimate way a version delta enters a comparison. Everything
    else is held fixed, and the difference is labelled as an arm pair rather than
    laundered into a time series.
    """
    diff = {k: [baseline.get(k), candidate.get(k)] for k in AB_SHARED_KEYS
            if baseline.get(k) != candidate.get(k)}
    if diff:
        raise GateBlocked(
            "score_comparability",
            f"A/B arms differ in more than the skill version: "
            f"{json.dumps(diff, sort_keys=True)}. A comparison with two moving parts cannot "
            f"attribute the difference to the edit.",
            "re-run the candidate arm against the same frozen suite, evaluator and contract "
            "digest as the baseline arm")
    if baseline.get("skill_version") == candidate.get("skill_version"):
        raise GateBlocked(
            "score_comparability",
            f"both arms report skill_version "
            f"{baseline.get('skill_version')!r}; there is no edit to evaluate. Two runs of "
            f"the same version measure run-to-run variance, not an improvement.",
            "stamp the candidate arm with the edited skill's new version")
    for name, sc in (("baseline", baseline), ("candidate", candidate)):
        if sc.get("contract_digest") != CONTRACT_DIGEST:
            raise GateBlocked(
                "score_comparability",
                f"{name} scorecard was produced under contract digest "
                f"{sc.get('contract_digest')!r}, but this runtime is "
                f"{CONTRACT_DIGEST!r}. The artifact graph changed between measurement and "
                f"decision, so the scores describe a system that no longer exists.",
                "re-run both arms under the current contract, or check out the code matching "
                "the digest the scorecards were produced under")


def recall_at_k(gold_ids: list[str], adjudications: list[dict], k: int,
                system_ranks: dict[str, int]) -> float | None:
    """Recall@k over *adjudicated* matches.

    Matching is never string overlap: a direction that rephrases the follow-up is
    a match and a direction that shares its nouns is not, and no lexical rule
    tells those apart. Every match here came from a rubric decision made
    elsewhere and recorded; unadjudicated pairs simply do not count.
    """
    if not gold_ids:
        return None
    matched = set()
    for a in adjudications:
        if not a.get("match"):
            continue
        rank = system_ranks.get(a.get("system_direction_id"))
        if rank is None or rank > k:
            continue
        matched.add(a.get("gold_direction_id"))
    return round(len(matched & set(gold_ids)) / len(gold_ids), 6)


# --------------------------------------------------------------------------
# retrospective-benchmark-builder
# --------------------------------------------------------------------------
@register
class RetrospectiveBenchmarkBuilder(Skill):
    """Pair seed papers with the follow-up work actually published afterwards.

    The benchmark's value and its danger are the same fact: the follow-ups are
    real, published, and almost certainly in the model's pretraining data. So this
    skill's primary output is not a score — it is a *floor*, measured by asking
    the model to recite the follow-ups directly. A recall@k above that floor is
    weak evidence of reasoning; a recall@k at or below it is evidence of nothing.
    """

    name = "retrospective-benchmark-builder"

    def execute(self, ctx: Context) -> SkillResult:
        coverage = ctx.store.read(self.name, "coverage_report")
        registry = ctx.store.read(self.name, "provider_registry")
        graph = ctx.store.read(self.name, "citation_graph", default={"nodes": [], "edges": []})
        warnings: list[str] = []

        k = int(ctx.external("recall_k", 10) or 10)
        domain = ctx.external("domain_scope", "unspecified")
        venues = list(ctx.external("venue_list", []) or [])
        seed_window = ctx.external("seed_window", None)
        eval_window = ctx.external("eval_window", None)
        cutoff = ctx.external("model_knowledge_cutoff", None)
        selection_rule = ctx.external("seed_selection_rule", None)
        gate_promotion = bool(ctx.external("gate_skill_promotion", False))

        if not seed_window or not eval_window:
            raise GateBlocked(
                "benchmark_windows",
                "seed window and evaluation window were not both supplied. Without a closed "
                "seed window the benchmark has no 'afterwards', and follow-up membership "
                "becomes whatever the corpus happens to contain.",
                "pass --set seed_window='{\"start\":\"2019-01-01\",\"end\":\"2019-12-31\"}' and "
                "--set eval_window='{\"start\":\"2020-01-01\",\"end\":\"2022-12-31\"}'")
        if not selection_rule:
            warnings.append(
                "no seed selection rule was stated. The rule is recorded as UNSTATED; a benchmark "
                "built by picking papers that come to mind measures fame, not judgment, and "
                "nobody reading the report can tell whether that happened.")
            selection_rule = "UNSTATED"
        if not cutoff:
            warnings.append(
                "no model knowledge cutoff supplied, so no follow-up can be classified as "
                "post-cutoff. The held-out subset is empty and every pair is inside the "
                "contamination window.")

        raw_pairs = self._corpus(ctx)
        pairs, adjudications = self._adjudicate(raw_pairs, seed_window, eval_window, cutoff)
        scoreable = [p for p in pairs if p["gold_directions"]]
        if not scoreable:
            raise GateBlocked(
                "benchmark_empty",
                f"no seed survived adjudication with at least one usable follow-up direction "
                f"({len(raw_pairs)} seeds supplied, "
                f"{sum(1 for a in adjudications if a['verdict'] == 'follow_up')} follow-ups "
                f"accepted). An empty benchmark scores every system 0/0, which reads as a "
                f"result and is not one.",
                "supply a corpus whose follow-ups carry an explicit relation and the four "
                f"direction fields {list(DESCRIPTOR_FIELDS)}")

        # The freeze covers exactly what a score depends on: which seeds, which
        # follow-ups, which directions, and how they are scored. The contamination
        # floor is measured *about* a frozen benchmark and is deliberately outside
        # the hash, so re-probing with a different model does not read as tampering.
        scoring = {"metric": f"recall@{k}", "k": k,
                   "matching": "rubric-adjudicated semantic match between direction descriptors",
                   "matching_is_not": "string overlap, embedding threshold, or title match",
                   "known_ceiling": ("unknown and below 1.0: not every good direction was "
                                     "published, and not everything published was good"),
                   "interpretation": ("comparative signal between system versions only; never an "
                                      "absolute measure of research quality")}
        content = {"domain_scope": domain, "venues": sorted(venues),
                   "seed_window": seed_window, "eval_window": eval_window,
                   "selection_rule": selection_rule, "scoring": scoring,
                   "pairs": [self._frozen_view(p) for p in scoreable]}
        content_hash = _sha(content)[:32]
        version = str(ctx.external("benchmark_version", "1") or "1")
        self._check_freeze(ctx, content_hash, version)

        floor = self._contamination_probe(ctx, scoreable, k)
        if floor["measured"] and floor["floor_recall"] is not None and floor["floor_recall"] > 0:
            warnings.append(
                f"contamination floor is {floor['floor_recall']:.2f}: the model named "
                f"{floor['floor_recall']:.0%} of the gold follow-ups when asked directly. Any "
                f"system recall@{k} at or below this measures recall, not reasoning.")
        if not floor["measured"]:
            warnings.append(
                "contamination floor is UNMEASURED. This benchmark may not gate skill promotion, "
                "and any recall@k measured against it is uninterpretable: an unmeasured floor is "
                "not a low floor.")
        blind = ctx.external("blind_rubric", None)
        if not blind:
            warnings.append(
                "no blind human rubric result was supplied. recall@k may be reported as a "
                "regression signal between versions, but not as evidence that the output is "
                "worth anything; the two do not substitute for each other.")

        usable = bool(floor["measured"])
        records: list[dict] = [{
            "record_type": "freeze",
            "benchmark_version": version,
            "content_hash": content_hash,
            "frozen_at": time.time(),
            "frozen_by_run_id": ctx.run_id,
            "contract_digest": CONTRACT_DIGEST,
            "domain_scope": domain, "venues": sorted(venues),
            "seed_window": seed_window, "eval_window": eval_window,
            "selection_rule": selection_rule,
            "scoring": scoring,
            "corpus_provenance": self._corpus_provenance(ctx),
            "coverage_status": coverage.get("status"),
            "citation_graph_nodes": len(graph.get("nodes", []) or []),
            "contamination_floor": floor,
            "blind_rubric": blind or {"status": "NOT_SUPPLIED"},
            "usable_for_promotion_gating": usable,
            "why_not_usable": None if usable else (
                "contamination floor unmeasured — see hard gate in "
                "skills/retrospective-benchmark-builder/SKILL.md"),
            "counts": {"seeds": len(scoreable),
                       "gold_directions": sum(len(p["gold_directions"]) for p in scoreable),
                       "held_out_post_cutoff": sum(
                           1 for p in scoreable for d in p["gold_directions"]
                           if d["split"] == "held_out_post_cutoff")},
            "held_out_policy": "post-cutoff subset is reported separately and never used to tune",
        }]
        records += [{"record_type": "pair", **p} for p in scoreable]
        records += [{"record_type": "adjudication", **a} for a in adjudications]
        ctx.store.write(self.name, "retro_benchmark", records)
        ctx.store.write(self.name, "retro_benchmark_report",
                        self._report(records[0], scoreable, adjudications, coverage, warnings))

        # Written first, then refused: the artifacts are the evidence of why the
        # refusal happened, and deleting them would leave the operator with an
        # exception and no way to see what the floor probe actually did.
        if gate_promotion and not usable:
            raise GateBlocked(
                "contamination_floor",
                "this benchmark was requested as a gate on skill promotion, but its "
                "contamination floor is unmeasured "
                f"({floor['why_unmeasured']}). The model under test has very likely read the "
                "follow-up papers; without a floor, a high recall@k is indistinguishable from "
                "memorization and would promote a skill on the strength of the model's memory.",
                "run the probe against a real model provider (not the offline stub), or supply "
                "recorded probe transcripts with --set contamination_probe_responses='{\"<seed_id>\":"
                "[\"<title>\", ...]}'; only then may this benchmark gate promotion")

        return SkillResult(
            self.name, produced=["retro_benchmark", "retro_benchmark_report"],
            warnings=warnings,
            detail={"seeds": len(scoreable), "content_hash": content_hash,
                    "contamination_floor": floor["floor_recall"],
                    "floor_measured": floor["measured"],
                    "usable_for_promotion_gating": usable})

    # -- corpus ---------------------------------------------------------
    def _corpus_provenance(self, ctx: Context) -> dict:
        raw = ctx.external("retro_corpus", None)
        if isinstance(raw, str):
            p = Path(raw)
            return {"kind": "supplied_file", "path": str(p),
                    "sha256": _sha(p.read_bytes())[:32] if p.exists() else None}
        return {"kind": "supplied_inline", "sha256": _sha(raw)[:32] if raw else None}

    def _corpus(self, ctx: Context) -> list[dict]:
        """Load the seed/follow-up corpus, or refuse.

        There is no third option. Scholarly APIs (OpenAlex, arXiv, Crossref,
        OpenReview) are unreachable from this environment — the egress proxy
        answers 403 — so a benchmark cannot be harvested here. Inventing seeds and
        follow-ups that look right would produce a benchmark that scores systems
        against fiction, which is worse than having no benchmark, because the
        numbers would be believed.
        """
        raw = ctx.external("retro_corpus", None)
        records: list[dict] | None = None
        if isinstance(raw, str):
            p = Path(raw)
            if not p.exists():
                raise GateBlocked(
                    "benchmark_corpus",
                    f"retro_corpus points at {p}, which does not exist",
                    "supply a readable .json or .jsonl corpus of seed/follow-up pairs")
            text = p.read_text(encoding="utf-8")
            if p.suffix == ".jsonl":
                records = [json.loads(l) for l in text.splitlines() if l.strip()]
            else:
                loaded = json.loads(text)
                records = loaded if isinstance(loaded, list) else (
                    loaded.get("pairs") or loaded.get("records") or [])
        elif isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict):
            records = raw.get("pairs") or raw.get("records") or []

        if records:
            return records

        live = [p for p in ctx.scholarly
                if p.available() and getattr(p, "_transport", None) is not None]
        if not live:
            raise GateBlocked(
                "benchmark_corpus",
                "no retrospective corpus was supplied and no scholarly provider has a working "
                "transport. Scholarly APIs (OpenAlex, arXiv, Crossref, OpenReview) are "
                "unreachable from this environment — the egress proxy returns 403 for every "
                "scholarly host — so the seed papers and their real follow-up work cannot be "
                "harvested here. This skill will not invent them: a benchmark of plausible "
                "titles would score every system against fiction, and the scores would still "
                "look like measurements.",
                "build one with tools/benchmark/build_retro_corpus.py, which derives seed/"
                "follow-up pairs from archived Papers With Code evaluation tables (a published "
                "record of who beat whom on which benchmark, needing no citation-intent judgment "
                "from us), and pass the result with --set retro_corpus=/path/to/corpus.json; see "
                "benchmarks/retro-v1/. Or supply your own corpus "
                "with records {seed_id, seed_title, seed_published, followups:[{id, title, "
                "published, relation, problem_delta, method_delta, mechanism, "
                "demonstrating_experiment}]} — or run where OpenAlex/Crossref are reachable and "
                "configure a provider transport")
        raise GateBlocked(
            "benchmark_corpus",
            f"no retrospective corpus was supplied. Providers {[p.name for p in live]} have "
            f"transports, but forward-citation traversal (seed -> works that cite it, filtered "
            f"to material follow-ups) is not implemented against them; a search transport "
            f"cannot assemble follow-up sets, and guessing them from a keyword search would "
            f"fabricate the gold standard.",
            "supply --set retro_corpus=/path/to/corpus.jsonl, or implement forward-citation "
            "traversal in providers.py before building a benchmark from live sources")

    # -- adjudication ---------------------------------------------------
    def _adjudicate(self, raw_pairs: list[dict], seed_window: Any, eval_window: Any,
                    cutoff: Any) -> tuple[list[dict], list[dict]]:
        """Separate genuine follow-ups from incidental citations, and record why.

        Every exclusion is written down. A benchmark whose adjudication decisions
        are invisible is over-trusted precisely because it looks clean.
        """
        pairs, decisions = [], []
        seed_end = str((seed_window or {}).get("end", "")) if isinstance(seed_window, dict) else ""
        for raw in raw_pairs:
            seed_id = str(raw.get("seed_id") or raw.get("id") or f"S-{len(pairs) + 1:03d}")
            gold = []
            for f in raw.get("followups", []) or raw.get("follow_ups", []) or []:
                fid = str(f.get("id") or f.get("doi") or f.get("title", ""))[:120]
                rel = _norm(f.get("relation", ""))
                pub = str(f.get("published", ""))
                d = {"seed_id": seed_id, "followup_id": fid, "relation": rel or None}
                if rel in INCIDENTAL_RELATIONS:
                    d.update(verdict="excluded",
                             reason="incidental citation: cites the seed without extending, "
                                    "replacing, diagnosing or refuting it")
                elif rel not in MATERIAL_RELATIONS:
                    d.update(verdict="excluded",
                             reason=f"relation {f.get('relation')!r} is not adjudicated; "
                                    f"membership in the gold set is not guessed from the text")
                elif seed_end and pub and pub <= seed_end:
                    d.update(verdict="excluded",
                             reason=f"published {pub} is not after the seed window "
                                    f"(ends {seed_end}); it cannot be a follow-up")
                else:
                    missing = [k for k in DESCRIPTOR_FIELDS if not str(f.get(k, "")).strip()]
                    if missing:
                        d.update(verdict="excluded",
                                 reason=f"direction descriptor incomplete, missing {missing}. "
                                        f"A follow-up that cannot be reduced to a direction "
                                        f"cannot be compared with a generated direction.")
                    else:
                        split = ("held_out_post_cutoff"
                                 if cutoff and pub and pub > str(cutoff) else "inside_cutoff")
                        gold.append({
                            "gold_direction_id": f"{seed_id}::{fid}",
                            "followup_id": fid, "title": f.get("title"),
                            "published": pub or None, "relation": rel, "split": split,
                            **{k: f.get(k) for k in DESCRIPTOR_FIELDS},
                        })
                        d.update(verdict="follow_up",
                                 reason=f"material relation {rel!r} with a complete direction "
                                        f"descriptor", split=split)
                decisions.append(d)
            pairs.append({
                "seed_id": seed_id,
                "seed_title": raw.get("seed_title") or raw.get("title"),
                "seed_published": raw.get("seed_published"),
                "gold_directions": gold,
                "n_supplied_citations": len(raw.get("followups", []) or
                                            raw.get("follow_ups", []) or []),
            })
        return pairs, decisions

    def _frozen_view(self, pair: dict) -> dict:
        """What the freeze hash covers: the pairing, not the commentary."""
        return {"seed_id": pair["seed_id"],
                "gold": [{k: d.get(k) for k in
                          ("gold_direction_id", "split", *DESCRIPTOR_FIELDS)}
                         for d in pair["gold_directions"]]}

    # -- freeze ---------------------------------------------------------
    def _check_freeze(self, ctx: Context, content_hash: str, version: str) -> None:
        existing = ctx.store.read(self.name, "retro_benchmark", default=[])
        prior = next((r for r in existing if r.get("record_type") == "freeze"), None)
        if prior is None or prior.get("content_hash") == content_hash:
            return
        override = ctx.external("supersede_frozen_benchmark", None)
        needed = ("new_version", "reason", "approved_by")
        if isinstance(override, dict) and all(str(override.get(f, "")).strip() for f in needed):
            if str(override["new_version"]) == str(prior.get("benchmark_version")):
                raise GateBlocked(
                    "benchmark_freeze",
                    f"supersede was approved but reuses version "
                    f"{prior.get('benchmark_version')!r}. Two different benchmarks under one "
                    f"version label make every recorded score ambiguous.",
                    "give the superseding benchmark a new version label")
            return
        raise GateBlocked(
            "benchmark_freeze",
            f"benchmark version {prior.get('benchmark_version')!r} was frozen at "
            f"{prior.get('content_hash')} on run {prior.get('frozen_by_run_id')!r}; the corpus "
            f"now supplied hashes to {content_hash}. Seeds and follow-ups are frozen before any "
            f"system runs against them. Adjusting them after seeing results is how a benchmark "
            f"becomes a description of the system that scored well on it, and it voids every "
            f"score already measured against the old content.",
            "build the revision as a NEW benchmark version — pass "
            "--set supersede_frozen_benchmark='{\"new_version\":\"2\",\"reason\":\"<what was "
            "wrong>\",\"approved_by\":\"<person>\"}' — and re-run every system against it; scores "
            "from version "
            f"{prior.get('benchmark_version')!r} may not be compared with scores from the revision")

    # -- contamination probe --------------------------------------------
    def _contamination_probe(self, ctx: Context, pairs: list[dict], k: int) -> dict:
        """Ask the model to name the follow-ups. That recall is the floor.

        This is the measurement the benchmark is worthless without. A system that
        scores 0.6 against a floor of 0.6 has demonstrated that it remembers the
        literature, which is not the capability under test.
        """
        supplied = ctx.external("contamination_probe_responses", None) or {}
        per_seed, unmeasured = [], []
        for p in pairs:
            gold_titles = {_norm(d.get("title") or d["followup_id"]) for d in p["gold_directions"]}
            gold_ids = {_norm(d["followup_id"]) for d in p["gold_directions"]}
            if p["seed_id"] in supplied:
                named = list(supplied[p["seed_id"]] or [])
                source = "recorded_probe_transcript"
            else:
                r = ctx.model.complete(
                    self._probe_prompt(p, k),
                    system="You are being probed for memorized knowledge, not asked to reason. "
                           "Name only work you actually recall.",
                    max_tokens=1200, json_mode=True)
                if r.synthetic:
                    # The offline stub cannot be probed. Treating its silence as
                    # "the model knows nothing" would manufacture a floor of zero,
                    # which is the single most flattering possible error.
                    unmeasured.append(p["seed_id"])
                    per_seed.append({"seed_id": p["seed_id"], "recall": None,
                                     "named": 0, "gold": len(gold_titles),
                                     "source": "offline-stub (not a measurement)"})
                    continue
                ctx.quota.record(ctx.model.name, tokens_in=r.tokens_in, tokens_out=r.tokens_out,
                                 usd=r.usd, endpoint="contamination_probe")
                named = self._parse_named(r.text)
                source = ctx.model.name
            hits = {g for g in gold_titles | gold_ids
                    if any(g and (g in _norm(n) or _norm(n) in g) for n in named)}
            denom = len(gold_titles) or 1
            per_seed.append({"seed_id": p["seed_id"],
                             "recall": round(min(len(hits), denom) / denom, 6),
                             "named": len(named), "gold": len(gold_titles), "source": source})
        measured = not unmeasured and bool(per_seed)
        return {
            "measured": measured,
            "floor_recall": _mean([s["recall"] for s in per_seed]) if measured else None,
            "method": "direct recitation probe: the model is asked to name known follow-ups to "
                      "each seed; the fraction of the gold set it names is the floor",
            "scored_by": "normalized title/id containment",
            "is_a_lower_bound": True,
            "lower_bound_note": ("title matching misses paraphrase and misses recognition that "
                                 "does not surface as a title, so the true contamination is at "
                                 "least this. The error direction is the dangerous one: an "
                                 "undercounted floor makes memorization look like reasoning."),
            # A recorded transcript does not carry the identity of the model that
            # produced it. Stamping this run's provider onto it would attribute the
            # floor to a model that may never have been probed.
            "probe_sources": sorted({s["source"] for s in per_seed}),
            "live_provider": getattr(ctx.model, "name", "unknown"),
            "probed_model_declared": ctx.external("probe_model_id", None),
            "per_seed": per_seed,
            "unmeasured_seeds": unmeasured,
            "why_unmeasured": (None if measured else
                               f"{len(unmeasured) or len(pairs)} seed(s) were not probed against a "
                               f"real model: provider "
                               f"{getattr(ctx.model, 'name', 'unknown')!r} returns synthetic output"),
        }

    def _probe_prompt(self, pair: dict, k: int) -> str:
        return ("Name the published works you already know of that directly follow up on this "
                "paper — works that extend, replace, diagnose or refute it. Do not reason about "
                "what such work would look like; list only what you recall.\n\n"
                f"SEED: {pair.get('seed_title')} ({pair.get('seed_published')})\n\n"
                f"Return JSON: {{\"known_followups\": [\"<title>\", ...]}} (at most {max(k, 20)}).")

    def _parse_named(self, text: str) -> list[str]:
        t = text.strip()
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
        if m:
            t = m.group(1).strip()
        try:
            d = json.loads(t)
        except ValueError:
            # A probe response that will not parse is not evidence of no knowledge.
            return [l.strip("-* \t") for l in text.splitlines() if l.strip()]
        if isinstance(d, list):
            return [str(x) for x in d]
        for key in ("known_followups", "followups", "works", "titles"):
            if isinstance(d.get(key), list):
                return [str(x if not isinstance(x, dict) else x.get("title", "")) for x in d[key]]
        return []

    # -- report ---------------------------------------------------------
    def _report(self, freeze: dict, pairs: list[dict], decisions: list[dict],
                coverage: dict, warnings: list[str]) -> str:
        floor = freeze["contamination_floor"]
        acc = [d for d in decisions if d["verdict"] == "follow_up"]
        exc = [d for d in decisions if d["verdict"] == "excluded"]
        L = [
            "# Retrospective benchmark — construction and limits", "",
            f"Version `{freeze['benchmark_version']}` · content hash "
            f"`{freeze['content_hash']}` · frozen by run `{freeze['frozen_by_run_id']}`",
            f"Contract digest `{freeze['contract_digest']}`", "",
            "## Contamination floor", "",
        ]
        if not floor["measured"]:
            L += ["**UNMEASURED.** " + str(floor["why_unmeasured"]) + "",
                  "",
                  "This benchmark **may not be used to gate skill promotion**. The model under "
                  "test has very likely read the follow-up papers, so recall@k here may be "
                  "measuring memory. An unmeasured floor is not a low floor, and a score read "
                  "against it is uninterpretable.", ""]
        else:
            L += [f"**{floor['floor_recall']:.3f}** — that fraction of the gold follow-ups was "
                  f"named when the model was asked to recite them, with no reasoning required. "
                  f"Probe sources: {', '.join(floor['probe_sources'])}; declared model under "
                  f"test: `{floor['probed_model_declared'] or 'NOT DECLARED'}`.", "",
                  "A system score at or below this number is not evidence of research judgment. "
                  "The floor is a **lower bound**: " + floor["lower_bound_note"], ""]
        L += ["| seed | probe recall | named | gold | source |", "|---|---|---|---|---|"]
        L += [f"| `{s['seed_id']}` | {'unmeasured' if s['recall'] is None else round(s['recall'], 2)}"
              f" | {s['named']} | {s['gold']} | {s['source']} |" for s in floor["per_seed"]]
        L += [
            "", "## Construction", "",
            f"- domain scope: {freeze['domain_scope']}",
            f"- venues: {', '.join(freeze['venues']) or '(none declared)'}",
            f"- seed window: {json.dumps(freeze['seed_window'])}",
            f"- evaluation window: {json.dumps(freeze['eval_window'])}",
            f"- seed selection rule: {freeze['selection_rule']}",
            f"- corpus: {freeze['corpus_provenance']['kind']} "
            f"(sha256 `{freeze['corpus_provenance']['sha256']}`)",
            f"- retrieval coverage at build time: **{coverage.get('status')}**",
            "",
            "Seeds and follow-ups came from an externally supplied corpus. Scholarly APIs are "
            "unreachable from this environment, so nothing here was harvested live and nothing "
            "was invented; the corpus hash above is what the benchmark rests on.",
            "", "## Adjudication", "",
            f"{len(acc)} follow-ups accepted, {len(exc)} citations excluded. A paper that cites "
            f"the seed is not a follow-up.", "",
            "| followup | verdict | why |", "|---|---|---|",
        ]
        L += [f"| `{d['followup_id'][:48]}` | {d['verdict']} | {d['reason']} |"
              for d in decisions[:60]]
        L += [
            "", "## Scoring", "",
            f"- metric: `{freeze['scoring']['metric']}`",
            f"- matching: {freeze['scoring']['matching']}",
            f"- matching is explicitly **not**: {freeze['scoring']['matching_is_not']}",
            f"- known ceiling: {freeze['scoring']['known_ceiling']}",
            f"- interpretation: {freeze['scoring']['interpretation']}",
            "", "## Held-out subset", "",
            f"{freeze['counts']['held_out_post_cutoff']} of "
            f"{freeze['counts']['gold_directions']} gold directions postdate the declared model "
            f"cutoff. {freeze['held_out_policy']}.",
            "", "## Blind rubric", "",
        ]
        if freeze["blind_rubric"].get("status") == "NOT_SUPPLIED":
            L += ["**Not run.** recall@k may be used as a regression signal between versions. It "
                  "may not be reported as a measure of research quality without a blind rubric in "
                  "which experts score generated directions against human-authored ones. The "
                  "retrospective metric catches regressions cheaply; the rubric is what "
                  "establishes the output is worth anything. Neither substitutes for the other."]
        else:
            L += ["```json", json.dumps(freeze["blind_rubric"], indent=1, sort_keys=True), "```"]
        L += ["", "## Freeze", "",
              f"This benchmark is frozen at `{freeze['content_hash']}`. Post-hoc modification is "
              f"rejected by `retrospective-benchmark-builder`; a revision must be a new version "
              f"and requires re-running every system against it.",
              "", "## Warnings at build time", ""]
        L += [f"- {w}" for w in warnings] or ["- none"]
        return "\n".join(L)


# --------------------------------------------------------------------------
# research-eval-harness
# --------------------------------------------------------------------------
@register
class ResearchEvalHarness(Skill):
    """Run the system against a frozen suite and produce a *comparable* score.

    Comparability is the whole product. A number without the system that produced
    it stamped onto it is a number that will eventually be subtracted from a
    different system's number and reported as progress.
    """

    name = "research-eval-harness"

    def execute(self, ctx: Context) -> SkillResult:
        evaluator_src = ctx.store.read(self.name, "evaluator_code")
        bench = ctx.store.read(self.name, "retro_benchmark", default=[])
        ctx.store.read(self.name, "retro_benchmark_report", default="")
        warnings: list[str] = []

        freeze = next((r for r in bench if r.get("record_type") == "freeze"), None)
        if freeze is None:
            raise GateBlocked(
                "benchmark_freeze",
                "the retrospective benchmark carries no freeze record, so there is no content "
                "hash to attach these scores to. A score against an unfrozen benchmark cannot "
                "later be shown to have been measured against the same questions.",
                "rebuild it with retrospective-benchmark-builder")

        suite = self._suite(ctx)
        skill_version = str(ctx.external("skill_version", required=True))
        traces = ctx.external("run_traces", None)
        if traces is None:
            raise GateBlocked(
                "eval_execution",
                "no run traces were supplied. This harness scores runs; it does not simulate "
                "them. Emitting a scorecard with no execution behind it would produce the one "
                "artifact this system exists to prevent: a measurement of nothing that reads "
                "like a measurement of something.",
                "execute the task suite against the system under evaluation and pass the traces "
                "with --set run_traces='[{\"task_id\":...,\"submission\":{...},"
                "\"files_read\":[...]}]'")
        by_task = {str(t.get("task_id")): t for t in traces}

        score_fn = self._load_evaluator(evaluator_src)
        evaluator_digest = _sha(evaluator_src)[:32]
        protected = self._protected_paths(ctx)

        runs, integrity_violations = [], 0
        for task in suite["tasks"]:
            tid = str(task.get("task_id"))
            rec = {
                "run_id": f"{ctx.run_id}:{tid}", "task_id": tid,
                "split": task.get("split", "train"),
                "guardrail": task.get("guardrail"),
                "skill_version": skill_version,
                "contract_digest": CONTRACT_DIGEST,
                "suite_hash": suite["suite_hash"],
                "evaluator_digest": evaluator_digest,
                "benchmark_hash": freeze["content_hash"],
                "environment_digest": ctx.external("environment_digest", None),
                "ts": time.time(),
            }
            trace = by_task.get(tid)
            if trace is None:
                # Not run is not zero. A zero would average in with real scores and
                # quietly make an incomplete evaluation look like a bad one.
                rec.update(status="NOT_RUN", valid=False, score=None,
                           failure_class="NOT_RUN",
                           detail="no trace supplied for this task")
                runs.append(rec)
                continue

            touched = self._isolation_breach(trace, protected)
            if touched:
                integrity_violations += 1
                rec.update(status="INTEGRITY_VIOLATION", valid=False, score=None,
                           failure_class="EVALUATOR_ISOLATION_BREACH",
                           detail=f"the run read grader-side material: {touched}. A decisive "
                                  f"test the evaluated process can read measures reading, not "
                                  f"capability, so this run has no score — not a low one.")
                runs.append(rec)
                continue

            if trace.get("self_reported") is not None:
                rec["self_reported_ignored"] = trace.get("self_reported")
            verdict = score_fn(trace.get("submission"), task.get("reference"))
            rec.update(
                status="SCORED" if verdict.get("valid") else "INVALID_SUBMISSION",
                valid=bool(verdict.get("valid")),
                score=verdict.get("score"),
                invalidations=verdict.get("invalidations", []),
                notes=verdict.get("notes", []),
                evaluator_version=verdict.get("evaluator_version"),
                cost=trace.get("cost"),
                failure_class=self._failure_class(verdict, task),
            )
            runs.append(rec)

        ctx.store.append_jsonl(self.name, "eval_runs", runs)

        scorecard = self._scorecard(ctx, runs, suite, freeze, skill_version, evaluator_digest,
                                    integrity_violations, warnings)
        baseline = ctx.external("baseline_scorecard", None)
        if isinstance(baseline, dict) and baseline:
            # Refuses loudly when the two are not the same system.
            assert_same_system(baseline, scorecard, label_a="baseline_scorecard",
                               label_b="this run")
            scorecard["compared_to_baseline"] = self._regression(baseline, scorecard, warnings)

        ctx.store.write(self.name, "eval_scorecard", scorecard)
        ctx.store.write(self.name, "eval_failure_taxonomy", self._taxonomy(runs, scorecard))

        if integrity_violations:
            warnings.append(
                f"{integrity_violations} run(s) read grader-side material and were voided, not "
                f"scored. The scorecard is marked not promotion-eligible.")
        if scorecard["n_not_run"]:
            warnings.append(
                f"{scorecard['n_not_run']} task(s) in the frozen suite had no trace and are "
                f"recorded as NOT_RUN with score null. They are excluded from every mean; the "
                f"suite was not fully executed.")
        if not freeze.get("usable_for_promotion_gating"):
            warnings.append(
                "the retrospective benchmark's contamination floor is unmeasured, so this "
                "scorecard may not gate skill promotion regardless of its scores.")
        return SkillResult(
            self.name, produced=["eval_runs", "eval_scorecard", "eval_failure_taxonomy"],
            warnings=warnings,
            detail={"tasks": len(suite["tasks"]), "scored": scorecard["n_scored"],
                    "held_out_mean": scorecard["means"].get("held_out"),
                    "integrity_violations": integrity_violations,
                    "suite_hash": suite["suite_hash"]})

    # -- suite ----------------------------------------------------------
    def _suite(self, ctx: Context) -> dict:
        raw = ctx.external("task_suite", required=True)
        if isinstance(raw, str):
            p = Path(raw)
            if not p.exists():
                raise GateBlocked("eval_suite", f"task_suite points at {p}, which does not exist",
                                  "supply a readable suite file")
            raw = json.loads(p.read_text(encoding="utf-8"))
        tasks = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(tasks, list) or not tasks:
            raise GateBlocked(
                "eval_suite", "the evaluation task suite is empty; there is nothing to measure",
                "pass --set task_suite='{\"suite_id\":...,\"tasks\":[{\"task_id\":...,"
                "\"reference\":{...},\"split\":\"held_out\"}]}'")
        # The suite must be frozen for the same reason the benchmark is: a suite
        # edited between two runs makes the two scores incomparable while looking
        # identical in every report.
        suite_hash = _sha([{k: t.get(k) for k in ("task_id", "reference", "split", "guardrail")}
                           for t in tasks])[:32]
        declared = (raw.get("frozen_hash") or raw.get("suite_hash")) if isinstance(raw, dict) else None
        if declared and str(declared) != suite_hash:
            raise GateBlocked(
                "suite_freeze",
                f"the task suite declares frozen hash {declared!r} but its contents hash to "
                f"{suite_hash}. The suite changed after it was frozen; scores measured before "
                f"and after are not comparable, and nothing in the report would show it.",
                "restore the frozen suite, or freeze the revision under a new suite_id and "
                "re-run every version against it")
        return {"suite_id": (raw.get("suite_id") if isinstance(raw, dict) else None) or "unnamed",
                "suite_hash": suite_hash, "tasks": tasks}

    # -- evaluator ------------------------------------------------------
    def _load_evaluator(self, src: str):
        """Execute the evaluator in-process.

        This is the grader side. The isolation rule is that the *agent* cannot read
        the evaluator, not that nobody can run it — knowing how you are scored is
        not cheating, knowing the answers is.
        """
        ns: dict[str, Any] = {"__name__": "researchforge_evaluator"}
        try:
            exec(compile(src, "evaluate.py", "exec"), ns)   # noqa: S102 - grader-side by design
        except Exception as e:
            raise GateBlocked(
                "evaluator_load",
                f"evaluator_code does not execute: {type(e).__name__}: {e}. A scorer that does "
                f"not run cannot be replaced by a default score.",
                "rebuild it with evaluator-builder") from e
        fn = ns.get("score")
        if not callable(fn):
            raise GateBlocked(
                "evaluator_load",
                "evaluator_code defines no callable `score(submission, reference)`",
                "rebuild it with evaluator-builder")
        return fn

    def _protected_paths(self, ctx: Context) -> list[str]:
        paths = []
        for aid in ("evaluator_code", "hidden_tests"):
            spec = ARTIFACTS.get(aid)
            if spec:
                paths.append(spec.path.split("|")[0].rstrip("/"))
        return paths

    def _isolation_breach(self, trace: dict, protected: list[str]) -> list[str]:
        seen = []
        blob = [str(x) for x in (trace.get("files_read") or [])]
        blob += [str(x) for x in (trace.get("tool_calls") or [])]
        blob += [str(x) for x in (trace.get("commands") or [])]
        for item in blob:
            for p in protected:
                if p and p in item:
                    seen.append(item[:160])
                    break
        return sorted(set(seen))

    def _failure_class(self, verdict: dict, task: dict) -> str:
        if not verdict.get("valid"):
            codes = sorted({i.get("code", "UNKNOWN") for i in verdict.get("invalidations", [])})
            return "INVALID:" + ",".join(codes)
        if verdict.get("score") is None:
            return "UNJUDGEABLE"
        threshold = task.get("pass_threshold")
        if threshold is not None and verdict["score"] < float(threshold):
            return "BELOW_THRESHOLD"
        return "PASS"

    # -- aggregation ----------------------------------------------------
    def _scorecard(self, ctx: Context, runs: list[dict], suite: dict, freeze: dict,
                   skill_version: str, evaluator_digest: str, integrity: int,
                   warnings: list[str]) -> dict:
        scored = [r for r in runs if r["status"] == "SCORED" and r["score"] is not None]
        by_split: dict[str, list[float]] = {}
        for r in scored:
            by_split.setdefault(r["split"], []).append(r["score"])
        guardrails = {str(r["guardrail"]): r["score"] for r in runs if r.get("guardrail")}
        guardrail_status = {name: ("VOID" if score is None else "MEASURED")
                            for name, score in guardrails.items()}

        retro = self._retro_score(ctx, freeze, warnings)
        card = {
            "scorecard_version": 1,
            "generated_at": time.time(),
            "run_id": ctx.run_id,
            # The four fields that make this number comparable with another number.
            "contract_digest": CONTRACT_DIGEST,
            "skill_version": skill_version,
            "suite_id": suite["suite_id"],
            "suite_hash": suite["suite_hash"],
            "evaluator_digest": evaluator_digest,
            "environment_digest": ctx.external("environment_digest", None),
            "n_tasks": len(suite["tasks"]),
            "n_scored": len(scored),
            "n_invalid": sum(1 for r in runs if r["status"] == "INVALID_SUBMISSION"),
            "n_not_run": sum(1 for r in runs if r["status"] == "NOT_RUN"),
            "integrity_violations": integrity,
            "means": {k: _mean(v) for k, v in sorted(by_split.items())},
            "scores_by_task": {r["task_id"]: r["score"] for r in runs},
            "guardrails": guardrails,
            "guardrail_status": guardrail_status,
            "self_reported_success_counted": False,
            "scoring_rules": {
                "invalid_scores_null_not_zero": True,
                "not_run_excluded_from_means": True,
                "integrity_violation_voids_run": True,
                "self_report": "a run's own claim of success is recorded and never scored",
            },
            "benchmark": {
                "version": freeze.get("benchmark_version"),
                "content_hash": freeze.get("content_hash"),
                "contamination_floor": freeze.get("contamination_floor", {}).get("floor_recall"),
                "contamination_floor_measured":
                    bool(freeze.get("contamination_floor", {}).get("measured")),
                "usable_for_promotion_gating": bool(freeze.get("usable_for_promotion_gating")),
                "blind_rubric": freeze.get("blind_rubric", {}).get("status", "PRESENT"),
                "retro_recall_at_k": retro,
            },
            "promotion_eligible": bool(freeze.get("usable_for_promotion_gating")) and integrity == 0,
            "why_not_promotion_eligible": None,
        }
        blockers = []
        if not freeze.get("usable_for_promotion_gating"):
            blockers.append("retrospective benchmark contamination floor is unmeasured")
        if integrity:
            blockers.append(f"{integrity} run(s) breached evaluator isolation")
        card["why_not_promotion_eligible"] = "; ".join(blockers) or None
        return card

    def _retro_score(self, ctx: Context, freeze: dict, warnings: list[str]) -> dict:
        """recall@k against the retrospective benchmark, only when adjudicated."""
        directions = ctx.external("system_directions", None)
        adjudications = ctx.external("match_adjudications", None)
        k = int(freeze.get("scoring", {}).get("k", 10))
        if not directions:
            return {"value": None, "why": "no system directions were submitted for scoring"}
        if not adjudications:
            warnings.append(
                "system directions were submitted but no rubric adjudications were supplied. "
                "recall@k is left null: matching directions by string overlap would report a "
                "number that measures vocabulary.")
            return {"value": None,
                    "why": "matching requires rubric adjudication; string overlap is not a match"}
        ranks = {str(d.get("direction_id")): int(d.get("rank", 10 ** 6)) for d in directions}
        gold = [g["gold_direction_id"] for r in
                ctx.store.read(self.name, "retro_benchmark", default=[])
                if r.get("record_type") == "pair" for g in r.get("gold_directions", [])]
        value = recall_at_k(gold, adjudications, k, ranks)
        floor = freeze.get("contamination_floor", {}).get("floor_recall")
        interpretable = (freeze.get("contamination_floor", {}).get("measured")
                         and value is not None and floor is not None and value > floor)
        return {"value": value, "k": k, "gold_directions": len(gold),
                "contamination_floor": floor,
                "above_floor": interpretable,
                "why": None if interpretable else
                       ("at or below the contamination floor, or the floor is unmeasured: this "
                        "number is not evidence of research judgment")}

    def _regression(self, baseline: dict, current: dict, warnings: list[str]) -> dict:
        out = {"baseline_run_id": baseline.get("run_id"), "deltas": {}, "regressions": []}
        for split in sorted(set(baseline.get("means", {})) | set(current.get("means", {}))):
            b, c = baseline.get("means", {}).get(split), current.get("means", {}).get(split)
            if b is None or c is None:
                out["deltas"][split] = None
                continue
            out["deltas"][split] = round(c - b, 6)
            if c < b:
                out["regressions"].append(split)
        for name, b in (baseline.get("guardrails") or {}).items():
            c = (current.get("guardrails") or {}).get(name)
            if b is not None and (c is None or c < b):
                out["regressions"].append(f"guardrail:{name}")
        if out["regressions"]:
            warnings.append(f"regression against baseline on {sorted(set(out['regressions']))}")
        return out

    # -- taxonomy -------------------------------------------------------
    def _taxonomy(self, runs: list[dict], card: dict) -> str:
        classes: dict[str, list[dict]] = {}
        for r in runs:
            classes.setdefault(r.get("failure_class") or "UNCLASSIFIED", []).append(r)
        L = [
            "# Failure taxonomy", "",
            f"Suite `{card['suite_id']}` (`{card['suite_hash']}`) · skill version "
            f"`{card['skill_version']}` · contract `{card['contract_digest']}`", "",
            f"{card['n_tasks']} tasks · {card['n_scored']} scored · {card['n_invalid']} invalid · "
            f"{card['n_not_run']} not run · {card['integrity_violations']} integrity violations",
            "",
            "Three classes below carry **no score at all** rather than a zero: `NOT_RUN`, "
            "`INVALID:*` and `EVALUATOR_ISOLATION_BREACH`. Zero is a legitimate score, so using "
            "it for a run that did not happen, could not be judged, or cheated would launder "
            "those runs into the mean.",
            "", "## Classes", "",
            "| class | n | tasks |", "|---|---|---|",
        ]
        for cls in sorted(classes):
            rs = classes[cls]
            L.append(f"| `{cls}` | {len(rs)} | {', '.join(r['task_id'] for r in rs[:8])} |")
        breaches = [r for r in runs if r["status"] == "INTEGRITY_VIOLATION"]
        if breaches:
            L += ["", "## Evaluator isolation breaches", ""]
            L += [f"- `{r['task_id']}`: {r['detail']}" for r in breaches]
        invalid = [r for r in runs if r["status"] == "INVALID_SUBMISSION"]
        if invalid:
            L += ["", "## Invalid submissions", ""]
            L += [f"- `{r['task_id']}`: "
                  f"{', '.join(i.get('code', '?') for i in r.get('invalidations', []))}"
                  for r in invalid]
        L += ["", "## What this taxonomy does not tell you", "",
              "It classifies how runs failed, not why the system failed. A cluster of "
              "`BELOW_THRESHOLD` on one split is a place to look, not a diagnosis."]
        return "\n".join(L)


# --------------------------------------------------------------------------
# skill-evolution-manager  (consolidates skill-package-auditor)
# --------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key.strip()] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key.strip()] = val.strip("'\"")
    return fm


def _parse_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        s = line.strip()
        if not s.startswith("- "):
            continue
        item = s[2:].strip().strip("`").strip()
        # "- none" and "- None; this skill consumes only external inputs." are both
        # the empty list written for a human, not entries.
        if item and not _norm(item).startswith("none"):
            out.append(item)
    return out


def audit_package(skills_dir: Path, manifest_path: Path, catalog_path: Path) -> dict:
    """Check the invariants the package validator checks, and say what broke.

    The invariants are not stylistic. Two producers for one artifact means nobody
    owns whether it is correct; a dangling input means a skill declares a
    dependency that can never be satisfied; a stale `depends_on` means the
    documented build order is not the real one. Each of these is a way for the
    graph to describe a system other than the one that runs.
    """
    findings: list[dict] = []

    def finding(sev, check, detail, remediation, skill=None):
        findings.append({"id": f"F-{len(findings) + 1:03d}", "severity": sev, "check": check,
                         "skill": skill, "detail": detail, "remediation": remediation})

    if not manifest_path.exists() or not catalog_path.exists():
        raise GateBlocked(
            "package_audit",
            f"cannot audit: {manifest_path} or {catalog_path} is missing. An audit that skips "
            f"the contract would report the SKILL.md files agree with nothing.",
            "pass --set catalog_source=/path/to/manifests")
    graph = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    artifacts = graph["artifacts"]
    consumers = graph.get("consumers", {})
    depends_on = graph.get("depends_on", {})
    internal = graph.get("internal_artifacts", {})
    names = [s["name"] for s in catalog["skills"]]
    entry_points = catalog.get("entry_points", [])

    # --- invariant 1: exactly one producer per artifact ----------------
    producers: dict[str, list[str]] = {}
    for aid, spec in artifacts.items():
        producers.setdefault(aid, []).append(spec["producer"])
    for owner, mapping in internal.items():
        for aid in mapping:
            if aid in artifacts:
                finding("error", "single_producer",
                        f"'{aid}' is declared both as a public artifact (producer "
                        f"'{artifacts[aid]['producer']}') and as an internal artifact of "
                        f"'{owner}'",
                        "remove one declaration; an artifact has exactly one owner", owner)
            producers.setdefault(aid, []).append(owner)
    dup = {a: p for a, p in producers.items() if len(p) > 1}
    for aid, ps in sorted(dup.items()):
        finding("error", "single_producer",
                f"'{aid}' has {len(ps)} producers: {sorted(ps)}",
                "give the artifact one producer; two writers means nobody owns correctness")

    # --- invariant 2: no dangling inputs -------------------------------
    for s in names:
        c = consumers.get(s, {})
        for aid in list(c.get("artifacts", [])) + list(c.get("feedback", [])):
            if aid not in artifacts and aid not in internal.get(s, {}):
                finding("error", "no_dangling_inputs",
                        f"'{s}' consumes '{aid}', which no skill produces",
                        "add a producer for it or remove the dependency", s)

    # --- invariant 3: depends_on in sync -------------------------------
    for s in names:
        c = consumers.get(s, {})
        want = sorted({artifacts[a]["producer"] for a in c.get("artifacts", [])
                       if a in artifacts} - {s})
        got = sorted(depends_on.get(s, []))
        if want != got:
            finding("error", "depends_on_in_sync",
                    f"'{s}' declares depends_on {got} but its consumed artifacts are produced "
                    f"by {want}",
                    "regenerate depends_on from the artifact graph; a hand-edited build order "
                    "is a build order that stops matching the graph", s)

    # --- invariant 4: acyclic ------------------------------------------
    cycles: list[list[str]] = []
    state: dict[str, int] = {}

    def visit(n: str, stack: list[str]) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            cycles.append(stack[stack.index(n):] + [n])
            return
        state[n] = 1
        for d in depends_on.get(n, []):
            visit(d, stack + [d])
        state[n] = 2

    for s in names:
        visit(s, [s])
    for cyc in cycles:
        finding("error", "acyclic", f"dependency cycle: {' -> '.join(cyc)}",
                "break the cycle, or declare the back edge as a feedback read — feedback edges "
                "are excluded from depends_on precisely so that cycles stay visible")

    # --- invariant 5: reachability -------------------------------------
    reachable: set[str] = set()

    def walk(n: str) -> None:
        if n in reachable:
            return
        reachable.add(n)
        for d in depends_on.get(n, []):
            walk(d)

    for e in entry_points:
        walk(e)
    for s in sorted(set(names) - reachable):
        finding("error", "reachability",
                f"'{s}' is not reachable from any entry point {entry_points}",
                "connect it to the graph or remove it; an unreachable skill can never run and "
                "its SKILL.md documents a capability the system does not have", s)
    consumed = {a for s in names for a in (list(consumers.get(s, {}).get("artifacts", []))
                                           + list(consumers.get(s, {}).get("feedback", [])))}
    terminal = sorted(set(artifacts) - consumed)

    # --- SKILL.md files ------------------------------------------------
    skill_records, procedure_fp = [], {}
    md_files = sorted(skills_dir.glob("*/SKILL.md"))
    if not md_files:
        finding("error", "skill_files", f"no SKILL.md found under {skills_dir}",
                "pass --set skills_dir=/path/to/skills")
    seen_names = set()
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        sections = _parse_sections(text)
        name = fm.get("name") or md.parent.name
        seen_names.add(name)
        missing_fm = [k for k in REQUIRED_FRONTMATTER if not fm.get(k)]
        if missing_fm:
            finding("error", "frontmatter", f"'{name}' frontmatter is missing {missing_fm}",
                    "add the required frontmatter keys", name)
        if name != md.parent.name:
            finding("error", "frontmatter",
                    f"frontmatter name '{name}' does not match directory '{md.parent.name}'",
                    "rename one of them; the directory is how the loader finds the skill", name)
        missing_sec = [s for s in REQUIRED_SECTIONS if s not in sections]
        if missing_sec:
            finding("error", "required_sections", f"'{name}' is missing sections {missing_sec}",
                    "add them; a skill without hard gates or verification documented is a "
                    "skill nobody can check", name)

        declared_in = _bullets(sections.get("Inputs", []))
        inputs = sorted(i for i in declared_in
                        if not i.startswith("external:") and not i.startswith("feedback:"))
        feedback = sorted(i.split(":", 1)[1].strip() for i in declared_in
                          if i.startswith("feedback:"))
        outputs = sorted(_bullets(sections.get("Outputs", [])))
        deps = sorted(_bullets(sections.get("Depends on", [])))

        if name in consumers:
            want_in = sorted(consumers[name].get("artifacts", []))
            want_fb = sorted(consumers[name].get("feedback", []))
            if inputs != want_in:
                finding("error", "doc_matches_contract",
                        f"'{name}' SKILL.md lists inputs {inputs}; the contract says {want_in}",
                        "regenerate the SKILL.md sections from the artifact graph", name)
            if feedback != want_fb:
                finding("error", "doc_matches_contract",
                        f"'{name}' SKILL.md lists feedback reads {feedback}; the contract says "
                        f"{want_fb}", "regenerate from the artifact graph", name)
        want_out = sorted(a for a, sp in artifacts.items() if sp["producer"] == name)
        if outputs != want_out:
            finding("error", "doc_matches_contract",
                    f"'{name}' SKILL.md lists outputs {outputs}; the contract says {want_out}",
                    "regenerate from the artifact graph", name)
        want_dep = sorted(depends_on.get(name, []))
        if deps != want_dep:
            finding("error", "doc_matches_contract",
                    f"'{name}' SKILL.md lists depends_on {deps}; the contract says {want_dep}",
                    "regenerate from the artifact graph", name)

        status = str(fm.get("implementation_status", "")).lower()
        implemented = name in REGISTRY
        if status in CLAIMS_RUNTIME and not implemented:
            finding("error", "doc_claims_runtime",
                    f"'{name}' declares implementation_status '{status}' but no runtime "
                    f"implementation is registered for it",
                    "either implement it or mark it specification-ready; documentation that "
                    "claims running code the package does not contain is the failure this "
                    "audit exists to catch", name)

        body = "\n".join(sections.get("Procedure", []))
        fp = _sha(re.sub(r"\d+\.\s*", "", _norm(body)))[:16]
        procedure_fp.setdefault(fp, []).append(name)
        skill_records.append({
            "name": name, "path": str(md), "version": fm.get("version"),
            "stage": fm.get("stage"), "artifact_kind": fm.get("artifact_kind"),
            "implementation_status": fm.get("implementation_status"),
            "consolidates": fm.get("consolidates", []),
            "sections_present": sorted(sections),
            "missing_sections": missing_sec, "missing_frontmatter": missing_fm,
            "inputs": inputs, "feedback": feedback, "outputs": outputs, "depends_on": deps,
            "procedure_fingerprint": fp,
            "procedure_steps": len([l for l in sections.get("Procedure", [])
                                    if re.match(r"^\s*\d+\.", l)]),
            "runtime_implementation": implemented,
        })

    for fp, group in sorted(procedure_fp.items()):
        if len(group) > 1:
            finding("warning", "template_clone",
                    f"{sorted(group)} share an identical Procedure section (fingerprint {fp}). "
                    f"Identical procedures across skills are low-specificity placeholders: they "
                    f"describe no skill in particular.",
                    "write the procedure each skill actually follows, or merge the skills")
    for rec in skill_records:
        if rec["procedure_steps"] <= 1 and not rec["missing_sections"]:
            finding("warning", "low_specificity",
                    f"'{rec['name']}' has a Procedure with {rec['procedure_steps']} numbered "
                    f"step(s); it is unlikely to constrain an agent",
                    "expand the procedure into checkable steps", rec["name"])
    for s in sorted(set(names) - seen_names):
        finding("error", "skill_files", f"'{s}' is in the catalog but has no SKILL.md",
                "add skills/<name>/SKILL.md or remove it from the catalog", s)

    documented_only = [r["name"] for r in skill_records if not r["runtime_implementation"]]
    spec_only = len(documented_only) == len(skill_records) and bool(skill_records)
    drifted = [r["name"] for r in skill_records
               if r["runtime_implementation"]
               and str(r["implementation_status"]).lower() not in CLAIMS_RUNTIME]
    if drifted:
        finding("warning", "doc_understates_runtime",
                f"{len(drifted)} skill(s) have a registered runtime implementation while their "
                f"frontmatter still declares a specification-only status: {sorted(drifted)}",
                "update implementation_status; this is drift in the safe direction, but the "
                "package still describes itself inaccurately")

    errors = [f for f in findings if f["severity"] == "error"]
    return {
        "status": "FAIL" if errors else "PASS",
        "checked_at": time.time(),
        "skills_dir": str(skills_dir),
        "manifest": str(manifest_path),
        "manifest_sha256": _sha(manifest_path.read_bytes())[:32],
        "contract_digest": CONTRACT_DIGEST,
        "counts": {"skills": len(skill_records), "artifacts": len(artifacts),
                   "internal_artifacts": sum(len(v) for v in internal.values()),
                   "errors": len(errors),
                   "warnings": sum(1 for f in findings if f["severity"] == "warning")},
        "invariants": {
            "single_producer": not any(f["check"] == "single_producer" for f in errors),
            "no_dangling_inputs": not any(f["check"] == "no_dangling_inputs" for f in errors),
            "depends_on_in_sync": not any(f["check"] == "depends_on_in_sync" for f in errors),
            "acyclic": not cycles,
            "reachability": not any(f["check"] == "reachability" for f in errors),
        },
        "entry_points": entry_points,
        "terminal_artifacts": terminal,
        "specification_only": spec_only,
        "specification_only_statement": (
            "This package ships workflow specifications only; no skill in it has a runtime "
            "implementation." if spec_only else
            f"{len(skill_records) - len(documented_only)} of {len(skill_records)} skills have a "
            f"registered runtime implementation" + (
                "; every skill in the package is backed by code."
                if not documented_only else
                f"; specification-only: {sorted(documented_only)}")),
        "findings": findings,
        "skills": skill_records,
    }


@register
class SkillEvolutionManager(Skill):
    """Audit the package, propose one bounded edit, and refuse to promote it.

    Refusing is the default. Promotion requires held-out improvement *and* an
    unregressed guardrail set *and* a benchmark whose contamination floor was
    actually measured. This skill never writes into the skills directory: it emits
    a diff. A system that edits its own live skills has no version it can be
    rolled back to and no measurement that was not taken on a moving target.
    """

    name = "skill-evolution-manager"

    def execute(self, ctx: Context) -> SkillResult:
        scorecard = ctx.store.read(self.name, "eval_scorecard")
        runs = ctx.store.read(self.name, "eval_runs", default=[])
        ctx.store.read(self.name, "eval_failure_taxonomy", default="")
        warnings: list[str] = []

        skills_dir = Path(ctx.external("skills_dir", _PKG_ROOT / "skills"))
        catalog_src = Path(ctx.external("skill_catalog_source", _PKG_ROOT / "manifests"))
        manifest = catalog_src / "artifact-graph.json" if catalog_src.is_dir() else catalog_src
        catalog = (catalog_src / "skill-catalog.json" if catalog_src.is_dir()
                   else catalog_src.parent / "skill-catalog.json")

        audit = audit_package(skills_dir, manifest, catalog)
        ctx.store.write(self.name, "skill_audit_machine_report", audit)
        ctx.store.write(self.name, "skill_audit_report", self._audit_md(audit))
        if audit["status"] == "FAIL":
            warnings.append(f"package audit FAILED with {audit['counts']['errors']} error(s); "
                            f"no promotion is possible while the contract is inconsistent")

        # --- hard gate: no online self-edit ---------------------------
        self._refuse_online_self_edit(ctx)

        edit = ctx.external("candidate_edit", None)
        if not edit:
            decision = self._decision(promote=False, reason="no candidate edit was proposed",
                                      blockers=["no candidate edit"], audit=audit,
                                      comparison=None, ctx=ctx, edit=None)
            ctx.store.write(self.name, "skill_patch",
                            "# no candidate edit was proposed in this run\n")
            ctx.store.write(self.name, "skill_eval_comparison",
                            {"compared": False,
                             "why": "no candidate edit, so nothing to compare",
                             "baseline": comparability_key(scorecard)})
            ctx.store.write(self.name, "skill_promotion_decision", decision)
            return SkillResult(self.name, produced=[], warnings=warnings,
                               detail={"audit_status": audit["status"],
                                       "findings": audit["counts"], "promoted": False})

        target, patch = self._bounded_patch(ctx, edit, skills_dir)
        ctx.store.write(self.name, "skill_patch", patch)

        candidate = ctx.external("candidate_scorecard", None)
        if not isinstance(candidate, dict) or not candidate:
            raise GateBlocked(
                "held_out_evaluation_missing",
                f"an edit to '{target}' was proposed but no candidate scorecard was supplied. "
                f"A skill edit without a held-out evaluation of the edited skill has no evidence "
                f"behind it, and 'the diff looks better' is the reasoning this gate exists to "
                f"reject.",
                "run research-eval-harness against the edited skill on the same frozen suite, "
                "then pass --set candidate_scorecard='<scorecard.json>'")

        assert_ab_arms(scorecard, candidate)

        # --- hard gate: contamination floor ---------------------------
        bench = candidate.get("benchmark", {}) or scorecard.get("benchmark", {}) or {}
        if not bench.get("usable_for_promotion_gating"):
            raise GateBlocked(
                "contamination_floor",
                "the retrospective benchmark behind these scorecards has an unmeasured "
                "contamination floor "
                f"(measured={bench.get('contamination_floor_measured')}), so it may not gate "
                "skill promotion. The model under test has very likely read the follow-up work; "
                "without a floor there is no way to tell an improvement in judgment from an "
                "improvement in recall, and this skill would promote the edit on the strength "
                "of the model's memory.",
                "measure the floor (retrospective-benchmark-builder with a real model provider "
                "or recorded probe transcripts), re-run the harness, and retry")

        comparison = self._compare(ctx, scorecard, candidate, warnings)
        ctx.store.write(self.name, "skill_eval_comparison", comparison)

        blockers = list(comparison["blockers"])
        if audit["status"] == "FAIL":
            blockers.append(f"package audit FAILED ({audit['counts']['errors']} errors)")
        decision = self._decision(promote=not blockers,
                                  reason=("held-out improvement with no guardrail regression"
                                          if not blockers else "; ".join(blockers)),
                                  blockers=blockers, audit=audit, comparison=comparison,
                                  ctx=ctx, edit={"target": target, **(edit or {})})
        ctx.store.write(self.name, "skill_promotion_decision", decision)
        if blockers:
            warnings.append(f"promotion of '{target}' REJECTED: {'; '.join(blockers)}")
        return SkillResult(
            self.name, produced=[], warnings=warnings,
            detail={"audit_status": audit["status"], "target_skill": target,
                    "promoted": decision["promote"],
                    "held_out_delta": comparison["deltas"].get("held_out"),
                    "train_delta": comparison["deltas"].get("train"),
                    "blockers": blockers})

    # -- gates ----------------------------------------------------------
    def _refuse_online_self_edit(self, ctx: Context) -> None:
        """Offline only, and proposals only.

        Two different refusals, same reason. Editing a skill while a run is in
        flight changes the system in the middle of the measurement that is
        supposed to justify the change. Applying a patch from inside this skill
        would make the edit its own approval.
        """
        if ctx.external("apply_patch", False):
            raise GateBlocked(
                "online_self_edit",
                "apply_patch was requested. This skill proposes edits; it does not apply them. "
                "A skill that writes its own replacement into the live package has promoted "
                "itself without anyone reviewing the diff, and leaves no version to roll back "
                "to.",
                "review evals/skill_patch.diff and apply it yourself under version control, "
                "after evals/promotion_decision.json says promote=true")
        active = ctx.external("active_run", None)
        if active:
            ident = active.get("run_id") if isinstance(active, dict) else active
            raise GateBlocked(
                "online_self_edit",
                f"research run {ident!r} is in flight. Skill evolution is an offline maintenance "
                f"entry point: editing a skill while it is being used changes the system "
                f"mid-measurement, so neither the run's results nor the edit's evaluation would "
                f"mean anything afterwards.",
                "wait for the run to reach a terminal state, then re-run this skill with no "
                "active_run")

    def _bounded_patch(self, ctx: Context, edit: Any, skills_dir: Path) -> tuple[str, str]:
        """One skill, bounded ops, and a diff — never a write."""
        if isinstance(edit, list):
            targets = sorted({str(e.get("target_skill")) for e in edit})
            raise GateBlocked(
                "bounded_edit",
                f"a list of {len(edit)} edits was supplied covering {targets}. Edits are "
                f"proposed one skill at a time: a change that touches several skills cannot be "
                f"attributed to any of them by a held-out comparison.",
                "propose one skill's edit per run")
        target = str(edit.get("target_skill") or "")
        ops = edit.get("edit_ops") or edit.get("ops") or []
        touched = {str(o.get("target_skill", target)) for o in ops if isinstance(o, dict)}
        if len(touched | {target}) > 1:
            raise GateBlocked(
                "bounded_edit",
                f"the proposed edit touches {sorted(touched | {target})}. One skill at a time.",
                "split it into separate proposals, each with its own held-out evaluation")
        md = skills_dir / target / "SKILL.md"
        if not target or not md.exists():
            raise GateBlocked(
                "bounded_edit",
                f"the edit targets {target!r}, which has no SKILL.md under {skills_dir}",
                "name an existing skill")
        allowed = {"add", "delete", "replace"}
        bad = [o.get("op") for o in ops if isinstance(o, dict) and o.get("op") not in allowed]
        if bad or not ops:
            raise GateBlocked(
                "bounded_edit",
                f"edit ops must be non-empty and drawn from {sorted(allowed)}; got "
                f"{[o.get('op') for o in ops if isinstance(o, dict)] or 'nothing'}. An "
                f"unbounded rewrite is not an edit whose effect can be attributed.",
                "express the change as add/delete/replace operations on named sections")

        before = md.read_text(encoding="utf-8")
        after = self._apply_in_memory(before, ops)
        limit = int(ctx.external("max_edit_lines", 120) or 120)
        diff = list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                         fromfile=f"a/{target}/SKILL.md",
                                         tofile=f"b/{target}/SKILL.md"))
        changed = sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
        if changed == 0:
            raise GateBlocked(
                "bounded_edit",
                f"the proposed ops change nothing in {target}/SKILL.md",
                "check the section names in the edit ops")
        if changed > limit:
            raise GateBlocked(
                "bounded_edit",
                f"the proposed edit changes {changed} lines (bound {limit}). A rewrite this "
                f"large cannot be attributed to a single mechanism by one held-out comparison.",
                f"split it, or raise the bound deliberately with --set max_edit_lines=<n>")
        header = (f"# proposed edit to {target} — NOT APPLIED\n"
                  f"# rationale: {edit.get('rationale', '(none given)')}\n"
                  f"# {changed} changed line(s); bound {limit}\n")
        return target, header + "".join(diff)

    def _apply_in_memory(self, text: str, ops: list[dict]) -> str:
        """Apply ops to a copy. Nothing here touches the file on disk."""
        sections = _parse_sections(text)
        out = text
        for o in ops:
            sec, op = str(o.get("section", "")), o.get("op")
            payload = str(o.get("text", ""))
            if sec not in sections:
                continue
            body = "\n".join(sections[sec])
            if op == "add":
                new = body.rstrip("\n") + "\n" + payload + "\n"
            elif op == "delete":
                new = body.replace(payload, "")
            else:
                new = body.replace(str(o.get("find", body)), payload) \
                    if o.get("find") else payload + "\n"
            out = out.replace(f"## {sec}\n{body}", f"## {sec}\n{new}", 1)
        return out

    # -- comparison and decision ----------------------------------------
    def _compare(self, ctx: Context, base: dict, cand: dict, warnings: list[str]) -> dict:
        min_improvement = float(ctx.external("min_improvement", 0.01) or 0.0)
        tolerance = float(ctx.external("guardrail_tolerance", 0.0) or 0.0)
        deltas, splits = {}, sorted(set(base.get("means", {})) | set(cand.get("means", {})))
        for split in splits:
            b, c = base.get("means", {}).get(split), cand.get("means", {}).get(split)
            deltas[split] = None if (b is None or c is None) else round(c - b, 6)

        blockers: list[str] = []
        held = deltas.get("held_out")
        if "held_out" not in splits:
            blockers.append("no held-out split in the evaluation; a train-only comparison cannot "
                            "distinguish an improvement from an overfit")
        elif held is None:
            blockers.append("held-out mean is null in at least one arm (nothing scored there)")
        elif held < min_improvement:
            blockers.append(f"held-out delta {held:+.4f} is below the minimum improvement "
                            f"{min_improvement:+.4f}")

        guard_deltas, regressed = {}, []
        for name in sorted(set(base.get("guardrails", {})) | set(cand.get("guardrails", {}))):
            b = (base.get("guardrails") or {}).get(name)
            c = (cand.get("guardrails") or {}).get(name)
            guard_deltas[name] = None if (b is None or c is None) else round(c - b, 6)
            if b is None or c is None:
                regressed.append(f"{name}(unmeasured)")
            elif c < b - tolerance:
                regressed.append(f"{name}({b:.3f}->{c:.3f})")
        if regressed:
            blockers.append(f"guardrail regression: {regressed}")
        if cand.get("integrity_violations"):
            blockers.append(f"{cand['integrity_violations']} integrity violation(s) in the "
                            f"candidate arm")
        if cand.get("n_not_run"):
            blockers.append(f"{cand['n_not_run']} task(s) in the candidate arm were not run; the "
                            f"suite was not fully executed")

        train = deltas.get("train")
        overfit = (train is not None and held is not None and train > 0 >= held)
        if overfit:
            warnings.append(
                f"the edit improves train by {train:+.4f} while held-out moves {held:+.4f}. That "
                f"is the signature of an overfit to the failure cases it was mined from.")
        return {
            "compared": True,
            "arms": {"baseline": comparability_key(base), "candidate": comparability_key(cand)},
            "held_fixed": {k: base.get(k) for k in AB_SHARED_KEYS},
            "deltas": deltas,
            "guardrail_deltas": guard_deltas,
            "min_improvement": min_improvement,
            "guardrail_tolerance": tolerance,
            "overfit_signature": overfit,
            "blockers": blockers,
            "note": ("held-out is the only split that decides. Train movement is reported "
                     "because its divergence from held-out is diagnostic, never because it "
                     "counts toward promotion."),
        }

    def _decision(self, *, promote: bool, reason: str, blockers: list[str], audit: dict,
                  comparison: dict | None, ctx: Context, edit: dict | None) -> dict:
        return {
            "promote": bool(promote),
            "decided_at": time.time(),
            "run_id": ctx.run_id,
            "contract_digest": CONTRACT_DIGEST,
            "target_skill": (edit or {}).get("target") or (edit or {}).get("target_skill"),
            "reason": reason,
            "blockers": blockers,
            "audit_status": audit["status"],
            "audit_errors": audit["counts"]["errors"],
            "comparison": comparison,
            "applied": False,
            "why_not_applied": ("this skill never writes into the skills directory. The patch is "
                                "a proposal; applying it is a human action under version "
                                "control."),
            "rollback_pointer": {
                "current_version": ((edit or {}).get("current_version")
                                    or ctx.external("skill_version", None)),
                "restore": "git checkout <current_version> -- skills/<target>/SKILL.md",
                "patch_artifact": "skill_patch",
            },
            "changelog": ((edit or {}).get("rationale") if promote else None),
        }

    # -- reports ---------------------------------------------------------
    def _audit_md(self, audit: dict) -> str:
        L = [
            "# Package and contract audit", "",
            f"**{audit['status']}** — {audit['counts']['errors']} error(s), "
            f"{audit['counts']['warnings']} warning(s) across {audit['counts']['skills']} skills "
            f"and {audit['counts']['artifacts']} artifacts.", "",
            f"Manifest `{audit['manifest']}` (sha256 `{audit['manifest_sha256']}`) · contract "
            f"digest `{audit['contract_digest']}`", "",
            "## Invariants", "", "| invariant | holds |", "|---|---|",
        ]
        L += [f"| {k} | {'yes' if v else '**NO**'} |"
              for k, v in sorted(audit["invariants"].items())]
        L += ["", "## Implementation status", "", audit["specification_only_statement"], ""]
        if audit["findings"]:
            L += ["## Findings", "", "| id | severity | check | skill | detail |",
                  "|---|---|---|---|---|"]
            L += [f"| {f['id']} | {f['severity']} | {f['check']} | {f['skill'] or '—'} | "
                  f"{f['detail']} |" for f in audit["findings"]]
            L += ["", "### Remediation", ""]
            L += [f"- **{f['id']}** {f['remediation']}" for f in audit["findings"]]
        else:
            L += ["## Findings", "", "None. Every invariant above was checked against the "
                  "manifest, not assumed from the documentation."]
        L += ["", "## Terminal artifacts", "",
              "Artifacts nothing consumes. These are deliverables, not orphans, but a new one "
              "appearing here usually means a consumer was forgotten:", ""]
        L += [f"- `{a}`" for a in audit["terminal_artifacts"]] or ["- none"]
        return "\n".join(L)
