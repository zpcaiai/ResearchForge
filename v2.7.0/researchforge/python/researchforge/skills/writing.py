"""Writing plane: spine-bound drafting, claim/citation audit, review simulation.

Three skills, and only one of them is allowed to be creative.

`manuscript-builder` writes prose, but only prose that is bound to a claim the
evidence graph already carries — and it refuses to write anything at all until
evidence is locked, because drafting before evidence lock is the mechanism by
which an unsupported sentence becomes a published claim.

`claim-citation-auditor` is deliberately model-free. It is the last place a
fabricated number or a decorative citation can be caught, and a check that asks a
language model whether a claim is supported is a check that can be talked out of
its answer. Everything here resolves mechanically against the experiment ledger,
the resolved reference list and the evidence graph. Citation existence is
necessary and not sufficient: a real DOI attached to a claim it does not support
is NOT_SUPPORTED, and it is reported as such rather than as a passing citation.

`review-simulator` simulates a review, then refuses to rebut anything the
integrity gate already ruled unsupported. Drafting a rebuttal for a blocked claim
would be advocacy for a claim the evidence does not support.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Any

from ..errors import GateBlocked
from ..skill import Context, Skill, SkillResult, register
from .innovation import SYNTHETIC_NOTE, _ask_json

# --------------------------------------------------------------------------
# draft grammar
#
# The draft is parsed, not trusted. `draft_manifest` is the builder's own report
# of what it wrote; the audit reads the manuscript file itself, because a claim
# that drifted out of the manifest is exactly the claim worth catching.
# --------------------------------------------------------------------------
SECTION_RE = re.compile(
    r"^\s*(?:\\(?:sub)*section\*?\{(?P<tex>[^}]*)\}|(?P<hashes>\#{1,4})\s+(?P<md>.+))\s*$")
CLAIM_MARKER_RE = re.compile(r"(?:%|<!--)\s*claim:\s*(?P<cid>[A-Za-z0-9._-]+)")
CITE_RE = re.compile(r"\\cite[tp]?\*?(?:\[[^\]]*\])*\{([^}]*)\}")
BRACKET_REF_RE = re.compile(r"\[((?:[A-Za-z]+-\d+)(?:\s*,\s*[A-Za-z]+-\d+)*)\]")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
NUMBER_RE = re.compile(r"(?<![\w.\-])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s?%?")
STRUCTURAL_RE = re.compile(
    r"\b(?:section|sec\.|table|tab\.|figure|fig\.|appendix|eq\.|equation|algorithm|"
    r"line|page|listing|step)s?\s*~?\s*\d+", re.I)
LATEX_STRUCTURE = ("\\documentclass", "\\usepackage", "\\begin", "\\end", "\\title",
                   "\\author", "\\maketitle", "\\bibliography", "\\bibliographystyle",
                   "\\input", "\\include", "\\newcommand", "\\date")

#: Comparative performance language. Under CM_NONE none of it is admissible, and
#: under CM_REPORTED it is admissible only alongside the required disclosure.
COMPARATIVE_RE = re.compile(
    r"\b(out-?perform\w*|improv\w*\s+(?:over|upon|on)\b|better\s+than|superior\s+to|"
    r"state[-\s]of[-\s]the[-\s]art|sota\b|beats?\b|surpass\w*|exceeds?\b|higher\s+than|"
    r"lower\s+than|reduces?\s+\w+\s+by|achiev\w*\s+(?:higher|lower|better)|new\s+best|"
    r"stronger\s+than|competitive\s+with)\b", re.I)
#: Sentences that assert a fact about the world. Everything else in a paper is
#: signposting, and grading signposting as an unsupported claim would bury the
#: real findings under noise.
ASSERTION_RE = re.compile(
    r"\b(demonstrat\w+|prove[sd]?|establish\w+|shows?\b|shown\b|confirm\w+|generaliz\w+|"
    r"guarantee\w*|robust\b|significant\w*|correlat\w+|caus\w+|enables?\b|yields?\b|"
    r"achiev\w+|reduc\w+|increas\w+|decreas\w+|outperform\w*|holds?\b|implies\b)\b", re.I)
GENERALIZATION_RE = re.compile(
    r"\b(in general|generally|all\b|any\b|always|universall?y|across\s+(?:all\s+)?domains|"
    r"arbitrary|every\b|in\s+every\s+setting)\b", re.I)

#: A claim about being best, not merely better. `COMPARATIVE_RE` covers "better
#: than X" for any X — including the source paper's own baseline, which is a
#: legitimate and narrow thing to say. This one covers the sentence that says the
#: method is at or beyond the current frontier, which is a claim about a
#: population of methods and needs the strongest of them in the ledger.
SOTA_CLAIM_RE = re.compile(
    r"\b(?:state[-\s]of[-\s]the[-\s]art|sota\b|new\s+(?:state[-\s]of[-\s]the[-\s]art|best|record)|"
    r"best[-\s](?:known|published|reported|performing)|outperform\w*\s+(?:all|every|prior|existing|"
    r"previous|current)|surpass\w*\s+(?:all|every|prior|existing|previous|current)|"
    r"competitive\s+with\s+(?:the\s+)?(?:state[-\s]of[-\s]the[-\s]art|best|leading)|"
    r"leading\s+method|top[-\s]performing|sets?\s+a\s+new\s+\w+|"
    # "the highest accuracy ever recorded" is the same claim in plainer words, and
    # the gate missed it entirely.
    r"(?:highest|lowest|best|strongest)\s+\w*\s*\w*\s*(?:ever\s+)?(?:recorded|reported|"
    r"achieved|published|seen|to\s+date)|first\s+to\s+(?:exceed|surpass|beat))\b", re.I)
#: Only a sentence about *this* work can be a SOTA claim by this work. "Recent
#: state-of-the-art models use rotary embeddings" is a description of the
#: literature, and grading it would make the check untrustworthy in exactly the
#: place a reviewer looks first.
SELF_REF_RE = re.compile(
    r"\b(?:we\b|our\b|ours\b|us\b|this\s+(?:paper|work|method|approach|study|system|model)|"
    r"the\s+proposed\b|proposed\s+(?:method|approach|model|system)|here\s+we)\b", re.I)
#: The frontier phrase has to be ASSERTED, not mentioned. "We follow the setup of
#: prior work, e.g. state-of-the-art transformers" contains both a self-reference
#: and a frontier phrase in one genuine sentence and claims nothing.
#: Sentence openers that continue the previous sentence's subject.
CONTINUATION_RE = re.compile(r"^(?:it|this|these|those|they|ours|the\s+(?:method|model|system|"
                             r"approach)\b)", re.I)
SOTA_MENTION_RE = re.compile(
    r"(?:\b(?:e\.g\.|i\.e\.|such\s+as|including|like|compared\s+(?:to|with)|"
    r"relative\s+to|against|versus|vs\.?|following|builds?\s+on|prior|previous|"
    r"existing|recent|other)\W{0,12}$)", re.I)

VERDICTS = ("SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "FABRICATED", "SCOPE_MISMATCH")
#: Severity order used when several defects apply to one sentence. The worst wins:
#: a fabricated number in a sentence that also has a weak citation is a fabrication.
_SEVERITY = {"FABRICATED": 4, "NOT_SUPPORTED": 3, "SCOPE_MISMATCH": 2,
             "PARTIALLY_SUPPORTED": 1, "SUPPORTED": 0}
BLOCKING_VERDICTS = ("FABRICATED", "NOT_SUPPORTED", "SCOPE_MISMATCH")


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


#: Abbreviations whose trailing period does not end a sentence. Without these the
#: digit branch below splits "e.g. 3 systems" in the wrong place.
_ABBREV = (r"(?<!e\.g\.)(?<!i\.e\.)(?<!\bcf\.)(?<!\bvs\.)(?<!etc\.)(?<!Fig\.)"
           r"(?<!Tab\.)(?<!\bEq\.)(?<!\bal\.)(?<!\bSec\.)(?<!\bNo\.)")


def _sentences(text: str) -> list[str]:
    """Split into sentences, including the ones that start with a number.

    The lookahead used to admit only an uppercase letter or an opening bracket, so
    any sentence beginning with a digit never started a new element — and results
    prose begins with digits constantly. Two sentences merged into one "claim",
    which is how a self-reference in one and an unrelated literature mention in the
    next became a single hard submission blocker on correct text.

    Adding the digit branch made abbreviations matter for the first time: without
    the lookbehinds, "e.g. 3 datasets" splits after "e.g.".
    """
    parts = re.split(_ABBREV + r"(?<=[.!?])\s+(?=[A-Z0-9\\$(\[])", _norm(text))
    return [p.strip() for p in parts if p.strip()]


def _quantities(text: str) -> list[dict[str, Any]]:
    """Numbers in a sentence that assert a measurement.

    Section/table/figure pointers and four-digit years are removed first: a year is
    a bibliographic fact carried by the citation check, and `Table 3` is not a
    result. Everything that survives is treated as a measurement and must be found
    in the ledger.

    Scientific notation is matched as ONE quantity, exponent included. Without the
    exponent this read `5.3462760410685855e-16` as `5.34628` — a dispersion of
    essentially zero rendered on a defense slide as five and a third, wrong by
    sixteen orders of magnitude. The binder caught it, but only as "unbound", so the
    diagnosis pointed at the wrong thing. A number extractor that silently drops an
    exponent is worse than one that refuses the string.
    """
    t = CITE_RE.sub(" ", text)
    t = re.sub(r"\\(?:ref|eqref|autoref|label|cref)\{[^}]*\}", " ", t)
    t = STRUCTURAL_RE.sub(" ", t)
    out: list[dict[str, Any]] = []
    for m in NUMBER_RE.finditer(t):
        raw = m.group(0).strip()
        percent = raw.endswith("%")
        body = raw.rstrip("%").strip()
        if not percent and "." not in body and re.fullmatch(r"(?:19|20)\d{2}", body):
            continue
        try:
            value = float(body)
        except ValueError:
            continue
        precision = len(body.split(".")[1]) if "." in body else 0
        out.append({"raw": raw, "value": value, "precision": precision, "percent": percent})
    return out


def _walk_numbers(node: Any, path: str = "") -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found += _walk_numbers(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _walk_numbers(v, f"{path}[{i}]")
    elif isinstance(node, bool):
        pass
    elif isinstance(node, (int, float)):
        found.append((path, float(node)))
    elif isinstance(node, str):
        try:
            found.append((path, float(node.strip().rstrip("%"))))
        except ValueError:
            pass
    return found


def _ledger_index(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every number the runs actually produced, with where it came from."""
    idx: list[dict[str, Any]] = []
    for rec in ledger:
        for metric, value in _walk_numbers(rec.get("metrics") or {}):
            idx.append({"experiment_id": rec.get("experiment_id"), "run_id": rec.get("run_id"),
                        "status": rec.get("status"), "metric": metric, "value": value})
    return idx


