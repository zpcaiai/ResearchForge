"""Feature gating on the Python side.

A self-hosted product cannot make its licence cryptographically unbypassable —
the customer has the source, and pretending otherwise leads to hostile DRM that
punishes honest users and stops nobody else. What a licence CAN do is make bypass
a deliberate act rather than an accident, and leave a record.

So the gate does three things and no more:
  1. refuses the gated skill with a message naming the feature and the tier;
  2. writes the refusal to provenance, so the run's own history shows it;
  3. when a gated skill runs unlicensed because the operator overrode the gate,
     stamps that fact into the run, where the release manifest will find it.

The TypeScript orchestrator has its own copy of this check. That is not
redundancy: `researchforge.runner` is invokable directly, so a gate that lives
only in the orchestrator gates nothing.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: skill -> the paid feature it belongs to. Skills absent from this map are free.
#: The split follows what a buyer can verify they are getting, not what was
#: expensive to build: everything up to and including the human selection gate is
#: free, because that is where the product's actual differentiator is visible and
#: a buyer must be able to see it before paying.
FEATURE_OF_SKILL: dict[str, str] = {
    "codebase-scaffolder": "experiment-engine",
    "experiment-runner": "experiment-engine",
    "evaluator-builder": "experiment-engine",
    "manuscript-builder": "manuscript",
    "claim-citation-auditor": "manuscript",
    "review-simulator": "manuscript",
    "figure-factory": "manuscript",
    "deck-factory": "deck",
    "release-gate": "release-gate",
}

COMMUNITY_FEATURES = ("ingest", "literature", "reproduction", "innovation", "human-gate")


@dataclass(frozen=True)
class LicenseState:
    edition: str
    features: tuple[str, ...]
    valid: bool
    reason: str
    licensee: str | None = None

    def allows(self, feature: str) -> bool:
        return self.valid and feature in self.features


def _path() -> Path:
    return Path(os.environ.get("RESEARCHFORGE_LICENSE",
                               str(Path.home() / ".researchforge" / "license.json")))


def load() -> LicenseState:
    """Read and verify the licence.

    Signature verification needs the public key the CLI was built with. When it is
    absent — a source checkout, a dev build — the licence is reported UNVERIFIED
    and treated as community. Trusting an unverified licence file would make the
    whole scheme a formality: anyone could write one.
    """
    p = _path()
    if not p.exists():
        return LicenseState("community", COMMUNITY_FEATURES, True, "no licence file")
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        lic = blob["license"]
    except Exception as e:  # noqa: BLE001
        return LicenseState("community", COMMUNITY_FEATURES, True,
                            f"licence file at {p} is unreadable ({e}); treating as community")

    pub = os.environ.get("RESEARCHFORGE_LICENSE_PUBKEY_PEM")
    if not pub:
        pk = Path(__file__).resolve().parents[2] / "packages/cli/src/pubkey.ts"
        if pk.exists():
            txt = pk.read_text(encoding="utf-8")
            if "__RESEARCHFORGE_LICENSE_PUBLIC_KEY_PEM__" not in txt:
                m = txt.split("-----BEGIN PUBLIC KEY-----")
                if len(m) > 1:
                    pub = "-----BEGIN PUBLIC KEY-----" + m[1].split("-----END PUBLIC KEY-----")[0] \
                          + "-----END PUBLIC KEY-----\n"
    if not pub:
        return LicenseState("community", COMMUNITY_FEATURES, True,
                            "no public key available to verify the licence; treating as community")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        key = load_pem_public_key(pub.encode())
        key.verify(base64.b64decode(blob["signature"]),
                   json.dumps(lic, separators=(",", ":")).encode()
                   if False else json.dumps(lic).encode())
    except Exception as e:  # noqa: BLE001
        return LicenseState("community", COMMUNITY_FEATURES, True,
                            f"licence signature does not verify ({type(e).__name__}); "
                            f"treating as community")
    exp = lic.get("expires")
    if exp:
        import datetime as _dt
        try:
            if _dt.date.fromisoformat(str(exp)[:10]) < _dt.date.today():
                return LicenseState("community", COMMUNITY_FEATURES, True,
                                    f"licence expired {exp}; treating as community")
        except ValueError:
            pass
    return LicenseState(lic.get("edition", "unknown"),
                        tuple(lic.get("features") or ()), True, "verified",
                        lic.get("licensee"))


def check(skill: str, *, allow_override: bool | None = None) -> dict[str, Any] | None:
    """Return None when permitted, or a refusal record.

    `RESEARCHFORGE_ALLOW_UNLICENSED=1` overrides the gate. It exists because a
    self-hosted customer can patch this file out in ten seconds anyway, and an
    override that leaves a record is strictly better than a patch that does not.
    The record is what the release manifest reads.
    """
    feature = FEATURE_OF_SKILL.get(skill)
    if feature is None:
        return None
    state = load()
    if state.allows(feature):
        return None
    override = (os.environ.get("RESEARCHFORGE_ALLOW_UNLICENSED") == "1"
                if allow_override is None else allow_override)
    rec = {"skill": skill, "feature": feature, "edition": state.edition,
           "licence_reason": state.reason, "overridden": bool(override)}
    if override:
        return {**rec, "permitted": True,
                "note": ("ran without a licence for this feature under an explicit override. "
                         "This is recorded in provenance and surfaces in the release manifest.")}
    return {**rec, "permitted": False,
            "message": (f"'{skill}' is part of the '{feature}' feature, which the "
                        f"'{state.edition}' edition does not include ({state.reason}).\n"
                        f"  Community includes: {', '.join(COMMUNITY_FEATURES)}.\n"
                        f"  Set RESEARCHFORGE_ALLOW_UNLICENSED=1 to proceed anyway; the run will "
                        f"carry that fact in its provenance and release manifest.")}
