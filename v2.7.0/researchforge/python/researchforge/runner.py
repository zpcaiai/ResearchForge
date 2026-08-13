"""The Python side of the language boundary.

The TypeScript orchestrator invokes this as a subprocess, one skill per call,
passing a JSON request on stdin and receiving a JSON response on stdout.

A subprocess per skill rather than a long-lived RPC server is deliberate: skills
run untrusted generated code and can hang, leak memory or die. Process isolation
makes 'the skill crashed' a normal, observable outcome instead of a corrupted
shared runtime, and it makes every invocation independently reproducible from the
request JSON alone.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import traceback
from pathlib import Path

from . import skills as _skills  # noqa: F401  (registers implementations)
from .artifacts import ArtifactStore
from .errors import GateBlocked, HumanDecisionRequired, NotImplementedYet, ResearchForgeError
from .generated import ARTIFACTS, CONTRACT_DIGEST, SKILLS
from .providers import DEFAULT_PROVIDERS, FixtureTransport, QuotaLedger, build_model_provider
from .provenance import ProvenanceLog
from .licensing import check as licence_check
from .skill import Context, get


def _err(kind: str, message: str, **extra) -> dict:
    return {"ok": False, "error": {"kind": kind, "message": message, **extra}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="researchforge-py")
    ap.add_argument("command", choices=["run", "describe", "contract"])
    ap.add_argument("--skill")
    ap.add_argument("--project")
    ap.add_argument("--run-id", default="local")
    ap.add_argument("--mode", default="guided", choices=["guided", "auto", "analysis-only"])
    ap.add_argument("--model", default="anthropic")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--schemas", default=str(Path(__file__).resolve().parents[2] / "schemas"))
    ap.add_argument("--fixtures", default="")
    a = ap.parse_args(argv)

    if a.command == "contract":
        print(json.dumps({"ok": True, "digest": CONTRACT_DIGEST,
                          "skills": sorted(SKILLS), "artifacts": len(ARTIFACTS)}))
        return 0
    if a.command == "describe":
        c = SKILLS.get(a.skill)
        if not c:
            print(json.dumps(_err("unknown_skill", f"{a.skill} is not in the contract")))
            return 2
        print(json.dumps({"ok": True, "skill": a.skill, **dataclasses.asdict(c)}))
        return 0

    cfg = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            cfg = json.loads(raw)

    project = Path(a.project or cfg.get("project") or ".").resolve()
    project.mkdir(parents=True, exist_ok=True)
    prov = ProvenanceLog(project)
    store = ArtifactStore(project, Path(a.schemas), a.run_id, prov)
    quota = QuotaLedger(project / "literature" / "quota_ledger.jsonl")
    for name, limits in (cfg.get("quota") or {}).items():
        quota.budget(name, max_calls=limits.get("max_calls"), max_usd=limits.get("max_usd"))

    providers = list(DEFAULT_PROVIDERS)
    if a.fixtures:
        t = FixtureTransport(Path(a.fixtures))
        for p in providers:
            p._transport = t

    try:
        model = build_model_provider(a.model, offline=a.offline)
    except ResearchForgeError as e:
        print(json.dumps(_err("provider_unavailable", str(e))))
        return 3

    ctx = Context(project=project, run_id=a.run_id, mode=a.mode, store=store, prov=prov,
                  quota=quota, model=model, scholarly=providers,
                  config=cfg.get("config") or {}, offline=a.offline)

    # The gate lives here, not only in the orchestrator: this module is invokable
    # directly, so a check that lives only upstream checks nothing.
    gate = licence_check(a.skill)
    if gate is not None and not gate.get("permitted"):
        prov.append(__import__("researchforge.provenance", fromlist=["Event"]).Event(
            prov.now(), a.run_id, a.skill, "gate", detail={"kind": "licence", **gate}))
        print(json.dumps(_err("licence_required", gate["message"],
                              feature=gate["feature"], edition=gate["edition"])))
        return 14
    if gate is not None and gate.get("overridden"):
        prov.append(__import__("researchforge.provenance", fromlist=["Event"]).Event(
            prov.now(), a.run_id, a.skill, "gate",
            detail={"kind": "licence_override", **gate}))

    try:
        result = get(a.skill)(ctx)
    except HumanDecisionRequired as e:
        print(json.dumps({"ok": False, "needs_human": True,
                          "prompt": e.prompt, "options_artifact": e.options_artifact}))
        return 10
    except GateBlocked as e:
        print(json.dumps(_err("gate_blocked", str(e), gate=e.gate,
                              remediation=e.remediation)))
        return 11
    except NotImplementedYet as e:
        print(json.dumps(_err("not_implemented", str(e), skill=e.skill,
                              batch=e.batch, missing=e.missing)))
        return 12
    except ResearchForgeError as e:
        print(json.dumps(_err(type(e).__name__, str(e))))
        return 13
    except Exception as e:  # noqa: BLE001
        print(json.dumps(_err("internal", f"{type(e).__name__}: {e}",
                              traceback=traceback.format_exc()[-3000:])))
        return 20

    print(json.dumps({"ok": True, **dataclasses.asdict(result),
                      "quota": quota.snapshot()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
