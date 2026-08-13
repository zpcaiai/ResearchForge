"""Evidence plane: provider management, coverage, search, citations, claim graph."""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from ..errors import ProviderUnavailable
from ..providers import DEFAULT_PROVIDERS
from ..skill import Context, Skill, SkillResult, register


@register
class LiteratureProviderManager(Skill):
    name = "literature-provider-manager"

    def execute(self, ctx: Context) -> SkillResult:
        budget = ctx.external("quota_budget", {}) or {}
        scope = ctx.external("domain_scope", "unspecified")
        registry, warnings = [], []
        for p in ctx.scholarly:
            avail = p.available()
            reason = ""
            if p.api_key_env and not os.environ.get(p.api_key_env):
                reason = f"{p.api_key_env} not set (BYOK)"
            if p.mailto_env and not os.environ.get(p.mailto_env):
                warnings.append(f"{p.name}: {p.mailto_env} unset — polite-pool rate limits will "
                                f"not apply and throttling is likely")
            registry.append({
                "name": p.name, "base_url": p.base_url, "available": avail,
                "unavailable_reason": reason,
                "capabilities": {k: v for k, v in vars(p.capabilities).items()},
                "budget": budget.get(p.name, {}),
            })
            ctx.quota.budget(p.name, max_calls=budget.get(p.name, {}).get("max_calls"),
                             max_usd=budget.get(p.name, {}).get("max_usd"))
        ctx.store.write(self.name, "provider_registry",
                        {"scope": scope, "providers": registry, "generated_at": time.time()})

        active = [r for r in registry if r["available"]]
        blind = []
        for cap, label in (("full_text", "no full-text search"),
                           ("citations", "no citation graph"),
                           ("code_search", "no code/artifact search"),
                           ("non_english", "no non-English coverage"),
                           ("preprints", "no preprint coverage"),
                           ("last_90_days", "no coverage of the last 90 days")):
            if not any(r["capabilities"].get(cap) for r in active):
                blind.append(label)
        # coverage is measured, never assumed; with no measurement yet it is UNKNOWN
        report = {
            "status": "UNKNOWN_COVERAGE",
            "measured": False,
            "method": "seeded-recall + saturation + cross-provider agreement",
            "score": None,
            "active_providers": [r["name"] for r in active],
            "named_blind_spots": blind,
            "note": ("No search has run yet, so coverage is unmeasured. This artifact starts at "
                     "UNKNOWN_COVERAGE by construction: a novelty claim is an absence claim, and "
                     "an unmeasured search cannot support one."),
        }
        if not active:
            warnings.append("no scholarly provider is available; every novelty judgment this run "
                            "will be UNKNOWN_COVERAGE")
        if "no full-text search" in blind:
            warnings.append("no full-text provider: mechanism-level near-duplicate search is "
                            "degraded, which is the search novelty verification most depends on")
        ctx.store.write(self.name, "coverage_report", report)
        ctx.store.append_jsonl(self.name, "quota_ledger",
                               [{"ts": time.time(), "event": "budget_set", "budget": budget}])
        return SkillResult(self.name, produced=["provider_registry", "coverage_report", "quota_ledger"],
                           warnings=warnings,
                           detail={"active": [r["name"] for r in active], "blind_spots": blind})


