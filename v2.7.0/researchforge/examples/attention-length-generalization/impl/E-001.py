"""Method under test for E-001 — attention entropy vs sequence length.

Written by the researcher, not by the scaffolder. The scaffolder deliberately
refuses to write this file: a generator that supplies the method it also measures
produces evidence about itself.

This is a real, small, CPU-only measurement — scaled dot-product attention over
random inputs at increasing sequence length — not a stand-in. It is deliberately
modest in scope, and the claim it can support is correspondingly modest, which is
what CM_NONE permits at RL0.
"""
from __future__ import annotations

import numpy as np

D_MODEL = 64
N_HEADS = 4
TRAIN_LEN = 128          # the length regime the hypothetical model was trained on
PROBE_LENS = (64, 128, 256, 512, 1024)
# A distribution over L positions is "diffuse" once its entropy exceeds this
# fraction of the uniform maximum. Fixed before running, not tuned to the result.
DIFFUSE_THRESHOLD = 0.90


def _attention_entropy(rng: np.random.Generator, length: int) -> tuple[float, float]:
    """Return (mean normalized entropy, fraction of queries attending past TRAIN_LEN)."""
    q = rng.normal(0, 1, (N_HEADS, length, D_MODEL)) / np.sqrt(D_MODEL)
    k = rng.normal(0, 1, (N_HEADS, length, D_MODEL))
    scores = np.einsum("hqd,hkd->hqk", q, k)
    scores -= scores.max(axis=-1, keepdims=True)
    w = np.exp(scores)
    w /= w.sum(axis=-1, keepdims=True)

    ent = -(w * np.log(w + 1e-12)).sum(axis=-1)
    normalized = ent / np.log(length)                       # 1.0 == uniform
    argmax_pos = w.argmax(axis=-1)
    beyond = float((argmax_pos >= TRAIN_LEN).mean()) if length > TRAIN_LEN else 0.0
    return float(normalized.mean()), beyond


def candidate(seed: int, config: dict) -> dict:
    rng = np.random.default_rng(seed)
    diffuse, beyond = [], []
    for L in PROBE_LENS:
        e, b = _attention_entropy(rng, L)
        diffuse.append(e >= DIFFUSE_THRESHOLD)
        beyond.append(b)
    return {
        # how often the claimed mechanism (entropy collapse toward uniform) fires
        "mechanism_activation": float(np.mean(diffuse)),
        # rate of the enumerated failure mode: attention landing outside the trained regime
        "failure_mode_incidence": float(np.mean(beyond)),
    }


def baseline(seed: int, config: dict) -> dict:
    """Reference condition: the same probe restricted to the trained length regime.

    At RL0 there is no external baseline to compare against, so the reference is
    our own unmodified implementation inside the regime it was designed for. Any
    difference measured here is a difference in input regime, nothing more.
    """
    rng = np.random.default_rng(seed)
    e, b = _attention_entropy(rng, TRAIN_LEN)
    return {"mechanism_activation": float(e >= DIFFUSE_THRESHOLD),
            "failure_mode_incidence": float(b)}
