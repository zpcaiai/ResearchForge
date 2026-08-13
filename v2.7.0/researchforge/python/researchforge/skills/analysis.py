"""The stage where a number is allowed to become a claim — or is refused.

Three skills live here and they are deliberately ordered as a funnel.

`data-analyst` never gets to interpret anything until it has proved the data is
not contaminated: the leakage checks run *before* the first statistic, because a
leaked split makes every downstream number meaningless while looking exactly like
a good result. A leak is therefore a gate, not a warning.

`integrity-auditor` is the gate that stops a result becoming a claim. It does not
trust the analyst's output: it re-derives every reported number from the raw
ledger, and a disagreement between the two is the signature of a fabricated or
stale value. It also refuses in three specific ways that matter more than
anything it affirms — too few seeds to say anything, a family of comparisons
reported without correction, and runs from different evaluator versions or
environments pooled as if they were the same experiment.

`finding-memory` exists so that the funnel is not a shredder. A branch that
failed is a finding with a tag, and the conditions under which the method stops
working are findings too. Nothing is discarded before it is distilled.

Convention for reading the ledger (`ExperimentResult`): the per-run facets this
module needs — seed, branch, evaluator version, environment digest — are read
from the record's `provenance` object, falling back to top level. They are read
here rather than assumed, because whether two runs are comparable is exactly the
question the auditor must be able to answer from the record alone.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Iterable

import numpy as np
from scipy import stats as sps

from ..errors import GateBlocked
from ..skill import Context, Skill, SkillResult, register

SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW")
#: BLOCKER and HIGH both stop evidence lock. HIGH is not "a bad MEDIUM": it is
#: reserved for defects that would make a published claim wrong, and a claim that
#: would be wrong must not be lockable just because it is not provably fabricated.
BLOCKING = ("BLOCKER", "HIGH")

DEFAULT_ALPHA = 0.05
DEFAULT_TARGET_POWER = 0.80
#: Two seeds give a variance estimate with one degree of freedom, which is not an
#: estimate of anything. Three is the floor at which a dispersion statement is
#: merely weak rather than meaningless.
MIN_SEEDS_FOR_INFERENCE = 3
TOL_ABS, TOL_REL = 1e-9, 1e-6


# ----------------------------------------------------------------------
# ledger reading
# ----------------------------------------------------------------------
def _facet(entry: dict, *names: str, default: Any = None) -> Any:
    prov = entry.get("provenance") or {}
    for n in names:
        if n in entry and entry[n] is not None:
            return entry[n]
        if n in prov and prov[n] is not None:
            return prov[n]
    return default


def _branch(entry: dict) -> str:
    """The comparable unit: one arm of one experiment.

    The arm alone is not it. Once the runner started invoking every declared arm,
    `arm` alone put E-001's baseline and E-ABL-001's baseline in a single group
    called "baseline" — two different conditions from two different experiments,
    averaged together and then compared against something. The experiment id alone
    is not it either: that is what pooled the method with its own control.
    """
    eid = str(entry.get("experiment_id") or "?")
    # Default to "candidate", the same default `execution._entry_arm` documents for
    # rows written before the runner invoked arms explicitly. Leaving those rows as
    # a bare experiment id made `_experiment_of` return "" for them, so the
    # cross-experiment guard in `_run_tests` compared "" to "" and passed every
    # pair — the exact contrast it exists to block. The ledger is append-only, so
    # one project holds both shapes.
    arm = _facet(entry, "branch", "arm", "variant", default=None) or "candidate"
    return f"{eid}:{arm}"


def _seed(entry: dict) -> Any:
    return _facet(entry, "seed", default=None)


def _stratum(entry: dict) -> tuple[str, str]:
    """Evaluator version and environment digest: the two things that decide comparability.

    Absent values become the literal string 'undeclared' rather than being made
    equal to each other by accident — 'we did not record it' and 'it was the
    same' must not collapse into one value here, because the whole point of the
    stratum is to notice when they differ.
    """
    return (str(_facet(entry, "evaluator_version", "evaluator", default="undeclared")),
            str(_facet(entry, "environment_digest", "env_digest", default="undeclared")))


def _ok(entry: dict) -> bool:
    return str(entry.get("status", "")).lower() in ("ok", "success", "succeeded", "completed", "pass")


def _numeric_metrics(entry: dict) -> dict[str, float]:
    out = {}
    for k, v in (entry.get("metrics") or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            out[k] = float(v)
    return out


def _require_ledger(ctx: Context, skill: str) -> list[dict]:
    """Read the ledger, or refuse.

    An analysis of no data is the exact failure mode this project exists to
    prevent, so 'the ledger is empty' cannot degrade into 'here is an analysis
    with caveats'. It raises.
    """
    rows = ctx.store.read(skill, "experiment_ledger", default=[])
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        raise GateBlocked(
            "no_experiment_results",
            "experiment_ledger.jsonl is empty or absent — no experiment run has been "
            "recorded for this project, so there is nothing to analyse.",
            "run 'experiment-runner' first. An analysis produced from zero runs is a "
            "fabrication regardless of how it is hedged, so it is refused rather than "
            "written with warnings.")
    if not any(_ok(r) and _numeric_metrics(r) for r in rows):
        statuses = sorted({str(r.get("status", "?")) for r in rows})
        raise GateBlocked(
            "no_usable_experiment_metrics",
            f"the ledger holds {len(rows)} entries but none is a successful run carrying "
            f"numeric metrics (statuses seen: {statuses}).",
            "fix the failing runs, or record why the metric is absent. Summarising a set "
            "of failures as though it contained measurements is not permitted.")
    return rows


def _group_values(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    """(branch, metric) -> per-seed observations drawn straight from the ledger.

    Both the analyst and the auditor call this. That is intentional: it is the
    ledger *reading convention*, not the arithmetic. The auditor's check is that
    the analyst's stored numbers match what this returns now — sharing the reader
    is what makes the two derivations comparable at all.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if not _ok(r):
            continue
        for metric, value in _numeric_metrics(r).items():
            groups.setdefault((_branch(r), metric), []).append({
                "value": value, "seed": _seed(r), "run_id": r.get("run_id"),
                "experiment_id": r.get("experiment_id"), "stratum": list(_stratum(r)),
            })
    for obs in groups.values():
        obs.sort(key=lambda o: (str(o["seed"]), str(o["run_id"])))
    return groups


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL_ABS + TOL_REL * max(abs(a), abs(b))


# ----------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------
def _summary(values: list[float], conf: float = 0.95) -> dict[str, Any]:
    """Point estimate *and* the uncertainty around it, or an explicit refusal.

    A mean with no interval is the format in which noise gets published, so the
    interval is not optional here: when n < 2 the interval is None and carries
    the reason, instead of being silently omitted.
    """
    n = len(values)
    arr = np.asarray(values, dtype=float)
    out: dict[str, Any] = {
        "n": n, "mean": float(arr.mean()) if n else None,
        "median": float(np.median(arr)) if n else None,
        "min": float(arr.min()) if n else None, "max": float(arr.max()) if n else None,
        "sd": None, "sem": None, "ci95": None, "uncertainty_refused": None,
    }
    if n < 2:
        out["uncertainty_refused"] = (
            f"n={n}: a single observation carries no dispersion information. No interval "
            f"is reported, and none may be inferred from the point estimate.")
        return out
    sd = float(arr.std(ddof=1))
    sem = sd / math.sqrt(n)
    half = float(sps.t.ppf(0.5 + conf / 2.0, n - 1)) * sem
    out.update(sd=sd, sem=sem, ci95=[float(arr.mean() - half), float(arr.mean() + half)])
    return out


def _hedges_g(a: list[float], b: list[float]) -> dict[str, Any]:
    """Bias-corrected standardised mean difference with an interval.

    Cohen's d is biased upward in small samples, and small samples are the norm
    for seed-level ML comparisons — which is precisely where an uncorrected d
    would flatter the result most.
    """
    n1, n2 = len(a), len(b)
    x, y = np.asarray(a, float), np.asarray(b, float)
    if n1 < 2 or n2 < 2:
        return {"g": None, "ci95": None, "refused":
                f"n1={n1}, n2={n2}: a pooled standard deviation cannot be estimated, so no "
                f"standardised effect size exists to report."}
    s_pool = math.sqrt(((n1 - 1) * x.var(ddof=1) + (n2 - 1) * y.var(ddof=1)) / (n1 + n2 - 2))
    if s_pool == 0:
        return {"g": None, "ci95": None, "refused":
                "pooled sd is exactly zero: every run returned the identical value, which "
                "means the metric is constant, not that the effect is infinite."}
    d = float((x.mean() - y.mean()) / s_pool)
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)
    g = j * d
    var_g = (j ** 2) * ((n1 + n2) / (n1 * n2) + d ** 2 / (2.0 * (n1 + n2 - 2)))
    half = 1.959963984540054 * math.sqrt(var_g)
    return {"g": g, "se": math.sqrt(var_g), "ci95": [g - half, g + half],
            "cohens_d": d, "small_sample_correction_j": j, "refused": None}


def _achieved_power(a: list[float], b: list[float], alpha: float) -> dict[str, Any]:
    """Post-hoc power at the observed effect, via the noncentral t distribution.

    Reported so that a null result can be read correctly: 'we found no
    difference' at 20% power means 'we could not have found one'. Where power
    cannot be computed at all, this refuses rather than returning a number.
    """
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return {"power": None, "refused":
                f"n1={n1}, n2={n2}: with fewer than two observations per arm there is no "
                f"variance estimate, so achieved power is undefined — not low, undefined."}
    eff = _hedges_g(a, b)
    if eff["g"] is None:
        return {"power": None, "refused": eff["refused"]}
    d = abs(eff["cohens_d"])
    df = n1 + n2 - 2
    nc = d * math.sqrt(n1 * n2 / (n1 + n2))
    crit = float(sps.t.ppf(1 - alpha / 2.0, df))
    power = float(1 - sps.nct.cdf(crit, df, nc) + sps.nct.cdf(-crit, df, nc))
    method = "noncentral t"
    if not math.isfinite(power):
        # scipy's noncentral t loses precision at large noncentrality. Falling back
        # is fine; silently returning nan as if it were a power figure is not.
        power = float(sps.norm.cdf(nc - crit) + sps.norm.cdf(-nc - crit))
        method = f"normal approximation (noncentral t was not finite at nc={nc:.1f})"
    if not math.isfinite(power):
        return {"power": None, "refused":
                f"achieved power could not be computed numerically at nc={nc:.1f}, df={df}. "
                f"No figure is reported rather than a placeholder."}
    power = min(1.0, max(0.0, power))
    return {"power": power, "method": method, "noncentrality": nc, "df": df, "alpha": alpha,
            "observed_abs_d": d, "refused": None,
            "note": "post-hoc power at the observed effect; it describes this comparison's "
                    "sensitivity, and must not be reused as a design-stage power target."}


LOWER_IS_BETTER_HINTS = ("loss", "error", "err", "rmse", "mae", "mse", "perplexity",
                         "ppl", "latency", "cost", "regret", "violation", "wer")


def _metric_direction(metric: str, declared: dict) -> dict:
    """Which way is better on this metric — declared, inferred, or assumed.

    'Improvement' is not a property of a number, and the direction is exactly
    what a favourable write-up gets to choose after the fact. So it is resolved
    once, recorded with its source, and the assumed case is labelled as assumed.
    """
    if metric in declared:
        return {"direction": declared[metric], "source": "declared"}
    low = metric.lower()
    if any(h in low for h in LOWER_IS_BETTER_HINTS):
        return {"direction": "lower_is_better", "source": "inferred from the metric name"}
    return {"direction": "higher_is_better",
            "source": "assumed — no direction was declared for this metric"}


#: Arm names that mean "this is the thing being compared against".
CONTROL_ARM_NAMES = ("control", "baseline", "base", "reference")


def _experiment_of(branch: str) -> str:
    """The experiment a branch belongs to, or '' for a flat branch name.

    `rsplit(":", 1)` is correct even when an experiment id itself contains a colon
    — nothing in ExperimentSpec.schema.json forbids one — because the arm is always
    the last segment.

    Branch ids are `E-001:candidate` once the runner invokes arms. Everything that
    pairs branches has to respect the boundary: E-001's candidate against
    E-ABL-001's baseline is arithmetic, not a contrast.
    """
    return branch.rsplit(":", 1)[0] if ":" in branch else ""


def _arm_of(branch: str) -> str:
    return branch.rsplit(":", 1)[1] if ":" in branch else branch


def _control_branches(branches: list[str], declared: str | None) -> list[str]:
    """Every branch that is a control, one per experiment where arms are named.

    There used to be exactly one control because there was exactly one flat set of
    branch names. With one control arm per experiment, returning "the" control
    would have to pick one experiment's baseline and use it for all of them.
    """
    if declared:
        return [b for b in branches if b == declared] or \
               [b for b in branches if _arm_of(b) == declared]
    named = [b for b in branches if _arm_of(b).lower() in CONTROL_ARM_NAMES]
    by_exp: dict[str, list[str]] = {}
    for b in named:
        by_exp.setdefault(_experiment_of(b), []).append(b)
    # one unambiguous control per experiment; two candidate controls in one
    # experiment is an ambiguity to report, not to resolve by sort order
    return sorted(b for group in by_exp.values() if len(group) == 1 for b in group)