#: findings that mean the derived statistics cannot be trusted as a source of truth
_FABRICATION_CODES = {"REPORTED_VALUE_MISMATCH", "VALUES_DIFFER_FROM_LEDGER",
                      "NO_RAW_SUPPORT", "STALE_ANALYSIS"}


def _derived_index(graph: list[dict[str, Any]], stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Statistics derived from the ledger, admissible only while the audit vouches for them.

    A mean over seven seeds is not in the ledger and never will be, so checking a
    draft only against raw run values marks every legitimate summary statistic as
    fabricated — which would train everyone to ignore the one alarm that matters.

    The loophole this could open is obvious, so it is closed by condition rather
    than by trust: derived values are accepted only because `integrity-auditor`
    independently recomputes them from the ledger and raises a fabrication-class
    finding when they disagree. If it raised one, this index is empty and the
    strict raw-only rule applies.
    """
    findings = stats.get("findings") or []
    if any(str(f.get("code")) in _FABRICATION_CODES for f in findings):
        return []
    idx: list[dict[str, Any]] = []
    for e in graph:
        for edge in e.get("support_edges") or []:
            if edge.get("kind") != "experiment_result":
                continue
            # `n` too: a claim that says "n=7 runs" is asserting a count of the
            # ledger, which is the most checkable number in the sentence. Leaving it
            # out marked every honest sample size as fabricated, and an auditor that
            # cries wolf on sample sizes is one nobody reads.
            for field in ("value", "sd", "n"):
                v = edge.get(field)
                if isinstance(v, (int, float)):
                    idx.append({"experiment_id": edge.get("experiment_id"),
                                "run_id": f"derived:{e.get('claim_id')}",
                                "status": "DERIVED", "metric": f"{edge.get('metric')}.{field}",
                                "value": float(v), "n": edge.get("n"),
                                "from_runs": edge.get("run_ids") or [],
                                "vouched_by": "integrity-auditor (no fabrication-class finding)"})
    return idx


def _match_number(q: dict[str, Any], idx: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find a ledger entry the drafted number could honestly be a rendering of.

    A draft rounds; the ledger does not. `0.82` in prose is accepted for a ledger
    value of 0.8213, and `82%` is accepted for 0.8213 — but only rounding and
    percent scaling are permitted. Nothing else is 'close enough': a number that
    needs any other transformation to reach the ledger did not come from it.
    """
    p = q["precision"]
    for entry in idx:
        v = float(entry["value"])
        for candidate in (v, v * 100.0, v / 100.0):
            if abs(candidate - q["value"]) <= 1e-9 or round(candidate, p) == round(q["value"], p):
                return entry
    return None


def _cited_keys(text: str) -> list[str]:
    keys: list[str] = []
    for m in CITE_RE.finditer(text):
        keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
    for m in BRACKET_REF_RE.finditer(text):
        keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
    keys += [m.group(0) for m in DOI_RE.finditer(text)]
    return list(dict.fromkeys(keys))


def _reference_index(refs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every string a draft could plausibly use to name a resolved reference.

    Includes the bibliography key shape produced by `citation-resolver`, so that a
    \\cite of a real entry resolves — and a \\cite of anything else does not.
    """
    idx: dict[str, dict[str, Any]] = {}
    for r in refs:
        aliases = [r.get("ref_id"), r.get("doi"), r.get("arxiv"), r.get("key")]
        for a in list(aliases):
            if a:
                aliases.append(str(a).replace("/", "_"))
        for a in aliases:
            if a:
                idx[str(a).strip().lower()] = r
    return idx


def _edge_ref(e: dict[str, Any]) -> str | None:
    for k in ("ref_id", "reference_id", "citation_key", "doi", "source_id", "source"):
        if e.get(k):
            return str(e[k])
    return None


def _edge_result(e: dict[str, Any]) -> str | None:
    """The most specific result identifier this edge names.

    `branch` comes first: an edge that names one arm of one experiment is evidence
    about that condition, and returning its experiment id instead would let a
    figure planned from it draw every arm and be verified against whichever one
    happens to match.
    """
    for k in ("result_id", "branch", "experiment_id", "run_id"):
        if e.get(k):
            return str(e[k])
    return None


def _edge_results(e: dict[str, Any]) -> list[str]:
    """Every identifier this edge names, most specific first.

    `_edge_result` returning only the most specific one meant a figure planned from
    an edge carrying `branch: "E-001:candidate"` could not bind to an analysis group
    whose branch was the arm-less `"E-001"` — the figure was refused as
    `figure_data_missing` for a claim that was fully supported. Offering both lets
    the binder match on whichever the analysis actually produced, while the numeric
    read-back check still has to pass.
    """
    out = []
    for k in ("result_id", "branch", "experiment_id", "run_id"):
        v = e.get(k)
        if v and str(v) not in out:
            out.append(str(v))
    return out


def _edge_relation(e: dict[str, Any]) -> str:
    rel = e.get("relation") or e.get("kind_of_support") or e.get("support")
    if rel is None and e.get("supports") is True:
        rel = "supports"
    return str(rel or "").lower()


def _edge_scope(e: dict[str, Any]) -> list[str]:
    scope = e.get("scope")
    if scope is None:
        return []
    if isinstance(scope, str):
        return [scope]
    if isinstance(scope, dict):
        return [str(v) for v in scope.values() if isinstance(v, (str, int, float))]
    if isinstance(scope, list):
        return [str(v) for v in scope]
    return []


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    esc = lambda s: '"' + str(s).replace('"', '""') + '"'
    return "\n".join([",".join(header)] + [",".join(esc(c) for c in r) for r in rows]) + "\n"


# ==========================================================================
# manuscript-builder
# ==========================================================================
@register
class ManuscriptBuilder(Skill):
    """Spine first, prose second, and neither before evidence is locked."""

    name = "manuscript-builder"
    #: A PDF is only produced when a real typesetting engine is present. A file
    #: named main.pdf that is not the typeset manuscript is worse than no file:
    #: it is the artifact a reader would trust most and verify least.
    optional_outputs = ("manuscript_pdf",)

    SECTIONS = ["Abstract", "Introduction", "Related Work", "Method",
                "Experimental Setup", "Results", "Limitations", "Conclusion"]

    def execute(self, ctx: Context) -> SkillResult:
        graph = ctx.store.read(self.name, "evidence_graph", default=[])
        findings = ctx.store.read(self.name, "findings", default=[])
        negative = ctx.store.read(self.name, "negative_findings", default=[])
        meta = ctx.store.read(self.name, "meta_analysis", default={})
        boundaries = ctx.store.read(self.name, "boundary_conditions", default="")
        cm = ctx.store.read(self.name, "comparison_mode")
        direction = ctx.store.read(self.name, "selected_direction")
        bib = ctx.store.read(self.name, "bibliography", default="")
        venue = ctx.external("target_venue", "unspecified venue")
        template = ctx.external("venue_template", None)
        warnings: list[str] = []

        self._require_evidence_lock(graph, findings, meta)

        # --- spine: the argument, before a word of prose -----------------
        spine = self._spine(graph, findings, negative, meta, cm, direction, venue)
        if spine["findings_not_in_evidence_graph"]:
            # The graph is the backbone the auditor grades against. A finding that
            # never reached it cannot be audited, so drafting it would produce a
            # sentence no downstream check could ever verify.
            warnings.append(
                f"{len(spine['findings_not_in_evidence_graph'])} finding(s) are not represented in "
                f"the evidence graph and were not drafted: "
                f"{spine['findings_not_in_evidence_graph']}. Register them as claims with support "
                f"edges first; a claim the graph does not carry cannot be audited.")
        ctx.store.write(self.name, "manuscript_spine", spine)
        ctx.store.write(self.name, "section_blueprint", self._blueprint(spine, cm, boundaries))

        # --- prose: one paragraph per spine claim, and nothing else ------
        claims = spine["claims"]
        fallback = {"paragraphs": [self._synthetic_paragraph(c) for c in claims]}
        data, synthetic = _ask_json(
            ctx, "You draft scientific prose that never states more than its evidence carries.",
            self._prompt(spine, cm, venue, template), fallback)
        paragraphs = data.get("paragraphs", []) if isinstance(data, dict) else list(data or [])

        known = {c["claim_id"] for c in claims}
        orphans = [p for p in paragraphs if p.get("claim_id") not in known]
        if orphans:
            # The spine exists to constrain the draft. Prose that cannot name the
            # claim it is arguing is the exact failure mode this skill was merged
            # to prevent, so it is dropped rather than filed under a nearby claim.
            warnings.append(
                f"dropped {len(orphans)} drafted paragraph(s) that referenced no claim in the "
                f"spine. A paragraph that cannot name its claim has no evidence behind it.")
            paragraphs = [p for p in paragraphs if p.get("claim_id") in known]
        forbidden = self._forbidden_hits(paragraphs, cm)
        if forbidden:
            warnings.append(
                f"removed {len(forbidden)} paragraph(s) using claim language forbidden under "
                f"{cm['mode']}: {sorted({f['pattern'] for f in forbidden})}")
            drop = {f["index"] for f in forbidden}
            paragraphs = [p for i, p in enumerate(paragraphs) if i not in drop]

        by_claim = {c["claim_id"]: c for c in claims}
        for p in paragraphs:
            # A drafter may place a claim's argument in any declared section; it may
            # not invent a section, because a section that is not in the spine is a
            # part of the paper nothing decided to write.
            named = _norm(p.get("section")).lower()
            p["section"] = next((s for s in spine["sections"] if s.lower() == named),
                                by_claim[p["claim_id"]]["section"])
        draft, manifest = self._render(spine, paragraphs, cm, venue, synthetic)
        ctx.store.write(self.name, "manuscript_draft", draft)
        ctx.store.write(self.name, "draft_manifest", manifest)
        ctx.store.write(self.name, "figure_plan", self._figure_plan(ctx, spine, cm, synthetic))

        produced = ["manuscript_spine", "section_blueprint", "manuscript_draft",
                    "draft_manifest", "figure_plan"]
        pdf = self._typeset(ctx)
        if pdf:
            ctx.store.write(self.name, "manuscript_pdf", pdf)
            produced.append("manuscript_pdf")
        else:
            warnings.append(
                "no typesetting engine (tectonic/pdflatex/pandoc) is available, so no PDF was "
                "produced. The .tex source is the manuscript; a placeholder PDF would be the "
                "artifact a reader trusts most and verifies least.")
        if not bib.strip() or bib.strip().startswith("% no resolvable"):
            warnings.append("the bibliography is empty: any citation in this draft resolves to "
                            "nothing and will be audited as fabricated.")
        if synthetic:
            warnings.append("OFFLINE: every paragraph is a structural placeholder, not prose. "
                            "The draft is marked synthetic and the integrity gate blocks on it.")
        return SkillResult(self.name, produced=produced, warnings=warnings, synthetic=synthetic,
                           next_state="WRITING",
                           detail={"claims": len(claims), "paragraphs": len(paragraphs),
                                   "comparison_mode": cm["mode"],
                                   "disclosure_inserted_in": manifest["disclosure"]["inserted_in"]})

    # ------------------------------------------------------------------
    def _require_evidence_lock(self, graph, findings, meta) -> None:
        """Refuse to draft until the evidence exists to draft against.

        This is the gate the whole writing plane rests on. A draft written before
        evidence lock has nothing to bind its claims to, so its claims get bound
        afterwards — to whatever happens to be available. That is how unsupported
        claims enter a paper, and it is not recoverable downstream: the auditor can
        only check claims against evidence that exists.
        """
        unlocked: list[str] = []
        supported = [e for e in graph if e.get("support_edges")]
        if not graph:
            unlocked.append("evidence_graph is empty (no claims registered)")
        elif not supported:
            unlocked.append(
                f"evidence_graph has {len(graph)} claim(s) and 0 support edges — every claim is "
                f"still UNSUPPORTED")
        result_refs = self._result_refs(graph, findings)
        if not result_refs:
            unlocked.append("no experiment result is referenced by any finding or support edge")
        if meta.get("evidence_locked") is False:
            unlocked.append("meta_analysis reports evidence_locked=false")
        if unlocked:
            raise GateBlocked(
                "evidence_lock",
                "manuscript-builder will not draft: " + "; ".join(unlocked)
                + ". Drafting before evidence lock is how unsupported claims enter a paper — "
                  "the prose gets written first and the evidence is found to fit it afterwards.",
                "run the experiment plane to completion (experiment-runner, integrity-auditor, "
                "finding-memory) and attach support edges in claim-evidence-graph, then re-run")

    def _result_refs(self, graph, findings) -> list[str]:
        refs: list[str] = []
        for f in findings:
            for k in ("result_ids", "experiment_ids", "run_ids", "evidence_ids"):
                refs += [str(x) for x in (f.get(k) or [])]
            ev = f.get("evidence")
            if isinstance(ev, dict):
                for k in ("result_ids", "experiment_ids", "run_ids"):
                    refs += [str(x) for x in (ev.get(k) or [])]
        for e in graph:
            for edge in e.get("support_edges") or []:
                if isinstance(edge, dict):
                    refs += _edge_results(edge)
        return list(dict.fromkeys(refs))

    def _spine(self, graph, findings, negative, meta, cm, direction, venue) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for e in graph:
            if not e.get("support_edges"):
                continue                       # unsupported claims do not get a paragraph
            refs = [_edge_ref(x) for x in e["support_edges"] if isinstance(x, dict) and _edge_ref(x)]
            results = [_edge_result(x) for x in e["support_edges"]
                       if isinstance(x, dict) and _edge_result(x)]
            claims.append({
                "claim_id": e["claim_id"],
                "statement": _norm(e.get("claim_text"))[:400],
                "kind": e.get("claim_type", "empirical"),
                "section": "Results" if results else "Related Work",
                "evidence": {"ref_ids": refs, "result_ids": results,
                             "graph_status": e.get("status")},
                "conflicts": e.get("conflicts") or [],
            })
        by_id = {c["claim_id"]: c for c in claims}
        orphan_findings: list[str] = []
        for i, f in enumerate(list(findings) + list(negative)):
            cid = f.get("claim_id") or f.get("finding_id") or f"F-{i + 1:03d}"
            claim = by_id.get(cid)
            if claim is None:
                orphan_findings.append(cid)
                continue
            # A finding that IS in the graph enriches its claim with the runs behind it.
            claim["evidence"]["result_ids"] = list(dict.fromkeys(
                claim["evidence"]["result_ids"] + self._result_refs([], [f])))
            if f in negative:
                # A negative result is a result. It goes in the paper, not in a drawer.
                claim["kind"] = "negative"
        disclosure = (cm.get("disclosure_required") or {})
        home = next((s for s in self.SECTIONS
                     for want in (disclosure.get("must_appear_in") or [])
                     if _norm(want).lower() in s.lower()), "Limitations")
        if disclosure.get("required") and disclosure.get("text_template"):
            # The disclosure is itself a claim, sourced to comparison_mode, so that
            # the invariant 'every paragraph names a spine claim' has no exception.
            claims.append({
                "claim_id": "MC-DISCLOSURE",
                "statement": disclosure["text_template"],
                "kind": "disclosure",
                "section": home,
                "evidence": {"ref_ids": [], "result_ids": [],
                             "graph_status": "DERIVED_FROM_COMPARISON_MODE"},
                "conflicts": [],
            })
        return {
            "thesis": _norm(direction.get("thesis") or
                            f"contribution of {', '.join(direction.get('selected_idea_ids', []))}"),
            "target_venue": venue,
            "comparison_mode": cm["mode"],
            "admissible_claim_scope": cm.get("admissible_idea_modes", []),
            "forbidden_claim_patterns": cm.get("forbidden_claim_patterns", []),
            "disclosure": disclosure,
            "sections": self.SECTIONS,
            "claims": claims,
            "argument_chain": [c["claim_id"] for c in claims],
            "findings_not_in_evidence_graph": orphan_findings,
            "meta_analysis_id": meta.get("id"),
            "built_before_prose": True,
        }

    def _prompt(self, spine, cm, venue, template) -> str:
        return (
            "Draft one paragraph per claim. Each paragraph must state ONLY what its claim's "
            "evidence carries, and must cite it.\n\n"
            f"HARD CONSTRAINT: comparison mode is {cm['mode']}. These claim patterns are "
            f"inadmissible and must not appear: {cm.get('forbidden_claim_patterns')}.\n"
            "Do not introduce any number that is not present in the claim's evidence.\n\n"
            f"Target venue: {venue}. Template: {template}.\n\n"
            "Return {\"paragraphs\":[{\"claim_id\",\"text\",\"citations\":[ref_ids]}]}\n\n"
            f"SPINE:\n{json.dumps(spine['claims'], indent=1)[:6000]}")

    def _synthetic_paragraph(self, claim) -> dict[str, Any]:
        ev = claim["evidence"]
        body = (claim["statement"] if claim["kind"] == "disclosure"
                else f"[synthetic] {claim['statement']}")
        return {"claim_id": claim["claim_id"], "text": body,
                "citations": ev["ref_ids"], "_synthetic": claim["kind"] != "disclosure"}

    def _forbidden_hits(self, paragraphs, cm) -> list[dict[str, Any]]:
        hits = []
        for i, p in enumerate(paragraphs):
            for pat in cm.get("forbidden_claim_patterns", []):
                if re.search(re.escape(str(pat)), str(p.get("text", "")), re.I):
                    hits.append({"index": i, "pattern": pat, "claim_id": p.get("claim_id")})
        return hits

    def _render(self, spine, paragraphs, cm, venue, synthetic):
        disclosure = spine["disclosure"]
        must_appear = list(disclosure.get("must_appear_in") or []) if disclosure.get("required") else []
        by_section: dict[str, list[dict[str, Any]]] = {}
        for p in paragraphs:
            by_section.setdefault(p["section"], []).append(p)

        lines = [f"% ResearchForge draft — venue: {venue}",
                 f"% comparison_mode: {cm['mode']} (derived from {cm.get('derived_from_level')})",
                 "% every paragraph below is preceded by the spine claim it argues"]
        if synthetic:
            lines.append(f"% SYNTHETIC: {SYNTHETIC_NOTE}")
        lines += ["\\documentclass{article}", "\\begin{document}",
                  f"\\title{{{_norm(spine['thesis'])[:160]}}}", "\\maketitle", ""]
        records: list[dict[str, Any]] = []
        inserted: list[str] = []
        index = 0
        for section in spine["sections"]:
            lines.append(f"\\section{{{section}}}")
            section_paras = list(by_section.get(section, []))
            if any(_norm(m).lower() in section.lower() for m in must_appear):
                # Under a degraded comparison mode the disclosure is not a courtesy;
                # it is the sentence that makes every other number in the section
                # readable. It goes in verbatim, in each section that requires it.
                if not any(p["claim_id"] == "MC-DISCLOSURE" for p in section_paras):
                    section_paras.append({"claim_id": "MC-DISCLOSURE",
                                          "text": disclosure["text_template"], "citations": []})
                inserted.append(section)
            for p in section_paras:
                cites = [c for c in (p.get("citations") or []) if c]
                text = _norm(p.get("text"))
                if cites:
                    # Inside the sentence, not after it: a citation stranded past the
                    # full stop is a sentence of its own to anything that reads the
                    # draft, and it belongs to the claim it was attached to.
                    stop = "." if text.endswith(".") else ""
                    text = (f"{text[:-1] if stop else text} "
                            f"\\cite{{{','.join(str(c) for c in cites)}}}{stop}")
                lines += [f"% claim: {p['claim_id']}", text, ""]
                records.append({"paragraph_index": index, "section": section,
                                "claim_id": p["claim_id"], "citations": cites,
                                "synthetic": bool(p.get("_synthetic"))})
                index += 1
        lines += ["\\bibliography{references}", "\\end{document}", ""]

        missing = [m for m in must_appear
                   if not any(_norm(m).lower() in s.lower() for s in inserted)]
        manifest = {
            "target_venue": venue,
            "comparison_mode": cm["mode"],
            "sections": spine["sections"],
            "paragraphs": records,
            "claim_index": {c["claim_id"]: {
                "section": c["section"], "evidence": c["evidence"],
                "paragraph_indices": [r["paragraph_index"] for r in records
                                      if r["claim_id"] == c["claim_id"]]}
                for c in spine["claims"]},
            "unwritten_claims": [c["claim_id"] for c in spine["claims"]
                                 if not any(r["claim_id"] == c["claim_id"] for r in records)],
            # An empty section is left in the draft rather than hidden: a paper with
            # no Method text is a fact the reviewer stage should be able to see.
            "empty_sections": [s for s in spine["sections"]
                               if not any(r["section"] == s for r in records)],
            "disclosure": {"required": bool(disclosure.get("required")),
                           "text": disclosure.get("text_template", ""),
                           "must_appear_in": must_appear,
                           "inserted_in": inserted,
                           "missing_sections": missing},
            "every_paragraph_bound_to_a_claim": all(r["claim_id"] for r in records),
            "_synthetic": synthetic,
            "_note": SYNTHETIC_NOTE if synthetic else None,
        }
        return "\n".join(lines), manifest

    def _figure_plan(self, ctx, spine, cm, synthetic) -> dict[str, Any]:
        # figure-factory is the producer of `selected_figure`; it is a directory
        # artifact, so existence is all this skill needs and all it checks.
        selected_exists = ctx.store.exists("selected_figure")
        figures = []
        for c in spine["claims"]:
            if c["kind"] == "disclosure" or not c["evidence"]["result_ids"]:
                continue
            figures.append({
                "figure_id": f"F-{len(figures) + 1:02d}",
                "claim_id": c["claim_id"],
                "kind": "results" if c["kind"] == "empirical" else "diagnostic",
                "caption": f"Evidence for {c['claim_id']}: {c['statement'][:120]}",
                "data_source": {"result_ids": c["evidence"]["result_ids"]},
                "status": "PLANNED",
            })
        return {"figures": figures, "comparison_mode": cm["mode"],
                "selected_figures_present": selected_exists,
                "no_figure_without_a_claim": True,
                "_synthetic": synthetic}

    def _blueprint(self, spine, cm, boundaries) -> str:
        L = ["# Section blueprint", "",
             f"Comparison mode **{cm['mode']}** bounds every claim below.", "",
             "| section | claim | evidence |", "|---|---|---|"]
        for c in spine["claims"]:
            ev = c["evidence"]
            L.append(f"| {c['section']} | `{c['claim_id']}` | "
                     f"results={ev['result_ids'] or '—'} refs={ev['ref_ids'] or '—'} |")
        L += ["", "## Boundary conditions carried into Limitations", "",
              _norm(boundaries)[:1500] or "- none recorded"]
        return "\n".join(L)

    def _typeset(self, ctx) -> bytes | None:
        tex = ctx.store.path_for("manuscript_draft")
        for engine, argv in (("tectonic", ["tectonic", "-X", "compile", str(tex)]),
                             ("pdflatex", ["pdflatex", "-interaction=nonstopmode", tex.name])):
            if not shutil.which(engine):
                continue
            try:
                subprocess.run(argv, cwd=str(tex.parent), capture_output=True, timeout=300)
            except (OSError, subprocess.SubprocessError):
                return None
            pdf = tex.with_suffix(".pdf")
            if pdf.exists():
                return pdf.read_bytes()
        return None


# ==========================================================================
# claim-citation-auditor
# ==========================================================================
def _read_specs(ctx, skill: str) -> list[dict[str, Any]]:
    """The compiled experiment specs, or [] when none were written.

    Imported lazily from the execution module so the two skills cannot disagree
    about what counts as a spec file — `experiments/` also holds the ablation plan
    and the DAG, and a second parser here would eventually drift from that one.
    Returning [] on any failure is deliberate: the only check that reads these
    treats "no spec declared a sota arm" as the *stricter* outcome, so a read
    failure cannot weaken a gate.
    """
    try:
        from .execution import _load_experiment_specs  # noqa: PLC0415
        return _load_experiment_specs(ctx, skill)
    except Exception:  # noqa: BLE001 - absence is a valid state, not an error
        return []


@register
class ClaimCitationAuditor(Skill):
    """The last place a fabricated claim can be caught.

    No model is consulted anywhere in this skill. Every verdict is a mechanical
    resolution against the experiment ledger, the resolved references and the
    evidence graph — because a check that asks a model whether a claim is
    supported is a check that can be argued out of its answer, and this one is the
    final one before release.
    """

    name = "claim-citation-auditor"

    def execute(self, ctx: Context) -> SkillResult:
        draft = ctx.store.read(self.name, "manuscript_draft")
        manifest = ctx.store.read(self.name, "draft_manifest")
        graph = ctx.store.read(self.name, "evidence_graph", default=[])
        ledger = ctx.store.read(self.name, "experiment_ledger", default=[])
        refs = ctx.store.read(self.name, "resolved_references", default=[])
        cm = ctx.store.read(self.name, "comparison_mode")
        repro = ctx.store.read(self.name, "source_repro_report", default={})
        stats = ctx.store.read(self.name, "stats_audit", default={})
        specs = _read_specs(ctx, self.name)
        warnings: list[str] = []

        numbers = _ledger_index(ledger) + _derived_index(graph, stats)
        ref_idx = _reference_index(refs)
        graph_idx = {e["claim_id"]: e for e in graph if e.get("claim_id")}
        blockers: list[dict[str, Any]] = []

        audited = [self._audit_claim(c, graph_idx, ref_idx, numbers, cm)
                   for c in _atomic_claims(draft)]
        audited = [a for a in audited if a is not None]
        for rec in audited:
            if rec["verdict"] in BLOCKING_VERDICTS:
                blockers.append({
                    "kind": f"claim_{rec['verdict'].lower()}",
                    "claim_id": rec["claim_id"], "locator": rec["locator"],
                    "detail": rec["reason"], "quote": rec["text"][:220],
                    "remediation": rec["remediation"],
                })

        blockers += self._enforce_comparison_mode(draft, audited, cm)
        blockers += self._enforce_sota_claims(audited, ledger, specs)
        blockers += self._carry_upstream(stats, repro, cm)

        if manifest.get("_synthetic"):
            blockers.append({
                "kind": "synthetic_draft", "claim_id": None, "locator": "draft_manifest",
                "detail": "the draft was produced by the offline stub provider; its prose is a "
                          "structural placeholder, not research",
                "quote": "", "remediation": "re-run manuscript-builder with a real model provider"})
        if not ledger:
            blockers.append({
                "kind": "empty_ledger", "claim_id": None, "locator": "experiment_ledger",
                "detail": "the experiment ledger is empty, so no quantitative claim in this draft "
                          "can be verified against a run that happened",
                "quote": "", "remediation": "run the experiments, or remove every number from the "
                                            "draft"})
        for i, b in enumerate(blockers, 1):
            b.setdefault("severity", "BLOCKER")
            b["blocker_id"] = f"B-{i:03d}"

        counts = {v: sum(1 for a in audited if a["verdict"] == v) for v in VERDICTS}
        counts["NOT_A_CLAIM"] = sum(1 for a in audited if a["verdict"] == "NOT_A_CLAIM")
        gate = self._gate(ctx, audited, counts, blockers, cm, manifest, ledger, refs)

        ctx.store.write(self.name, "claim_audit", audited)
        ctx.store.write(self.name, "citation_audit", self._citation_md(audited, ref_idx, refs, cm))
        ctx.store.write(self.name, "integrity_gate", gate)
        ctx.store.write(self.name, "submission_blockers", self._blockers_md(blockers, gate, cm))

        if counts["FABRICATED"]:
            warnings.append(f"{counts['FABRICATED']} claim(s) are FABRICATED: a number or a "
                            f"citation in the draft corresponds to nothing that exists.")
        if counts["NOT_SUPPORTED"]:
            warnings.append(f"{counts['NOT_SUPPORTED']} claim(s) are NOT_SUPPORTED. Some of these "
                            f"carry real, resolvable citations — existence is not support.")
        return SkillResult(self.name,
                           produced=["claim_audit", "citation_audit", "integrity_gate",
                                     "submission_blockers"],
                           warnings=warnings, synthetic=bool(manifest.get("_synthetic")),
                           next_state="REVIEWING",
                           detail={"verdict": gate["verdict"], "counts": counts,
                                   "blockers": len(blockers), "comparison_mode": cm["mode"]})

    # ------------------------------------------------------------------
    def _audit_claim(self, claim, graph_idx, ref_idx, numbers, cm) -> dict[str, Any] | None:
        text = claim["text"]
        prose = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])*(\{[^}]*\})*", " ", text)
        if len(re.sub(r"[^A-Za-z]", "", prose)) < 3:
            return None                     # a bare macro is not a sentence to grade
        cited = _cited_keys(text)
        quantities = _quantities(text)
        entry = graph_idx.get(claim["claim_id"]) if claim["claim_id"] else None
        edges = [e for e in ((entry or {}).get("support_edges") or []) if isinstance(e, dict)]
        conflicts = (entry or {}).get("conflicts") or []

        rec: dict[str, Any] = {
            "claim_id": claim["claim_id"],
            "locator": f"{claim['section']}#p{claim['paragraph']}",
            "section": claim["section"],
            "text": text,
            "quantitative": bool(quantities),
            "citations": cited,
            "resolved_citations": [], "unresolved_citations": [],
            "supporting_edges": [], "number_checks": [],
            "verdict": "SUPPORTED", "reason": "", "remediation": "",
        }
        # The comparison-mode disclosure is not an empirical claim and is not graded
        # as one. It states what the project did NOT establish, and its source is
        # comparison_mode itself — requiring evidence for it would invert its meaning.
        disclosure_text = _norm((cm.get("disclosure_required") or {}).get("text_template"))
        if disclosure_text and len(_norm(text)) >= 20 and _norm(text) in disclosure_text:
            rec["kind"] = "disclosure"
            rec["reason"] = ("verbatim disclosure required by comparison mode "
                             f"{cm['mode']}, sourced to reproduction/comparison_mode.json")
            return rec

        findings: list[tuple[str, str, str]] = []   # (verdict, reason, remediation)

        # --- 1. numbers: verified against the ledger, or fabricated ------
        for q in quantities:
            hit = _match_number(q, numbers)
            rec["number_checks"].append({
                "value": q["raw"], "matched": bool(hit),
                "ledger_entry": hit,
            })
            if not hit:
                findings.append((
                    "FABRICATED",
                    f"the number {q['raw']} appears in the draft but matches neither a recorded "
                    f"metric nor an audited derived statistic ({len(numbers)} admissible values). "
                    f"A number with no run behind it is fabricated regardless of how it got there.",
                    "delete the number, or point it at the experiment_ledger entry it came from"))

        # --- 2. citations: existence first, then support ----------------
        for key in cited:
            ref = ref_idx.get(key.strip().lower())
            if ref is None:
                rec["unresolved_citations"].append(key)
                findings.append((
                    "FABRICATED",
                    f"citation '{key}' resolves to no entry in resolved_references — the cited "
                    f"work is not known to exist.",
                    "resolve the reference through citation-resolver, or remove the citation"))
                continue
            rec["resolved_citations"].append(
                {"key": key, "ref_id": ref.get("ref_id"), "doi": ref.get("doi"),
                 "status": ref.get("status")})
            supporting = [e for e in edges
                          if _edge_ref(e) and str(_edge_ref(e)).lower() in
                          {str(x).lower() for x in (key, ref.get("ref_id"), ref.get("doi")) if x}]
            if not supporting:
                findings.append((
                    "NOT_SUPPORTED",
                    f"citation '{key}' resolves to a real work, but the evidence graph records no "
                    f"edge in which that work supports {claim['claim_id'] or 'this claim'}. "
                    f"Citation existence is necessary and not sufficient: a real DOI attached to a "
                    f"claim it does not support is an unsupported claim.",
                    "verify the cited work against the proposition and record a support edge, or "
                    "restate the claim to what the work actually shows"))
                continue
            for e in supporting:
                rel = _edge_relation(e)
                rec["supporting_edges"].append({"ref": _edge_ref(e), "relation": rel,
                                                "scope": _edge_scope(e)})
                if rel in ("refutes", "contradicts", "conflicts"):
                    findings.append((
                        "NOT_SUPPORTED",
                        f"the evidence graph records that '{key}' CONTRADICTS this claim.",
                        "remove the claim or report the conflict explicitly"))
                elif rel in ("partial", "partially_supports", "weak", "indirect"):
                    findings.append((
                        "PARTIALLY_SUPPORTED",
                        f"'{key}' supports this claim only in part (relation={rel}).",
                        "narrow the claim to the part the source establishes"))
                scope = _edge_scope(e)
                if scope and GENERALIZATION_RE.search(text):
                    findings.append((
                        "SCOPE_MISMATCH",
                        f"the sentence generalizes, but its evidence is scoped to {scope}.",
                        "restate the claim within the evidence's scope"))

        # --- 3. result-backed and unbacked claims -----------------------
        result_edges = [e for e in edges if _edge_result(e)]
        for e in result_edges:
            rec["supporting_edges"].append({"result": _edge_result(e),
                                            "relation": _edge_relation(e) or "supports",
                                            "scope": _edge_scope(e)})
        if conflicts:
            findings.append(("PARTIALLY_SUPPORTED",
                             f"the evidence graph records {len(conflicts)} unresolved conflict(s) "
                             f"against this claim.",
                             "resolve or report the conflict before submission"))
        assertive = bool(ASSERTION_RE.search(text) or COMPARATIVE_RE.search(text)
                         or quantities or claim["claim_id"])
        if not assertive:
            rec["verdict"] = "NOT_A_CLAIM"
            rec["reason"] = ("carries no number, citation, comparative language or claim binding; "
                             "graded as prose, not as an assertion of fact")
            return rec
        if not cited and not edges and not quantities:
            findings.append((
                "NOT_SUPPORTED",
                f"the sentence asserts a fact but names neither a citation nor an experiment "
                f"result, and the evidence graph carries no support edge for "
                f"{claim['claim_id'] or 'it'}.",
                "attach evidence, or delete the sentence"))

        # --- 4. comparison mode, per claim ------------------------------
        comparative = COMPARATIVE_RE.search(text)
        forbidden = [p for p in cm.get("forbidden_claim_patterns", [])
                     if re.search(re.escape(str(p)), text, re.I)]
        if cm["mode"] == "CM_NONE" and (comparative or forbidden):
            findings.append((
                "NOT_SUPPORTED",
                f"comparative performance claim under {cm['mode']}: the baseline was never "
                f"reproduced (level {cm.get('derived_from_level')}), so there is nothing this "
                f"claim could be measured against. Matched: "
                f"{comparative.group(0) if comparative else forbidden}.",
                "remove the comparison, or raise the reproduction level and re-derive the "
                "comparison mode"))
        elif forbidden:
            findings.append((
                "NOT_SUPPORTED",
                f"claim language forbidden under {cm['mode']}: {forbidden}",
                "restate within the admissible claim scope for this comparison mode"))

        if findings:
            findings.sort(key=lambda f: -_SEVERITY[f[0]])
            rec["verdict"], rec["reason"], rec["remediation"] = findings[0]
            rec["all_findings"] = [{"verdict": v, "reason": r} for v, r, _ in findings]
        else:
            rec["reason"] = ("every number resolves to a ledger entry and every citation has a "
                             "recorded support edge")
        return rec

    def _enforce_comparison_mode(self, draft, audited, cm) -> list[dict[str, Any]]:
        """The disclosure requirement is what makes the degradation path bind.

        `reproduction-fallback-planner` derives a comparison mode from what was
        actually reproduced and states the disclosure that mode requires. If the
        draft can omit that sentence and still ship, the whole degradation path is
        decorative.
        """
        out: list[dict[str, Any]] = []
        disclosure = cm.get("disclosure_required") or {}
        if not disclosure.get("required"):
            return out
        required_text = _norm(disclosure.get("text_template"))
        sections = _sections_of(draft)
        flat = _norm(draft)
        if required_text and required_text not in flat:
            out.append({
                "kind": "missing_disclosure", "claim_id": None, "locator": "manuscript_draft",
                "detail": f"comparison mode {cm['mode']} requires the disclosure "
                          f"\"{required_text[:160]}\" and the draft does not contain it. Without "
                          f"it, every comparison in this paper reads as measured when it was not.",
                "quote": "", "remediation": "insert the disclosure verbatim in "
                                            f"{disclosure.get('must_appear_in')}"})
        for want in disclosure.get("must_appear_in") or []:
            body = next((b for name, b in sections.items()
                         if _norm(want).lower() in name.lower()), None)
            if body is None:
                out.append({
                    "kind": "missing_disclosure_section", "claim_id": None,
                    "locator": f"section:{want}",
                    "detail": f"comparison mode {cm['mode']} requires the disclosure to appear in a "
                              f"'{want}' section, and the draft has no such section.",
                    "quote": "", "remediation": f"add a '{want}' section carrying the disclosure"})
            elif required_text and required_text not in _norm(body):
                out.append({
                    "kind": "missing_disclosure", "claim_id": None,
                    "locator": f"section:{want}",
                    "detail": f"the required disclosure for {cm['mode']} is absent from the "
                              f"'{want}' section.",
                    "quote": "", "remediation": "insert the disclosure verbatim in that section"})
        if cm["mode"] == "CM_NONE":
            n = sum(1 for a in audited if COMPARATIVE_RE.search(a["text"]))
            if n:
                out.append({
                    "kind": "comparative_claim_under_CM_NONE", "claim_id": None,
                    "locator": "manuscript_draft",
                    "detail": f"{n} comparative performance claim(s) appear under CM_NONE. At "
                              f"{cm.get('derived_from_level')} no baseline was reproduced, so no "
                              f"comparison in this draft has anything behind it.",
                    "quote": "", "remediation": "remove every comparative claim, or raise the "
                                                "reproduction level"})
        return out

    def _enforce_sota_claims(self, audited, ledger, specs) -> list[dict[str, Any]]:
        """A claim to be state-of-the-art requires the state of the art in the ledger.

        This is the check that makes the SOTA arm binding. Without it the arm is a
        planning nicety: the blueprint can declare one, nobody runs it, and the
        manuscript still says "outperforms all prior methods" on the strength of a
        two-arm comparison against the source paper's baseline. That sentence is not
        a slight overstatement — it is a claim about a population that was never
        sampled.

        The check refuses to be satisfied by a reported number. A number from
        someone else's table was produced on their hardware, their data version and
        their tuning budget; placing our measured number beside it and calling the
        difference a result is the single most common way a comparison becomes
        fiction. Where only reported numbers exist, the claim has to be rewritten,
        not annotated.
        """
        out: list[dict[str, Any]] = []
        # A paragraph that has established "we" carries it forward. Requiring the
        # self-reference in the same sentence let "Our approach is simple. It
        # outperforms all prior methods." pass with zero blockers, and pronoun
        # continuation is how most results paragraphs are actually written.
        self_ref_paragraphs = {a["locator"] for a in audited if SELF_REF_RE.search(a["text"])}
        claims = []
        for a in audited:
            m = SOTA_CLAIM_RE.search(a["text"])
            if not m:
                continue
            # Mentioned, not asserted: "compared to the state of the art",
            # "e.g. state-of-the-art transformers".
            if SOTA_MENTION_RE.search(a["text"][:m.start()]):
                continue
            about_us = bool(SELF_REF_RE.search(a["text"]))
            if not about_us and a["locator"] in self_ref_paragraphs and CONTINUATION_RE.match(
                    a["text"].lstrip()):
                about_us = True
            if not about_us and not a["citations"]:
                # An unattributed frontier claim is a claim by this paper. "ForgeNet
                # outperforms all prior methods" names no one else and cites no one;
                # reading it as a statement about the literature is the reading that
                # lets it through.
                about_us = True
            if about_us:
                claims.append(a)
        if not claims:
            return out

        measured = [e for e in ledger
                    if str((e.get("provenance") or {}).get("arm") or e.get("arm") or "") == "sota"
                    and str(e.get("status", "")).upper() == "COMPLETED"
                    and (e.get("metrics") or {})]
        if measured:
            return out

        planned = [sp for sp in (specs or []) if isinstance(sp, dict) and (sp.get("sota") or {}).get("required")]
        attempted = [e for e in ledger
                     if str((e.get("provenance") or {}).get("arm") or e.get("arm") or "") == "sota"]
        if not planned:
            detail = ("no experiment in this project ever declared a state-of-the-art arm, so the "
                      "strongest method the field has is absent from the ledger entirely. The "
                      "comparison that was run was against the source paper's baseline.")
            remediation = ("re-run result-reproducer with --set sota_methods='[...]' so the "
                           "blueprint compiles a sota arm, or rewrite the claim to name the "
                           "baseline it actually beat")
        elif not attempted:
            detail = (f"{len(planned)} experiment(s) declare a state-of-the-art arm and not one "
                      f"run of it reached the ledger. The arm was planned and never executed.")
            remediation = ("supply impl.sota(seed, config) and re-run experiment-runner, or "
                           "rewrite the claim")
        else:
            classes = sorted({str(e.get("failure_class") or e.get("not_run_reason") or e.get("status"))
                              for e in attempted})
            detail = (f"{len(attempted)} state-of-the-art run(s) exist in the ledger and none "
                      f"completed with metrics ({', '.join(classes)}). A comparison against a "
                      f"method that did not run is not a comparison.")
            remediation = "fix the sota arm and re-run, or rewrite the claim"
        for a in claims:
            out.append({
                "kind": "sota_claim_without_measured_sota_arm",
                "claim_id": a["claim_id"], "locator": a["locator"],
                "detail": detail, "quote": a["text"][:220], "remediation": remediation})
        return out

    def _carry_upstream(self, stats, repro, cm) -> list[dict[str, Any]]:
        out = []
        for issue in (stats.get("issues") or stats.get("findings") or []):
            if not isinstance(issue, dict):
                continue
            sev = str(issue.get("severity", "")).upper()
            if sev in ("BLOCKER", "CRITICAL", "HIGH"):
                out.append({
                    "kind": "stats_audit", "claim_id": issue.get("claim_id"),
                    "locator": "stats_audit", "quote": "",
                    "detail": f"integrity-auditor flagged {issue.get('code', 'an issue')}: "
                              f"{issue.get('detail') or issue.get('message')}",
                    "remediation": issue.get("remediation", "resolve upstream in integrity-auditor")})
        if repro.get("level") == "RL0" and cm["mode"] != "CM_NONE":
            out.append({
                "kind": "mode_level_mismatch", "claim_id": None, "locator": "comparison_mode",
                "detail": f"the reproduction report is RL0 but the comparison mode is "
                          f"{cm['mode']}. These disagree; the weaker one governs.",
                "quote": "", "remediation": "re-run reproduction-fallback-planner"})
        return out

    def _gate(self, ctx, audited, counts, blockers, cm, manifest, ledger, refs) -> dict[str, Any]:
        checks = [
            {"name": "every_number_traced_to_the_ledger",
             "status": "FAIL" if counts["FABRICATED"] else "PASS",
             "detail": f"{sum(len(a['number_checks']) for a in audited)} number(s) checked against "
                       f"{len(_ledger_index(ledger))} ledger value(s)"},
            {"name": "citation_support_not_merely_existence",
             "status": "FAIL" if counts["NOT_SUPPORTED"] else "PASS",
             "detail": f"{sum(len(a['resolved_citations']) for a in audited)} resolvable citation(s) "
                       f"checked for a support edge, against {len(refs)} resolved reference(s)"},
            {"name": "comparison_mode_respected",
             "status": "FAIL" if any(b["kind"].startswith(("comparative", "missing_disclosure"))
                                     for b in blockers) else "PASS",
             "detail": f"mode={cm['mode']}, disclosure_required="
                       f"{bool((cm.get('disclosure_required') or {}).get('required'))}"},
            {"name": "sota_claims_have_a_measured_sota_arm",
             "status": "FAIL" if any(b["kind"] == "sota_claim_without_measured_sota_arm"
                                     for b in blockers) else "PASS",
             "detail": (f"{sum(1 for a in audited if SOTA_CLAIM_RE.search(a['text']) and SELF_REF_RE.search(a['text']))}"
                        f" frontier claim(s) about this work; "
                        f"{len([e for e in ledger if str((e.get('provenance') or {}).get('arm') or e.get('arm') or '') == 'sota' and str(e.get('status', '')).upper() == 'COMPLETED' and (e.get('metrics') or {})])}"
                        f" completed state-of-the-art run(s) in the ledger")},
            {"name": "every_claim_bound_to_the_spine",
             "status": "PASS" if manifest.get("every_paragraph_bound_to_a_claim") else "FAIL",
             "detail": f"unbound sentences: {sum(1 for a in audited if not a['claim_id'])}"},
            {"name": "prose_is_not_synthetic",
             "status": "FAIL" if manifest.get("_synthetic") else "PASS",
             "detail": "offline stub output is not research"},
        ]
        verdict = "BLOCK" if blockers else (
            "PASS_WITH_CONDITIONS" if counts["PARTIALLY_SUPPORTED"] else "PASS")
        return {
            "verdict": verdict,
            "submission_permitted": not blockers,
            "decided_at": time.time(),
            "run_id": ctx.run_id,
            "comparison_mode": cm["mode"],
            "counts": counts,
            "checks": checks,
            "blockers": blockers,
            "citation_existence_is_not_support": True,
            "model_consulted": False,
            "synthetic_inputs": bool(manifest.get("_synthetic")),
            "human_reviewed": False,
        }

    def _citation_md(self, audited, ref_idx, refs, cm) -> str:
        cited = [a for a in audited if a["citations"]]
        L = ["# Citation audit", "",
             "A citation is graded on whether the cited work supports the proposition it is "
             "attached to. Existence is necessary and not sufficient — a resolvable DOI on a claim "
             "it does not support is reported below as NOT_SUPPORTED, not as a passing citation.",
             "", f"- resolved reference pool: {len(refs)}",
             f"- sentences carrying citations: {len(cited)}",
             f"- comparison mode: **{cm['mode']}**", "",
             "| claim | citation | resolves | supports | verdict |", "|---|---|---|---|---|"]
        for a in cited:
            for key in a["citations"]:
                resolves = "yes" if any(r["key"] == key for r in a["resolved_citations"]) else "NO"
                supports = "yes" if any(
                    str(e.get("ref", "")).lower() == key.lower() for e in a["supporting_edges"]
                ) else "NO"
                L.append(f"| `{a['claim_id'] or '—'}` | `{key}` | {resolves} | {supports} | "
                         f"{a['verdict']} |")
        if not cited:
            L.append("| — | (no citations in the draft) | — | — | — |")
        unsupported = [a for a in audited
                       if a["verdict"] == "NOT_SUPPORTED" and a["resolved_citations"]]
        L += ["", "## Real citations that do not support their claim", ""]
        L += [f"- `{a['claim_id'] or a['locator']}`: {a['reason']}" for a in unsupported] or \
             ["- none"]
        return "\n".join(L)

    def _blockers_md(self, blockers, gate, cm) -> str:
        L = [f"# Submission blockers — verdict: {gate['verdict']}", "",
             f"Comparison mode: **{cm['mode']}**. "
             f"Submission permitted: **{gate['submission_permitted']}**.", ""]
        if not blockers:
            L += ["No blocker survived the audit.", "",
                  "This is not a statement that the paper is correct. It is a statement that every "
                  "number in it resolves to a recorded run, every citation has a recorded support "
                  "edge, and the disclosure its comparison mode requires is present."]
            return "\n".join(L)
        L += [f"{len(blockers)} blocker(s). Each one is a claim the evidence does not carry; none "
              f"is waivable by re-running this skill.", ""]
        for b in blockers:
            L += [f"## `{b['blocker_id']}` {b['kind']}", "",
                  f"- claim: `{b.get('claim_id') or '—'}` at `{b['locator']}`",
                  f"- finding: {b['detail']}"]
            if b.get("quote"):
                L.append(f"- text: > {b['quote']}")
            L += [f"- remediation: {b['remediation']}", ""]
        return "\n".join(L)


def _sections_of(draft: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "(preamble)"
    for line in draft.splitlines():
        m = SECTION_RE.match(line)
        if m:
            current = _norm(m.group("tex") or m.group("md"))
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _atomic_claims(draft: str) -> list[dict[str, Any]]:
    """Split the draft into the smallest units that can be true or false."""
    out: list[dict[str, Any]] = []
    section = "(preamble)"
    pending_claim: str | None = None
    buffer: list[str] = []
    paragraph = 0

    def flush():
        nonlocal buffer, pending_claim, paragraph
        text = _norm(" ".join(buffer))
        buffer = []
        if not text:
            return                      # blank lines must not clear a pending marker
        for s in _sentences(text):
            out.append({"claim_id": pending_claim, "section": section,
                        "text": s, "paragraph": paragraph})
        paragraph += 1
        pending_claim = None

    for raw in draft.splitlines():
        line = raw.strip()
        m = SECTION_RE.match(raw)
        if m:
            flush()
            section = _norm(m.group("tex") or m.group("md"))
            continue
        cm = CLAIM_MARKER_RE.search(line)
        if cm:
            flush()
            pending_claim = cm.group("cid")
            continue
        if not line:
            flush()
            continue
        if line.startswith("%") or line.startswith("<!--"):
            continue
        if any(line.startswith(c) for c in LATEX_STRUCTURE):
            continue
        buffer.append(line)
    flush()
    return out


# ==========================================================================
# review-simulator
# ==========================================================================
@register
class ReviewSimulator(Skill):
    """Simulate the review, then plan the revision — but never rebut a blocker."""

    name = "review-simulator"

    def execute(self, ctx: Context) -> SkillResult:
        gate = ctx.store.read(self.name, "integrity_gate")
        citation_audit = ctx.store.read(self.name, "citation_audit", default="")
        spine = ctx.store.read(self.name, "manuscript_spine")
        draft = ctx.store.read(self.name, "manuscript_draft")
        graph = ctx.store.read(self.name, "evidence_graph", default=[])
        ledger = ctx.store.read(self.name, "experiment_ledger", default=[])
        venue = ctx.external("target_venue", "unspecified venue")
        envelope = ctx.external("resource_envelope", {}) or {}
        warnings: list[str] = []

        points = self._grounded_points(gate, graph, ledger, spine)
        fallback = {"reviews": [
            {"reviewer_id": f"R{i}", "role": role, "recommendation": "unknown",
             "comments": [f"[synthetic] no model produced this review ({role})"], "_synthetic": True}
            for i, role in enumerate(("methods", "evidence", "venue-fit"), 1)]}
        data, synthetic = _ask_json(
            ctx, "You are a demanding but fair reviewer for a top venue.",
            self._prompt(venue, spine, gate, citation_audit, draft), fallback)
        reviews = data.get("reviews", []) if isinstance(data, dict) else list(data or [])
        for i, r in enumerate(reviews, 1):
            r.setdefault("reviewer_id", f"R{i}")
            r["_synthetic"] = synthetic

        triage = self._triage(points, envelope)
        blocked = gate["verdict"] == "BLOCK"
        report = {
            "target_venue": venue,
            "generated_at": time.time(),
            "submission_permitted_by_integrity_gate": gate["submission_permitted"],
            "recommendation": ("DO_NOT_SUBMIT" if blocked else "REVISE_THEN_SUBMIT"),
            "recommendation_rationale": (
                f"the integrity gate returned {gate['verdict']} with {len(gate['blockers'])} "
                f"blocker(s); reviewer opinion cannot override an unsupported claim"
                if blocked else
                "no blocker survived the audit; the remaining points are reviewer judgement"),
            "simulated_reviews": reviews,
            "grounded_points": points,
            "evidence_coverage": {
                "claims_in_spine": len(spine.get("claims", [])),
                "claims_with_support_edges": sum(1 for e in graph if e.get("support_edges")),
                "ledger_runs": len(ledger),
                "failed_runs": sum(1 for r in ledger
                                   if str(r.get("status", "")).lower() not in ("ok", "success",
                                                                               "completed")),
            },
            "_synthetic": synthetic,
            "_note": SYNTHETIC_NOTE if synthetic else None,
        }
        ctx.store.write(self.name, "review_report", report)
        ctx.store.write(self.name, "review_triage", triage)
        ctx.store.write(self.name, "revision_matrix", self._matrix(triage))
        ctx.store.write(self.name, "revision_experiment_plan", self._plan(triage, envelope))
        ctx.store.write(self.name, "response_to_reviewers", self._response(triage, gate, venue))
        ctx.store.write(self.name, "revision_backlog", self._backlog(triage))

        unfundable = [t for t in triage["items"] if t["decision"] == "CONCEDE_UNFUNDABLE"]
        if unfundable:
            warnings.append(
                f"{len(unfundable)} reviewer point(s) would need experiments the declared resource "
                f"envelope cannot fund. They are planned as concessions, not as promises: a "
                f"rebuttal that promises an unfundable experiment is a rebuttal that will fail.")
        if blocked:
            warnings.append(
                f"{len(gate['blockers'])} integrity blocker(s) are conceded verbatim in "
                f"response_to_reviewers. No rebuttal text was drafted for them — arguing for a "
                f"claim the evidence does not support is advocacy, not review response.")
        if synthetic:
            warnings.append("OFFLINE: the simulated reviews are placeholders. The grounded points "
                            "below them are real — they are derived from the gate and the ledger.")
        return SkillResult(self.name,
                           produced=["review_report", "review_triage", "revision_matrix",
                                     "revision_experiment_plan", "response_to_reviewers",
                                     "revision_backlog"],
                           warnings=warnings, synthetic=synthetic, next_state="REVIEWING",
                           detail={"points": len(points), "blocked": blocked,
                                   "conceded": sum(1 for t in triage["items"]
                                                   if t["decision"].startswith("CONCEDE"))})

    # ------------------------------------------------------------------
    def _grounded_points(self, gate, graph, ledger, spine) -> list[dict[str, Any]]:
        """Reviewer points that are facts, not opinions.

        These come from the integrity gate and the ledger, so they survive whether
        or not a model was available to write a review around them.
        """
        pts: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for b in gate.get("blockers", []):
            grouped.setdefault(b["kind"], []).append(b)
        for kind, group in grouped.items():
            # One point per kind of blocker. Seven copies of "this sentence has no
            # evidence" is one reviewer objection, and splitting it seven ways makes
            # the response document unreadable without adding a single fact.
            pts.append({"point_id": f"P-{len(pts) + 1:03d}", "severity": "BLOCKER",
                        "category": "INTEGRITY",
                        "source": f"integrity_gate:{','.join(b['blocker_id'] for b in group)}",
                        "claim_id": group[0].get("claim_id"),
                        "text": f"{len(group)} x {kind}: {group[0]['detail']}",
                        "remediation": group[0]["remediation"]})
        unsupported = [e for e in graph if not e.get("support_edges")]
        if unsupported:
            pts.append({"point_id": f"P-{len(pts) + 1:03d}", "severity": "MAJOR",
                        "category": "EVIDENCE", "source": "evidence_graph",
                        "claim_id": None,
                        "text": f"{len(unsupported)} claim(s) in the evidence graph still carry no "
                                f"support edge",
                        "remediation": "attach evidence or drop the claims"})
        failed = [r for r in ledger
                  if str(r.get("status", "")).lower() not in ("ok", "success", "completed")]
        if failed:
            pts.append({"point_id": f"P-{len(pts) + 1:03d}", "severity": "MAJOR",
                        "category": "EXPERIMENT", "source": "experiment_ledger",
                        "claim_id": None,
                        "text": f"{len(failed)} of {len(ledger)} recorded runs did not complete "
                                f"successfully",
                        "remediation": "re-run or report them as failures in the paper"})
        if not spine.get("claims"):
            pts.append({"point_id": f"P-{len(pts) + 1:03d}", "severity": "BLOCKER",
                        "category": "EVIDENCE", "source": "manuscript_spine", "claim_id": None,
                        "text": "the manuscript spine carries no claims",
                        "remediation": "there is no contribution to review"})
        return pts

    def _prompt(self, venue, spine, gate, citation_audit, draft) -> str:
        return (
            f"Review this manuscript for {venue}. Return "
            "{\"reviews\":[{\"reviewer_id\",\"role\",\"recommendation\",\"comments\":[...],"
            "\"questions\":[...]}]} with three reviewers.\n\n"
            f"The integrity gate has already returned {gate['verdict']}. Do not argue with it; "
            "review what remains.\n\n"
            f"THESIS: {spine.get('thesis')}\n"
            f"CLAIMS: {json.dumps(spine.get('claims', []))[:3000]}\n"
            f"CITATION AUDIT:\n{citation_audit[:2000]}\n\nDRAFT:\n{draft[:6000]}")

    def _triage(self, points, envelope) -> dict[str, Any]:
        budget_usd = float(envelope.get("budget_usd", 0) or 0)
        hours = float(envelope.get("hours", 0) or 0)
        gpus = int(envelope.get("gpus", 0) or 0)
        items = []
        for p in points:
            needs_experiment = p["category"] in ("EXPERIMENT",) or "re-run" in p["remediation"]
            fundable = (budget_usd > 0 or hours > 0 or gpus > 0)
            if p["severity"] == "BLOCKER":
                # A blocker is conceded. It is not triaged into "we will argue".
                decision, effort = "CONCEDE_BLOCKER", "n/a"
            elif needs_experiment and not fundable:
                decision, effort = "CONCEDE_UNFUNDABLE", "beyond envelope"
            elif needs_experiment:
                decision, effort = "ADDRESS_WITH_EXPERIMENT", "within envelope"
            else:
                decision, effort = "ADDRESS_IN_TEXT", "low"
            items.append({**p, "decision": decision, "effort": effort,
                          "rebuttal_permitted": p["severity"] != "BLOCKER"})
        return {"items": items, "envelope": envelope,
                "policy": "blockers are conceded, never rebutted; experiments are planned only "
                          "when the declared envelope can fund them"}

    def _matrix(self, triage) -> str:
        return _csv(["point_id", "severity", "category", "source", "decision", "effort",
                     "rebuttal_permitted", "remediation"],
                    [[t["point_id"], t["severity"], t["category"], t["source"], t["decision"],
                      t["effort"], t["rebuttal_permitted"], t["remediation"]]
                     for t in triage["items"]] or [["—", "—", "—", "—", "NONE", "—", "—", "—"]])

    def _plan(self, triage, envelope) -> str:
        doable = [t for t in triage["items"] if t["decision"] == "ADDRESS_WITH_EXPERIMENT"]
        refused = [t for t in triage["items"] if t["decision"] == "CONCEDE_UNFUNDABLE"]
        L = ["# Revision experiment plan", "",
             f"Declared resource envelope: `{json.dumps(envelope)}`.", ""]
        L += ["## Planned", ""] + (
            [f"- `{t['point_id']}` {t['remediation']} (— {t['text'][:120]})" for t in doable]
            or ["- none"])
        L += ["", "## Not planned — outside the envelope", ""]
        L += ([f"- `{t['point_id']}` {t['text'][:140]}" for t in refused] or ["- none"])
        if refused:
            L += ["", "These are carried into the paper as limitations rather than promised in a "
                      "rebuttal. Promising an experiment the project cannot fund is a commitment "
                      "the revision would fail to keep."]
        return "\n".join(L)

    def _response(self, triage, gate, venue) -> str:
        L = [f"# Response to reviewers — {venue}", ""]
        if gate["verdict"] == "BLOCK":
            L += ["> This response is drafted against a manuscript the integrity gate has blocked. "
                  "The blocked points below are conceded. No rebuttal text is generated for them: "
                  "arguing for a claim the evidence does not support is advocacy, not a response.",
                  ""]
        for t in triage["items"]:
            L += [f"## `{t['point_id']}` ({t['severity']}, {t['category']})", "",
                  f"**Reviewer point.** {t['text']}", ""]
            if not t["rebuttal_permitted"] and t.get("claim_id"):
                L += ["**Response.** We do not contest this point. The claim it concerns is not "
                      "supported by our evidence and has been removed or restated; "
                      f"remediation: {t['remediation']}.", ""]
            elif not t["rebuttal_permitted"]:
                L += ["**Response.** We do not contest this point. Our own pre-submission "
                      "integrity gate blocked the manuscript on it; "
                      f"remediation: {t['remediation']}.", ""]
            elif t["decision"] == "CONCEDE_UNFUNDABLE":
                L += ["**Response.** We agree this experiment would strengthen the paper and it is "
                      "outside our declared resource envelope. We state this as a limitation "
                      "rather than promise a run we cannot fund.", ""]
            else:
                L += [f"**Response.** Addressed: {t['remediation']}.", ""]
        if not triage["items"]:
            L += ["No grounded reviewer point was produced.", ""]
        return "\n".join(L)

    def _backlog(self, triage) -> str:
        order = {"CONCEDE_BLOCKER": 0, "ADDRESS_WITH_EXPERIMENT": 1, "ADDRESS_IN_TEXT": 2,
                 "CONCEDE_UNFUNDABLE": 3}
        items = sorted(triage["items"], key=lambda t: order.get(t["decision"], 9))
        L = ["# Revision backlog", "", "Blockers first: nothing else in this list matters while a "
             "claim in the paper is unsupported.", ""]
        L += [f"- [{t['decision']}] `{t['point_id']}` {t['text'][:140]}" for t in items] or ["- none"]
        return "\n".join(L)