@register
class LiteratureSearch(Skill):
    name = "literature-search"
    optional_outputs = ("library_hits", "library_note_links")

    def execute(self, ctx: Context) -> SkillResult:
        model = ctx.store.read(self.name, "paper_model")
        registry = ctx.store.read(self.name, "provider_registry")
        coverage = ctx.store.read(self.name, "coverage_report")
        objective = ctx.external("search_objective",
                                 f"related work and competing methods for: {model.get('title')}")
        active = [p for p in ctx.scholarly if p.name in
                  {r["name"] for r in registry["providers"] if r["available"]}]

        queries = self._queries(model, objective)
        plan = {"objective": objective, "queries": queries,
                "providers": [p.name for p in active],
                "seeded_recall_probe": [c["claim_id"] for c in model.get("claims", [])[:5]]}
        ctx.store.write(self.name, "literature_search_plan", plan)

        hits: list[dict[str, Any]] = []
        log: list[dict[str, Any]] = []
        warnings: list[str] = []
        for q in queries:
            for p in active:
                try:
                    ctx.quota.check(p.name)
                    rows = p.search(q, limit=25)
                    ctx.quota.record(p.name, endpoint="search")
                    for r in rows:
                        r.setdefault("_provider", p.name)
                        r.setdefault("_query", q)
                    hits.extend(rows)
                    log.append({"ts": time.time(), "provider": p.name, "query": q,
                                "returned": len(rows), "error": None})
                except ProviderUnavailable as e:
                    log.append({"ts": time.time(), "provider": p.name, "query": q,
                                "returned": 0, "error": str(e)[:300]})
                    warnings.append(f"{p.name}: {str(e).splitlines()[0][:140]}")

        seen, dedup = set(), []
        for h in hits:
            k = (h.get("doi") or h.get("id") or h.get("title", "")).strip().lower()
            if k and k in seen:
                continue
            seen.add(k)
            dedup.append(h)

        ctx.store.write(self.name, "literature_candidates", dedup)
        ctx.store.write(self.name, "literature_retrieval_log", log)

        returned = sum(l["returned"] for l in log)
        errors = [l for l in log if l["error"]]
        if returned == 0:
            warnings.append(
                "search returned zero works. This is NOT evidence that the field is empty. "
                "Coverage stays UNKNOWN_COVERAGE and no NOVEL_ENOUGH verdict may be issued.")
        ctx.store.write(self.name, "landscape_report", self._landscape(model, dedup, coverage, log))
        ctx.store.write(self.name, "system_matrix",
                        self._csv(["system", "provider", "year", "venue"],
                                  [[h.get("title", "")[:80], h.get("_provider", ""),
                                    str(h.get("year", "")), str(h.get("venue", ""))] for h in dedup[:50]]))
        ctx.store.write(self.name, "benchmark_matrix",
                        self._csv(["benchmark", "reported_by", "value", "source"],
                                  [[m, "source paper", "see paper", model.get("title", "")[:60]]
                                   for m in model.get("metrics", [])[:20]]))
        warnings = list(dict.fromkeys(warnings))   # one line per provider, not per query
        produced = ["literature_search_plan", "literature_candidates", "literature_retrieval_log",
                    "landscape_report", "system_matrix", "benchmark_matrix"]
        return SkillResult(self.name, produced=produced, warnings=warnings,
                           detail={"queries": len(queries), "returned": returned,
                                   "unique": len(dedup), "provider_errors": len(errors)})

    def _queries(self, model, objective):
        title = model.get("title", "")
        qs = [title, objective]
        qs += [f"{title} {m}" for m in model.get("methods", [])[:3]]
        qs += [f"{d}" for d in model.get("datasets", [])[:3]]
        out, seen = [], set()
        for q in qs:
            q = re.sub(r"\s+", " ", str(q)).strip()
            if 4 < len(q) < 300 and q.lower() not in seen:
                seen.add(q.lower()); out.append(q)
        return out[:8]

    def _csv(self, header, rows):
        esc = lambda s: '"' + str(s).replace('"', '""') + '"'
        return "\n".join([",".join(header)] + [",".join(esc(c) for c in r) for r in rows])

    def _landscape(self, model, hits, coverage, log):
        lines = [f"# Landscape — {model.get('title','(untitled)')}", "",
                 f"Retrieved {len(hits)} unique works across {len({l['provider'] for l in log})} providers.", ""]
        if coverage.get("status") == "UNKNOWN_COVERAGE":
            lines += ["> **Coverage is UNKNOWN.** This landscape describes what the configured "
                      "providers returned, not what exists. Absence from this document is not "
                      "evidence of absence from the field.", ""]
        if coverage.get("named_blind_spots"):
            lines += ["## Named blind spots", ""] + \
                     [f"- {b}" for b in coverage["named_blind_spots"]] + [""]
        lines += ["## Works", ""]
        for h in hits[:40]:
            lines.append(f"- {h.get('title','(untitled)')} — {h.get('_provider','?')}"
                         + (f" ({h.get('year')})" if h.get("year") else ""))
        if not hits:
            lines.append("- none returned")
        return "\n".join(lines)


