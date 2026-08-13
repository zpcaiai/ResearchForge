"""Skills that are specified but not built.

Every one of these raises. None of them returns a plausible result.

This file is the whole reason the package exists. The failure mode it guards
against is a system that produces sixty markdown files and then reports the
feature as implemented — so an unbuilt stage here does not degrade gracefully, it
stops the run and names the batch that would build it and what that batch needs.
"""
from __future__ import annotations

from ..errors import NotImplementedYet
from ..skill import Context, Skill, SkillResult, register

# skill -> (IMPLEMENTATION_PLAN batch, what it actually requires)
UNBUILT: dict[str, tuple[str, str]] = {}


def _make(skill_name: str, batch: str, missing: str) -> type[Skill]:
    class _Stub(Skill):
        name = skill_name

        def execute(self, ctx: Context) -> SkillResult:
            raise NotImplementedYet(skill_name, batch, missing)

    _Stub.__name__ = "Stub_" + skill_name.replace("-", "_")
    _Stub.__qualname__ = _Stub.__name__
    return _Stub


for _name, (_batch, _missing) in UNBUILT.items():
    register(_make(_name, _batch, _missing))


@register
class Orchestrator(Skill):
    """The Python half of the orchestrator: it persists state, it does not decide it.

    Transition decisions live in the TypeScript layer. This exists so that
    `research_state` is written through the same contract-enforcing store as every
    other artifact rather than being poked at directly from the other language.
    """

    name = "researchforge-orchestrator"

    def execute(self, ctx: Context) -> SkillResult:
        import time
        state = ctx.external("state", required=True)
        history = ctx.external("history", []) or []
        ctx.store.write(self.name, "research_state", {
            "run_id": ctx.run_id, "mode": ctx.mode, "state": state,
            "history": history, "updated_at": time.time(),
            "contract_digest": __import__("researchforge.generated", fromlist=["x"]).CONTRACT_DIGEST,
        })
        return SkillResult(self.name, produced=["research_state"], next_state=state)
