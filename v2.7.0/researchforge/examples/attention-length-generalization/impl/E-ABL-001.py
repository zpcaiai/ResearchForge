"""Isolation of M-001 — remove the mechanism, measure THE SAME THING.

Rewritten 2026-08-12. This file used to return `primary` and `seed_dispersion`
while the experiment it ablates measured `mechanism_activation` and
`failure_mode_incidence`. Disjoint metric sets, so the contrast the ablation
exists to make could never be constructed from the ledger — the ablation was
structurally incapable of answering the only question it was created to answer.

An ablation measures the metric the claim rests on, with the mechanism removed.
Anything else is a different experiment wearing an ablation's name.
"""
from __future__ import annotations

import numpy as np

D_MODEL = 64
N_HEADS = 4
TRAIN_LEN = 128
PROBE_LENS = (64, 128, 256, 512, 1024)
DIFFUSE_THRESHOLD = 0.90


def _probe(rng: np.random.Generator, length: int, *, softmax: bool) -> tuple[float, float]:
    """The same probe as E-001. `softmax=False` is the ablation: M-001 removed.

    M-001 is the softmax normalisation itself — the mechanism the length-generalisation
    claim attributes the entropy drift to. Replacing it with uniform attention keeps
    the compute budget identical and changes only the mechanism under test.
    """
    q = rng.normal(0, 1, (N_HEADS, length, D_MODEL)) / np.sqrt(D_MODEL)
    k = rng.normal(0, 1, (N_HEADS, length, D_MODEL))
    scores = np.einsum("hqd,hkd->hqk", q, k)
    if softmax:
        scores -= scores.max(axis=-1, keepdims=True)
        w = np.exp(scores)
        w /= w.sum(axis=-1, keepdims=True)
    else:
        w = np.full_like(scores, 1.0 / length)      # mechanism removed, budget matched
    ent = -(w * np.log(w + 1e-12)).sum(axis=-1) / np.log(length)
    beyond = float((w.argmax(axis=-1) >= TRAIN_LEN).mean()) if length > TRAIN_LEN else 0.0
    return float(ent.mean()), beyond


def _run(seed: int, softmax: bool) -> dict:
    rng = np.random.default_rng(seed)
    diffuse, beyond, ents = [], [], []
    for L in PROBE_LENS:
        e, b = _probe(rng, L, softmax=softmax)
        diffuse.append(e >= DIFFUSE_THRESHOLD)
        beyond.append(b)
        ents.append(e)
    return {
        "mechanism_activation": float(np.mean(diffuse)),
        "failure_mode_incidence": float(np.mean(beyond)),
        # dispersion of the underlying quantity within one run, so the ablation can be
        # judged against noise rather than against a point estimate
        "seed_dispersion": float(np.std(ents, ddof=1)),
    }


def candidate(seed: int, config: dict) -> dict:
    """Mechanism REMOVED. This is the ablated arm."""
    return _run(seed, softmax=False)


def baseline(seed: int, config: dict) -> dict:
    """Our own full method, mechanism enabled. The internal reference."""
    return _run(seed, softmax=True)