def _ambiguous_controls(branches: list[str], declared: str | None) -> dict[str, list[str]]:
    """Experiments with more than one control-named arm, which get no control.

    Dropping them was right and silent was not. `NO_DECLARED_CONTROL` only fires
    when the control set is globally empty, so one clean experiment elsewhere
    suppressed the warning entirely — and the ambiguous experiment's real effect
    was filed as "a difference, not an improvement", with a reading that said
    "without a declared control" when one had been found and discarded.
    """
    if declared:
        return {}
    by_exp: dict[str, list[str]] = {}
    for b in branches:
        if _arm_of(b).lower() in CONTROL_ARM_NAMES:
            by_exp.setdefault(_experiment_of(b), []).append(b)
    return {e: sorted(g) for e, g in by_exp.items() if len(g) > 1}


def _pair_control(a: str, b: str, controls: set[str]) -> str | None:
    """Which of two branches is the control for their comparison, if either is.

    Both being controls means the contrast is control-vs-control, which is a
    difference between two references and not an improvement in anything.
    """
    ca, cb = a in controls, b in controls
    if ca == cb:
        return None
    return a if ca else b


def _control_branch(branches: list[str], declared: str | None) -> str | None:
    """Back-compatible single-control view: a name only when there is exactly one."""
    found = _control_branches(branches, declared)
    return found[0] if len(found) == 1 else None


def _mde(n_per_arm: int, alpha: float, power: float) -> float | None:
    """Smallest standardised effect this many seeds could have detected."""
    if n_per_arm < 2:
        return None
    df = 2 * n_per_arm - 2
    z_a = float(sps.t.ppf(1 - alpha / 2.0, df))
    z_b = float(sps.norm.ppf(power))
    return float((z_a + z_b) * math.sqrt(2.0 / n_per_arm))


