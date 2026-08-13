"""Execution: scaffolding, bounded repair, branch search, and the ledger.

Two skills live here, and between them they own the moment where the system stops
describing research and starts doing it. That is exactly where fabrication becomes
cheap, so both are built around refusals.

`codebase-scaffolder` writes real modules and real tests derived from the
ExperimentSpecs, and then — as the consolidated debug-and-repair half — classifies
failures reported back through the experiment ledger into terminal and non-terminal
and STOPS. There is a hard cap on repair attempts and the cap is recorded, because
an agent that repairs forever is indistinguishable from one that never diagnoses.

`experiment-runner` reads the sandbox manifest before anything else. On this
deployment there is no GPU and `untrusted_code_execution_allowed` is False whenever
no container engine exists, and in that case the runner does not execute generated
code and does not produce numbers. Every planned run is still recorded — with
`status: "NOT_RUN"` and a named reason — because "we did not run this" is a
first-class result and must be as visible in the ledger as a measurement would be.
A ledger entry containing a number nobody measured is the single failure this whole
system exists to prevent; every branch below is written so that it cannot happen.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..errors import ContractViolation, GateBlocked
from ..generated import ARTIFACTS, INTERNAL_ARTIFACT_SPECS
from ..provenance import Event, sha256_file
from ..skill import Context, Skill, SkillResult, register

try:  # pyyaml is present in practice; JSON is a YAML subset, so a fallback is honest
    import yaml as _yaml
except Exception:  # pragma: no cover - exercised only on a stripped install
    _yaml = None

# ---------------------------------------------------------------------------
# Ledger vocabulary. These strings end up in `experiment_ledger.jsonl` and are
# read by data-analyst and integrity-auditor, so they are constants, not literals
# scattered through the code.
STATUS_NOT_RUN = "NOT_RUN"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_INVALID = "INVALID"

#: reasons a planned run was never executed. Each names a missing precondition,
#: never a property of the result — because there is no result.
NOT_RUN_REASONS = {
    "NO_ISOLATION": ("the sandbox manifest reports untrusted_code_execution_allowed=False, so "
                     "generated code was not executed. This host is not a security boundary."),
    "TERMINAL_CODE_DEFECT": "the codebase was classified terminal by debug-and-repair; running it would measure a bug",
    "EVALUATOR_MISSING": "no evaluator_code exists, so no metric could be computed even from a successful run",
    "NO_ENTRY_POINT": "codebase-scaffolder produced no entry point for this experiment",
    "TIMEBOX_EXHAUSTED": "the run-stage timebox was spent before this run started",
}

#: failure classes and whether a repair attempt could honestly fix them here.
#: Anything not in REPAIRABLE is terminal by construction: the scaffolder will not
#: guess at a method, install a package it cannot download, or retry a timeout.
REPAIRABLE = ("SYNTAX_ERROR", "COMPILE_ERROR", "NAME_ERROR", "LOCAL_IMPORT_ERROR")
TERMINAL_CLASSES = ("METHOD_NOT_IMPLEMENTED", "MISSING_DEPENDENCY", "NO_ISOLATION",
                    "EVALUATOR_MISSING", "TIMEOUT", "INVALID_METRICS", "RESOURCE_EXHAUSTED",
                    "TERMINAL_CODE_DEFECT", "NO_ENTRY_POINT", "TIMEBOX_EXHAUSTED", "UNKNOWN")

#: the marker the generated entry point prints its result on. Anything else on
#: stdout is treated as noise, so a run cannot smuggle a metric through by echoing.
RESULT_MARKER = "RF_RESULT "


# ---------------------------------------------------------------------------
# shared helpers
def _parse_structured(text: str) -> Any:
    if _yaml is not None:
        return _yaml.safe_load(text)
    return json.loads(text)


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _module_name(experiment_id: str) -> str:
    n = re.sub(r"[^0-9a-zA-Z_]+", "_", str(experiment_id)).strip("_").lower() or "unnamed"
    return n if n[0].isalpha() or n[0] == "_" else "exp_" + n


def _bytes_of(v: Any, default: int) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*", str(v or ""), re.I)
    if not m:
        return default
    return int(float(m.group(1)) * {"": 1, "k": 1 << 10, "m": 1 << 20,
                                    "g": 1 << 30, "t": 1 << 40}[m.group(2).lower()])


def _as_mapping(value: Any) -> dict[str, Any]:
    """Coerce a YAML-valued artifact to a mapping without inventing content.

    ArtifactStore returns `.yaml` artifacts as raw text; anything that does not
    parse into a mapping becomes an empty one, so a malformed container config
    yields no limits rather than plausible-looking defaults.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = _parse_structured(value)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _may_read(ctx: Context, skill: str, artifact_id: str) -> None:
    """Enforce the read side of the contract for reads the store cannot do itself.

    Directory- and glob-valued artifacts cannot go through ArtifactStore.read (it
    would try to read a directory, or a path with a literal '*' in it). The
    permission check still has to happen, so it is called directly rather than
    skipped — skipping it is how an undeclared dependency gets in.
    """
    ctx.store._may_read(skill, artifact_id)  # noqa: SLF001 - deliberate, see docstring


def _note_read(ctx: Context, skill: str, artifact_id: str, p: Path) -> None:
    ctx.prov.append(Event(ctx.prov.now(), ctx.run_id, skill, "artifact_read", artifact_id,
                          str(p.relative_to(ctx.project)) if p.is_relative_to(ctx.project) else str(p),
                          None, {}))


def _read_dir_artifact(ctx: Context, skill: str, artifact_id: str, default: Any = None) -> Any:
    """Directory-valued artifacts carry their provenance in `_manifest.json`."""
    _may_read(ctx, skill, artifact_id)
    p = ctx.store.path_for(artifact_id) / "_manifest.json"
    if not p.exists():
        return default
    _note_read(ctx, skill, artifact_id, p)
    return json.loads(p.read_text(encoding="utf-8"))


def _load_experiment_specs(ctx: Context, skill: str) -> list[dict[str, Any]]:
    """Resolve the `experiments/*.yaml` glob into the specs that actually exist.

    Files that do not carry an `experiment_id` are skipped rather than coerced:
    `experiments/` also holds the blueprint compiler's ablation plan, and silently
    treating that as an experiment would put a run in the ledger for something
    nobody specified.
    """
    _may_read(ctx, skill, "experiment_specs")
    root = ctx.project / "experiments"
    specs: list[dict[str, Any]] = []
    if not root.is_dir():
        return specs
    for p in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
        try:
            obj = _parse_structured(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for item in (obj if isinstance(obj, list) else [obj]):
            if isinstance(item, dict) and item.get("experiment_id"):
                item = dict(item)
                item["_source_path"] = str(p.relative_to(ctx.project))
                item["_source_digest"] = sha256_file(p)
                specs.append(item)
        _note_read(ctx, skill, "experiment_specs", p)
    return specs


def _evaluator_digest(ctx) -> str | None:
    """sha256 of the scoring code + spec, or None when no evaluator exists yet.

    None is honest: an experiment scored by nothing has no evaluator to change.
    A fabricated constant here would make the stability check pass vacuously.
    """
    import hashlib
    h = hashlib.sha256()
    got = False
    for aid in ("evaluator_code", "evaluator_spec"):
        try:
            p = ctx.store.path_for(aid)
        except Exception:  # noqa: BLE001
            continue
        if p.exists():
            h.update(p.read_bytes())
            got = True
    return h.hexdigest()[:16] if got else None


def _metric_names(spec: dict[str, Any]) -> list[str]:
    out = []
    for m in spec.get("metrics") or []:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict) and m.get("name"):
            out.append(str(m["name"]))
    return out


#: The arm names a spec may declare, in the order they are run. `sota` is the
#: current strongest published method; it is last because it is the one most
#: likely to be missing an implementation, and a timebox that runs out should
#: cost the comparison arm rather than the experiment's own two conditions.
ARM_KEYS = ("baseline", "candidate", "sota")


def _entry_arm(entry: dict[str, Any]) -> str:
    """Which arm a ledger row belongs to.

    Rows written before the runner invoked arms explicitly carry no `arm` at all.
    They were produced by the default invocation, which was the candidate, so that
    is what they are read as — stated here rather than left to a `.get(..., None)`
    that would quietly create a fourth, nameless arm in every aggregation.
    """
    prov = entry.get("provenance") or {}
    return str(prov.get("arm") or entry.get("arm") or "candidate")


def _spec_arms(spec: dict[str, Any]) -> list[str]:
    """The arms this spec actually declares, as the runner will invoke them.

    Kept in one place because the generated entry point derives its `--arm`
    choices by the same rule. If the two ever disagree, the runner asks for an arm
    the entry point rejects and the failure is reported as a runtime error, which
    is the least informative form the failure could take.
    """
    arms = [a for a in ARM_KEYS if isinstance(spec.get(a), dict)]
    return arms or ["candidate"]


