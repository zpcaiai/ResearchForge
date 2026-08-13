"""Method under test for E-002 — does the metric discriminate, and can it be gamed?

Evaluation-methodology work, which is one of the two innovation modes CM_NONE
leaves open. It measures the measurement, so it needs no external baseline.
"""
from __future__ import annotations

import numpy as np

N_PAIRS = 200


def _metric(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=-1)


def candidate(seed: int, config: dict) -> dict:
    rng = np.random.default_rng(seed)
    # discriminability: how reliably the metric separates two conditions that
    # genuinely differ by a known effect
    a = rng.normal(0.0, 1.0, (N_PAIRS, 32))
    b = rng.normal(0.3, 1.0, (N_PAIRS, 32))
    correct = (_metric(b) > _metric(a)).mean()

    # gaming sensitivity: how much a degenerate constant submission moves the score.
    # A metric a constant can win is not measuring the thing it claims to.
    honest = _metric(rng.normal(0.3, 1.0, (N_PAIRS, 32)))
    degenerate = np.full(N_PAIRS, 0.3)
    gaming = float(np.mean(degenerate >= honest))

    return {"discriminability": float(correct), "gaming_sensitivity": gaming}


def baseline(seed: int, config: dict) -> dict:
    rng = np.random.default_rng(seed)
    a = rng.normal(0.0, 1.0, (N_PAIRS, 32))
    b = rng.normal(0.0, 1.0, (N_PAIRS, 32))   # no true effect: chance is the reference
    return {"discriminability": float((_metric(b) > _metric(a)).mean()),
            "gaming_sensitivity": 0.5}