def _holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values.

    Holm rather than plain Bonferroni because it controls the same family-wise
    error rate while being uniformly more powerful, and rather than
    Benjamini-Hochberg because BH controls only the false discovery rate and
    assumes a dependence structure across metrics that nobody has checked here.
    """
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj


def _bh(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i], reverse=True)
    adj = [0.0] * m
    running = 1.0
    for pos, i in enumerate(order):
        rank = m - pos
        running = min(running, m / rank * pvals[i])
        adj[i] = min(1.0, running)
    return adj


# ----------------------------------------------------------------------
# data-analyst
# ----------------------------------------------------------------------
class _TransformLog:
    """Every operation applied to the data, with its reason and its postcondition.

    The log is the only thing that makes a prepared dataset auditable after the
    fact: without it, 'we dropped the outliers' and 'we dropped the rows that
    disagreed with us' are indistinguishable in the artifact.
    """

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, op: str, *, reason: str, reversible: bool, rows_before: int | None = None,
               rows_after: int | None = None, params: dict | None = None,
               postcondition: str = "", ok: bool = True, detail: dict | None = None) -> dict:
        e = {"seq": len(self.entries) + 1, "ts": time.time(), "op": op, "reason": reason,
             "reversible": reversible, "rows_before": rows_before, "rows_after": rows_after,
             "params": params or {}, "postcondition": postcondition,
             "postcondition_ok": ok, "detail": detail or {}}
        self.entries.append(e)
        return e


def _split_rows(dataset: Any) -> dict[str, list[dict]]:
    if not isinstance(dataset, dict):
        return {}
    splits = dataset.get("splits") if isinstance(dataset.get("splits"), dict) else dataset
    out = {}
    for k, v in splits.items():
        if isinstance(v, list) and all(isinstance(r, dict) for r in v):
            out[k] = v
    return out


def _columns(rows: list[dict]) -> dict[str, list[Any]]:
    cols: dict[str, list[Any]] = {}
    for r in rows:
        for k, v in r.items():
            cols.setdefault(k, []).append(v)
    return {k: v for k, v in cols.items() if len(v) == len(rows)}


def _is_numeric(values: list[Any]) -> bool:
    return bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                for v in values)


def _leakage_checks(dataset: Any, log: _TransformLog) -> list[dict]:
    """Train/test overlap, target leakage and temporal leakage — before any analysis.

    These run first and their failures are BLOCKERs rather than warnings because a
    contaminated split does not produce a slightly optimistic number; it produces
    a number that measures nothing, while looking better than an honest one.
    """
    findings: list[dict] = []
    splits = _split_rows(dataset)
    if not splits:
        # Refusing to certify is not the same as passing. If no raw dataset was
        # supplied, this says so instead of recording three green checks.
        findings.append({
            "check": "leakage_suite", "severity": "MEDIUM", "status": "NOT_RUN",
            "detail": "no raw dataset was supplied to this skill, so train/test overlap, "
                      "target leakage and temporal leakage could not be inspected. This is "
                      "'unchecked', not 'clean'; any claim that depends on split hygiene "
                      "must cite a check that actually ran."})
        log.record("leakage_check.suite", reason="no raw dataset supplied", reversible=True,
                   postcondition="suite reported NOT_RUN rather than PASS", ok=True)
        return findings

    target = dataset.get("target") if isinstance(dataset, dict) else None
    time_col = dataset.get("time_column") if isinstance(dataset, dict) else None
    id_col = dataset.get("id_column") if isinstance(dataset, dict) else None
    train = splits.get("train", [])
    held_out = {k: v for k, v in splits.items() if k != "train"}

    # --- 1. train/test overlap -------------------------------------
    overlaps: list[dict] = []
    train_hashes = {_sha(r): r for r in train}
    train_ids = {r.get(id_col) for r in train} if id_col else set()
    for name, rows in held_out.items():
        dup = [r for r in rows if _sha(r) in train_hashes]
        shared_ids = ([r.get(id_col) for r in rows if r.get(id_col) in train_ids]
                      if id_col else [])
        if dup or shared_ids:
            overlaps.append({"split": name, "duplicate_rows": len(dup),
                             "shared_ids": sorted({str(i) for i in shared_ids})[:20]})
    findings.append({
        "check": "train_test_overlap", "severity": "BLOCKER" if overlaps else "LOW",
        "status": "FAIL" if overlaps else "PASS", "detail": overlaps or
        f"no exact row or id overlap between train ({len(train)} rows) and "
        f"{sorted(held_out)}"})
    log.record("leakage_check.train_test_overlap", reason="a row seen in training cannot "
               "also measure generalisation", reversible=True,
               postcondition="held-out splits share no row or id with train",
               ok=not overlaps, detail={"overlaps": overlaps})

    # --- 2. target leakage -----------------------------------------
    leaks: list[dict] = []
    if target:
        for name, rows in splits.items():
            cols = _columns(rows)
            tgt = cols.get(target)
            if tgt is None or len(rows) < 3:
                continue
            for col, vals in cols.items():
                if col == target or col == id_col:
                    continue
                if _is_numeric(vals) and _is_numeric(tgt) and len(set(tgt)) > 1 and len(set(vals)) > 1:
                    r = float(np.corrcoef(np.asarray(vals, float), np.asarray(tgt, float))[0, 1])
                    if math.isfinite(r) and abs(r) >= 0.999:
                        leaks.append({"split": name, "column": col, "kind": "near_perfect_correlation",
                                      "pearson_r": r})
                        continue
                # A feature that determines the target exactly, while being coarser than a
                # row identifier, is a post-outcome variable in disguise.
                mapping: dict[Any, set] = {}
                for v, t in zip(vals, tgt):
                    mapping.setdefault(_sha(v), set()).add(_sha(t))
                if (len(mapping) <= len(rows) / 2 and len(mapping) > 1
                        and all(len(s) == 1 for s in mapping.values())):
                    leaks.append({"split": name, "column": col,
                                  "kind": "functional_determination_of_target",
                                  "distinct_values": len(mapping), "rows": len(rows)})
                if col != target and any(w in col.lower() for w in ("target", "label", "outcome"))\
                        and col.lower() != str(target).lower():
                    leaks.append({"split": name, "column": col, "kind": "outcome_named_feature"})
    findings.append({
        "check": "target_leakage", "severity": "BLOCKER" if leaks else "LOW",
        "status": "FAIL" if leaks else ("PASS" if target else "NOT_RUN"),
        "detail": leaks or (f"no feature determines '{target}'" if target else
                            "no target column declared, so target leakage was not inspected")})
    log.record("leakage_check.target_leakage", reason="a feature computed from the outcome "
               "makes the model's score a measure of the leak, not the method", reversible=True,
               postcondition="no feature determines or near-perfectly correlates with the target",
               ok=not leaks, detail={"leaks": leaks})

    # --- 3. temporal leakage ---------------------------------------
    temporal: list[dict] = []
    if time_col:
        def _key(v):
            return (0, float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) \
                else (1, str(v))
        train_times = [_key(r[time_col]) for r in train if time_col in r]
        for name, rows in held_out.items():
            times = [_key(r[time_col]) for r in rows if time_col in r]
            if not train_times or not times:
                continue
            if max(train_times) >= min(times):
                temporal.append({"split": name, "max_train_time": max(train_times)[1],
                                 "min_eval_time": min(times)[1],
                                 "kind": "train_extends_into_evaluation_period"})
    findings.append({
        "check": "temporal_leakage", "severity": "BLOCKER" if temporal else "LOW",
        "status": "FAIL" if temporal else ("PASS" if time_col else "NOT_RUN"),
        "detail": temporal or (f"train ends before every held-out split begins on '{time_col}'"
                               if time_col else
                               "no time column declared, so temporal ordering was not inspected")})
    log.record("leakage_check.temporal_leakage", reason="training on the future and testing on "
               "the past inflates every forecast metric", reversible=True,
               postcondition="max(train time) < min(eval time) for every held-out split",
               ok=not temporal, detail={"violations": temporal})
    return findings


@register
class DataAnalyst(Skill):
    """Prepare the data honestly, then analyse it, keeping every step on the record.

    Consolidates v0.2.0 `data-prep-agent` and `data-analysis-agent`: they shared
    the leakage question, and separating them let a leak be introduced in one and
    go unnoticed in the other.
    """

    name = "data-analyst"

    def execute(self, ctx: Context) -> SkillResult:
        ledger = _require_ledger(ctx, self.name)
        specs = ctx.store.read(self.name, "experiment_specs", default="")
        best = ctx.store.read(self.name, "best_candidate", default={})
        tree = ctx.store.read(self.name, "experiment_tree", default={})
        ranked = ctx.store.read(self.name, "ranked_branches", default={})
        question = ctx.external("analysis_question", "unstated — no analysis question was supplied")
        dataset = ctx.external("raw_dataset", None)
        claim_map = ctx.external("claim_map", {}) or {}
        declared, declaration_source = self._declared_metrics(ctx, specs)
        warnings: list[str] = []
        log = _TransformLog()

        # ---- A. profile ------------------------------------------------
        log.record("load_raw", reason="read-only load; the raw dataset is never rewritten",
                   reversible=True, rows_before=None,
                   rows_after=sum(len(v) for v in _split_rows(dataset).values()) or None,
                   postcondition="raw source untouched", ok=True)
        profile = self._profile(dataset, ledger)
        ctx.store.write(self.name, "data_profile", profile)
        log.record("profile", reason="schema, missingness, duplicates and dispersion must be "
                   "known before any transformation is proposed", reversible=True,
                   postcondition="profile written", ok=True,
                   detail={"columns_profiled": len(profile.get("columns", []))})

        # ---- B. leakage, BEFORE any analysis ---------------------------
        leak_findings = _leakage_checks(dataset, log)
        # The log is written before the refusal so that the evidence for the refusal
        # survives it: a gate that destroys its own justification is not auditable.
        ctx.store.write(self.name, "data_transform_log", log.entries)
        blockers = [f for f in leak_findings if f["severity"] == "BLOCKER"]
        if blockers:
            raise GateBlocked(
                "data_leakage",
                "leakage detected before analysis: "
                + "; ".join(f"{f['check']} -> {json.dumps(f['detail'])[:300]}" for f in blockers),
                "repair the split or drop the leaking feature and re-run preparation. This is a "
                "blocker and not a warning: every number computed downstream of a leak measures "
                "the leak. No analysis_results were written.")

        # ---- C. freeze the prepared data --------------------------------
        prepared = self._freeze(ctx, dataset, log)
        ctx.store.write(self.name, "prepared_data", prepared)

        # ---- D. analysis over the ledger --------------------------------
        groups_raw = _group_values(ledger)
        failed = [r for r in ledger if not _ok(r)]
        groups = []
        for (branch, metric), obs in sorted(groups_raw.items()):
            values = [o["value"] for o in obs]
            s = _summary(values)
            strata = sorted({tuple(o["stratum"]) for o in obs})
            groups.append({
                "group_id": f"{branch}::{metric}", "branch": branch, "metric": metric,
                # Carried explicitly rather than parsed back out of `branch`. The
                # manuscript plans figures per experiment while the analysis groups per
                # (experiment, arm, metric); making the consumer re-derive the
                # experiment by splitting a string is how the two drift apart.
                "experiment_id": _experiment_of(branch) or branch,
                "arm": _arm_of(branch),
                "kind": "confirmatory" if metric in declared else "exploratory",
                "seeds": [o["seed"] for o in obs], "run_ids": [o["run_id"] for o in obs],
                "values": values,          # the full distribution, never a chosen subset
                "strata": [{"evaluator_version": a, "environment_digest": b} for a, b in strata],
                **s})
        log.record("aggregate_by_branch_and_seed",
                   reason="every seed of every branch is reported; a best-seed summary would "
                          "make the selection invisible", reversible=True,
                   rows_before=len(ledger), rows_after=len(groups),
                   postcondition="len(values) == number of successful runs in the group",
                   ok=all(g["n"] == len(g["values"]) for g in groups))

        exploratory = sorted({g["metric"] for g in groups if g["kind"] == "exploratory"})
        if exploratory:
            warnings.append(
                f"metrics {exploratory} are not declared in the experiment specs and are labelled "
                f"exploratory. They may motivate a further experiment; they may not support a "
                f"confirmatory claim in the manuscript.")
        if failed:
            warnings.append(
                f"{len(failed)} of {len(ledger)} runs did not succeed and contribute no values. "
                f"The failure rate is part of the result and is carried into analysis_results.")

        reported = []
        for g in groups:
            reported.append({
                "reported_id": f"r::{g['group_id']}::mean", "group_id": g["group_id"],
                "branch": g["branch"], "metric": g["metric"], "statistic": "mean",
                "value": g["mean"], "interval": g["ci95"],
                "claim_ids": list(claim_map.get(g["metric"], [])),
            })

        results = {
            "run_id": ctx.run_id, "generated_at": time.time(),
            "analysis_question": question,
            "ledger_digest": _sha(ledger), "n_runs": len(ledger),
            "n_successful": len(ledger) - len(failed), "n_failed": len(failed),
            "failure_rate": len(failed) / len(ledger),
            "confirmatory_metrics": sorted(declared),
            "metric_declaration_source": declaration_source,
            "exploratory_metrics": exploratory,
            "groups": groups, "reported": reported,
            "comparisons": self._comparisons(groups, ctx),
            "leakage_checks": leak_findings,
            "best_candidate_declared": (best or {}).get("experiment_id") if isinstance(best, dict) else None,
            "branch_ranking_source": "ranked_branches" if ranked else "none",
            "search_tree_nodes": len(tree.get("nodes", [])) if isinstance(tree, dict) else 0,
            "inference_deferred_to": "integrity-auditor",
            "note": "no p-value, correction or keep/kill verdict is computed here. Summarising "
                    "and judging in one skill is how a favourable summary gets to grade itself.",
        }
        ctx.store.write(self.name, "analysis_results", results)
        ctx.store.write(self.name, "analysis_plots", self._plots(ctx, groups))
        ctx.store.write(self.name, "analysis_code", self._code(results))
        ctx.store.write(self.name, "analysis_report",
                        self._report(results, question, leak_findings, log, failed))
        # rewritten last so the log covers the steps taken after the leakage gate too
        ctx.store.write(self.name, "data_transform_log", log.entries)
        return SkillResult(
            self.name,
            produced=["data_profile", "data_transform_log", "prepared_data", "analysis_results",
                      "analysis_plots", "analysis_code", "analysis_report"],
            warnings=warnings,
            detail={"groups": len(groups), "n_runs": len(ledger), "n_failed": len(failed),
                    "leakage_status": {f["check"]: f["status"] for f in leak_findings},
                    "transform_ops": len(log.entries)})

    # ------------------------------------------------------------------
    def _declared_metrics(self, ctx: Context, specs: Any) -> tuple[set[str], str]:
        """Which metrics were named before the runs — and where that list came from.

        The source is carried because 'no metric was pre-registered' and 'this
        metric was not pre-registered' are different accusations, and only one of
        them can be made when the spec is missing entirely.
        """
        declared = set(ctx.external("confirmatory_metrics", []) or [])
        if declared:
            return declared, "external:confirmatory_metrics"
        text = specs if isinstance(specs, str) else json.dumps(specs)
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- ") and ":" not in line:
                declared.add(line[2:].strip())
        return declared, ("experiment_specs" if declared else "none")

    def _profile(self, dataset: Any, ledger: list[dict]) -> dict:
        splits = _split_rows(dataset)
        cols_out = []
        for name, rows in sorted(splits.items()):
            for col, vals in sorted(_columns(rows).items()):
                entry = {"split": name, "column": col, "n": len(vals),
                         "missing": sum(1 for v in vals if v is None),
                         "distinct": len({_sha(v) for v in vals}),
                         "dtype": "number" if _is_numeric(vals) else "other"}
                if _is_numeric(vals):
                    arr = np.asarray(vals, float)
                    entry.update(mean=float(arr.mean()), sd=float(arr.std(ddof=1)) if len(arr) > 1 else None,
                                 min=float(arr.min()), max=float(arr.max()))
                cols_out.append(entry)
        return {
            "raw_dataset_supplied": bool(splits),
            "splits": {k: {"rows": len(v), "duplicate_rows": len(v) - len({_sha(r) for r in v})}
                       for k, v in sorted(splits.items())},
            "columns": cols_out,
            "ledger": {"rows": len(ledger),
                       "branches": sorted({_branch(r) for r in ledger}),
                       "metrics": sorted({m for r in ledger for m in _numeric_metrics(r)}),
                       "seeds_per_branch": {b: sorted({str(_seed(r)) for r in ledger
                                                       if _branch(r) == b})
                                            for b in sorted({_branch(r) for r in ledger})}},
        }

    def _freeze(self, ctx: Context, dataset: Any, log: _TransformLog) -> dict:
        """Write the splits out and checksum them, so 'the data' stops being a moving target."""
        splits = _split_rows(dataset)
        target_dir = ctx.store.path_for("prepared_data")
        target_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, rows in sorted(splits.items()):
            p = target_dir / f"{name}.jsonl"
            p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
            files[name] = {"path": f"analysis/prepared/{name}.jsonl", "rows": len(rows),
                           "sha256": _sha(rows)}
        log.record("freeze_splits", reason="split membership must be immutable before any metric "
                   "is computed against it", reversible=True, rows_before=sum(len(v) for v in splits.values()),
                   rows_after=sum(len(v) for v in splits.values()),
                   postcondition="row counts preserved and each split checksummed",
                   ok=all(files[n]["rows"] == len(splits[n]) for n in files))
        return {"frozen_at": time.time(), "raw_source_mutated": False, "splits": files,
                "split_semantics": {"train": "parameters may be fit on this and nothing else",
                                    "val": "model selection only; reporting from it is exploratory",
                                    "test": "read once, after the confirmatory analysis is fixed"},
                "note": ("empty when no raw dataset was supplied: the ledger-only analysis has no "
                         "prepared splits, and inventing them would fake a preparation step that "
                         "never happened.") if not files else None}

    def _comparisons(self, groups: list[dict], ctx: Context) -> list[dict]:
        control = ctx.external("control_branch", None)
        by_metric: dict[str, list[dict]] = {}
        for g in groups:
            by_metric.setdefault(g["metric"], []).append(g)
        out = []
        for metric, gs in sorted(by_metric.items()):
            names = sorted(g["branch"] for g in gs)
            pairs = ([(control, b) for b in names if b != control and control in names]
                     if control else
                     [(a, b) for i, a in enumerate(names) for b in names[i + 1:]])
            for a, b in pairs:
                out.append({"metric": metric, "branch_a": a, "branch_b": b,
                            "kind": "confirmatory" if gs[0]["kind"] == "confirmatory"
                                    else "exploratory"})
        return out

    def _plots(self, ctx: Context, groups: list[dict]) -> dict:
        """Diagnostic plots showing every seed, not a bar of means.

        A bar chart of means is how a two-seed difference is made to look like a
        result; the per-seed scatter makes the sample size impossible to hide.
        """
        out_dir = ctx.store.path_for("analysis_plots")
        out_dir.mkdir(parents=True, exist_ok=True)
        plots = []
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as e:  # rendering is optional; claiming plots that do not exist is not
            return {"rendered": False, "reason": f"matplotlib unavailable: {e}",
                    "planned": sorted({g["metric"] for g in groups})}
        by_metric: dict[str, list[dict]] = {}
        for g in groups:
            by_metric.setdefault(g["metric"], []).append(g)
        for metric, gs in sorted(by_metric.items()):
            fig, ax = plt.subplots(figsize=(1.6 * max(3, len(gs)), 3.2))
            for i, g in enumerate(sorted(gs, key=lambda x: x["branch"])):
                ax.scatter([i] * len(g["values"]), g["values"], s=28, zorder=3)
                if g["ci95"]:
                    ax.plot([i, i], g["ci95"], lw=3, alpha=0.35, zorder=2)
                ax.scatter([i], [g["mean"]], marker="_", s=420, zorder=4)
            ax.set_xticks(range(len(gs)))
            ax.set_xticklabels([g["branch"] for g in sorted(gs, key=lambda x: x["branch"])],
                               rotation=20, ha="right")
            ax.set_ylabel(metric)
            ax.set_title(f"{metric}: every seed, with 95% CI")
            fig.tight_layout()
            name = f"{metric.replace('/', '_')}_by_branch.png"
            fig.savefig(out_dir / name, dpi=110)
            plt.close(fig)
            digest = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
            plots.append({"file": name, "metric": metric, "sha256": digest,
                          "shows": "one point per seed, mean bar, 95% t interval",
                          "source": "experiment_ledger.jsonl via analysis_results.groups"})
        return {"rendered": True, "plots": plots,
                "generated_at": time.time(), "from": "analysis_results.groups"}

    def _code(self, results: dict) -> str:
        return (
            '"""Recompute this analysis from the raw ledger.\n\n'
            "Written by data-analyst so the numbers in analysis_results.json can be checked\n"
            "without trusting the process that produced them. It reads experiment_ledger.jsonl\n"
            "and re-derives every group summary, then diffs against the stored results.\n"
            '"""\n'
            "import json, math, sys\n"
            "from pathlib import Path\n\n"
            "PROJECT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')\n\n\n"
            "def ok(r):\n"
            "    return str(r.get('status', '')).lower() in "
            "('ok', 'success', 'succeeded', 'completed', 'pass')\n\n\n"
            "def branch(r):\n"
            "    p = r.get('provenance') or {}\n"
            "    return str(r.get('branch') or p.get('branch') or p.get('arm') or "
            "r.get('experiment_id'))\n\n\n"
            "def main():\n"
            "    rows = [json.loads(l) for l in "
            "(PROJECT / 'experiment_ledger.jsonl').read_text().splitlines() if l.strip()]\n"
            "    groups = {}\n"
            "    for r in rows:\n"
            "        if not ok(r):\n"
            "            continue\n"
            "        for m, v in (r.get('metrics') or {}).items():\n"
            "            if isinstance(v, (int, float)) and not isinstance(v, bool):\n"
            "                groups.setdefault((branch(r), m), []).append(float(v))\n"
            "    stored = json.loads((PROJECT / 'analysis/analysis_results.json').read_text())\n"
            "    bad = 0\n"
            "    for g in stored['groups']:\n"
            "        mine = sorted(groups.get((g['branch'], g['metric']), []))\n"
            "        if mine != sorted(g['values']):\n"
            "            print('VALUES DIFFER', g['group_id'], mine, g['values'])\n"
            "            bad += 1\n"
            "            continue\n"
            "        mean = sum(mine) / len(mine)\n"
            "        if abs(mean - g['mean']) > 1e-9 + 1e-6 * abs(mean):\n"
            "            print('MEAN DIFFERS', g['group_id'], mean, g['mean'])\n"
            "            bad += 1\n"
            "    print(('OK: ' if not bad else 'MISMATCHES: ') + str(bad))\n"
            "    return 1 if bad else 0\n\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
            f"\n# ledger_digest at analysis time: {results['ledger_digest']}\n")

    def _report(self, results, question, leak, log, failed) -> str:
        lines = ["# Analysis report", "",
                 f"Question: {question}", "",
                 f"Runs in ledger: {results['n_runs']} "
                 f"({results['n_successful']} successful, {results['n_failed']} failed; "
                 f"failure rate {results['failure_rate']:.2f})", "",
                 "## Preparation and leakage", ""]
        for f in leak:
            lines.append(f"- **{f['check']}**: {f['status']} ({f['severity']}) — "
                         f"{json.dumps(f['detail'])[:400]}")
        lines += ["", f"Transformations applied and logged: {len(log.entries)} "
                      f"(see analysis/transform_log.jsonl; each carries its reason, its "
                      f"reversibility and the postcondition checked after it).", "",
                  "## Results", "",
                  "| branch | metric | kind | n seeds | mean | 95% CI | all values |",
                  "|---|---|---|---|---|---|---|"]
        for g in results["groups"]:
            ci = (f"[{g['ci95'][0]:.4g}, {g['ci95'][1]:.4g}]" if g["ci95"]
                  else f"refused ({g['uncertainty_refused']})")
            lines.append(f"| {g['branch']} | {g['metric']} | {g['kind']} | {g['n']} | "
                         f"{g['mean']:.6g} | {ci} | {', '.join(f'{v:.6g}' for v in g['values'])} |")
        lines += ["", "Every seed is listed. A summary that showed only the best one would make "
                      "the selection invisible, which is the point of listing them.", ""]
        if failed:
            lines += ["## Runs that did not produce a metric", ""]
            for r in failed:
                lines.append(f"- `{r.get('experiment_id')}` / seed {_seed(r)}: "
                             f"status={r.get('status')} — excluded from every mean above, and "
                             f"counted in the failure rate.")
            lines.append("")
        lines += ["## What this report deliberately does not say", "",
                  "No significance test, multiple-comparison correction or keep/kill verdict "
                  "appears here. Those belong to `integrity-auditor`, which recomputes these "
                  "numbers from the raw ledger rather than trusting this file.", ""]
        return "\n".join(lines)


