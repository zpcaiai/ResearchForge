"""Machine-evaluable void conditions.

An `invalid_condition` says when a run is VOID rather than negative. The
distinction carries the whole falsifiability argument: a negative result is
evidence, a void one is nothing at all, and a system that cannot tell them apart
reports its voids as findings.

Until now every condition the blueprint compiler emitted was prose — "count
completed runs per condition in the experiment ledger" — which no runtime can
evaluate. The acceptance grader caught it: not one condition was ever checked on
any run, so the mechanism was decorative. Prose is still carried, for the human
reading the spec; it is no longer the only thing there.

Each condition now has a `check` object with a `kind` this module can evaluate.
A condition whose kind is unknown is reported UNCHECKED, never satisfied — the
same rule the per-run checker in the generated code already followed.
"""
from __future__ import annotations

from typing import Any

Verdict = dict[str, Any]


def _completed(ledger: list[dict], exp: str) -> list[dict]:
    return [r for r in ledger
            if r.get("experiment_id") == exp and r.get("status") == "COMPLETED"]


def _by_condition(runs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in runs:
        arm = (r.get("provenance") or {}).get("arm") or r.get("arm") or "candidate"
        out.setdefault(str(arm), []).append(r)
    return out


def evaluate(condition: dict, *, experiment_id: str, ledger: list[dict],
             artifacts: dict[str, Any] | None = None) -> Verdict:
    """Return {code, status: FIRED|CLEAR|UNCHECKED, detail}."""
    code = condition.get("code", "?")
    chk = condition.get("check") or {}
    kind = chk.get("kind")
    arts = artifacts or {}
    runs = _completed(ledger, experiment_id)

    def out(status: str, **d) -> Verdict:
        return {"code": code, "status": status, "check_kind": kind, **d}

    if not kind:
        return out("UNCHECKED", reason="condition carries no machine-evaluable check")

    if kind == "min_completed_runs_per_condition":
        need = int(chk.get("value", 1))
        groups = _by_condition(runs)
        # The expected arms come from the SPEC, not from the rows that happen to
        # exist. Deriving them from observed rows made the check non-monotone: an
        # arm with one completed run FIRED, and the same arm with *zero* completed
        # runs produced no group key and CLEARED. The check got weaker as the arm
        # got worse, which is the direction that manufactures confidence.
        expected = [str(a) for a in (chk.get("conditions") or [])] or sorted(groups)
        counts = {a: len(groups.get(a, [])) for a in expected}
        for a, rows in groups.items():          # an arm the spec never declared still counts
            counts.setdefault(a, len(rows))
        if not counts:
            return out("FIRED", reason="no condition completed a single run", need=need)
        short = {k: v for k, v in counts.items() if v < need}
        return out("FIRED" if short else "CLEAR", need=need, counts=counts, short=short,
                   expected_conditions=expected)

    if kind == "ledger_arm_completed":
        # Reads the ledger rather than an artifact flag. The SOTA arm's condition
        # used to check `baseline_assets.sota_established`, a field no runtime path
        # ever sets True — so a fully measured state-of-the-art arm could not clear
        # it, and the experiment stayed permanently unsatisfiable.
        arm = str(chk.get("arm", ""))
        need = int(chk.get("value", 1))
        rows = [r for r in runs
                if str((r.get("provenance") or {}).get("arm") or r.get("arm") or "candidate") == arm
                and (r.get("metrics") or {})]
        return out("CLEAR" if len(rows) >= need else "FIRED",
                   arm=arm, need=need, completed=len(rows))

    if kind == "field_stable_across_runs":
        field = chk.get("field", "")
        seen = {str((r.get("provenance") or {}).get(field, r.get(field))) for r in runs}
        seen.discard("None")
        if not seen:
            # Nothing recorded the field. That is not stability; it is absence of
            # evidence, and treating it as CLEAR is how an evaluator swap goes
            # unnoticed.
            return out("UNCHECKED", reason=f"no run recorded '{field}'", field=field)
        return out("FIRED" if len(seen) > 1 else "CLEAR", field=field, distinct=sorted(seen))

    if kind == "metric_names_stable":
        sets = {tuple(sorted((r.get("metrics") or {}).keys())) for r in runs}
        if not sets:
            return out("UNCHECKED", reason="no completed run carried metrics")
        return out("FIRED" if len(sets) > 1 else "CLEAR",
                   distinct=[list(s) for s in sorted(sets)])

    if kind == "configs_match_except":
        ignore = set(chk.get("ignore") or []) | {"seed", "arm", "run_id"}
        sets = set()
        for r in runs:
            cfg = (r.get("provenance") or {}).get("config") or r.get("config") or {}
            sets.add(tuple(sorted((k, str(v)) for k, v in cfg.items() if k not in ignore)))
        if not sets:
            return out("UNCHECKED", reason="no run recorded a config to compare")
        return out("FIRED" if len(sets) > 1 else "CLEAR", distinct_configs=len(sets))

    if kind == "artifact_field_present":
        art = arts.get(chk.get("artifact", ""))
        if art is None:
            return out("UNCHECKED", reason=f"artifact '{chk.get('artifact')}' was not supplied")
        val = art.get(chk.get("field", "")) if isinstance(art, dict) else None
        return out("CLEAR" if val else "FIRED", artifact=chk.get("artifact"),
                   field=chk.get("field"), value=val)

    if kind == "text_present_in_artifact":
        art = arts.get(chk.get("artifact", ""))
        if art is None:
            return out("UNCHECKED", reason=f"artifact '{chk.get('artifact')}' was not supplied")
        text = art if isinstance(art, str) else str(art)
        needle = str(chk.get("text", ""))
        if not needle:
            return out("UNCHECKED", reason="no text to look for")
        return out("CLEAR" if needle in text else "FIRED",
                   artifact=chk.get("artifact"), chars=len(needle))

    return out("UNCHECKED", reason=f"unknown check kind '{kind}'")


def evaluate_all(spec: dict, ledger: list[dict],
                 artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    exp = spec.get("experiment_id", "?")
    verdicts = [evaluate(c, experiment_id=exp, ledger=ledger, artifacts=artifacts)
                for c in (spec.get("invalid_conditions") or [])]
    fired = [v for v in verdicts if v["status"] == "FIRED"]
    unchecked = [v for v in verdicts if v["status"] == "UNCHECKED"]
    return {
        "experiment_id": exp,
        "verdicts": verdicts,
        "void": bool(fired),
        "fired": [v["code"] for v in fired],
        "unchecked": [v["code"] for v in unchecked],
        # An experiment none of whose conditions could be evaluated is not "valid".
        # It is unfalsifiable, and saying so is the point of this module.
        "falsifiable": bool(verdicts) and len(unchecked) < len(verdicts),
    }