def _metric_direction(spec: dict[str, Any], name: str) -> str | None:
    """Return 'maximize'/'minimize', or None when the spec never said.

    None is load-bearing: without a direction there is no defensible way to call
    one number better than another, so ranking refuses instead of assuming.
    """
    for m in spec.get("metrics") or []:
        if isinstance(m, dict) and str(m.get("name")) == name:
            d = str(m.get("direction") or m.get("goal") or "").lower()
            if d in ("max", "maximize", "higher_is_better", "higher"):
                return "maximize"
            if d in ("min", "minimize", "lower_is_better", "lower"):
                return "minimize"
    return None


# ===========================================================================
#  codebase-scaffolder  (consolidates codebase-scaffolder + debug-and-repair)
# ===========================================================================

_RUNTIME_MODULE = '''"""GENERATED by researchforge codebase-scaffolder. Do not edit by hand.

Shared runtime for generated experiment entry points.

Nothing in here produces a number. It provides the plumbing an experiment needs
(seeding, result emission, invalid-condition checking) and a single, loud failure
for the one thing a code generator must never supply: the method under test.
"""
from __future__ import annotations

import json
import random
import sys


RESULT_MARKER = "__RESULT_MARKER__"


class MethodNotImplemented(RuntimeError):
    """No implementation of the method under test exists.

    Raised instead of returning a plausible metric. A generator that invents the
    algorithm it is supposed to be measuring produces numbers about itself.
    """


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy  # noqa: PLC0415
    except Exception:
        return
    numpy.random.seed(seed % (2 ** 32 - 1))


def emit(payload: dict) -> None:
    sys.stdout.write(RESULT_MARKER + json.dumps(payload, sort_keys=True) + "\\n")
    sys.stdout.flush()


def check_invalid_conditions(conditions, metrics):
    """Return the conditions that fired, plus the ones that could not be checked.

    A condition expressed in prose is reported as unchecked rather than assumed
    satisfied; "we could not check" and "it passed" are different findings.
    """
    fired, unchecked = [], []
    ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
           "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
           "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
    for c in conditions or []:
        if isinstance(c, dict) and c.get("metric") in metrics and c.get("op") in ops:
            if ops[c["op"]](metrics[c["metric"]], c.get("value")):
                fired.append(c)
        else:
            unchecked.append(c)
    return fired, unchecked
'''.replace("__RESULT_MARKER__", RESULT_MARKER)


_EXPERIMENT_MODULE = '''"""GENERATED by researchforge codebase-scaffolder from ExperimentSpec __EXP_ID__.

Entry point for one experiment. Regenerating this file from the spec is the only
repair action the scaffolder performs, so hand edits here will be overwritten;
put the method under test in `impl.py` next to this file instead.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rf_runtime import MethodNotImplemented, check_invalid_conditions, emit, seed_everything

SPEC = json.loads(__SPEC_JSON__)
EXPERIMENT_ID = SPEC["experiment_id"]
METRICS = __METRICS__
SEEDS = __SEEDS__
INVALID_CONDITIONS = SPEC.get("invalid_conditions") or []
# The arms this spec actually declares. Hard-coding baseline|candidate meant a
# spec could carry a state-of-the-art arm that the runner refused to accept on
# the command line, so the plan required a comparison the harness could not run.
ARMS = [a for a in ("baseline", "candidate", "sota") if isinstance(SPEC.get(a), dict)] \
    or ["baseline", "candidate"]


def load_impl():
    """Load the sibling `impl.py` that supplies the method under test.

    Absent by design: the spec says what to measure, not how the candidate works.
    """
    try:
        import impl  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    return impl


def run(seed: int, arm: str = "candidate") -> dict:
    """Run one arm for one seed and return {metric_name: number}.

    Raises MethodNotImplemented when no implementation is available. It does not
    fall back to a default, a constant or a random draw.
    """
    seed_everything(seed)
    impl = load_impl()
    if impl is None or not hasattr(impl, arm):
        raise MethodNotImplemented(
            f"{EXPERIMENT_ID}: no impl.{arm}(seed, config) was found next to this module. "
            f"The scaffolder refuses to synthesize the method under test; supply impl.py "
            f"or accept that this experiment cannot be measured.")
    config = SPEC.get(arm) or {}
    out = getattr(impl, arm)(seed, config)
    if not isinstance(out, dict):
        raise TypeError(f"impl.{arm} returned {type(out).__name__}, expected a metric dict")
    undeclared = sorted(set(out) - set(METRICS))
    if undeclared:
        raise ValueError(f"impl.{arm} returned metrics not declared in the spec: {undeclared}")
    return {k: float(v) for k, v in out.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"experiment {EXPERIMENT_ID}")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--arm", default="candidate", choices=ARMS)
    a = ap.parse_args(argv)
    started = time.time()
    try:
        metrics = run(a.seed, a.arm)
    except MethodNotImplemented as e:
        emit({"experiment_id": EXPERIMENT_ID, "seed": a.seed, "arm": a.arm,
              "status": "NOT_IMPLEMENTED", "metrics": {}, "error": str(e),
              "failure_class": "METHOD_NOT_IMPLEMENTED"})
        return 3
    except Exception as e:  # noqa: BLE001 - the ledger needs the class, not a traceback
        emit({"experiment_id": EXPERIMENT_ID, "seed": a.seed, "arm": a.arm,
              "status": "FAILED", "metrics": {}, "error": f"{type(e).__name__}: {e}",
              "failure_class": "RUNTIME_ERROR"})
        return 1
    fired, unchecked = check_invalid_conditions(INVALID_CONDITIONS, metrics)
    emit({"experiment_id": EXPERIMENT_ID, "seed": a.seed, "arm": a.arm,
          "status": "INVALID" if fired else "COMPLETED", "metrics": metrics,
          "invalid_conditions_fired": fired, "invalid_conditions_unchecked": unchecked,
          "seconds": round(time.time() - started, 4)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


_TEST_MODULE = '''"""GENERATED tests for experiment __EXP_ID__.

These test the contract between the spec and the entry point. The load-bearing
one is `test_missing_implementation_raises_instead_of_returning_a_number`: it is
the only thing standing between "no method exists" and a metric in the ledger.
"""
from __future__ import annotations

import json

import pytest

from __MODULE__ import experiment
from rf_runtime import MethodNotImplemented, check_invalid_conditions


def test_entry_point_exposes_the_documented_interface():
    assert callable(experiment.run) and callable(experiment.main)


def test_declared_metrics_match_the_spec():
    assert experiment.METRICS == __METRICS__
    assert experiment.SPEC["experiment_id"] == __EXP_ID_REPR__


def test_declared_seeds_match_the_spec():
    assert experiment.SEEDS == __SEEDS__


def test_missing_implementation_raises_instead_of_returning_a_number(monkeypatch):
    monkeypatch.setattr(experiment, "load_impl", lambda: None)
    with pytest.raises(MethodNotImplemented):
        experiment.run(experiment.SEEDS[0] if experiment.SEEDS else 0)


def test_undeclared_metrics_are_rejected(monkeypatch):
    class _Impl:
        @staticmethod
        def candidate(seed, config):
            return {"__not_a_declared_metric__": 1.0}

    monkeypatch.setattr(experiment, "load_impl", lambda: _Impl)
    with pytest.raises(ValueError, match="not declared"):
        experiment.run(0)


def test_invalid_conditions_are_checked_not_assumed():
    fired, unchecked = check_invalid_conditions(
        [{"metric": "m", "op": ">", "value": 1.0}, "expressed in prose"], {"m": 2.0})
    assert len(fired) == 1 and len(unchecked) == 1