# ----------------------------------------------------------------------
# integrity-auditor
# ----------------------------------------------------------------------
def _finding(code: str, severity: str, message: str, *, metrics=(), branches=(), claims=(),
             evidence=None, remediation: str = "") -> dict:
    return {"finding_id": f"F-{code.lower()}-{_sha([code, message])[:8]}",
            "code": code, "severity": severity, "message": message,
            "affected": {"metrics": sorted(set(metrics)), "branches": sorted(set(branches)),
                         "claims": sorted(set(claims))},
            "evidence": evidence or {}, "remediation": remediation,
            "blocks_evidence_lock": severity in BLOCKING}


@register
class IntegrityAuditor(Skill):
    """The gate between a result and a claim.

    Consolidates v0.2.0 `statistical-integrity-auditor` and `result-meta-analyzer`,
    because meta-analysis is where selective reporting actually happens: the skill
    that aggregates repeated trials and the skill that detects cherry-picking must
    share one view of the raw runs, or the aggregator gets to choose what the
    auditor sees.
    """

    name = "integrity-auditor"

    def execute(self, ctx: Context) -> SkillResult:
        ledger = _require_ledger(ctx, self.name)
        results = ctx.store.read(self.name, "analysis_results")
        code = ctx.store.read(self.name, "analysis_code", default="")
        specs = ctx.store.read(self.name, "experiment_specs", default="")
        criteria = dict(ctx.external("keep_kill_criteria", {}) or {})
        alpha = float(criteria.get("alpha", DEFAULT_ALPHA))
        min_seeds = int(criteria.get("min_seeds", MIN_SEEDS_FOR_INFERENCE))
        target_power = float(criteria.get("target_power", DEFAULT_TARGET_POWER))
        # The smallest standardised effect worth claiming. It has a default because a
        # missing criterion must not silently become 'any difference counts', and the
        # default is recorded in the decision so nobody mistakes it for a choice.
        min_effect = float(criteria.get("min_effect_size", 0.3))
        max_failure_rate = float(criteria.get("max_failure_rate", 0.34))

        raw = _group_values(ledger)
        branches = sorted({b for (b, _m) in raw})
        controls = set(_control_branches(branches, ctx.external("control_branch", None)))
        control = _control_branch(branches, ctx.external("control_branch", None))
        directions = {m: _metric_direction(m, ctx.external("metric_direction", {}) or {})
                      for (_b, m) in raw}
        findings: list[dict] = []
        claim_ids = {(r.get("branch"), r.get("metric")): list(r.get("claim_ids") or [])
                     for r in results.get("reported", [])}

        findings += self._check_values_against_ledger(results, raw, claim_ids)
        strata, findings_strata = self._check_comparability(ledger, raw)
        findings += findings_strata
        tests, findings_tests = self._run_tests(raw, strata, alpha, min_seeds, target_power,
                                                min_effect, claim_ids, results)
        findings += findings_tests
        ambiguous = _ambiguous_controls(branches, ctx.external("control_branch", None))
        for exp, group in sorted(ambiguous.items()):
            findings.append(_finding(
                "AMBIGUOUS_CONTROL", "HIGH",
                f"experiment {exp or '(unnamed)'} has more than one control-named arm "
                f"({group}), so no control could be identified for it and every contrast inside "
                f"it is reported as a difference rather than an improvement. The reading text on "
                f"those contrasts says 'without a declared control', which is not what happened: "
                f"controls were found and discarded as ambiguous.",
                branches=group,
                remediation="declare control_branch explicitly, or rename all but one of "
                            f"{group} so exactly one arm of {exp or 'the experiment'} is a "
                            f"control."))
        if not controls and len(branches) > 1:
            findings.append(_finding(
                "NO_DECLARED_CONTROL", "MEDIUM",
                f"no branch is identifiable as the control among {branches}, so 'better' has no "
                f"referent: a difference can be measured, but an improvement cannot be claimed.",
                branches=branches,
                remediation="declare control_branch, or name the comparator 'baseline'."))
        correction, findings_mc = self._correct(tests, alpha)
        findings += findings_mc
        findings += self._check_reporting_hygiene(results, ledger, raw, max_failure_rate,
                                                  claim_ids)
        if not str(specs).strip():
            findings.append(_finding(
                "NO_EXPERIMENT_SPEC", "MEDIUM",
                "no experiment specification is on disk, so the audit cannot check the reported "
                "analysis against the design it was supposed to follow. Assumption structure, "
                "the pre-registered metric set and the intended contrasts are all being taken "
                "from the results themselves.",
                metrics=sorted({m for (_b, m) in raw}),
                remediation="produce experiment_specs before the confirmatory analysis."))

        by_sev = {s: [f for f in findings if f["severity"] == s] for s in SEVERITIES}
        blocking = [f for f in findings if f["severity"] in BLOCKING]
        blocked_metrics = sorted({m for f in blocking for m in f["affected"]["metrics"]})
        blocked_branches = sorted({b for f in blocking for b in f["affected"]["branches"]})
        blocked_claims = sorted({c for f in blocking for c in f["affected"]["claims"]})

        audit = {
            "run_id": ctx.run_id, "generated_at": time.time(),
            "ledger_digest_now": _sha(ledger),
            "ledger_digest_at_analysis": results.get("ledger_digest"),
            "ledger_unchanged_since_analysis":
                _sha(ledger) == results.get("ledger_digest"),
            "analysis_code_present": bool(code and "experiment_ledger" in str(code)),
            "experiment_specs_available": bool(str(specs).strip()),
            "n_runs": len(ledger), "alpha": alpha, "min_seeds_required": min_seeds,
            "target_power": target_power, "min_effect_size_of_interest": min_effect,
            "control_branch": control if control else sorted(controls) or None,
            "control_branches": sorted(controls),
            "metric_directions": directions,
            "strata": strata,
            "tests": tests,
            "multiple_comparison_correction": correction,
            "findings": findings,
            "severity_counts": {s: len(v) for s, v in by_sev.items()},
            "evidence_lock": {
                "blocked": bool(blocking),
                "blocked_by": [f["finding_id"] for f in blocking],
                "blocked_metrics": blocked_metrics,
                "blocked_branches": blocked_branches,
                "blocked_claims": blocked_claims,
                "rule": "BLOCKER and HIGH findings prevent evidence lock for the claims they "
                        "affect. They are not advisory: a claim whose statistics do not survive "
                        "this audit cannot be locked, and therefore cannot be written as a "
                        "result.",
            },
        }
        ctx.store.write(self.name, "stats_audit", audit)
        ctx.store.write(self.name, "stats_required_fixes", self._fixes_md(blocking, by_sev))

        meta = self._meta(ledger, raw, strata, tests, alpha, criteria, controls, directions,
                          min_effect)
        meta["run_id"] = ctx.run_id
        meta["best_run_only_summary_used"] = any(
            r.get("statistic") in ("max", "best") for r in results.get("reported", []))
        ctx.store.write(self.name, "meta_analysis", meta)
        decision = self._decide(meta, audit, criteria, min_effect, target_power, max_failure_rate)
        meta["recommendation"] = decision["recommendation"]
        ctx.store.write(self.name, "meta_analysis", meta)
        ctx.store.write(self.name, "meta_analysis_decision", decision["markdown"])

        warnings = [f"{f['severity']}: {f['message']}" for f in findings
                    if f["severity"] in BLOCKING]
        return SkillResult(
            self.name,
            produced=["stats_audit", "stats_required_fixes", "meta_analysis",
                      "meta_analysis_decision"],
            warnings=warnings,
            next_state=None if blocking else "EVIDENCE_LOCKED",
            detail={"severity_counts": audit["severity_counts"],
                    "evidence_lock_blocked": bool(blocking),
                    "family_size": correction["family_size"],
                    "correction": correction["method"],
                    "recommendation": decision["recommendation"]})

    # ---------------- A. reported vs raw -------------------------------
    def _check_values_against_ledger(self, results, raw, claim_ids) -> list[dict]:
        """Recompute every reported number from the ledger.

        This is the check that catches a fabricated value. A number that appears
        in the analysis but cannot be re-derived from the append-only ledger has
        no provenance, and there is no benign version of that: it is either
        invented, hand-edited, or computed from data that no longer exists.
        """
        out: list[dict] = []
        ledger_metrics = {m for (_b, m) in raw}
        for g in results.get("groups", []):
            key = (g.get("branch"), g.get("metric"))
            stored = [float(v) for v in g.get("values", [])]
            if g.get("metric") not in ledger_metrics:
                out.append(_finding(
                    "NO_RAW_SUPPORT", "BLOCKER",
                    f"metric '{g.get('metric')}' is reported for branch '{g.get('branch')}' but "
                    f"appears nowhere in the experiment ledger. It has no run behind it.",
                    metrics=[g.get("metric")], branches=[g.get("branch")],
                    claims=claim_ids.get(key, []),
                    evidence={"ledger_metrics": sorted(ledger_metrics)},
                    remediation="delete the number, or run the experiment that would produce it."))
                continue
            actual = sorted(o["value"] for o in raw.get(key, []))
            if sorted(stored) != actual and not (
                    len(stored) == len(actual)
                    and all(_close(a, b) for a, b in zip(sorted(stored), actual))):
                out.append(_finding(
                    "VALUES_DIFFER_FROM_LEDGER", "BLOCKER",
                    f"the per-seed values reported for {key[0]}/{key[1]} do not match the ledger.",
                    metrics=[key[1]], branches=[key[0]], claims=claim_ids.get(key, []),
                    evidence={"reported": stored, "ledger": actual},
                    remediation="re-run data-analyst against the current ledger; do not edit "
                                "analysis_results.json by hand."))
                continue
            # A mean that disagrees with the very values printed beside it is the
            # cheapest fabrication there is, and the easiest to catch.
            if stored and g.get("mean") is not None:
                mean = float(np.mean(stored))
                if not _close(mean, float(g["mean"])):
                    out.append(_finding(
                        "REPORTED_VALUE_MISMATCH", "BLOCKER",
                        f"reported mean {g['mean']!r} for {key[0]}/{key[1]} does not equal the "
                        f"mean of the values it is derived from ({mean!r}).",
                        metrics=[key[1]], branches=[key[0]], claims=claim_ids.get(key, []),
                        evidence={"reported_mean": g["mean"], "recomputed_mean": mean,
                                  "values": stored},
                        remediation="recompute from the ledger. A hand-set summary statistic is "
                                    "indistinguishable from a fabricated one."))
        for r in results.get("reported", []):
            key = (r.get("branch"), r.get("metric"))
            obs = [o["value"] for o in raw.get(key, [])]
            if not obs or r.get("value") is None:
                continue
            stat = r.get("statistic", "mean")
            recomputed = {"mean": float(np.mean(obs)), "median": float(np.median(obs)),
                          "max": float(np.max(obs)), "min": float(np.min(obs))}.get(stat)
            if recomputed is not None and not _close(float(r["value"]), recomputed):
                out.append(_finding(
                    "REPORTED_VALUE_MISMATCH", "BLOCKER",
                    f"reported {stat} for {key[0]}/{key[1]} is {r['value']!r} but the ledger "
                    f"gives {recomputed!r}.",
                    metrics=[key[1]], branches=[key[0]], claims=r.get("claim_ids", []),
                    evidence={"reported": r["value"], "from_ledger": recomputed,
                              "ledger_values": obs, "statistic": stat},
                    remediation="the ledger is the record of what happened; the report must "
                                "follow it, not the reverse."))
        return out

    # ---------------- B. comparability ---------------------------------
    def _check_comparability(self, ledger, raw) -> tuple[list[dict], list[dict]]:
        """Runs from different evaluators or environments are not repeats of one experiment.

        Pooling them produces a number that answers no question: the between-run
        variance now contains an evaluator change. So aggregation across strata is
        refused outright rather than reported with a caveat.
        """
        strata_map: dict[tuple[str, str], list[dict]] = {}
        for r in ledger:
            if _ok(r):
                strata_map.setdefault(_stratum(r), []).append(r)
        strata = []
        for i, (key, rows) in enumerate(sorted(strata_map.items()), start=1):
            strata.append({"stratum_id": f"S{i}", "evaluator_version": key[0],
                           "environment_digest": key[1], "n_runs": len(rows),
                           "branches": sorted({_branch(r) for r in rows}),
                           "experiment_ids": sorted({str(r.get("experiment_id")) for r in rows})})
        out: list[dict] = []
        if len(strata) > 1:
            evs = sorted({s["evaluator_version"] for s in strata})
            envs = sorted({s["environment_digest"] for s in strata})
            differing = ([f"evaluator_version {evs}"] if len(evs) > 1 else []) + \
                        ([f"environment_digest {envs}"] if len(envs) > 1 else [])
            out.append(_finding(
                "INCOMPARABLE_STRATA", "BLOCKER",
                "the ledger contains runs that are not comparable to each other ("
                + "; ".join(differing) + "). Aggregation across them is refused: a pooled mean "
                "over two evaluator versions measures the evaluator change as well as the method.",
                metrics=sorted({m for (_b, m) in raw}),
                branches=sorted({b for (b, _m) in raw}),
                evidence={"strata": strata},
                remediation="re-run the older stratum under the current evaluator and "
                            "environment, or report the strata separately and claim nothing "
                            "about their combination."))
        elif strata and strata[0]["evaluator_version"] == "undeclared" \
                and strata[0]["environment_digest"] == "undeclared":
            # Not blocking: nothing contradicts comparability. But 'nobody recorded it'
            # must not be filed as 'verified identical', so it is stated as unverified.
            out.append(_finding(
                "COMPARABILITY_UNVERIFIED", "MEDIUM",
                "no run declares an evaluator version or environment digest, so the runs are "
                "being aggregated on an assumption of comparability that nothing in the record "
                "supports.",
                metrics=sorted({m for (_b, m) in raw}),
                remediation="record evaluator_version and environment_digest in each ledger "
                            "entry's provenance."))
        return strata, out

    # ---------------- C. tests, effect sizes, power --------------------
    def _run_tests(self, raw, strata, alpha, min_seeds, target_power, min_effect,
                   claim_ids, results):
        findings: list[dict] = []
        tests: list[dict] = []
        by_metric: dict[str, set[str]] = {}
        for (b, m) in raw:
            by_metric.setdefault(m, set()).add(b)
        reported_pairs = {(c.get("metric"), tuple(sorted((c.get("branch_a"), c.get("branch_b")))))
                          for c in results.get("comparisons", [])}
        for st in (strata or [{"stratum_id": "S1", "evaluator_version": "undeclared",
                               "environment_digest": "undeclared"}]):
            skey = (st["evaluator_version"], st["environment_digest"])
            for metric, branches in sorted(by_metric.items()):
                names = sorted(branches)
                for i, a in enumerate(names):
                    for b in names[i + 1:]:
                        if _experiment_of(a) != _experiment_of(b):
                            # arms of different experiments are not conditions of one
                            # comparison; testing them against each other would fill the
                            # correction family with contrasts nobody designed
                            continue
                        oa = [o for o in raw.get((a, metric), []) if tuple(o["stratum"]) == skey]
                        ob = [o for o in raw.get((b, metric), []) if tuple(o["stratum"]) == skey]
                        va, vb = [o["value"] for o in oa], [o["value"] for o in ob]
                        if not va or not vb:
                            continue
                        t = self._one_test(metric, a, b, va, vb, alpha, min_seeds,
                                           target_power, min_effect, st,
                                           [o["seed"] for o in oa], [o["seed"] for o in ob])
                        t["reported_by_analysis"] = (metric, tuple(sorted((a, b)))) in reported_pairs
                        tests.append(t)
                        claims = claim_ids.get((a, metric), []) + claim_ids.get((b, metric), [])
                        if t["refusal"]:
                            findings.append(_finding(
                                "INSUFFICIENT_SEEDS", "BLOCKER", t["refusal"],
                                metrics=[metric], branches=[a, b], claims=claims,
                                evidence={"n_a": t["n_a"], "n_b": t["n_b"],
                                          "min_seeds_required": min_seeds},
                                remediation=f"run at least {min_seeds} seeds per arm, or state "
                                            f"the observation as an anecdote rather than a "
                                            f"result."))
                        elif t["power"]["power"] is not None and t["power"]["power"] < target_power:
                            equiv = t["equivalence"]["established"]
                            # Post-hoc power is a function of the observed p-value, so it is
                            # always low when nothing was found. Precision, not power, decides
                            # whether a null is a null: if the interval already excludes the
                            # effect worth claiming, the experiment answered the question.
                            sev = ("LOW" if equiv else
                                   "HIGH" if not t["significant_raw"] else "MEDIUM")
                            findings.append(_finding(
                                "UNDERPOWERED", sev,
                                f"{metric}: {a} vs {b} reached only "
                                f"{t['power']['power']:.2f} achieved power at alpha={alpha} "
                                f"(target {target_power}). "
                                + ("The 95% interval for the standardised effect nevertheless "
                                   "excludes |g| >= "
                                   f"{t['equivalence']['bound']}, so this is a null result with "
                                   "adequate precision rather than missing data."
                                   if equiv else
                                   "A non-significant result at this power is 'we could not "
                                   "have detected it', not 'there is no effect', and must not "
                                   "be written as a null finding."
                                   if not t["significant_raw"] else
                                   "A significant result at this power is likely to be an "
                                   "overestimate of the effect (winner's curse)."),
                                metrics=[metric], branches=[a, b], claims=claims,
                                evidence={"achieved_power": t["power"]["power"],
                                          "mde_at_target_power": t["mde_at_target_power"],
                                          "n_a": t["n_a"], "n_b": t["n_b"]},
                                remediation="add seeds until the minimum detectable effect is "
                                            "smaller than the effect you intend to claim."))
                        if t["assumptions"]["duplicate_seeds"]:
                            findings.append(_finding(
                                "NON_INDEPENDENT_OBSERVATIONS", "HIGH",
                                f"{metric}: {a}/{b} contain repeated seed identifiers "
                                f"{t['assumptions']['duplicate_seeds']}; the runs are not "
                                f"independent replicates and the interval is too narrow.",
                                metrics=[metric], branches=[a, b], claims=claims,
                                remediation="use distinct seeds, or model the dependence."))
        return tests, findings

    def _one_test(self, metric, a, b, va, vb, alpha, min_seeds, target_power, min_effect,
                  stratum, seeds_a=(), seeds_b=()) -> dict:
        eff = _hedges_g(va, vb)
        power = _achieved_power(va, vb, alpha)
        t: dict[str, Any] = {
            "test_id": f"{stratum['stratum_id']}::{metric}::{a}_vs_{b}",
            "stratum_id": stratum["stratum_id"], "metric": metric,
            "branch_a": a, "branch_b": b, "n_a": len(va), "n_b": len(vb),
            "values_a": sorted(va), "values_b": sorted(vb),
            "mean_a": float(np.mean(va)), "mean_b": float(np.mean(vb)),
            "difference": float(np.mean(va) - np.mean(vb)),
            "effect_size": eff, "power": power,
            "mde_at_target_power": _mde(min(len(va), len(vb)), alpha, target_power),
            "p_raw": None, "test": None, "refusal": None,
            "significant_raw": False, "significant_corrected": None,
            "difference_ci95": None,
            "assumptions": self._assumptions(va, vb, seeds_a, seeds_b),
            "equivalence": self._equivalence(eff, min_effect),
        }
        if len(va) < min_seeds or len(vb) < min_seeds:
            # Refusing to produce a p-value is the honest output here: a test on
            # two runs per arm has no power to be wrong with, and printing 0.04
            # beside it would launder that.
            t["refusal"] = (
                f"{metric}: {a} (n={len(va)}) vs {b} (n={len(vb)}) has fewer than "
                f"{min_seeds} seeds per arm. No p-value, interval or verdict is computed: "
                f"there is not enough data here to say anything, and saying it weakly would "
                f"still be saying it.")
            return t
        welch = sps.ttest_ind(va, vb, equal_var=False)
        t["p_raw"] = float(welch.pvalue)
        t["statistic"] = float(welch.statistic)
        t["test"] = ("Welch two-sample t-test (unequal variances not assumed; seed-level runs "
                     "are the unit of analysis)")
        t["significant_raw"] = bool(t["p_raw"] < alpha)
        # Difference in means with its own interval — the effect size in the metric's
        # own units, which is what a reader actually needs.
        sa, sb = np.var(va, ddof=1), np.var(vb, ddof=1)
        se = math.sqrt(sa / len(va) + sb / len(vb))
        if se > 0:
            df = (sa / len(va) + sb / len(vb)) ** 2 / (
                (sa / len(va)) ** 2 / (len(va) - 1) + (sb / len(vb)) ** 2 / (len(vb) - 1))
            half = float(sps.t.ppf(1 - alpha / 2, df)) * se
            t["difference_ci95"] = [t["difference"] - half, t["difference"] + half]
            t["welch_df"] = float(df)
        if not t["assumptions"]["normality_ok"]:
            u = sps.mannwhitneyu(va, vb, alternative="two-sided")
            t["nonparametric"] = {"test": "Mann-Whitney U", "p": float(u.pvalue),
                                  "why": "normality was rejected, so the t-test's p-value is "
                                         "reported alongside a rank test rather than alone"}
        return t

    def _equivalence(self, eff, bound) -> dict:
        """Does the interval already exclude an effect worth claiming?

        This is what distinguishes 'no effect' from 'no data'. Without it, every
        small-sample null looks the same as a well-measured zero.
        """
        ci = eff.get("ci95")
        if not ci or bound <= 0:
            return {"established": False, "bound": bound,
                    "why": "no effect-size interval, or no minimum effect of interest declared"}
        inside = max(abs(ci[0]), abs(ci[1])) < bound
        return {"established": bool(inside), "bound": bound, "g_ci95": ci,
                "why": (f"the 95% interval for g is within +/-{bound}" if inside else
                        f"the 95% interval for g still admits |g| >= {bound}, so a difference "
                        f"worth claiming has not been ruled out")}

    def _assumptions(self, va, vb, seeds_a=(), seeds_b=()) -> dict:
        out: dict[str, Any] = {"normality_ok": True, "normality": {}, "equal_variance": {},
                               "duplicate_seeds": []}
        for name, v in (("a", va), ("b", vb)):
            if len(v) >= 3 and len(set(v)) > 1:
                w = sps.shapiro(v)
                out["normality"][name] = {"test": "Shapiro-Wilk", "p": float(w.pvalue)}
                if w.pvalue < 0.05:
                    out["normality_ok"] = False
            else:
                out["normality"][name] = {"test": "Shapiro-Wilk", "p": None,
                                          "why": "n<3 or constant: not testable"}
        # Levene needs at least three points per arm: with two, every absolute
        # deviation from the median is identical and the statistic is 0/0.
        if len(va) >= 3 and len(vb) >= 3 and len(set(va) | set(vb)) > 1:
            lev = sps.levene(va, vb)
            out["equal_variance"] = {"test": "Levene", "p": float(lev.pvalue),
                                     "handled_by": "Welch correction is used regardless, so an "
                                                   "unequal variance does not invalidate the test"}
        for name, seeds in (("a", seeds_a), ("b", seeds_b)):
            present = [s for s in seeds if s is not None]
            dupes = sorted({str(s) for s in present if present.count(s) > 1})
            if dupes:
                out["duplicate_seeds"].append({"arm": name, "seeds": dupes})
        return out

    # ---------------- D. multiple comparisons --------------------------
    def _correct(self, tests, alpha) -> tuple[dict, list[dict]]:
        live = [t for t in tests if t["p_raw"] is not None]
        metrics = sorted({t["metric"] for t in tests})
        branches = sorted({b for t in tests for b in (t["branch_a"], t["branch_b"])})
        if len(live) <= 1:
            for t in live:
                t["p_holm"] = t["p_raw"]
                t["p_bh"] = t["p_raw"]
                t["significant_corrected"] = t["significant_raw"]
            return ({"applied": False, "method": "none",
                     "family_size": len(live),
                     "why": "a single hypothesis test was performed, so there is no family over "
                            "which an error rate could inflate. Applying a correction here would "
                            "be theatre.",
                     "metrics_compared": metrics, "branches_compared": branches}, [])
        ps = [t["p_raw"] for t in live]
        holm, bh = _holm(ps), _bh(ps)
        demoted = []
        for t, ph, pb in zip(live, holm, bh):
            t["p_holm"], t["p_bh"] = ph, pb
            t["significant_corrected"] = bool(ph < alpha)
            if t["significant_raw"] and not t["significant_corrected"]:
                demoted.append(t)
        correction = {
            "applied": True, "method": "Holm-Bonferroni (step-down, family-wise error rate)",
            "family_size": len(live),
            "family_definition": "every branch-pair comparison the ledger supports, for every "
                                 "metric, within a comparability stratum",
            "metrics_compared": metrics, "branches_compared": branches,
            "why": (f"{len(branches)} branches and {len(metrics)} metric(s) yield {len(live)} "
                    f"simultaneous comparisons; at alpha={alpha} the chance of at least one "
                    f"false positive without correction is about "
                    f"{1 - (1 - alpha) ** len(live):.2f}. Holm is used rather than plain "
                    f"Bonferroni because it controls the same family-wise error rate with more "
                    f"power, and rather than Benjamini-Hochberg because BH controls only the "
                    f"false discovery rate and assumes a dependence structure across these "
                    f"metrics that nobody has established."),
            "secondary_reported": "Benjamini-Hochberg FDR values are recorded per test for "
                                  "readers who prefer an FDR framing; the gate uses Holm.",
            "alpha": alpha,
        }
        findings = []
        if demoted:
            findings.append(_finding(
                "SURVIVES_ONLY_UNCORRECTED", "HIGH",
                "these comparisons are significant only before multiple-comparison correction: "
                + ", ".join(f"{t['test_id']} (p={t['p_raw']:.4g}, Holm={t['p_holm']:.4g})"
                            for t in demoted)
                + ". Reporting them as findings would be selecting the winner of a family that "
                  "was searched.",
                metrics=[t["metric"] for t in demoted],
                branches=[b for t in demoted for b in (t["branch_a"], t["branch_b"])],
                evidence={"family_size": len(live), "alpha": alpha},
                remediation="pre-register the single comparison of interest and re-run it, or "
                            "report the corrected value."))
        return correction, findings

    # ---------------- E. reporting hygiene -----------------------------
    def _check_reporting_hygiene(self, results, ledger, raw, max_failure_rate, claim_ids):
        out: list[dict] = []
        for g in results.get("groups", []):
            n_ledger = len(raw.get((g.get("branch"), g.get("metric")), []))
            if n_ledger >= 2 and len(g.get("values", [])) < n_ledger:
                out.append(_finding(
                    "SELECTIVE_SEED_REPORTING", "HIGH",
                    f"{g['branch']}/{g['metric']} reports {len(g.get('values', []))} of "
                    f"{n_ledger} successful runs. A subset chosen after seeing the results is a "
                    f"claim about the subset, not about the method.",
                    metrics=[g.get("metric")], branches=[g.get("branch")],
                    claims=claim_ids.get((g.get("branch"), g.get("metric")), []),
                    remediation="report every seed, or state the exclusion rule that was fixed "
                                "before the runs."))
        for r in results.get("reported", []):
            if r.get("statistic") in ("max", "best") and \
                    len(raw.get((r.get("branch"), r.get("metric")), [])) > 1:
                out.append(_finding(
                    "BEST_RUN_ONLY_SUMMARY", "HIGH",
                    f"{r['branch']}/{r['metric']} is summarised by its {r['statistic']} while "
                    f"repeated trials exist. The best run is a sample from the tail, not an "
                    f"estimate of the method.",
                    metrics=[r.get("metric")], branches=[r.get("branch")],
                    claims=r.get("claim_ids", []),
                    remediation="report the mean with an interval and the full distribution."))
        failed = [r for r in ledger if not _ok(r)]
        rate = len(failed) / len(ledger)
        if rate > max_failure_rate:
            out.append(_finding(
                "HIGH_FAILURE_RATE", "HIGH",
                f"{len(failed)} of {len(ledger)} runs failed ({rate:.0%}). Every summary above "
                f"is conditioned on the runs that survived, which is a survivorship-biased view "
                f"of the method unless the failures are independent of the outcome — and nothing "
                f"here shows that they are.",
                metrics=sorted({m for (_b, m) in raw}),
                branches=sorted({_branch(r) for r in failed}),
                evidence={"failure_rate": rate, "threshold": max_failure_rate,
                          "failed_run_ids": [r.get("run_id") for r in failed][:50]},
                remediation="diagnose the failures. If they correlate with the condition under "
                            "test, the failure rate IS the result."))
        if results.get("ledger_digest") and _sha(ledger) != results.get("ledger_digest"):
            out.append(_finding(
                "STALE_ANALYSIS", "BLOCKER",
                "the ledger has changed since analysis_results.json was written, so the reported "
                "numbers describe a different set of runs than the ones now on record.",
                metrics=sorted({m for (_b, m) in raw}),
                remediation="re-run data-analyst against the current ledger."))
        exploratory = set(results.get("exploratory_metrics", []))
        promoted = [r for r in results.get("reported", [])
                    if r.get("metric") in exploratory and r.get("claim_ids")]
        if promoted and results.get("metric_declaration_source") == "none":
            # With no experiment spec on disk there is no plan to compare against, so
            # post-hoc promotion can be neither shown nor ruled out. 'Unverifiable' is
            # the only accurate severity: asserting the violation would be as unfounded
            # as asserting compliance.
            out.append(_finding(
                "PREREGISTRATION_UNVERIFIABLE", "MEDIUM",
                "claims are attached to metrics "
                + str(sorted({r["metric"] for r in promoted}))
                + ", but no experiment specification was available, so whether these metrics "
                  "were chosen before or after seeing the results cannot be established.",
                metrics=sorted({r["metric"] for r in promoted}),
                claims=[c for r in promoted for c in r["claim_ids"]],
                remediation="produce experiment_specs, or declare confirmatory_metrics, so the "
                            "pre-registered set is on the record."))
        elif promoted:
            out.append(_finding(
                "POST_HOC_PROMOTION", "HIGH",
                "metrics that were not declared in the experiment specs are attached to claims: "
                + ", ".join(f"{r['metric']} -> {r['claim_ids']}" for r in promoted)
                + ". Choosing the metric after seeing the numbers is the same search that "
                  "multiple-comparison correction exists to price, but invisible.",
                metrics=[r["metric"] for r in promoted],
                claims=[c for r in promoted for c in r["claim_ids"]],
                remediation="label these exploratory, or add them to the pre-registered spec and "
                            "re-run."))
        return out

    # ---------------- F. meta-analysis ---------------------------------
    def _meta(self, ledger, raw, strata, tests, alpha, criteria, controls, directions,
              min_effect) -> dict:
        per_stratum = []
        for st in strata:
            skey = (st["evaluator_version"], st["environment_digest"])
            aggs = []
            for (branch, metric), obs in sorted(raw.items()):
                vals = [o["value"] for o in obs if tuple(o["stratum"]) == skey]
                if not vals:
                    continue
                aggs.append({"branch": branch, "metric": metric,
                             "experiment_ids": sorted({str(o["experiment_id"]) for o in obs
                                                       if tuple(o["stratum"]) == skey}),
                             **_summary(vals)})
            per_stratum.append({**st, "aggregates": aggs})

        refused = len(strata) > 1
        pooled = []
        if not refused:
            for t in tests:
                eff = t["effect_size"]
                if eff.get("g") is None:
                    pooled.append({"metric": t["metric"], "contrast": f"{t['branch_a']}-{t['branch_b']}",
                                   "pooled": None, "refused": eff.get("refused")})
                    continue
                pooled.append({
                    "metric": t["metric"], "contrast": f"{t['branch_a']}-{t['branch_b']}",
                    "model": "single stratum; effect reported with its interval, no pooling "
                             "across incompatible designs",
                    "hedges_g": eff["g"], "ci95": eff["ci95"],
                    "difference": t["difference"], "difference_ci95": t["difference_ci95"],
                    "k_runs": t["n_a"] + t["n_b"],
                    "p_holm": t.get("p_holm"), "significant_corrected": t.get("significant_corrected"),
                })
        heterogeneity = self._heterogeneity(tests) if not refused else None

        failure_rates = {}
        for b in sorted({_branch(r) for r in ledger}):
            rows = [r for r in ledger if _branch(r) == b]
            failure_rates[b] = {"runs": len(rows),
                                "failed": sum(1 for r in rows if not _ok(r)),
                                "rate": sum(1 for r in rows if not _ok(r)) / len(rows)}
        positive, null, negative, between = [], [], [], []
        for t in tests:
            d = directions.get(t["metric"], {"direction": "higher_is_better"})
            higher_better = d["direction"] == "higher_is_better"
            label = {"metric": t["metric"], "contrast": f"{t['branch_a']} vs {t['branch_b']}",
                     "branches": [t["branch_a"], t["branch_b"]],
                     "difference": t["difference"], "ci95": t["difference_ci95"],
                     "g": t["effect_size"].get("g"), "p_holm": t.get("p_holm"),
                     "achieved_power": t["power"].get("power"),
                     "direction_of_merit": d, "equivalence": t["equivalence"]}
            if t["refusal"] or t.get("p_holm") is None:
                null.append({**label, "reading": "undetermined — the comparison was refused for "
                                                 "insufficient seeds, which is not a null result"})
            elif not t.get("significant_corrected"):
                if t["equivalence"]["established"]:
                    null.append({**label, "reading":
                                 f"null with adequate precision: the interval excludes "
                                 f"|g| >= {min_effect}, so an effect worth claiming is ruled out"})
                else:
                    null.append({**label, "reading": "inconclusive: underpowered, so absence of "
                                                     "evidence is not evidence of absence"})
            elif (control := _pair_control(t["branch_a"], t["branch_b"], controls)):
                # the control of THIS pair, not a single global one: each experiment
                # has its own reference condition once arms are run per experiment
                challenger = t["branch_a"] if t["branch_b"] == control else t["branch_b"]
                flip = challenger == t["branch_b"]
                delta = -t["difference"] if flip else t["difference"]
                ci, g = t["difference_ci95"], t["effect_size"].get("g")
                equiv = t["equivalence"]
                if flip:
                    # the interval and the effect size are directional too; flipping the
                    # point estimate alone would print a gain beside a negative interval
                    ci = [-ci[1], -ci[0]] if ci else None
                    g = -g if g is not None else None
                    if equiv.get("g_ci95"):
                        equiv = {**equiv, "g_ci95": [-equiv["g_ci95"][1], -equiv["g_ci95"][0]]}
                improved = (delta > 0) == higher_better
                oriented = {**label, "contrast": f"{challenger} vs {control}",
                            "challenger": challenger, "control": control,
                            "branches": [challenger, control],
                            "difference": delta, "ci95": ci, "g": g, "equivalence": equiv}
                (positive if improved else negative).append({
                    **oriented,
                    "reading": (f"{challenger} beats the control on {t['metric']} after "
                                f"correction" if improved else
                                f"{challenger} is worse than the control on {t['metric']} after "
                                f"correction")})
            else:
                between.append({**label, "reading":
                                "a real difference between two non-control branches; without a "
                                "declared control this is a difference, not an improvement"})
        return {
            "run_id": None, "generated_at": time.time(),
            "n_runs": len(ledger),
            "strata": per_stratum,
            "cross_stratum_aggregation": {
                "attempted": not refused, "refused": refused,
                "reason": ("runs differ in evaluator version and/or environment digest; they are "
                           "not repeats of one experiment and pooling them would attribute the "
                           "evaluator change to the method" if refused else None),
                "strata_ids": [s["stratum_id"] for s in strata]},
            "pooled_effects": pooled,
            "heterogeneity": heterogeneity,
            "failure_rates": failure_rates,
            "control_branch": (sorted(controls)[0] if len(controls) == 1
                               else sorted(controls) or None),
            "control_branches": sorted(controls),
            "metric_directions": directions,
            "min_effect_size_of_interest": min_effect,
            "evidence": {"positive": positive, "null": null, "negative": negative,
                         "between_variants": between},
            "best_run_only_summary_used": False,
        }

    def _heterogeneity(self, tests) -> dict | None:
        """Cochran's Q and I^2 across comparisons of the same contrast.

        Reported because a pooled estimate over heterogeneous studies is a
        number with no referent; when I^2 is high the right output is the spread,
        not the average.
        """
        groups: dict[str, list[dict]] = {}
        for t in tests:
            if t["effect_size"].get("g") is not None:
                groups.setdefault(f"{t['metric']}::{t['branch_a']}-{t['branch_b']}", []).append(t)
        out = []
        for key, ts in sorted(groups.items()):
            if len(ts) < 2:
                continue
            g = np.array([t["effect_size"]["g"] for t in ts])
            w = np.array([1.0 / (t["effect_size"]["se"] ** 2) for t in ts])
            gp = float((w * g).sum() / w.sum())
            q = float((w * (g - gp) ** 2).sum())
            df = len(ts) - 1
            i2 = max(0.0, (q - df) / q) if q > 0 else 0.0
            out.append({"contrast": key, "k": len(ts), "fixed_effect_g": gp,
                        "se": float(1 / math.sqrt(w.sum())), "Q": q, "df": df, "I2": i2,
                        "usable": i2 < 0.5,
                        "note": "I^2 >= 0.5: the studies disagree more than sampling error "
                                "explains, so the pooled value should not be quoted as the effect"
                                if i2 >= 0.5 else None})
        return {"contrasts": out} if out else None

    # ---------------- G. keep / kill -----------------------------------
    def _decide(self, meta, audit, criteria, min_effect, target_power, max_failure_rate) -> dict:
        blocking = [f for f in audit["findings"] if f["severity"] in BLOCKING]
        pos = meta["evidence"]["positive"]
        neg = meta["evidence"]["negative"]
        worst_failure = max((v["rate"] for v in meta["failure_rates"].values()), default=0.0)
        strong = [p for p in pos if abs(p.get("g") or 0) >= min_effect]

        if blocking:
            rec = "BLOCKED_PENDING_FIXES"
            why = ("unresolved BLOCKER/HIGH integrity findings exist. No keep/kill verdict is "
                   "issued from statistics that have not passed the audit — a GO decided on "
                   "numbers that may be wrong is worse than no decision.")
        elif worst_failure > max_failure_rate:
            rec = "STOP_AND_DIAGNOSE"
            why = (f"the worst branch failure rate is {worst_failure:.0%}, above the "
                   f"{max_failure_rate:.0%} criterion. A surviving win among mostly failed trials "
                   f"is not evidence the method works; it is evidence it works sometimes, which "
                   f"is a different and weaker claim.")
        elif strong:
            rec = "CONTINUE"
            why = (f"{len(strong)} comparison(s) remain significant after Holm correction with "
                   f"|g| >= {min_effect}, and the failure rate is within criterion.")
        elif neg:
            rec = "PIVOT"
            why = ("the corrected evidence points the wrong way. That is a result, and it belongs "
                   "in the findings memory rather than in a retry loop.")
        elif meta["evidence"].get("between_variants") and not meta.get("control_branch"):
            rec = "NARROW_CLAIM"
            why = ("branches differ from each other, but no control is declared, so nothing here "
                   "supports the word 'better'. Declare the comparator or narrow the claim to a "
                   "statement of difference.")
        elif any(e["reading"].startswith("null with adequate precision")
                 for e in meta["evidence"]["null"]):
            rec = "NARROW_CLAIM"
            why = ("an adequately powered null. The honest output is a narrowed claim plus a "
                   "negative finding, not another round of seeds hoping for a different answer.")
        else:
            rec = "CONTINUE_WITH_MORE_SEEDS"
            why = (f"nothing is determined at this sample size; achieved power is below "
                   f"{target_power}. The next run is more seeds, not more branches.")

        md = ["# Keep / kill decision", "",
              f"**Recommendation: {rec}**", "", why, "",
              "## Criteria applied", "",
              f"- alpha: {audit['alpha']}",
              f"- minimum seeds per arm: {audit['min_seeds_required']}",
              f"- target power: {target_power}",
              f"- minimum effect size (|Hedges g|): {min_effect}",
              f"- maximum acceptable failure rate: {max_failure_rate}",
              f"- control branch: {meta.get('control_branch') or 'NONE IDENTIFIED'}",
              f"- source of criteria: "
              f"{'external keep/kill criteria' if criteria else 'module defaults (none supplied)'}",
              "", "## Aggregation", ""]
        if meta["cross_stratum_aggregation"]["refused"]:
            md += [f"Cross-stratum aggregation was **refused**: "
                   f"{meta['cross_stratum_aggregation']['reason']}.", "",
                   "Per-stratum results are reported separately below and must not be combined "
                   "in the manuscript.", ""]
        for st in meta["strata"]:
            md.append(f"- `{st['stratum_id']}` evaluator=`{st['evaluator_version']}` "
                      f"env=`{st['environment_digest']}` — {st['n_runs']} runs, "
                      f"branches {st['branches']}")
        md += ["", "## Evidence, in all three directions", ""]
        for name, items in (("Positive", meta["evidence"]["positive"]),
                            ("Negative", meta["evidence"]["negative"]),
                            ("Between non-control branches",
                             meta["evidence"].get("between_variants", [])),
                            ("Null / undetermined", meta["evidence"]["null"])):
            md.append(f"### {name} ({len(items)})")
            for e in items:
                ci = (f"[{e['ci95'][0]:.4g}, {e['ci95'][1]:.4g}]" if e.get("ci95") else "no interval")
                md.append(f"- {e['metric']}, {e['contrast']}: diff={e['difference']:.4g} {ci}, "
                          f"g={e['g'] if e['g'] is None else round(e['g'], 3)}, "
                          f"Holm p={e['p_holm']}, power={e['achieved_power']} — {e['reading']}")
            md.append("")
        if blocking:
            md += ["## What must be fixed first", ""]
            md += [f"- **{f['severity']}** {f['code']}: {f['message']}" for f in blocking]
            md.append("")
        return {"recommendation": rec, "markdown": "\n".join(md)}

    def _fixes_md(self, blocking, by_sev) -> str:
        lines = ["# Required fixes before evidence lock", "",
                 f"BLOCKER: {len(by_sev['BLOCKER'])}  HIGH: {len(by_sev['HIGH'])}  "
                 f"MEDIUM: {len(by_sev['MEDIUM'])}  LOW: {len(by_sev['LOW'])}", ""]
        if not blocking:
            lines += ["No BLOCKER or HIGH finding. Evidence lock is not prevented by the "
                      "statistical audit.", "",
                      "Remaining MEDIUM/LOW items are recorded in stats_audit.json; they do not "
                      "block, and they are not thereby resolved.", ""]
        for f in blocking:
            lines += [f"## {f['severity']} — {f['code']}", "", f["message"], "",
                      f"- affected metrics: {f['affected']['metrics'] or 'n/a'}",
                      f"- affected branches: {f['affected']['branches'] or 'n/a'}",
                      f"- affected claims: {f['affected']['claims'] or 'none mapped'}",
                      f"- remediation: {f['remediation']}", ""]
        for f in by_sev["MEDIUM"] + by_sev["LOW"]:
            lines.append(f"- {f['severity']} {f['code']}: {f['message']}")
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# finding-memory
# ----------------------------------------------------------------------
def _fid(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_sha([str(p) for p in parts])[:10]}"


