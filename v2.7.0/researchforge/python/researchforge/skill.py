"""Skill base class, execution context and registry."""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactStore
from .errors import GateBlocked, HumanDecisionRequired, NotImplementedYet, ResearchForgeError
from .generated import SKILLS
from .providers import ModelProvider, QuotaLedger, ScholarlyProvider
from .provenance import Event, ProvenanceLog


@dataclass
class Context:
    project: Path
    run_id: str
    mode: str                                   # guided | auto | analysis-only
    store: ArtifactStore
    prov: ProvenanceLog
    quota: QuotaLedger
    model: ModelProvider
    scholarly: list[ScholarlyProvider]
    config: dict[str, Any] = field(default_factory=dict)
    offline: bool = False

    def external(self, key: str, default: Any = None, *, required: bool = False) -> Any:
        """Read a declared external input.

        External inputs are the ones that legitimately enter from outside the
        artifact graph. Requiring them by name here keeps 'external' from becoming
        the loophole through which undeclared state leaks into a run.
        """
        if key in self.config:
            return self.config[key]
        if required:
            raise GateBlocked("external_input",
                              f"required external input '{key}' was not supplied",
                              f"pass --set {key}=<value> or add it to the run config")
        return default


@dataclass
class SkillResult:
    skill: str
    produced: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    synthetic: bool = False
    needs_human: dict[str, Any] | None = None
    next_state: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class Skill:
    """One node of the artifact graph.

    Subclasses implement `execute`. The base class handles provenance bracketing
    and — importantly — verifies afterwards that the skill actually produced what
    its contract says it produces. A skill that returns success without writing
    its artifacts is the exact failure this system exists to make impossible, so
    it is caught here rather than three stages later.
    """

    name: str = ""
    #: artifacts this skill may legitimately skip, with the condition under which it may
    optional_outputs: tuple[str, ...] = ()

    def execute(self, ctx: Context) -> SkillResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # ------------------------------------------------------------------
    def __call__(self, ctx: Context) -> SkillResult:
        started = time.time()
        ctx.prov.append(Event(started, ctx.run_id, self.name, "skill_start", detail={"mode": ctx.mode}))
        try:
            result = self.execute(ctx)
        except (HumanDecisionRequired, GateBlocked, NotImplementedYet) as e:
            ctx.prov.append(Event(time.time(), ctx.run_id, self.name, "skill_end",
                                  detail={"outcome": type(e).__name__, "message": str(e),
                                          "seconds": round(time.time() - started, 3)}))
            raise
        except ResearchForgeError:
            raise
        except Exception as e:  # unexpected: record the trace, do not swallow
            ctx.prov.append(Event(time.time(), ctx.run_id, self.name, "skill_end",
                                  detail={"outcome": "error", "message": str(e),
                                          "traceback": traceback.format_exc()[-2000:]}))
            raise
        self._verify_outputs(ctx, result)
        ctx.prov.append(Event(time.time(), ctx.run_id, self.name, "skill_end",
                              detail={"outcome": "ok", "produced": result.produced,
                                      "synthetic": result.synthetic,
                                      "seconds": round(time.time() - started, 3)}))
        return result

    def _verify_outputs(self, ctx: Context, result: SkillResult) -> None:
        declared = set(SKILLS[self.name].produces)
        missing = {a for a in declared - set(self.optional_outputs)
                   if not ctx.store.exists(a)}
        if missing:
            raise GateBlocked(
                "output_completeness",
                f"skill '{self.name}' returned success but did not produce "
                f"{sorted(missing)}. Its contract declares these outputs.",
                "either write the artifacts or mark them in `optional_outputs` with a reason",
            )


REGISTRY: dict[str, Callable[[], Skill]] = {}


def register(cls: type[Skill]) -> type[Skill]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} has no name")
    if cls.name not in SKILLS:
        raise ValueError(f"{cls.name} is not in the contract; add it to the manifests first")
    REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Skill:
    if name not in REGISTRY:
        raise ResearchForgeError(
            f"skill '{name}' has no implementation registered. "
            f"Registered: {sorted(REGISTRY)}"
        )
    return REGISTRY[name]()
