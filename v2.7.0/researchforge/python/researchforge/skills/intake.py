"""Intake: repository, sandbox, ingestion, paper model."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..errors import GateBlocked
from ..skill import Context, Skill, SkillResult, register

ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)


@register
class ProjectRepoManager(Skill):
    name = "project-repo-manager"

    def execute(self, ctx: Context) -> SkillResult:
        locator = ctx.external("paper_locator", required=True)
        intent = ctx.external("project_intent", "unspecified")
        root = ctx.project
        git = (root / ".git").exists()
        if not git:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "runtime@researchforge.local"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "ResearchForge"], cwd=root, check=True)
            (root / ".gitignore").write_text(
                # data and secrets must never be committable by accident
                "*.env\n.env\n**/secrets*\nsource/assets/\nanalysis/prepared/\n"
                "*.ckpt\n*.pt\n*.safetensors\n__pycache__/\n", encoding="utf-8")
        for d in ("source", "literature", "reproduction", "baseline", "ideas", "experiments",
                  "analysis", "evidence", "paper", "figures", "slides", "review", "release",
                  ".researchforge"):
            (root / d).mkdir(exist_ok=True)
        ctx.store.write(self.name, "quest_repo",
                        {"root": str(root), "initialized_git": not git,
                         "paper_locator": locator, "project_intent": intent,
                         "created_at": time.time()})
        ctx.store.write(self.name, "branch_map",
                        {"main": "main", "ideas": {}, "experiments": {},
                         "policy": "failed branches are retained until their findings are distilled"})
        ctx.store.write(self.name, "checkpoint_metadata", [
            {"ts": time.time(), "state": "INGESTED", "note": "project initialized",
             "run_id": ctx.run_id}])
        return SkillResult(self.name, produced=list(ctx.store and
                           ["quest_repo", "branch_map", "checkpoint_metadata"]),
                           next_state="INGESTED")


@register
class SandboxProvisioner(Skill):
    name = "sandbox-provisioner"

    def execute(self, ctx: Context) -> SkillResult:
        profile = ctx.external("security_profile", "no-network-untrusted-code")
        warn: list[str] = []
        has_docker = subprocess.run(["which", "docker"], capture_output=True).returncode == 0
        if not has_docker:
            warn.append(
                "docker not available: generated code would run in a venv on this host, which is "
                "NOT a security boundary. Untrusted-code execution stays disabled until a real "
                "sandbox exists.")
        freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                                capture_output=True, text=True, timeout=120)
        ctx.store.write(self.name, "environment_lock", freeze.stdout)
        ctx.store.write(self.name, "sandbox_container_config", {
            "engine": "docker" if has_docker else "none",
            "image": "python:3.11-slim" if has_docker else None,
            "limits": {"cpus": 4, "memory": "8g", "network": "none", "pids": 512,
                       "timeout_seconds": 14400},
            "mounts": {"project": "ro", "workdir": "rw"},
            "grader_context": "separate; hidden tests are never mounted into the agent context",
        })
        ctx.store.write(self.name, "sandbox_manifest", {
            "profile": profile,
            "isolation": "container" if has_docker else "none",
            "untrusted_code_execution_allowed": bool(has_docker),
            "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
            "warnings": warn,
        })
        return SkillResult(self.name,
                           produced=["environment_lock", "sandbox_container_config", "sandbox_manifest"],
                           warnings=warn)


@register
class PaperIngest(Skill):
    name = "paper-ingest"
    # visual fallback only fires when text extraction is unreliable
    optional_outputs = ("visual_notes", "paper_assets")

    def execute(self, ctx: Context) -> SkillResult:
        locator = str(ctx.external("paper_locator", required=True))
        raw, origin, media = self._fetch(locator, ctx)
        text, warnings, assets = self._extract(raw, media)

        if len(text.split()) < 300:
            warnings.append(
                f"only {len(text.split())} words extracted. Below 300 words this is an abstract "
                f"page, not a paper: claims cannot be anchored and every downstream novelty and "
                f"feasibility judgment would rest on the abstract alone.")

        ctx.store.write(self.name, "paper_source_file", raw if isinstance(raw, (str, bytes)) else str(raw))
        ctx.store.write(self.name, "normalized_text", text)
        locmap, paragraphs = self._locators(text)
        ctx.store.write(self.name, "locator_map", locmap)
        ctx.store.write(self.name, "source_manifest", {
            "locator": locator, "origin": origin, "media_type": media,
            "retrieved_at": time.time(),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "words": len(text.split()), "paragraphs": paragraphs,
            "arxiv_id": (ARXIV_RE.search(locator) or [None])[0] if ARXIV_RE.search(locator) else None,
            "doi": (DOI_RE.search(text).group(0) if DOI_RE.search(text) else None),
        })
        # This artifact used to print "none: ... anchored, ordered content" whenever
        # the warning list happened to be empty — an affirmative all-clear for checks
        # that were never run. In a system whose entire pitch is refusing to report
        # work it did not do, that was the worst defect in the codebase. It now
        # reports what was checked and what was not, and can no longer say "none".
        checked = [
            f"word count: {len(text.split())}",
            f"paragraph anchors: {paragraphs}",
            f"media type: {media}",
        ]
        not_checked = [
            "column order was NOT verified against the rendered page",
            "float/footnote/running-header splicing into body sentences was NOT detected",
            "section headings are matched by regex; unconventional layouts are missed silently",
            "figure and table captions are matched by regex; unnumbered captions are missed",
        ]
        body = ["# Layout and extraction", "", "## Checked", ""]
        body += [f"- {c}" for c in checked]
        body += ["", "## Warnings", ""]
        body += ([f"- {w}" for w in warnings] if warnings
                 else ["- no warning was raised by the checks that ran"])
        body += ["", "## NOT checked", "",
                 "These are not clean bills of health. Nothing below was tested, and a "
                 "downstream skill must not read their absence as evidence.", ""]
        body += [f"- {c}" for c in not_checked]
        ctx.store.write(self.name, "layout_warnings", "\n".join(body))
        if assets:
            ctx.store.write(self.name, "paper_assets", json.dumps(assets, indent=1))
        produced = ["paper_source_file", "normalized_text", "locator_map",
                    "source_manifest", "layout_warnings"] + (["paper_assets"] if assets else [])
        return SkillResult(self.name, produced=produced, warnings=warnings, next_state="INGESTED")

    # -- helpers -------------------------------------------------------
    def _fetch(self, locator: str, ctx: Context):
        p = Path(locator.replace("file://", ""))
        if p.exists():
            media = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
                     ".txt": "text/plain", ".md": "text/markdown"}.get(p.suffix.lower(), "text/plain")
            return (p.read_bytes() if media == "application/pdf" else p.read_text(encoding="utf-8", errors="replace")), f"file:{p}", media
        if locator.startswith(("http://", "https://")):
            if ctx.offline:
                raise GateBlocked("network", f"offline mode cannot fetch {locator}",
                                  "pass a local file, or run without --offline")
            import httpx
            r = httpx.get(locator, follow_redirects=True, timeout=60.0,
                          headers={"User-Agent": "ResearchForge/0.3 (+researchforge.local)"})
            if r.status_code != 200:
                raise GateBlocked("ingest", f"HTTP {r.status_code} for {locator}", "check the URL")
            return ((r.content if "pdf" in r.headers.get("content-type", "") else r.text),
                    locator, r.headers.get("content-type", "text/html").split(";")[0])
        raise GateBlocked("ingest", f"cannot resolve locator {locator!r}",
                          "supply a local path, file:// URL, or http(s) URL")

    def _extract(self, raw, media: str):
        warnings: list[str] = []
        assets: list[dict[str, Any]] = []
        if media == "application/pdf":
            from pypdf import PdfReader
            import io
            rd = PdfReader(io.BytesIO(raw if isinstance(raw, bytes) else raw.encode()))
            pages = [(pg.extract_text() or "") for pg in rd.pages]
            # pypdf hands back lone surrogates for math-italic glyphs (U+D835 range).
            # Left alone they crash the artifact store on `.encode()`, and a real
            # paper then looks like a runtime bug rather than an extraction problem.
            # Measured on 18 real PDFs: 1 in 18 was un-ingestable for this reason.
            fixed = 0
            for i, t in enumerate(pages):
                try:
                    t.encode("utf-8")
                except UnicodeEncodeError:
                    pages[i] = t.encode("utf-8", "replace").decode("utf-8")
                    fixed += 1
            if fixed:
                warnings.append(
                    f"{fixed} page(s) contained unpaired surrogates — almost always math "
                    f"glyphs — and were transcoded lossily. Formulae on those pages are "
                    f"unreliable and any claim anchored there should be re-read by hand.")
            empty = sum(1 for t in pages if len(t.strip()) < 40)
            if empty > len(pages) * 0.3:
                warnings.append(
                    f"{empty}/{len(pages)} pages yielded almost no text. This PDF is likely scanned "
                    f"or vector-only; the visual fallback path is required before these sections "
                    f"can be trusted.")
            return "\n\n".join(pages), warnings, assets
        if media in ("text/html", "application/xhtml+xml"):
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "lxml")
            for t in soup(["script", "style", "nav", "footer"]):
                t.decompose()
            for i, img in enumerate(soup.find_all("img")):
                assets.append({"kind": "image", "index": i, "src": img.get("src"),
                               "alt": img.get("alt")})
            if soup.find_all("table"):
                warnings.append(f"{len(soup.find_all('table'))} HTML tables flattened to text; "
                                f"numeric cells may lose their row/column identity")
            return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")), warnings, assets
        return str(raw), warnings, assets

    def _locators(self, text: str):
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        return ({"granularity": "paragraph",
                 "anchors": [{"anchor": f"p{i}", "offset": text.find(p), "chars": len(p),
                              "preview": p.strip()[:80]} for i, p in enumerate(paras)]},
                len(paras))


# The number separator must allow "1." and "1.1." with no space and roman numerals:
# ICML/PMLR style writes "1. Introduction", which the whitespace-only version missed
# entirely. Measured cost of that miss: 7 of 18 real papers detected exactly two
# sections, so every body claim was filed under "Abstract".
HEAD_RE = re.compile(
    r"^\s*(?:((?:\d+(?:\.\d+)*|[IVXLC]+)[.)]?)[\s.]*)?(abstract|introduction|related work|background|method(?:s|ology)?|"
    r"approach|model|architecture|experiments?|experimental setup|results?|discussion|analysis|"
    r"ablation[a-z ]*|limitations?|conclusions?|future work|references|appendix)\s*$",
    re.I | re.M)
CLAIM_CUE = re.compile(
    r"\b(we (?:propose|introduce|present|show|demonstrate|prove|find|observe|achieve|improve|outperform)|"
    r"our (?:method|model|approach|system) (?:achieves|outperforms|improves|reduces|yields)|"
    r"results? (?:show|indicate|demonstrate)|achieves? (?:a )?(?:new )?state[- ]of[- ]the[- ]art)\b", re.I)
NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|BLEU|F1|AUC|mAP|accuracy|points?|x\b)", re.I)


@register
class PaperModelBuilder(Skill):
    name = "paper-model-builder"

    def execute(self, ctx: Context) -> SkillResult:
        text = ctx.store.read(self.name, "normalized_text")
        locmap = ctx.store.read(self.name, "locator_map")
        sections = self._sections(text)
        claims = self._claims(text, sections)
        warnings: list[str] = []
        if len(sections) < 3:
            warnings.append(
                f"only {len(sections)} sections detected. Heading detection is regex-based and "
                f"fails on unconventional layouts; claim anchors below are therefore weak.")
        if not claims:
            warnings.append("no claims matched the cue patterns; the paper model is structural only")

        ctx.store.write(self.name, "section_map",
                        {"sections": sections, "detector": "regex/heading-v1",
                         "anchor_granularity": locmap.get("granularity")})
        ctx.store.write(self.name, "figure_table_index",
                        {"figures": self._captions(text, "figure"),
                         "tables": self._captions(text, "table")})
        model = {
            "paper_id": ctx.store.read(self.name, "normalized_text") and
                        hashlib.sha256(text.encode()).hexdigest()[:16],
            "title": self._title(text),
            "sections": [{"id": s["id"], "title": s["title"], "start": s["start"]} for s in sections],
            "claims": [{"claim_id": c["claim_id"], "text": c["text"], "locator": c["locator"],
                        "quantitative": c["quantitative"]} for c in claims],
            "methods": self._terms(text, ("architecture", "algorithm", "loss", "objective", "encoder", "decoder")),
            "datasets": self._terms(text, ("dataset", "corpus", "benchmark", "WMT", "ImageNet", "GLUE")),
            "metrics": sorted({m.split()[-1] for m in NUM_RE.findall(text)[:200]} |
                              set(re.findall(r"\b(BLEU|F1|AUC|mAP|accuracy|perplexity|WER)\b", text, re.I))),
            "limitations": [s["title"] for s in sections if "limitation" in s["title"].lower()],
            "locators": {"granularity": locmap.get("granularity"),
                         "anchor_count": len(locmap.get("anchors", []))},
        }
        ctx.store.write(self.name, "paper_model", model)
        atoms = self._atoms(claims)
        ctx.store.write(self.name, "contribution_atoms", {"atoms": atoms, "source": "claim-cue-v1"})
        ctx.store.write(self.name, "method_dependency_graph", {
            "nodes": [{"id": a["atom_id"], "label": a["summary"][:70]} for a in atoms],
            "edges": [{"from": atoms[i]["atom_id"], "to": atoms[i + 1]["atom_id"],
                       "kind": "textual_order", "confidence": "low",
                       "note": "ordering only; not a verified causal dependency"}
                      for i in range(len(atoms) - 1)],
        })
        return SkillResult(self.name,
                           produced=["section_map", "figure_table_index", "paper_model",
                                     "contribution_atoms", "method_dependency_graph"],
                           warnings=warnings, next_state="MODELED",
                           detail={"sections": len(sections), "claims": len(claims),
                                   "atoms": len(atoms)})

    # -- helpers -------------------------------------------------------
    def _title(self, text: str) -> str:
        for line in text.splitlines():
            s = line.strip()
            if 15 < len(s) < 200 and not s.lower().startswith(("arxiv", "abstract", "http")):
                return s
        return "(title not detected)"

    def _sections(self, text: str):
        out = []
        for i, m in enumerate(HEAD_RE.finditer(text)):
            out.append({"id": f"s{i}", "number": m.group(1), "title": m.group(2).strip(),
                        "start": m.start()})
        return out

    def _captions(self, text: str, kind: str):
        pat = re.compile(rf"^\s*{kind}\s+(\d+)\s*[:.]\s*(.{{0,200}})", re.I | re.M)
        return [{"id": f"{kind[0]}{m.group(1)}", "number": m.group(1),
                 "caption": m.group(2).strip(), "offset": m.start()} for m in pat.finditer(text)]

    def _claims(self, text: str, sections):
        def sect_of(off):
            cur = None
            for s in sections:
                if s["start"] <= off:
                    cur = s
            return cur["id"] if cur else "s?"
        out = []
        for i, sent in enumerate(re.split(r"(?<=[.!?])\s+", text)):
            s = sent.strip()
            if len(s) < 25 or len(s) > 600 or not CLAIM_CUE.search(s):
                continue
            off = text.find(s)
            out.append({"claim_id": f"C-{len(out)+1:03d}", "text": s,
                        "locator": {"section": sect_of(off), "offset": off},
                        "quantitative": bool(NUM_RE.search(s))})
            if len(out) >= 60:
                break
        return out

    def _terms(self, text: str, cues):
        found = []
        for c in cues:
            for m in re.finditer(rf"\b\w*{re.escape(c)}\w*\b", text, re.I):
                w = m.group(0)
                if w.lower() not in {x.lower() for x in found}:
                    found.append(w)
                if len(found) > 40:
                    return found
        return found

    def _atoms(self, claims):
        atoms = []
        for c in claims:
            if not c["quantitative"] and len(atoms) >= 12:
                continue
            atoms.append({
                "atom_id": f"A-{len(atoms)+1:03d}",
                "summary": c["text"][:220],
                "claim_ids": [c["claim_id"]],
                "kind": "empirical" if c["quantitative"] else "conceptual",
                "attackable_as": ["assumption", "evaluation_scope", "generality"],
                "locator": c["locator"],
            })
            if len(atoms) >= 20:
                break
        return atoms