@register
class FindingMemory(Skill):
    """Keep what was learned, including — especially — what did not work.

    Consolidates v0.2.0 `finding-memory-manager` and `negative-result-curator`.
    Negative results are findings with a tag, not a separate store: separating
    them is how they got curated into a file nobody read. They are written to
    `findings.jsonl` like everything else and *also* projected into
    `negative_findings.jsonl` for the consumers that want only those.
    """

    name = "finding-memory"

    def execute(self, ctx: Context) -> SkillResult:
        ledger = _require_ledger(ctx, self.name)
        meta = ctx.store.read(self.name, "meta_analysis", default={})
        decision = ctx.store.read(self.name, "meta_analysis_decision", default="")
        report = ctx.store.read(self.name, "analysis_report", default="")
        tree = ctx.store.read(self.name, "experiment_tree", default={})
        assumptions = ctx.store.read(self.name, "assumption_tests", default=[])
        repro_codes = ctx.store.read(self.name, "repro_failure_taxonomy", default=[])
        repro_report = ctx.store.read(self.name, "reproduction_report", default={})
        fallbacks = ctx.store.read(self.name, "fallback_decision_log", default=[])
        decisions = ctx.store.read(self.name, "decision_log", default=[])
        prior_work = ctx.store.read(self.name, "closest_prior_work", default=[])
        previous = ctx.store.read(self.name, "findings", default=[])
        warnings: list[str] = []

        if not meta:
            # A findings file built without the auditor's aggregation would be a set of
            # impressions. Not fatal — but it must be visible in every record it produces.
            warnings.append(
                "meta_analysis is absent: findings are being distilled from the raw ledger "
                "alone, without the integrity audit. Every finding below is marked "
                "confidence=low for that reason.")

        findings: list[dict] = []
        negatives: list[dict] = []
        covered_branches: set[str] = set()

        findings += self._from_effects(meta, bool(meta))
        neg, fneg = self._negative_from_effects(meta, bool(meta))
        negatives += neg
        findings += fneg
        neg2, fneg2 = self._negative_from_failures(ledger)
        negatives += neg2
        findings += fneg2
        neg3, fneg3 = self._negative_from_reproduction(repro_codes, repro_report, fallbacks)
        negatives += neg3
        findings += fneg3
        findings += self._engineering_lessons(ledger, assumptions, decisions, prior_work,
                                              tree, report)

        boundaries = self._boundaries(meta, ledger)
        findings += [b["finding"] for b in boundaries]

        for f in findings:
            covered_branches |= set(f.get("scope", {}).get("branches", []))
        for n in negatives:
            covered_branches |= {n["branch"]} if n.get("branch") else set()

        # ---- the gate: nothing is discarded before it is distilled -----
        ledger_branches = {_branch(r) for r in ledger}
        missing = sorted(ledger_branches - covered_branches)
        if missing:
            raise GateBlocked(
                "undistilled_evidence",
                f"branches {missing} appear in the experiment ledger but produced no finding, "
                f"positive or negative.",
                "every branch that was run must leave a record. A branch that is dropped without "
                "a finding is the failure mode this skill exists to prevent: the run is paid for "
                "and the lesson is thrown away.")

        current, superseded = self._version(previous, findings)
        ctx.store.write(self.name, "findings", current + superseded)
        ctx.store.write(self.name, "negative_findings", negatives)
        ctx.store.write(self.name, "boundary_conditions",
                        self._boundaries_md(boundaries, meta, decision))
        ctx.store.write(self.name, "finding_memory_graph",
                        self._graph(current, superseded, negatives, boundaries))
        if superseded:
            warnings.append(
                f"{len(superseded)} earlier finding(s) were superseded by this run's evidence. "
                f"They remain in findings.jsonl with status='superseded' — conflicting memory is "
                f"versioned, never deleted.")
        return SkillResult(
            self.name,
            produced=["findings", "negative_findings", "boundary_conditions",
                      "finding_memory_graph"],
            warnings=warnings,
            detail={"findings": len(current), "superseded": len(superseded),
                    "negative_findings": len(negatives),
                    "boundary_conditions": len(boundaries),
                    "branches_covered": sorted(covered_branches)})

    # ------------------------------------------------------------------
    def _confidence(self, item: dict, audited: bool) -> tuple[str, str]:
        if not audited:
            return "low", "no integrity audit was available when this finding was distilled"
        power = item.get("achieved_power")
        if item.get("p_holm") is not None and item["p_holm"] < 0.05 and (power or 0) >= 0.8:
            return "high", "significant after multiple-comparison correction at adequate power"
        if item.get("p_holm") is not None and item["p_holm"] < 0.05:
            return "medium", (f"significant after correction but achieved power is {power}; the "
                              f"effect size is probably overestimated")
        if (power or 0) >= 0.8:
            return "medium", "adequately powered, and the interval excludes a large effect"
        return "low", "underpowered: this finding constrains the next experiment, not a claim"

    def _record(self, fid, kind, statement, *, context, evidence, confidence, why,
                scope, tags, source_ids) -> dict:
        return {
            "finding_id": fid, "kind": kind, "statement": statement, "context": context,
            "evidence": evidence, "confidence": confidence, "confidence_rationale": why,
            "scope": scope, "tags": sorted(set(tags)), "source_ids": sorted(set(source_ids)),
            "status": "current", "supersedes": [], "created_at": time.time(),
            "advisory": True,
            "advisory_note": "memory is advisory. It informs the next experiment; it can never "
                             "stand in for evidence from the run being written up.",
        }

    def _from_effects(self, meta, audited) -> list[dict]:
        out = []
        for e in (meta.get("evidence", {}) or {}).get("positive", []):
            conf, why = self._confidence(e, audited)
            # branches come from the structured field, never from parsing the human-readable
            # contrast: a label is for reading, and scope decides what this finding governs
            a, b = (list(e.get("branches") or []) + ["?", "?"])[:2]
            out.append(self._record(
                _fid("SCI", e["metric"], e["contrast"]), "scientific",
                f"On {e['metric']}, {e['contrast']}: difference {e['difference']:.4g} "
                f"(95% CI {e.get('ci95')}), Hedges g "
                f"{None if e.get('g') is None else round(e['g'], 3)}, Holm-corrected p "
                f"{e.get('p_holm')}.",
                context="aggregated across seeds within one comparability stratum",
                evidence={"artifacts": ["meta_analysis", "experiment_ledger"],
                          "achieved_power": e.get("achieved_power")},
                confidence=conf, why=why,
                scope={"branches": [a, b], "metrics": [e["metric"]],
                       "holds_under": "the evaluator version and environment of this stratum only"},
                tags=["positive", "confirmatory-candidate"],
                source_ids=["meta_analysis"]))
        return out

    def _negative_from_effects(self, meta, audited):
        """Nulls, reversals and undetermined comparisons — each with its own reading.

        The distinction that matters here is between 'the method did not help'
        and 'we could not tell'. Collapsing them is how an underpowered study
        becomes a null result in a later literature review.
        """
        negatives, findings = [], []
        ev = meta.get("evidence", {}) or {}
        for e in ev.get("negative", []):
            kind = "worse_than_comparator"
            statement = (f"On {e['metric']}, {e['contrast']} is worse after correction: "
                         f"difference {e['difference']:.4g} (95% CI {e.get('ci95')}).")
            pivot = ("the direction is informative: test whether the mechanism that hurts here "
                     "helps in the regime it was designed for, rather than retrying the same "
                     "configuration.")
            negatives.append(self._negative(e, kind, statement, pivot, audited, implementation=False))
        for e in ev.get("null", []):
            reading = e.get("reading", "")
            if reading.startswith("null with adequate precision"):
                kind, implementation = "scientific_null", False
                statement = (f"On {e['metric']}, {e['contrast']} shows no effect worth "
                             f"claiming: difference {e['difference']:.4g} "
                             f"(95% CI {e.get('ci95')}), and the standardised-effect interval "
                             f"excludes |g| >= {(e.get('equivalence') or {}).get('bound')}.")
                pivot = ("a real null. Record it and narrow the claim; re-running for a different "
                         "answer is the behaviour this record exists to prevent.")
            else:
                kind, implementation = "undetermined_underpowered", False
                statement = (f"On {e['metric']}, {e['contrast']} is undetermined: achieved power "
                             f"{e.get('achieved_power')}. Absence of evidence, not evidence of "
                             f"absence.")
                pivot = "add seeds before drawing any conclusion from this contrast."
            negatives.append(self._negative(e, kind, statement, pivot, audited,
                                            implementation=implementation))
        for n in negatives:
            findings.append(self._record(
                n["finding_id"], "negative", n["statement"],
                context=n["conditions"],
                evidence=n["evidence"], confidence=n["confidence"],
                why=n["confidence_rationale"],
                scope={"branches": n["branches"], "metrics": [n["metric"]] if n.get("metric") else [],
                       "holds_under": n["conditions"]},
                tags=["negative", n["kind"]], source_ids=n["source_ids"]))
        return negatives, findings

    def _negative(self, e, kind, statement, pivot, audited, *, implementation) -> dict:
        conf, why = self._confidence(e, audited)
        branches = [str(b) for b in (e.get("branches") or []) if b]
        return {
            "finding_id": _fid("NEG", e.get("metric"), e.get("contrast"), kind),
            "kind": kind, "metric": e.get("metric"), "contrast": e.get("contrast"),
            "branch": branches[0] if branches else None, "branches": branches,
            "statement": statement,
            "is_implementation_failure": implementation,
            "distinction": ("the code ran and produced metrics, so this is a statement about the "
                            "method rather than about the implementation"
                            if not implementation else
                            "the run did not complete, so this says nothing about the science yet"),
            "conditions": "as configured in this stratum, at the seed count recorded",
            "evidence": {"artifacts": ["meta_analysis", "experiment_ledger"],
                         "achieved_power": e.get("achieved_power"), "p_holm": e.get("p_holm")},
            "confidence": conf, "confidence_rationale": why,
            "pivot_suggestion": pivot,
            "retention": "negative evidence is never deleted because it weakens the narrative; "
                         "it is superseded only by better evidence on the same question.",
            "tags": ["negative", kind],
            "source_ids": ["meta_analysis"],
        }

    def _negative_from_failures(self, ledger):
        """A branch that crashed is a finding too — an engineering one, clearly labelled."""
        negatives, findings = [], []
        by_branch: dict[str, list[dict]] = {}
        for r in ledger:
            by_branch.setdefault(_branch(r), []).append(r)
        for branch, rows in sorted(by_branch.items()):
            failed = [r for r in rows if not _ok(r)]
            if not failed:
                continue
            rate = len(failed) / len(rows)
            statuses = sorted({str(r.get("status")) for r in failed})
            n = {
                "finding_id": _fid("NEG", branch, "execution_failure"),
                "kind": "implementation_failure", "metric": None, "contrast": None,
                "branch": branch, "branches": [branch],
                "statement": (f"Branch '{branch}' failed in {len(failed)} of {len(rows)} runs "
                              f"({rate:.0%}); statuses {statuses}."),
                "is_implementation_failure": True,
                "distinction": ("execution failure, not a scientific null: nothing follows from "
                                "this about whether the idea works."),
                "conditions": f"seeds {sorted({str(_seed(r)) for r in failed})}",
                "evidence": {"artifacts": ["experiment_ledger"],
                             "run_ids": [r.get("run_id") for r in failed][:50]},
                "confidence": "high", "confidence_rationale":
                    "the failure is recorded directly in the append-only ledger",
                "pivot_suggestion": ("diagnose before re-running: if the failures correlate with "
                                     "the condition under test, the failure rate is the result."),
                "retention": "kept so the same dead end is not re-run in a later session",
                "tags": ["negative", "engineering", "implementation_failure"],
                "source_ids": ["experiment_ledger"],
            }
            negatives.append(n)
            findings.append(self._record(
                n["finding_id"], "negative", n["statement"], context=n["conditions"],
                evidence=n["evidence"], confidence="high", why=n["confidence_rationale"],
                scope={"branches": [branch], "metrics": [], "holds_under": n["conditions"]},
                tags=n["tags"], source_ids=n["source_ids"]))
        return negatives, findings

    def _negative_from_reproduction(self, codes, report, fallbacks):
        negatives, findings = [], []
        for c in codes if isinstance(codes, list) else []:
            code = c.get("code")
            if not code:
                continue
            n = {
                "finding_id": _fid("NEG", "repro", code, c.get("target")),
                "kind": "reproduction_failure", "metric": None, "contrast": None,
                "branch": None, "branches": [],
                "statement": (f"Reproduction of {c.get('target', 'the source artifact')} hit "
                              f"{code}."),
                "is_implementation_failure": True,
                "distinction": ("a reproduction shortfall constrains what may be claimed; it is "
                                "not evidence that the original result is wrong."),
                "conditions": f"reproduction level {report.get('level', 'unknown')}",
                "evidence": {"artifacts": ["repro_failure_taxonomy", "reproduction_report"]},
                "confidence": "high",
                "confidence_rationale": "recorded by the reproducer at the time of the attempt",
                "pivot_suggestion": "the failure taxonomy is itself evidence for a "
                                    "reproducibility-methodology direction",
                "retention": "kept: the next project should not pay this cost again",
                "tags": ["negative", "reproduction"], "source_ids": ["repro_failure_taxonomy"],
            }
            negatives.append(n)
            findings.append(self._record(
                n["finding_id"], "negative", n["statement"], context=n["conditions"],
                evidence=n["evidence"], confidence="high", why=n["confidence_rationale"],
                scope={"branches": [], "metrics": [], "holds_under": n["conditions"]},
                tags=n["tags"], source_ids=n["source_ids"]))
        return negatives, findings

    def _engineering_lessons(self, ledger, assumptions, decisions, prior_work,
                             tree=None, report="") -> list[dict]:
        """Reusable lessons, tagged apart from scientific conclusions.

        Kept separate by tag because an engineering lesson generalises across
        projects while a scientific conclusion does not, and merging them is how
        a local workaround becomes a claim about the world.
        """
        out = []
        tree = tree if isinstance(tree, dict) else {}
        seeds = {b: sorted({str(_seed(r)) for r in ledger if _branch(r) == b})
                 for b in sorted({_branch(r) for r in ledger})}
        explored = len(tree.get("nodes", [])) if isinstance(tree, dict) else 0
        out.append(self._record(
            _fid("ENG", "seed_budget", sorted(seeds)), "engineering",
            "Seed budget actually spent per branch: "
            + ", ".join(f"{b}={len(s)}" for b, s in seeds.items())
            + f". {explored} node(s) of the search tree were explored. Any later comparison "
              f"inherits this sample size and its power.",
            context="derived from the ledger, not from the plan",
            evidence={"artifacts": ["experiment_ledger", "experiment_tree"], "seeds": seeds,
                      "search_tree_nodes": explored,
                      "analyst_narrative_present": bool(str(report).strip())},
            confidence="high", why="counted directly from the append-only ledger",
            scope={"branches": sorted(seeds), "metrics": [],
                   "holds_under": "this project's compute envelope"},
            tags=["engineering", "reusable"], source_ids=["experiment_ledger"]))
        for a in (assumptions if isinstance(assumptions, list) else [])[:20]:
            if not isinstance(a, dict):
                continue
            out.append(self._record(
                _fid("ENG", "assumption", a.get("assumption_id", a.get("id", ""))), "methodological",
                f"Assumption probe: {str(a.get('statement') or a.get('summary') or a)[:200]}",
                context="cheap falsification probe proposed during ideation",
                evidence={"artifacts": ["assumption_tests"]},
                confidence="low", why="a probe is a plan, not a measurement",
                scope={"branches": [], "metrics": [], "holds_under": "untested unless a ledger "
                                                                    "entry references it"},
                tags=["methodological"], source_ids=["assumption_tests"]))
        for d in (decisions if isinstance(decisions, list) else [])[:50]:
            if isinstance(d, dict) and d.get("rejected"):
                out.append(self._record(
                    _fid("ENG", "rejected", json.dumps(d.get("rejected"), sort_keys=True)),
                    "methodological",
                    f"Directions rejected at the human gate: {d.get('rejected')}. Retained so a "
                    f"later session does not rediscover them as novel.",
                    context="user-feedback-gate decision",
                    evidence={"artifacts": ["decision_log"]},
                    confidence="high", why="a recorded human decision",
                    scope={"branches": [], "metrics": [], "holds_under": "this project"},
                    tags=["methodological", "rejected-but-retained"], source_ids=["decision_log"]))
        return out

    # ------------------------------------------------------------------
    def _boundaries(self, meta, ledger) -> list[dict]:
        """Where the method stops working — a first-class finding, not a caveat."""
        out = []

        def add(fid_parts, statement, branches, metrics, why, tags):
            f = self._record(_fid("BND", *fid_parts), "boundary", statement,
                             context="boundary condition derived from this run's evidence",
                             evidence={"artifacts": ["meta_analysis", "experiment_ledger"]},
                             confidence="medium", why=why,
                             scope={"branches": branches, "metrics": metrics,
                                    "holds_under": "outside this boundary the method is "
                                                   "untested, not proven to work"},
                             tags=["boundary"] + tags, source_ids=["meta_analysis"])
            out.append({"finding": f, "statement": statement, "branches": branches,
                        "metrics": metrics})

        strata = meta.get("strata", []) or []
        if len(strata) > 1:
            add(["strata"],
                "Results are bounded to a single evaluator version and environment: the ledger "
                "contains more than one, and they were not combined. Nothing here supports a "
                "claim that spans them.",
                sorted({b for s in strata for b in s.get("branches", [])}), [],
                "the auditor refused cross-stratum aggregation", ["comparability"])
        for e in (meta.get("evidence", {}) or {}).get("null", []):
            if "underpowered" in e.get("reading", "") or "undetermined" in e.get("reading", ""):
                a = [str(b) for b in (e.get("branches") or [])]
                add(["power", e["metric"], e["contrast"]],
                    f"On {e['metric']}, {e['contrast']} is outside the resolving power of this "
                    f"experiment (achieved power {e.get('achieved_power')}). The method's "
                    f"behaviour here is unknown, and must be described as unknown.",
                    a, [e["metric"]], "achieved power below target", ["resolution-limit"])
        for branch, fr in sorted((meta.get("failure_rates") or {}).items()):
            if fr.get("rate", 0) > 0:
                add(["failure", branch],
                    f"Branch '{branch}' does not run reliably: {fr['failed']} of {fr['runs']} "
                    f"runs failed. Wherever that failure mode lives, the method stops working.",
                    [branch], [], "failures observed in the ledger", ["reliability"])
        if not out:
            branches = sorted({_branch(r) for r in ledger})
            add(["untested_regions"],
                "No boundary was probed: every run used the same configuration family, so the "
                "edges of the method's applicability are simply untested. Claiming generality "
                "from this evidence is unsupported.",
                branches, sorted({m for r in ledger for m in _numeric_metrics(r)}),
                "no ablation or stress condition appears in the ledger", ["untested"])
        return out

    def _boundaries_md(self, boundaries, meta, decision) -> str:
        lines = ["# Boundary conditions", "",
                 "Where the method stops working, and where it was never tested. These are "
                 "findings, not caveats: each one is also a record in findings.jsonl.", ""]
        for b in boundaries:
            lines += [f"## {b['finding']['finding_id']}", "", b["statement"], "",
                      f"- branches: {b['branches'] or 'n/a'}",
                      f"- metrics: {b['metrics'] or 'n/a'}",
                      f"- tags: {b['finding']['tags']}", ""]
        rec = meta.get("recommendation")
        if rec:
            lines += ["## Relation to the keep/kill decision", "",
                      f"The integrity auditor recommended **{rec}**. Boundary conditions narrow "
                      f"whatever claim survives that recommendation; they do not soften it.", ""]
        return "\n".join(lines)

    def _version(self, previous, new) -> tuple[list[dict], list[dict]]:
        """New evidence supersedes old memory on the same question; it never erases it."""
        # Keyed on the question (which branches, which metric), not on the answer's
        # kind: a positive finding that this run contradicts must be superseded by
        # the negative one, and keying on kind would let the two coexist as truths.
        def key_of(f):
            sc = f.get("scope") or {}
            return (tuple(sc.get("metrics", [])), tuple(sc.get("branches", [])))

        new_keys = {key_of(f) for f in new}
        new_by_key = {key_of(f): f for f in new}
        superseded = []
        new_ids = {f["finding_id"] for f in new}
        for old in previous if isinstance(previous, list) else []:
            if not isinstance(old, dict):
                continue
            key = key_of(old)
            if old.get("status") == "superseded":
                superseded.append(old)
                continue
            if key in new_keys and old.get("finding_id") not in new_ids:
                o = dict(old)
                o["status"] = "superseded"
                o["superseded_by"] = new_by_key[key]["finding_id"]
                o["superseded_reason"] = ("newer evidence on the same branches and metric. The "
                                          "record is retained: a memory that quietly changed its "
                                          "mind cannot be audited.")
                superseded.append(o)
        return new, superseded

    def _graph(self, current, superseded, negatives, boundaries) -> dict:
        nodes = [{"id": f["finding_id"], "kind": f["kind"], "status": f["status"],
                  "confidence": f["confidence"], "tags": f["tags"]}
                 for f in current + superseded]
        edges = []
        for f in current:
            for src in f["source_ids"]:
                edges.append({"from": f["finding_id"], "to": f"artifact:{src}",
                              "rel": "derived_from"})
        bnd = [b["finding"] for b in boundaries]
        for f in current:
            if f["kind"] != "scientific":
                continue
            for b in bnd:
                if set(f["scope"]["branches"]) & set(b["scope"]["branches"]):
                    edges.append({"from": f["finding_id"], "to": b["finding_id"],
                                  "rel": "bounded_by"})
        by_metric: dict[str, list[dict]] = {}
        for f in current:
            for m in f["scope"]["metrics"]:
                by_metric.setdefault(m, []).append(f)
        for m, fs in by_metric.items():
            pos = [f for f in fs if "positive" in f["tags"]]
            neg = [f for f in fs if "negative" in f["tags"]]
            for p in pos:
                for n in neg:
                    if set(p["scope"]["branches"]) & set(n["scope"]["branches"]):
                        edges.append({"from": p["finding_id"], "to": n["finding_id"],
                                      "rel": "contradicts",
                                      "note": "both are retained; the conflict is the record"})
        for s in superseded:
            if s.get("superseded_by"):
                edges.append({"from": s["superseded_by"], "to": s["finding_id"],
                              "rel": "supersedes"})
        return {"generated_at": time.time(), "nodes": nodes, "edges": edges,
                "counts": {"current": len(current), "superseded": len(superseded),
                           "negative": len(negatives), "boundary": len(bnd)},
                "retrieval_rule": "retrieve by scope overlap with the current task and show the "
                                  "provenance edge; a finding presented without its source is "
                                  "indistinguishable from an assumption."}