@register
class CitationResolver(Skill):
    name = "citation-resolver"
    optional_outputs = ("citation_graph", "citation_clusters", "citation_gap_candidates")

    def execute(self, ctx: Context) -> SkillResult:
        registry = ctx.store.read(self.name, "provider_registry")
        raw = ctx.external("reference_strings", []) or []
        seed = ctx.external("seed_paper_id", None)
        warnings: list[str] = []

        resolved = []
        for i, s in enumerate(raw):
            doi = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", str(s), re.I)
            arx = re.search(r"\b\d{4}\.\d{4,5}\b", str(s))
            resolved.append({
                "ref_id": f"R-{i+1:03d}", "raw": str(s)[:400],
                "doi": doi.group(0) if doi else None,
                "arxiv": arx.group(0) if arx else None,
                "status": "IDENTIFIED" if (doi or arx) else "UNRESOLVED",
                "resolved_via": "pattern" if (doi or arx) else None,
            })
        unresolved = [r for r in resolved if r["status"] == "UNRESOLVED"]
        if unresolved:
            warnings.append(f"{len(unresolved)}/{len(resolved)} references could not be resolved to "
                            f"an identifier. They may not be cited in the manuscript until they are.")
        if not raw:
            warnings.append("no reference strings supplied; the bibliography is empty. Any citation "
                            "in a later draft would have nothing to resolve against.")

        ctx.store.write(self.name, "resolved_references", resolved)
        ctx.store.write(self.name, "bibliography", self._bib(resolved))
        ctx.store.write(self.name, "citation_resolution_report", self._report(resolved, registry))

        graph_capable = any(r["capabilities"].get("citations") and r["available"]
                            for r in registry["providers"])
        produced = ["resolved_references", "bibliography", "citation_resolution_report"]
        if graph_capable and seed:
            ctx.store.write(self.name, "citation_graph",
                            {"seed": seed, "nodes": [], "edges": [],
                             "note": "expansion requires a live provider transport"})
            ctx.store.write(self.name, "citation_clusters", {"clusters": []})
            ctx.store.write(self.name, "citation_gap_candidates", [])
            produced += ["citation_graph", "citation_clusters", "citation_gap_candidates"]
        else:
            warnings.append("citation-graph expansion skipped: no available provider exposes a "
                            "citation graph, or no seed identifier was given. Gap mining that "
                            "depends on graph structure is therefore unavailable this run.")
        return SkillResult(self.name, produced=produced, warnings=warnings)

    def _bib(self, resolved):
        out = []
        for r in resolved:
            if r["status"] != "IDENTIFIED":
                continue
            key = (r["doi"] or r["arxiv"] or r["ref_id"]).replace("/", "_")
            out.append("@misc{%s,\n  note = {%s},\n%s}\n" % (
                key, r["raw"][:200].replace("{", "").replace("}", ""),
                (f"  doi = {{{r['doi']}}},\n" if r["doi"] else
                 f"  eprint = {{{r['arxiv']}}},\n  archivePrefix = {{arXiv}},\n")))
        return "\n".join(out) or "% no resolvable references\n"

    def _report(self, resolved, registry):
        ident = sum(1 for r in resolved if r["status"] == "IDENTIFIED")
        lines = ["# Citation resolution", "",
                 f"- supplied: {len(resolved)}", f"- identified: {ident}",
                 f"- unresolved: {len(resolved)-ident}", "",
                 "An identifier proves the work exists. It does not prove the work supports the "
                 "claim it is attached to — that check belongs to `claim-citation-auditor` and has "
                 "not run.", "", "## Providers", ""]
        lines += [f"- {p['name']}: {'available' if p['available'] else 'unavailable — ' + (p['unavailable_reason'] or 'probe failed')}"
                  for p in registry["providers"]]
        return "\n".join(lines)