'''


@register
class CodebaseScaffolder(Skill):
    """Generate the experiment code, then diagnose failures once and stop.

    The repair half is deliberately small. It regenerates a module from its spec
    and nothing else, because that is the only edit whose correctness follows from
    an artifact rather than from a guess.
    """

    name = "codebase-scaffolder"

    #: hard cap on repair attempts, counted cumulatively across runs. Chosen small
    #: on purpose: the third identical failure is information about the diagnosis,
    #: not about the code, and more attempts only bury it.
    REPAIR_ATTEMPT_CAP = 3

    def execute(self, ctx: Context) -> SkillResult:
        warnings: list[str] = []
        specs = _load_experiment_specs(ctx, self.name)
        if not specs:
            raise GateBlocked(
                "no_experiment_specs",
                "codebase-scaffolder found no ExperimentSpec in experiments/*.yaml. There is "
                "nothing to implement, and generating a plausible-looking project for an "
                "experiment nobody specified would put untethered code in the worktree.",
                "run research-blueprint-compiler first, or supply experiments/<id>.yaml "
                "conforming to schemas/ExperimentSpec.schema.json",
            )
        blueprint_text = ctx.store.read(self.name, "research_blueprint", default="")
        if not blueprint_text:
            warnings.append("research_blueprint is absent; modules were derived from the "
                            "ExperimentSpecs alone and no stage-level structure was imposed.")
        baseline = ctx.store.read(self.name, "baseline_assets", default={})

        code_root = ctx.project / "code"
        tests_root = code_root / "tests"
        code_root.mkdir(parents=True, exist_ok=True)
        tests_root.mkdir(parents=True, exist_ok=True)

        generated: list[dict[str, Any]] = []
        entry_points: dict[str, str] = {}
        generated.append(self._write(code_root / "rf_runtime.py", _RUNTIME_MODULE, ctx, "shared"))
        generated.append(self._write(tests_root / "conftest.py", self._conftest(), ctx, "shared"))

        for spec in specs:
            mod = _module_name(spec["experiment_id"])
            pkg = code_root / mod
            pkg.mkdir(parents=True, exist_ok=True)
            generated.append(self._write(pkg / "__init__.py",
                                         f'"""GENERATED package for {spec["experiment_id"]}."""\n',
                                         ctx, spec["experiment_id"]))
            generated.append(self._write(pkg / "experiment.py", self._experiment_src(spec),
                                         ctx, spec["experiment_id"]))
            generated.append(self._write(tests_root / f"test_{mod}.py", self._test_src(spec, mod),
                                         ctx, spec["experiment_id"]))
            entry_points[spec["experiment_id"]] = str((pkg / "experiment.py").relative_to(ctx.project))

        # ---- static verification -------------------------------------
        # py_compile parses and byte-compiles; it does not execute. This skill does
        # not consume sandbox_manifest, so it has no basis on which to decide that
        # running generated code is safe, and therefore does not run any.
        compile_failures = self._compile_check([g["path"] for g in generated], ctx)

        # ---- debug and repair (bounded) ------------------------------
        prior = self._read_repair_commits(ctx)
        used = {}
        for rec in prior:
            used[rec.get("experiment_id", "?")] = used.get(rec.get("experiment_id", "?"), 0) + 1
        ledger = ctx.store.read(self.name, "experiment_ledger", default=[])
        diagnoses, new_commits, warns = self._diagnose_and_repair(
            ctx, specs, entry_points, ledger, compile_failures, used)
        warnings += warns

        commits = prior + new_commits
        ctx.store.write(self.name, "repair_commits", "".join(
            json.dumps(c, sort_keys=True) + "\n" for c in commits))

        terminal = bool(diagnoses) and all(d["terminal"] for d in diagnoses)
        capped = [d["experiment_id"] for d in diagnoses if d.get("stop_reason") == "repair_attempt_cap_reached"]
        stop_reason = ("no_failures_reported" if not diagnoses else
                       "repair_attempt_cap_reached" if capped else
                       "terminal_classification" if terminal else "repair_attempted_awaiting_rerun")
        ctx.store.write(self.name, "debug_terminal_status", {
            "terminal": terminal,
            "stop_reason": stop_reason,
            "repair_attempt_cap": self.REPAIR_ATTEMPT_CAP,
            "repair_attempts_used_total": len(commits),
            "repair_attempts_used_per_experiment": {k: used.get(k, 0) for k in
                                                    sorted({d["experiment_id"] for d in diagnoses} | set(used))},
            "experiments_at_cap": sorted(capped),
            "assessed_at_run_id": ctx.run_id,
            "policy": ("repair is capped and the cap is recorded. An unbounded repair loop turns a "
                       "diagnosable defect into wall-clock and hides the diagnosis; when the cap is "
                       "reached this skill stops and hands the failure to a human."),
        })
        ctx.store.write(self.name, "failure_diagnosis", {
            "assessed_at_run_id": ctx.run_id,
            "sources": ["static verification of generated modules",
                        "experiment_ledger feedback from prior runs"],
            "repair_attempt_cap": self.REPAIR_ATTEMPT_CAP,
            "diagnoses": diagnoses,
            "note": ("Absence of a diagnosis means no failure was reported, not that the code "
                     "works: nothing here has been executed by this skill."),
        })

        ctx.store.write(self.name, "implementation_plan",
                        self._plan(specs, generated, entry_points, baseline, blueprint_text, ctx))
        ctx.store.write(self.name, "code_tests", {
            "root": "code/tests/",
            "framework": "pytest",
            "files": [g for g in generated if g["path"].startswith("code/tests/")],
            "covers": sorted(entry_points),
            "not_covered": ("the method under test: there is no implementation to test, and a test "
                            "asserting a value the scaffolder chose would test the scaffolder"),
            "run_id": ctx.run_id,
        })
        ctx.store.write(self.name, "code_worktree", {
            "root": "code/",
            "generated_at": time.time(),
            "run_id": ctx.run_id,
            "files": generated,
            "entry_points": entry_points,
            "entry_point_protocol": {
                "invocation": ("python <entry_point> --seed <int> "
                               "[--arm baseline|candidate|sota]  (the arms the spec declares; "
                               "`--arm sota` exists only where a state-of-the-art arm was planned)"),
                "result_line": f"{RESULT_MARKER}<json>",
                "exit_codes": {"0": "completed or invalid", "1": "runtime failure",
                               "3": "method not implemented"},
            },
            "executed_by_this_skill": False,
            "execution_note": ("codebase-scaffolder does not consume sandbox_manifest and therefore "
                               "has no basis for deciding that running generated code is safe. "
                               "Verification here is static; execution is experiment-runner's call."),
        })
        return SkillResult(
            self.name,
            produced=["code_worktree", "code_tests", "implementation_plan",
                      "failure_diagnosis", "debug_terminal_status", "repair_commits"],
            warnings=warnings, next_state="CODE_SCAFFOLDED",
            detail={"experiments": len(specs), "files": len(generated),
                    "terminal": terminal, "stop_reason": stop_reason,
                    "repair_attempts_used_total": len(commits),
                    "repair_attempt_cap": self.REPAIR_ATTEMPT_CAP})

    # -- generation ----------------------------------------------------
    def _write(self, p: Path, src: str, ctx: Context, origin: str) -> dict[str, Any]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
        return {"path": str(p.relative_to(ctx.project)), "sha256": _sha256_text(src),
                "bytes": len(src.encode()), "from_spec": origin, "generated": True}

    def _experiment_src(self, spec: dict[str, Any]) -> str:
        public = {k: v for k, v in spec.items() if not k.startswith("_")}
        return (_EXPERIMENT_MODULE
                .replace("__SPEC_JSON__", json.dumps(json.dumps(public, sort_keys=True)))
                .replace("__METRICS__", repr(_metric_names(spec)))
                .replace("__SEEDS__", repr([int(s) for s in (spec.get("seeds") or []) if str(s).lstrip("-").isdigit()]))
                .replace("__EXP_ID__", str(spec["experiment_id"])))

    def _test_src(self, spec: dict[str, Any], mod: str) -> str:
        return (_TEST_MODULE
                .replace("__MODULE__", mod)
                .replace("__METRICS__", repr(_metric_names(spec)))
                .replace("__SEEDS__", repr([int(s) for s in (spec.get("seeds") or []) if str(s).lstrip("-").isdigit()]))
                .replace("__EXP_ID_REPR__", repr(str(spec["experiment_id"])))
                .replace("__EXP_ID__", str(spec["experiment_id"])))

    def _conftest(self) -> str:
        return ('"""GENERATED. Puts the generated worktree on sys.path for pytest."""\n'
                "import sys\n"
                "from pathlib import Path\n\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n")

    def _compile_check(self, rel_paths: list[str], ctx: Context) -> dict[str, str]:
        """Byte-compile every generated file. Parsing is not execution."""
        import py_compile

        out: dict[str, str] = {}
        for rel in rel_paths:
            p = ctx.project / rel
            if p.suffix != ".py":
                continue
            try:
                py_compile.compile(str(p), cfile=str(p) + "c", doraise=True)
            except py_compile.PyCompileError as e:
                out[rel] = str(e)[-400:]
            finally:
                Path(str(p) + "c").unlink(missing_ok=True)
        return out

    # -- debug and repair ----------------------------------------------
    def _read_repair_commits(self, ctx: Context) -> list[dict[str, Any]]:
        """Prior attempts are read back so the cap survives a re-invocation.

        A cap that resets every run is not a cap; it is a slower unbounded loop.
        """
        raw = ctx.store.read(self.name, "repair_commits", default="")
        out = []
        for line in str(raw).splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def _classify(self, entry: dict[str, Any]) -> str:
        explicit = entry.get("failure_class") or entry.get("not_run_reason")
        if explicit in REPAIRABLE or explicit in TERMINAL_CLASSES:
            return str(explicit)
        blob = " ".join(str(entry.get(k, "")) for k in ("error", "stderr_tail", "detail"))
        prov = entry.get("provenance") or {}
        blob += " " + str(prov.get("stderr_tail", ""))
        for pat, cls in ((r"SyntaxError|IndentationError", "SYNTAX_ERROR"),
                         (r"NameError", "NAME_ERROR"),
                         (r"ModuleNotFoundError: No module named '(rf_runtime|impl|exp_)", "LOCAL_IMPORT_ERROR"),
                         (r"ModuleNotFoundError|ImportError", "MISSING_DEPENDENCY"),
                         (r"MethodNotImplemented", "METHOD_NOT_IMPLEMENTED"),
                         (r"MemoryError|Killed|OOM", "RESOURCE_EXHAUSTED")):
            if re.search(pat, blob):
                return cls
        return "UNKNOWN"

    def _diagnose_and_repair(self, ctx, specs, entry_points, ledger, compile_failures, used):
        """One diagnosis pass, one bounded repair pass, then stop.

        There is no loop here on purpose. Whether a repair worked is observable
        only after experiment-runner runs again, so re-running this skill is the
        iteration mechanism — and `used` carries the count forward so iteration
        terminates.
        """
        warnings: list[str] = []
        diagnoses: list[dict[str, Any]] = []
        commits: list[dict[str, Any]] = []
        by_spec = {s["experiment_id"]: s for s in specs}

        failures: dict[str, dict[str, Any]] = {}
        for rel, msg in compile_failures.items():
            owner = next((eid for eid, ep in entry_points.items() if ep == rel), rel)
            failures[owner] = {"experiment_id": owner, "failure_class": "COMPILE_ERROR",
                               "error": msg, "evidence": f"static verification of {rel}"}
        for entry in ledger:
            if entry.get("status") in (STATUS_COMPLETED, None):
                continue
            eid = str(entry.get("experiment_id", "?"))
            failures[eid] = {"experiment_id": eid,
                             "failure_class": self._classify(entry),
                             "error": str(entry.get("error") or entry.get("not_run_reason") or "")[:400],
                             "evidence": f"experiment_ledger entry (status={entry.get('status')})"}

        for eid in sorted(failures):
            f = failures[eid]
            cls = f["failure_class"]
            attempts = used.get(eid, 0)
            repairable = cls in REPAIRABLE
            at_cap = attempts >= self.REPAIR_ATTEMPT_CAP
            d = {"experiment_id": eid, "failure_class": cls, "evidence": f["evidence"],
                 "error": f["error"], "repair_attempts_used": attempts,
                 "repair_attempt_cap": self.REPAIR_ATTEMPT_CAP}
            if not repairable:
                d.update(terminal=True, stop_reason="not_repairable_by_codegen",
                         root_cause=self._root_cause(cls),
                         remediation=self._remediation(cls),
                         next_action_owner="human")
            elif at_cap:
                d.update(terminal=True, stop_reason="repair_attempt_cap_reached",
                         root_cause=(f"{cls} survived {attempts} regeneration attempts, which is the "
                                     f"cap. Surviving regeneration means the defect is in the spec or "
                                     f"the environment, not in the generated text."),
                         remediation="review the ExperimentSpec and the environment lock by hand",
                         next_action_owner="human")
                warnings.append(f"{eid}: repair attempt cap ({self.REPAIR_ATTEMPT_CAP}) reached; "
                                f"classified terminal and stopped rather than retrying.")
            else:
                spec = by_spec.get(eid)
                repaired_path = None
                if spec is not None:
                    pkg = ctx.project / "code" / _module_name(eid)
                    pkg.mkdir(parents=True, exist_ok=True)
                    tgt = pkg / "experiment.py"
                    tgt.write_text(self._experiment_src(spec), encoding="utf-8")
                    repaired_path = str(tgt.relative_to(ctx.project))
                used[eid] = attempts + 1
                commits.append({"ts": time.time(), "run_id": ctx.run_id, "experiment_id": eid,
                                "attempt": attempts + 1, "cap": self.REPAIR_ATTEMPT_CAP,
                                "failure_class": cls, "action": "regenerate_module_from_spec",
                                "path": repaired_path,
                                "verified": False,
                                "note": ("regeneration is the only repair this skill performs; whether "
                                         "it worked is decided by the next experiment-runner pass, "
                                         "not asserted here")})
                d.update(terminal=False, stop_reason="repair_attempted_awaiting_rerun",
                         root_cause=self._root_cause(cls),
                         remediation="re-run experiment-runner; the outcome decides, not this skill",
                         next_action_owner="experiment-runner")
            diagnoses.append(d)
        return diagnoses, commits, warnings

    def _root_cause(self, cls: str) -> str:
        return {
            "METHOD_NOT_IMPLEMENTED": ("no impl.py supplies the method under test. This is not a bug "
                                       "to repair: the scaffolder must not invent the algorithm whose "
                                       "behaviour the experiment is meant to measure."),
            "MISSING_DEPENDENCY": "a package the code imports is absent and cannot be fetched from here",
            "NO_ISOLATION": "execution was never attempted; there is no defect to diagnose yet",
            "EVALUATOR_MISSING": "no evaluator exists, so nothing could have scored the run",
            "TIMEOUT": "the run exceeded its timebox; repeating it would consume the same budget again",
            "INVALID_METRICS": "the run emitted metrics the spec does not declare",
            "RESOURCE_EXHAUSTED": "the run exceeded its memory or process limits",
            "SYNTAX_ERROR": "generated source did not parse",
            "COMPILE_ERROR": "generated source did not byte-compile",
            "NAME_ERROR": "generated source referenced an undefined name",
            "LOCAL_IMPORT_ERROR": "a generated module could not import a sibling generated module",
        }.get(cls, "unclassified failure; refusing to guess at a cause or to retry blindly")

    def _remediation(self, cls: str) -> str:
        return {
            "METHOD_NOT_IMPLEMENTED": "write code/<module>/impl.py exposing baseline(seed, config) and candidate(seed, config)",
            "MISSING_DEPENDENCY": "add the package to the environment lock and re-provision the sandbox",
            "NO_ISOLATION": "provision a container sandbox, then re-run experiment-runner",
            "EVALUATOR_MISSING": "run evaluator-builder",
            "TIMEOUT": "raise the timebox deliberately, or reduce the experiment's scale in its spec",
            "INVALID_METRICS": "reconcile impl.py's return value with the spec's metrics list",
            "RESOURCE_EXHAUSTED": "raise the sandbox limits deliberately, or reduce the run's scale",
        }.get(cls, "diagnose by hand; this skill will not retry it")

    # -- plan ----------------------------------------------------------
    def _plan(self, specs, generated, entry_points, baseline, blueprint_text, ctx) -> str:
        L = ["# Implementation plan", "",
             f"Generated by codebase-scaffolder in run `{ctx.run_id}` from "
             f"{len(specs)} ExperimentSpec(s).", "",
             "## Blueprint -> module mapping", ""]
        for s in specs:
            mod = _module_name(s["experiment_id"])
            L += [f"### `{s['experiment_id']}` -> `code/{mod}/`",
                  f"- hypothesis: {str(s.get('hypothesis', 'unstated'))[:200]}",
                  f"- metrics: {', '.join(_metric_names(s)) or 'none declared'}",
                  f"- seeds: {s.get('seeds') or 'none declared'}",
                  f"- datasets: {s.get('datasets') or 'none declared'}",
                  f"- entry point: `{entry_points[s['experiment_id']]}`",
                  f"- tests: `code/tests/test_{mod}.py`", ""]
        L += ["## What is deliberately not generated", "",
              "- **The method under test.** Each module loads `impl.py` and raises "
              "`MethodNotImplemented` when it is absent. A generator that writes the algorithm "
              "it is also measuring produces evidence about itself.",
              "- **Metric values.** Nothing in the generated code returns a number that did not "
              "come from `impl.py`, and undeclared metric names are rejected at the boundary.",
              "- **Dataset acquisition.** Datasets named in the specs "
              f"({sorted({str(d) for s in specs for d in (s.get('datasets') or [])})}) are not "
              "downloaded here; the sandbox owns network policy.", "",
              "## Baseline assets carried in", ""]
        repos = (baseline or {}).get("repos") or []
        L += ([f"- {r.get('url')} (rank {r.get('rank')}, {r.get('kind')})" for r in repos]
              or ["- none: result-reproducer located no baseline repository, so nothing was vendored"])
        L += ["", "## Verification performed", "",
              "- Static byte-compilation of every generated file.",
              "- No execution: this skill does not read `sandbox_manifest` and so cannot judge "
              "whether running generated code is safe here. `experiment-runner` makes that call.", ""]
        if blueprint_text:
            L += ["## Blueprint digest", "",
                  f"`{_sha256_text(blueprint_text)[:16]}` ({len(blueprint_text)} bytes)", ""]
        return "\n".join(L)


# ===========================================================================
#  experiment-runner
#  (consolidates experiment-tree-search + experiment-ledger + artifact-provenance)
# ===========================================================================

@register
class ExperimentRunner(Skill):
    """Run what can honestly be run; record everything else as not run.

    The order of the checks below is the design. The sandbox manifest is consulted
    before any code is looked at, so there is no path through this skill in which
    generated code executes without an explicit, recorded permission to do so.
    """

    name = "experiment-runner"

    def execute(self, ctx: Context) -> SkillResult:
        warnings: list[str] = []
        started = time.time()

        try:
            sandbox = ctx.store.read(self.name, "sandbox_manifest")
        except ContractViolation as e:
            # 'we could not find out whether execution is permitted' must not collapse
            # into 'execution is permitted'. There is no default here.
            raise GateBlocked(
                "sandbox_unknown",
                "experiment-runner cannot find sandbox_manifest. Without it there is no record of "
                "whether executing generated code is permitted here, and 'unknown' must never "
                "default to 'allowed'.",
                "run sandbox-provisioner first",
            ) from e
        specs = _load_experiment_specs(ctx, self.name)
        if not specs:
            raise GateBlocked(
                "no_experiment_specs",
                "no ExperimentSpec exists, so there are no planned runs to execute or to record. "
                "An empty ledger written here would look like a completed run stage.",
                "run research-blueprint-compiler to emit experiments/*.yaml",
            )

        allowed = bool(sandbox.get("untrusted_code_execution_allowed"))
        isolation = sandbox.get("isolation", "unknown")
        container = _as_mapping(ctx.store.read(self.name, "sandbox_container_config", default={}))
        env_lock = ctx.store.read(self.name, "environment_lock", default="")
        acceptance = ctx.store.read(self.name, "acceptance_criteria", default="")
        dag = ctx.store.read(self.name, "blueprint_dag", default={})
        blueprint = ctx.store.read(self.name, "research_blueprint", default="")
        diagnosis = ctx.store.read(self.name, "failure_diagnosis", default={})
        terminal_status = ctx.store.read(self.name, "debug_terminal_status", default={})
        evaluator = ctx.store.read(self.name, "evaluator_code", default="")
        evaluator_spec = ctx.store.read(self.name, "evaluator_spec", default="")
        lineage = ctx.store.read(self.name, "idea_lineage_graph", default={})
        mutants = ctx.store.read(self.name, "mutant_candidates", default=[])
        repair_commits = ctx.store.read(self.name, "repair_commits", default="")
        impl_plan = ctx.store.read(self.name, "implementation_plan", default="")
        worktree = _read_dir_artifact(ctx, self.name, "code_worktree", default={}) or {}
        code_tests = _read_dir_artifact(ctx, self.name, "code_tests", default={}) or {}
        hidden_tests = _read_dir_artifact(ctx, self.name, "hidden_tests", default={}) or {}
        entry_points = worktree.get("entry_points") or {}

        # Every declared input is read, and what was read is recorded. An input the
        # contract names but the skill ignores is a dependency nobody can audit.
        inputs_seen = {
            "research_blueprint_sha256": _sha256_text(str(blueprint)) if blueprint else None,
            "acceptance_criteria_sha256": _sha256_text(str(acceptance)) if acceptance else None,
            "implementation_plan_sha256": _sha256_text(str(impl_plan)) if impl_plan else None,
            "evaluator_code_sha256": _sha256_text(str(evaluator)) if str(evaluator).strip() else None,
            "evaluator_spec_present": bool(str(evaluator_spec).strip()),
            "environment_lock_sha256": _sha256_text(str(env_lock)),
            "generated_test_files": len((code_tests or {}).get("files", [])),
            "prior_repair_commits": len([l for l in str(repair_commits).splitlines() if l.strip()]),
            "idea_lineage_nodes": len((lineage or {}).get("nodes", []) or []) if isinstance(lineage, dict) else 0,
            "mutant_candidates": len(mutants) if isinstance(mutants, list) else 0,
            # only the count: hidden tests stay out of the agent's context by design,
            # and reading them here would defeat the point of hiding them.
            "hidden_test_files_declared": len((hidden_tests or {}).get("files", [])),
            "hidden_test_contents_read": False,
        }

        timebox = float(ctx.external("experiment_timebox_seconds", 1800))
        per_run_timeout = float(ctx.external("experiment_run_timeout_seconds", 300))
        limits = container.get("limits") or {}

        # ---- the refusal, stated once, applied to every planned run ----
        blocked: str | None = None
        if not allowed:
            blocked = "NO_ISOLATION"
            warnings.append(
                "sandbox_manifest.untrusted_code_execution_allowed is False (isolation="
                f"{isolation!r}). No generated code was executed and no metric was produced. "
                "Every planned run is in the ledger with status NOT_RUN; that is the result.")
        elif terminal_status.get("terminal"):
            blocked = "TERMINAL_CODE_DEFECT"
            warnings.append(
                "debug-and-repair classified the codebase terminal ("
                f"{terminal_status.get('stop_reason')}). Running it would measure a known defect, "
                "so the planned runs are recorded NOT_RUN instead.")
        elif not str(evaluator).strip():
            blocked = "EVALUATOR_MISSING"
            warnings.append(
                "no evaluator_code exists. A run could have produced output, but nothing could "
                "have scored it, so no run was started and none is recorded as measured.")

        # ---- plan, execute (or refuse), record -------------------------
        entries: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        root_id = f"root:{ctx.run_id}"
        nodes.append({"node_id": root_id, "kind": "root", "parent": None,
                      "label": "blueprint", "status": "EXPANDED",
                      "dag_stages": len((dag or {}).get("stages", []) or []) if isinstance(dag, dict) else 0})

        sandbox_prov = {"isolation": isolation, "profile": sandbox.get("profile"),
                        "untrusted_code_execution_allowed": allowed,
                        "host": sandbox.get("host"), "limits": limits,
                        "environment_lock_sha256": _sha256_text(str(env_lock)),
                        "network_enforced_by": isolation if allowed else "n/a (nothing executed)"}

        for spec in specs:
            eid = str(spec["experiment_id"])
            branch_id = f"branch:{eid}"
            seeds = [int(s) for s in (spec.get("seeds") or []) if str(s).lstrip("-").isdigit()] or [0]
            entry_rel = entry_points.get(eid)
            nodes.append({"node_id": branch_id, "kind": "experiment", "parent": root_id,
                          "experiment_id": eid, "hypothesis": str(spec.get("hypothesis", ""))[:200],
                          "metrics": _metric_names(spec), "seeds": seeds,
                          "entry_point": entry_rel, "status": "PLANNED"})
            # Every arm the spec declares, not just the candidate. The runner used to
            # invoke the entry point with no --arm at all, so it always measured the
            # default one: a spec could declare a baseline and a state-of-the-art
            # condition, report `conditions: 3` in its resources, and produce a ledger
            # containing one arm. Every comparison built on that ledger was a
            # comparison of the candidate with itself.
            arms = _spec_arms(spec)
            for arm in arms:
                for seed in seeds:
                    reason = blocked or (None if entry_rel else "NO_ENTRY_POINT")
                    if reason is None and (time.time() - started) >= timebox:
                        reason = "TIMEBOX_EXHAUSTED"
                    attempt_id = f"{eid}:arm={arm}:seed={seed}:{ctx.run_id}"
                    if reason is not None:
                        entry = self._not_run_entry(ctx, spec, seed, attempt_id, reason,
                                                    sandbox_prov, entry_rel, diagnosis, arm=arm)
                    else:
                        entry = self._run(ctx, spec, seed, attempt_id, entry_rel, limits,
                                          per_run_timeout, sandbox_prov, arm=arm)
                    entries.append(entry)
                    nodes.append({"node_id": f"run:{attempt_id}", "kind": "run",
                                  "parent": branch_id, "experiment_id": eid, "seed": seed,
                                  "arm": arm, "status": entry["status"],
                                  "metrics": entry["metrics"],
                                  "not_run_reason": entry.get("not_run_reason")})
            # A condition the spec designs but names no arm for cannot be invoked, so
            # it will never appear in the ledger. Saying so here is the difference
            # between a contrast that was measured and found small, and one that was
            # never run — which read identically in every downstream artifact before.
            planned_conditions = ((spec.get("resources") or {}).get("conditions"))
            if isinstance(planned_conditions, int) and planned_conditions > len(arms):
                warnings.append(
                    f"{eid} designs {planned_conditions} condition(s) but only {len(arms)} of them "
                    f"({', '.join(arms)}) is a runnable arm. The remaining "
                    f"{planned_conditions - len(arms)} will not be measured, and no result from "
                    f"this experiment can rest on them.")
            elif isinstance(planned_conditions, int) and planned_conditions < len(arms):
                warnings.append(
                    f"{eid} declares {len(arms)} runnable arm(s) ({', '.join(arms)}) but budgets "
                    f"for {planned_conditions}. The run will cost more than the plan said.")

        # mutants are recorded as unexpanded frontier, never as results
        for i, m in enumerate(mutants if isinstance(mutants, list) else []):
            nodes.append({"node_id": f"mutant:{m.get('id', i)}", "kind": "mutant_candidate",
                          "parent": root_id, "status": "UNEXPANDED",
                          "label": str(m.get("summary") or m.get("id") or f"mutant-{i}")[:160],
                          "reason_unexpanded": ("no ExperimentSpec compiles this mutant into a "
                                                "falsifiable run; it is frontier, not evidence")})

        # ---- ledger: append, never rewrite ----------------------------
        ctx.store.append_jsonl(self.name, "experiment_ledger", entries)

        starved = [e for e in entries if e.get("not_run_reason") == "TIMEBOX_EXHAUSTED"]
        if starved:
            by_arm: dict[str, int] = {}
            for e in starved:
                by_arm[e.get("arm", "?")] = by_arm.get(e.get("arm", "?"), 0) + 1
            # Arms run in a fixed order, so the timebox always eats the last one
            # first — which is `sota`, the arm that makes a comparison mean
            # anything. That is a defensible priority and an indefensible silence:
            # every other NOT_RUN reason warned, this one did not, and the missing
            # contrast simply vanished from `contrasts` with no trace.
            warnings.append(
                f"the run-stage timebox ({timebox}s) expired with "
                f"{len(starved)} planned run(s) unstarted: "
                f"{', '.join(f'{a}x{n}' for a, n in sorted(by_arm.items()))}. Arms are executed "
                f"in the order {', '.join(ARM_KEYS)}, so the timebox is spent last-first and the "
                f"comparison arm is the one it takes. Any contrast involving those arms is absent "
                f"from ranked_branches rather than negative.")

        measured = [e for e in entries if e["status"] == STATUS_COMPLETED and e["metrics"]]

        # ---- void conditions, evaluated rather than recited -------------
        # Each spec's invalid_conditions now carry machine-evaluable predicates.
        # Evaluating them here is what makes a spec falsifiable in practice: a run
        # whose void conditions fired is not a negative result, it is nothing, and
        # it must not be allowed to nominate a best candidate.
        from ..invalid_conditions import evaluate_all
        full_ledger = ctx.store.read(self.name, "experiment_ledger", default=[]) + entries
        arts = {}
        try:
            arts["baseline_assets"] = ctx.store.read(self.name, "baseline_assets", default=None)
        except Exception:  # noqa: BLE001 - optional input; absence is itself reported
            pass
        validity = [evaluate_all(sp, full_ledger, arts) for sp in specs]
        # Narrowing conditions are evaluated by the same machinery and kept in a
        # separate list, because a fired one limits what the result may claim while
        # leaving the measurement intact. Folding them into invalid_conditions made
        # a declared-but-unmeasured state-of-the-art arm delete six completed runs.
        narrowing = []
        for sp in specs:
            conds = sp.get("narrowing_conditions") or []
            if not conds:
                continue
            ev = evaluate_all({"experiment_id": sp["experiment_id"],
                               "invalid_conditions": conds}, full_ledger, arts)
            narrowing.append({"experiment_id": sp["experiment_id"], "fired": ev["fired"],
                              "unchecked": ev["unchecked"],
                              "narrows": [c.get("narrows_to") for c in conds
                                          if c.get("code") in ev["fired"]]})
            for code in ev["fired"]:
                c = next((x for x in conds if x.get("code") == code), {})
                warnings.append(
                    f"{sp['experiment_id']} is NARROWED by {code}: {c.get('narrows_to', '')} "
                    f"The runs stand; what they may be claimed to show does not.")
        void_ids = {v["experiment_id"] for v in validity if v["void"]}
        unfalsifiable = [v["experiment_id"] for v in validity if not v["falsifiable"]]
        for v in validity:
            if v["void"]:
                warnings.append(
                    f"{v['experiment_id']} is VOID: {', '.join(v['fired'])}. A void run is not a "
                    f"negative result — it is nothing, and nothing may be concluded from it.")
        if unfalsifiable:
            warnings.append(
                f"{len(unfalsifiable)} experiment(s) had no evaluable void condition at all "
                f"({', '.join(unfalsifiable)}): they cannot be invalidated, so they cannot be "
                f"confirmed either.")

        ctx.store.write(self.name, "experiment_tree", {
            "validity": validity, "void_experiments": sorted(void_ids),
            "narrowing": narrowing,
            "narrowing_is_not_void": ("a narrowed experiment measured what it measured; only the "
                                      "scope of the claim it supports is reduced"),
            "run_id": ctx.run_id, "root": root_id, "nodes": nodes,
            "expansion_policy": ("breadth over the compiled ExperimentSpecs. No node is expanded on "
                                 "the basis of an unmeasured score, so with nothing measured the "
                                 "tree is a plan, not a search."),
            "measured_runs": len(measured), "planned_runs": len(entries),
            "inputs_seen": inputs_seen,
        })

        # a void experiment's runs are excluded before ranking, not annotated after
        live = [e for e in entries if e.get("experiment_id") not in void_ids]
        live_measured = [e for e in measured if e.get("experiment_id") not in void_ids]
        ranked, best, rank_warn = self._rank(
            [sp for sp in specs if sp.get("experiment_id") not in void_ids], live, live_measured)
        warnings += rank_warn
        if void_ids and isinstance(best, dict):
            best.setdefault("excluded_void_experiments", sorted(void_ids))
        ctx.store.write(self.name, "ranked_branches", ranked)
        ctx.store.write(self.name, "best_candidate", best)

        # ---- provenance and manifest ----------------------------------
        manifest, mwarn = self._manifest(ctx, entries)
        warnings += mwarn
        ctx.store.write(self.name, "artifact_manifest", manifest)
        prov_note = self._consolidate_provenance(ctx, entries, manifest, inputs_seen)

        return SkillResult(
            self.name,
            produced=["experiment_ledger", "experiment_tree", "ranked_branches",
                      "best_candidate", "artifact_manifest", "provenance_log"],
            warnings=warnings, next_state="EXPERIMENTS_RECORDED",
            detail={"planned_runs": len(entries), "executed": len([e for e in entries
                                                                   if e["provenance"]["executed"]]),
                    "not_run": len([e for e in entries if e["status"] == STATUS_NOT_RUN]),
                    "measured_runs": len(measured), "isolation": isolation,
                    "untrusted_code_execution_allowed": allowed,
                    "provenance_log": prov_note})

    # -- ledger entries -------------------------------------------------
    def _base_entry(self, ctx, spec, seed, attempt_id, status, sandbox_prov, entry_rel,
                    arm="candidate"):
        return {
            "run_id": ctx.run_id,
            "experiment_id": str(spec["experiment_id"]),
            "attempt_id": attempt_id,
            "seed": seed,
            "arm": arm,
            "status": status,
            "metrics": {},
            "artifacts": [],
            "warnings": [],
            "provenance": {
                "skill": self.name,
                "recorded_at": time.time(),
                "spec_path": spec.get("_source_path"),
                "spec_sha256": spec.get("_source_digest"),
                "entry_point": entry_rel,
                "declared_metrics": _metric_names(spec),
                "arm": arm,
                # Stamped per run so EVALUATOR_CHANGED_MID_RUN can actually be
                # evaluated. Without it the condition reports UNCHECKED forever,
                # which reads like "fine" and means "we never looked" — the more
                # dangerous of the two possible errors.
                "evaluator_digest": _evaluator_digest(ctx),
                "sandbox": sandbox_prov,
                "executed": False,
            },
        }

    def _not_run_entry(self, ctx, spec, seed, attempt_id, reason, sandbox_prov, entry_rel,
                       diagnosis, arm="candidate"):
        """A planned run that was never started.

        `metrics` stays `{}`. There is no partial credit, no placeholder, no zero:
        a zero is a measurement and nothing was measured.
        """
        e = self._base_entry(ctx, spec, seed, attempt_id, STATUS_NOT_RUN, sandbox_prov, entry_rel,
                             arm=arm)
        e["not_run_reason"] = reason
        e["failure_class"] = reason
        e["provenance"]["not_run_reason"] = reason
        e["provenance"]["reason_detail"] = NOT_RUN_REASONS.get(reason, "unspecified precondition failure")
        e["provenance"]["evidence"] = ("sandbox_manifest" if reason == "NO_ISOLATION"
                                       else "debug_terminal_status" if reason == "TERMINAL_CODE_DEFECT"
                                       else "code_worktree entry_points" if reason == "NO_ENTRY_POINT"
                                       else "evaluator_code" if reason == "EVALUATOR_MISSING"
                                       else "run-stage timebox")
        e["warnings"] = [f"NOT_RUN ({reason}): {e['provenance']['reason_detail']}",
                         "no metric is recorded for this entry because none was measured"]
        if reason == "TERMINAL_CODE_DEFECT":
            e["provenance"]["diagnoses"] = [d for d in (diagnosis or {}).get("diagnoses", [])
                                            if d.get("experiment_id") == str(spec["experiment_id"])]
        return e

    def _run(self, ctx, spec, seed, attempt_id, entry_rel, limits, timeout, sandbox_prov,
             arm="candidate"):
        """Execute one seed of one arm of one experiment, under limits and a timebox."""
        e = self._base_entry(ctx, spec, seed, attempt_id, STATUS_FAILED, sandbox_prov, entry_rel,
                             arm=arm)
        entry_path = ctx.project / entry_rel
        cmd = [sys.executable, str(entry_path), "--seed", str(seed), "--arm", arm]
        workdir = Path(tempfile.mkdtemp(prefix="rf-run-"))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": str(workdir),
               "TMPDIR": str(workdir),
               "PYTHONHASHSEED": str(seed),          # determinism is part of the record
               "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": str(ctx.project / "code")}
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               cwd=str(entry_path.parent), env=env,
                               preexec_fn=_rlimits(limits, timeout))
            out, err, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            e.update(status=STATUS_TIMEOUT, failure_class="TIMEOUT")
            e["provenance"].update(executed=True, command=cmd, seconds=round(time.time() - t0, 3),
                                   timeout_seconds=timeout, limits=limits)
            e["warnings"] = [f"exceeded the per-run timeout of {timeout}s; no metric was produced"]
            shutil.rmtree(workdir, ignore_errors=True)
            return e
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        e["provenance"].update(executed=True, command=cmd, exit_code=rc,
                               seconds=round(time.time() - t0, 3), timeout_seconds=timeout,
                               limits=limits, stdout_sha256=_sha256_text(out),
                               stderr_tail=err[-1000:],
                               entry_point_sha256=sha256_file(entry_path) if entry_path.exists() else None)
        payload = None
        for line in out.splitlines():
            if line.startswith(RESULT_MARKER):
                try:
                    payload = json.loads(line[len(RESULT_MARKER):])
                except json.JSONDecodeError:
                    payload = None
        if payload is None:
            e["failure_class"] = "UNKNOWN"
            e["warnings"] = ["the run emitted no parseable result line; nothing is recorded as measured"]
            return e

        declared = set(_metric_names(spec))
        raw = payload.get("metrics") or {}
        bad = [k for k, v in raw.items() if k not in declared or not isinstance(v, (int, float))
               or isinstance(v, bool)]
        if bad:
            # A number the spec never declared has no meaning to compare against, and a
            # non-numeric "metric" cannot be aggregated. Both are refused at the boundary.
            e.update(status=STATUS_INVALID, failure_class="INVALID_METRICS")
            e["warnings"] = [f"run returned undeclared or non-numeric metrics {sorted(bad)}; "
                             f"no metric from this run entered the ledger"]
            return e

        status = {"COMPLETED": STATUS_COMPLETED, "INVALID": STATUS_INVALID,
                  "NOT_IMPLEMENTED": STATUS_FAILED, "FAILED": STATUS_FAILED}.get(
                      str(payload.get("status")), STATUS_FAILED)
        e["status"] = status
        e["metrics"] = {k: float(v) for k, v in raw.items()} if status == STATUS_COMPLETED else {}
        if payload.get("failure_class"):
            e["failure_class"] = str(payload["failure_class"])
        if payload.get("error"):
            e["error"] = str(payload["error"])[:600]
        if payload.get("invalid_conditions_fired"):
            e["warnings"].append("an invalid_condition from the spec fired; the run is not a result")
        if payload.get("invalid_conditions_unchecked"):
            e["warnings"].append(f"{len(payload['invalid_conditions_unchecked'])} invalid_condition(s) "
                                 f"could not be checked mechanically and were not assumed satisfied")
        if status == STATUS_COMPLETED:
            e["artifacts"] = [{"path": entry_rel, "sha256": e["provenance"].get("entry_point_sha256"),
                               "role": "entry_point"}]
        return e

    # -- ranking ---------------------------------------------------------
    def _contrasts(self, arms: dict, directions: dict[str, str | None] | None = None) -> dict:
        """Candidate minus each control, per metric, with the arms' spread beside it.

        A difference of means with no dispersion beside it is the number that gets
        quoted; putting `n` and the observed range next to it is the cheapest way to
        keep the reader from reading four seeds as a result. No significance is
        claimed here — integrity-auditor owns that, and duplicating it would give the
        pipeline two answers to one question.
        """
        cand = arms.get("candidate") or {}
        out: dict[str, dict] = {}
        for other in ("baseline", "sota"):
            stats = arms.get(other)
            if not stats:
                out[f"candidate_vs_{other}"] = {
                    "metrics": {},
                    "absent_because": (f"the {other} arm produced no measurement in this "
                                       f"experiment. A contrast that was never computed must not "
                                       f"read as one that came out flat."),
                }
                continue
            per_metric = {}
            for k, c in cand.items():
                o = stats.get(k)
                if not o:
                    continue
                d = (directions or {}).get(k)
                delta = c["mean"] - o["mean"]
                per_metric[k] = {
                    "candidate_mean": c["mean"], f"{other}_mean": o["mean"],
                    "difference": delta,
                    # Signed difference alone reads as "better" to every human and
                    # every downstream agent. On a minimize metric a +0.70 against
                    # the state of the art is a candidate 85% WORSE, and this
                    # artifact is published.
                    "direction": d,
                    "candidate_is": ("undetermined: the spec declared no direction for this "
                                     "metric, so no sign of this difference means better"
                                     if not d else
                                     "better" if (delta > 0) == (d == "maximize") else
                                     "worse" if delta else "identical"),
                    "n": {"candidate": c["n"], other: o["n"]},
                    "observed_range": {"candidate": [c["min"], c["max"]],
                                       other: [o["min"], o["max"]]},
                }
            if not per_metric:
                out[f"candidate_vs_{other}"] = {
                    "metrics": {},
                    "absent_because": ("one of the two arms produced no measurement on any shared "
                                       "metric, so this contrast was not computed. Absent is not "
                                       "zero and is not negative."),
                }
            if per_metric:
                out[f"candidate_vs_{other}"] = {
                    "metrics": per_metric,
                    "significance_tested": False,
                    "caveat": ("a difference of means over seeds; whether it exceeds the "
                               "dispersion is decided by integrity-auditor, not here"),
                }
        return out

    def _rank(self, specs, entries, measured):
        """Rank branches by measured evidence only.

        With no measurements there is no ranking. The alternative — ordering by
        spec order, by hypothesis length, by anything — manufactures a preference
        and then hands it to the manuscript as a finding.
        """
        warnings: list[str] = []
        by_spec = {str(s["experiment_id"]): s for s in specs}
        branches = []
        def agg(rows):
            vals: dict[str, list[float]] = {}
            for r in rows:
                for k, v in (r["metrics"] or {}).items():
                    vals.setdefault(k, []).append(float(v))
            return {k: {"n": len(v), "mean": sum(v) / len(v), "min": min(v), "max": max(v)}
                    for k, v in vals.items()}

        for eid, spec in by_spec.items():
            runs = [e for e in entries if e["experiment_id"] == eid]
            # Per arm, never pooled. Pooling was invisible while the runner only ever
            # executed the candidate; the moment the baseline and state-of-the-art arms
            # actually run, a pooled mean is the average of a method and the thing it is
            # being compared against — a number that describes no condition that exists
            # and that would then have been ranked, reported and cited.
            by_arm = {}
            for r in runs:
                by_arm.setdefault(_entry_arm(r), []).append(r)
            arms = {a: agg(rows) for a, rows in sorted(by_arm.items())}
            # The branch's own score is its candidate arm: ranking answers "which
            # direction do we carry forward", and that is a question about the method,
            # not about its controls.
            branches.append({
                "branch_id": f"branch:{eid}", "experiment_id": eid, "rank": None,
                "status_counts": {s: sum(1 for r in runs if r["status"] == s)
                                  for s in sorted({r["status"] for r in runs})},
                "arms": arms,
                "scored_arm": "candidate",
                "measured_metrics": arms.get("candidate", {}),
                "contrasts": self._contrasts(
                    arms, {m: _metric_direction(spec, m) for m in _metric_names(spec)}),
                "not_run_reasons": sorted({r.get("not_run_reason") for r in runs
                                           if r.get("not_run_reason")}),
            })

        if not measured:
            ranked = {"ranking_possible": False, "primary_metric": None, "branches": branches,
                      "reason": ("no run produced a measurement, so no branch can be ordered above "
                                 "another. Ranking unmeasured branches would invent the comparison "
                                 "the experiments were supposed to make."),
                      "requires": "at least one COMPLETED run with a declared metric"}
            best = {"selected": None,
                    "reason": ranked["reason"],
                    "candidates_considered": [b["experiment_id"] for b in branches],
                    "not_run_reasons": sorted({r for b in branches for r in b["not_run_reasons"]}),
                    "requires": ranked["requires"]}
            warnings.append("best_candidate is null: nothing was measured, so there is no best.")
            return ranked, best, warnings

        # a primary metric only counts if the spec said which way is better
        primary = None
        for b in branches:
            spec = by_spec[b["experiment_id"]]
            for name in _metric_names(spec):
                if name in b["measured_metrics"] and _metric_direction(spec, name):
                    primary = (name, _metric_direction(spec, name))
                    break
            if primary:
                break
        if primary is None:
            # Two very different causes reach here and used to produce the same
            # message. A branch is scored by its CANDIDATE arm, so an experiment
            # whose candidate was starved by the timebox has empty measured_metrics
            # while other arms measured — and the operator was told to go and
            # declare a metric direction that was already declared.
            no_candidate = [b["experiment_id"] for b in branches
                            if not b["measured_metrics"]
                            and any(v for v in (b.get("arms") or {}).values())]
            if no_candidate:
                ranked = {"ranking_possible": False, "primary_metric": None,
                          "branches": branches,
                          "reason": (f"{len(no_candidate)} branch(es) measured something but not "
                                     f"their candidate arm ({', '.join(no_candidate)}), and a "
                                     f"branch is scored by its candidate. Ranking on a control "
                                     f"arm would order the baselines."),
                          "requires": "a completed candidate arm for at least one experiment"}
                warnings.append(
                    f"refusing to rank: {', '.join(no_candidate)} measured only non-candidate "
                    f"arm(s). The metric direction is not the problem.")
            else:
                ranked = {"ranking_possible": False, "primary_metric": None, "branches": branches,
                          "reason": ("measurements exist but no metric declares a direction, so "
                                     "'better' is undefined. Assuming higher-is-better is a "
                                     "research claim, not a default."),
                          "requires": "a metrics entry of the form {name, direction: "
                                      "maximize|minimize}"}
                warnings.append("metrics were measured but no direction was declared; "
                                "refusing to rank.")
            best = {"selected": None, "reason": ranked["reason"],
                    "candidates_considered": [b["experiment_id"] for b in branches],
                    "requires": ranked["requires"]}
            return ranked, best, warnings

        name, direction = primary
        scored = [b for b in branches if name in b["measured_metrics"]]
        scored.sort(key=lambda b: b["measured_metrics"][name]["mean"], reverse=(direction == "maximize"))
        for i, b in enumerate(scored):
            b["rank"] = i + 1
        ranked = {"ranking_possible": True, "primary_metric": {"name": name, "direction": direction},
                  "branches": branches,
                  "reason": f"ordered by mean {name} across completed seeds ({direction})",
                  "caveat": ("this is an ordering over measured means with no variance test; "
                             "integrity-auditor decides whether the difference is real")}
        top = scored[0]
        best = {"selected": top["experiment_id"], "branch_id": top["branch_id"],
                "primary_metric": {"name": name, "direction": direction},
                "value": top["measured_metrics"][name]["mean"],
                "seeds": top["measured_metrics"][name]["n"],
                "reason": f"highest mean {name} among branches with completed runs",
                "significance_tested": False,
                "caveat": "'best' here means 'ranked first by a mean'; it is not a significance claim"}
        return ranked, best, warnings

    # -- manifest --------------------------------------------------------
    def _manifest(self, ctx, entries):
        """Hash every file in the project and name the ones the contract knows about."""
        warnings: list[str] = []
        skip_dirs = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules", ".venv"}
        declared: dict[str, str] = {}
        for aid, spec in ARTIFACTS.items():
            declared[spec.path.split("|")[0]] = aid
        for m in INTERNAL_ARTIFACT_SPECS.values():
            for aid, spec in m.items():
                declared[spec.path.split("|")[0]] = aid

        def classify(rel: str) -> tuple[str | None, str]:
            if rel in declared:
                return declared[rel], "declared_artifact"
            for pat, aid in declared.items():
                if "*" in pat and fnmatch.fnmatch(rel, pat):
                    return aid, "declared_artifact"
                if pat.endswith("/") and rel.startswith(pat):
                    return aid, "declared_artifact_member"
            return None, "undeclared_file"

        parents = self._parents(ctx)
        items, total, count = [], 0, 0
        truncated = False
        for p in sorted(ctx.project.rglob("*")):
            if not p.is_file() or any(part in skip_dirs for part in p.relative_to(ctx.project).parts):
                continue
            count += 1
            if count > 20000 or total > (2 << 30):
                truncated = True
                break
            rel = str(p.relative_to(ctx.project))
            aid, kind = classify(rel)
            total += p.stat().st_size
            items.append({"artifact_id": aid or f"unregistered:{rel}", "path": rel,
                          "sha256": sha256_file(p), "kind": kind, "bytes": p.stat().st_size,
                          "parents": parents.get(aid, []) if aid else []})
        if truncated:
            warnings.append("artifact_manifest was truncated at the walk limit; it is incomplete "
                            "and says so rather than presenting a partial walk as a full one.")
        undeclared = [i["path"] for i in items if i["kind"] == "undeclared_file"]
        manifest = {
            "project_id": ctx.project.name,
            "run_id": ctx.run_id,
            "generated_at": time.time(),
            "artifacts": items,
            "counts": {"files": len(items), "declared": len(items) - len(undeclared),
                       "undeclared": len(undeclared), "bytes": total},
            "truncated": truncated,
            "undeclared_files": undeclared[:200],
            "ledger_entries_this_run": len(entries),
            "self_reference_note": ("artifact_manifest.json and provenance.jsonl are hashed as they "
                                    "stood when this walk ran. Writing this manifest appends to both, "
                                    "so their digests here are of the pre-write state by construction."),
        }
        return manifest, warnings

    def _parents(self, ctx) -> dict[str, list[str]]:
        """Derive artifact lineage from the provenance log the runtime already keeps.

        Parents are the artifacts a producer read before it wrote — which is the
        only lineage claim the log actually supports.
        """
        reads: dict[str, list[str]] = {}
        out: dict[str, list[str]] = {}
        for ev in ctx.prov.read():
            if ev.kind == "artifact_read" and ev.artifact_id:
                reads.setdefault(ev.skill, []).append(ev.artifact_id)
            elif ev.kind == "artifact_write" and ev.artifact_id:
                out[ev.artifact_id] = sorted(set(reads.get(ev.skill, [])))
        return out

    def _consolidate_provenance(self, ctx, entries, manifest, inputs_seen=None) -> str:
        """Summarize lineage into `provenance_log` without clobbering the live log.

        `provenance_log` resolves to the same file the runtime appends to on every
        read and write. Rewriting it would destroy the run's own history in the act
        of documenting it, so when the paths coincide a summary is appended — and
        it is appended in the runtime's own Event shape so the log stays parseable.
        """
        target = ctx.store.path_for("provenance_log").resolve()
        events = ctx.prov.read()
        kinds: dict[str, int] = {}
        for ev in events:
            kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
        summary_detail = {
            "summary_of": "provenance_log consolidation by experiment-runner",
            "events_total": len(events),
            "events_by_kind": kinds,
            "artifacts_seen": sorted({ev.artifact_id for ev in events if ev.artifact_id}),
            "ledger_entries_appended": len(entries),
            "ledger_statuses": {s: sum(1 for e in entries if e["status"] == s)
                                for s in sorted({e["status"] for e in entries})},
            "manifest_files": manifest["counts"]["files"],
            "undeclared_files": manifest["counts"]["undeclared"],
        }
        summary_detail.update(inputs_seen or {})
        if target == ctx.prov.path.resolve():
            # Event-shaped so ProvenanceLog.read() keeps working on the merged file.
            record = {"ts": time.time(), "run_id": ctx.run_id, "skill": self.name,
                      "kind": "provenance_summary", "artifact_id": "provenance_log",
                      "path": str(target.relative_to(ctx.project)), "digest": None,
                      "detail": summary_detail}
            ctx.store.append_jsonl(self.name, "provenance_log", [record])
            return "appended_summary_to_runtime_log"
        ctx.store.write(self.name, "provenance_log",
                        [{"ts": ev.ts, "run_id": ev.run_id, "skill": ev.skill, "kind": ev.kind,
                          "artifact_id": ev.artifact_id, "path": ev.path, "digest": ev.digest,
                          "detail": ev.detail} for ev in events])
        return "wrote_consolidated_copy"


def _rlimits(limits: dict[str, Any], timeout: float):
    """Build the child-process limiter.

    These are a backstop, not isolation. Real isolation is whatever
    `sandbox_manifest.isolation` names; rlimits only stop a runaway run from
    taking the host down with it, and the ledger records them as such.
    """
    cpu = int(min(float(limits.get("timeout_seconds", timeout) or timeout), max(timeout, 1)) + 1)
    mem = _bytes_of(limits.get("memory"), 2 << 30)
    nproc = int(limits.get("pids", 256) or 256)

    def _apply() -> None:  # pragma: no cover - runs in the child
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 30, 1 << 30))
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        os.setsid()

    return _apply
