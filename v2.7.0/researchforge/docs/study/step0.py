#!/usr/bin/env python3
"""Protocol §4 step 0 — measure each metric's own seed variance BEFORE any
tolerance is chosen.

Why this must come first: a "reproduction failure" declared against a fixed +-5%
tolerance is meaningless if the metric's own run-to-run dispersion is wider than
5%. Pham et al. (2020) trained the same LeNet5 sixteen times and got accuracies
from 8.6% to 99.0%; against that, +-5% would classify noise as a failed
reproduction, every time.

The study's 20 papers produced no runnable experiment, so step 0 could not be done
there. It CAN be done here, on the worked example's experiments, which do run —
and the result replaces the hard-coded tolerance floor in result-reproducer with
a measured one.
"""
from __future__ import annotations

import importlib.util, json, statistics, sys
from pathlib import Path

N_SEEDS = 30          # protocol asks for >=3; 30 gives a usable estimate of the SD itself
REL_FLOOR = 0.02      # the protocol's relative floor
SD_MULTIPLIER = 2.0


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def probe(mod, arm: str = "candidate"):
    vals: dict[str, list[float]] = {}
    for seed in range(1, N_SEEDS + 1):
        out = getattr(mod, arm)(seed, {})
        for k, v in out.items():
            vals.setdefault(k, []).append(float(v))
    return vals


out = {"n_seeds": N_SEEDS, "metrics": {}}
for fn, label in (("impl_e001.py", "E-001"), ("impl_e002.py", "E-002"), ("impl_abl.py", "E-ABL-001")):
    p = Path(fn)
    if not p.exists():
        continue
    mod = load(p, fn.replace(".py", ""))
    for arm in ("candidate", "baseline"):
        if not hasattr(mod, arm):
            continue
        for metric, vs in probe(mod, arm).items():
            mean = statistics.fmean(vs)
            sd = statistics.stdev(vs)
            key = f"{label}/{arm}/{metric}"
            # A metric that is identically zero across every seed is CONSTANT, not
            # infinitely noisy. Reporting inf there would be an artefact of dividing
            # by the mean, and it would push the tolerance to "anything goes" for
            # precisely the metric where an exact match is the right test.
            if sd == 0:
                out["metrics"][key] = {
                    "mean": round(mean, 6), "sd": 0.0, "relative_sd": 0.0,
                    "min": round(min(vs), 6), "max": round(max(vs), 6),
                    "spread_as_fraction_of_mean": 0.0,
                    "constant": True,
                    "tolerance_relative": 0.0 if mean == 0 else REL_FLOOR,
                    "tolerance_source": ("constant across all seeds; require exact match"
                                         if mean == 0 else f"constant; relative floor {REL_FLOOR}"),
                    "floor_would_have_been_wrong_by": None,
                }
                continue
            rel_sd = sd / abs(mean) if mean else float("inf")
            if mean == 0:
                # non-zero dispersion around a zero mean: relative tolerance is
                # undefined, so state the absolute one instead of a fake ratio
                out["metrics"][key] = {
                    "mean": 0.0, "sd": round(sd, 6), "relative_sd": None,
                    "min": round(min(vs), 6), "max": round(max(vs), 6),
                    "spread_as_fraction_of_mean": None,
                    "tolerance_absolute": round(SD_MULTIPLIER * sd, 6),
                    "tolerance_relative": None,
                    "tolerance_source": "mean is zero; only an absolute tolerance is meaningful",
                    "floor_would_have_been_wrong_by": None,
                }
                continue
            # the protocol's rule, now with a measured term instead of a guess
            tol = max(SD_MULTIPLIER * rel_sd, REL_FLOOR)
            out["metrics"][key] = {
                "mean": round(mean, 6), "sd": round(sd, 6),
                "relative_sd": round(rel_sd, 6),
                "min": round(min(vs), 6), "max": round(max(vs), 6),
                "spread_as_fraction_of_mean": round((max(vs) - min(vs)) / abs(mean), 6) if mean else None,
                "tolerance_relative": round(tol, 6),
                "tolerance_source": ("2x measured relative SD" if SD_MULTIPLIER * rel_sd > REL_FLOOR
                                     else f"relative floor {REL_FLOOR}"),
                "floor_would_have_been_wrong_by": (round(SD_MULTIPLIER * rel_sd / REL_FLOOR, 1)
                                                   if SD_MULTIPLIER * rel_sd > REL_FLOOR else None),
            }
Path("step0_results.json").write_text(json.dumps(out, indent=1))
print(f"{'metric':<38} {'mean':>10} {'rel SD':>8} {'spread':>8} {'tolerance':>10}  source")
for k, v in out["metrics"].items():
    rs = f"{v['relative_sd']:>7.2%}" if v.get("relative_sd") is not None else "      -"
    sp = f"{v['spread_as_fraction_of_mean']:>7.1%}" if v.get("spread_as_fraction_of_mean") is not None else "      -"
    tl = (f"{v['tolerance_relative']:>9.2%}" if v.get("tolerance_relative") is not None
          else f"{v.get('tolerance_absolute', 0):>9.4f}a")
    print(f"{k:<38} {v['mean']:>10.4f} {rs} {sp} {tl}  {v['tolerance_source']}")
wide = [k for k, v in out["metrics"].items()
        if v.get("tolerance_relative") is not None and v["tolerance_relative"] > REL_FLOOR]
print(f"\n{len(wide)}/{len(out['metrics'])} metrics need a tolerance WIDER than the 2% floor.")
if wide:
    worst = max(((k, v) for k, v in out["metrics"].items()
                 if v.get("tolerance_relative") is not None),
                key=lambda kv: kv[1]["tolerance_relative"])
    print(f"widest: {worst[0]} needs {worst[1]['tolerance_relative']:.1%} — "
          f"{worst[1]['tolerance_relative']/REL_FLOOR:.0f}x the floor.")
    print("Judging that metric against a fixed 2% tolerance would classify its own noise as a failed reproduction.")