@register
class ClaimEvidenceGraph(Skill):
    name = "claim-evidence-graph"

    def execute(self, ctx: Context) -> SkillResult:
        model = ctx.store.read(self.name, "paper_model")
        refs = ctx.store.read(self.name, "resolved_references", default=[])
        # Experiment evidence arrives long after the first pass over this skill.
        # The graph is a store, not a one-shot transform: it is re-entered every
        # time new evidence lands, which is the only way an own-work claim can ever
        # acquire a support edge. Read as a feedback edge — declared, not sneaked.
        ledger = ctx.store.read(self.name, "experiment_ledger", default=[])
        completed = [r for r in ledger if r.get("status") == "COMPLETED" and r.get("metrics")]
        claims = model.get("claims", [])
        registry, edges = [], []
        for c in claims:
            cid = c["claim_id"]
            registry.append({
                "claim_id": cid, "claim_text": c["text"],
                "claim_type": "empirical" if c.get("quantitative") else "conceptual",
                "origin": "source_paper", "locator": c.get("locator"),
                "status": "UNVERIFIED",
            })
            edges.append({
                "claim_id": cid, "claim_text": c["text"][:300],
                "claim_type": "empirical" if c.get("quantitative") else "conceptual",
                "support_edges": [],
                "conflicts": [],
                "status": "UNSUPPORTED",
            })
        # --- own-work claims, one per measured metric, each born with its support ---
        own = self._own_claims(completed)
        registry += [{"claim_id": o["claim_id"], "claim_text": o["claim_text"],
                      "claim_type": "empirical", "origin": "own_work",
                      "locator": None, "status": o["status"]} for o in own]
        edges += own

        ctx.store.write(self.name, "claim_registry",
                        {"claims": registry, "source": model.get("paper_id"),
                         "source_claims": len(claims), "own_work_claims": len(own)})
        ctx.store.write(self.name, "evidence_graph", edges)

        w = []
        if not edges:
            w.append("no claims extracted; the evidence graph is empty and cannot gate anything")
        unsupported = [e for e in edges if not e["support_edges"]]
        if unsupported:
            w.append(f"{len(unsupported)}/{len(edges)} claims remain UNSUPPORTED. Source-paper "
                     f"claims stay unsupported until a citation is verified to support the "
                     f"proposition; they are not evidence for our own work.")
        if own:
            w.append(f"{len(own)} own-work claim(s) attached to {len(completed)} completed runs. "
                     f"Each names its run ids, so a number in the manuscript can be traced to the "
                     f"runs that produced it — or fail the citation audit.")
        elif completed:
            w.append("completed runs exist but produced no numeric metric to claim")
        return SkillResult(self.name, produced=["claim_registry", "evidence_graph"], warnings=w,
                           next_state="EVIDENCE_EXPANDED",
                           detail={"source_claims": len(claims), "own_claims": len(own),
                                   "completed_runs": len(completed), "references": len(refs)})

    def _own_claims(self, completed: list[dict]) -> list[dict]:
        """One claim per (experiment, arm, metric), supported by the runs that measured it.

        Deliberately mechanical. A claim generated here says only what the ledger
        says — the mean over seeds of ONE condition, and which run ids produced it.
        Anything more interesting is the manuscript's job, and it will have to
        justify itself against these edges rather than inventing its own.

        Per arm, not per experiment. Averaging a method together with the baseline
        it is being compared against produces a number that is arithmetically valid
        and describes no condition that was ever run — and because it is a real
        average of real runs, every downstream integrity check passes it.
        """
        import statistics
        by: dict[tuple, list] = {}
        for r in completed:
            arm = str((r.get("provenance") or {}).get("arm") or r.get("arm") or "candidate")
            for m, v in (r.get("metrics") or {}).items():
                if isinstance(v, (int, float)):
                    # attempt_id, not run_id: `_base_entry` sets run_id to the
                    # orchestration run for EVERY row, so a claim promising "a
                    # number can be traced to the runs that produced it" listed the
                    # same id three times and traced to nothing.
                    by.setdefault((r.get("experiment_id", "?"), arm, m), []).append(
                        (str(r.get("attempt_id") or r.get("run_id")), float(v)))
        out = []
        for i, ((exp, arm, metric), rows) in enumerate(sorted(by.items()), 1):
            vals = [v for _, v in rows]
            mean = statistics.fmean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            out.append({
                "claim_id": f"OC-{i:03d}",
                "claim_text": (f"In {exp}, the {arm} arm's {metric} measured {mean:.4f} "
                               f"(sd {sd:.4f}, n={len(vals)} runs)."),
                "claim_type": "empirical",
                "support_edges": [{"kind": "experiment_result", "experiment_id": exp,
                                   "arm": arm, "branch": f"{exp}:{arm}",
                                   "metric": metric, "value": round(mean, 6),
                                   "sd": round(sd, 6), "n": len(vals),
                                   "run_ids": [rid for rid, _ in rows]}],
                "conflicts": [],
                "status": "SUPPORTED",
            })
        return out
