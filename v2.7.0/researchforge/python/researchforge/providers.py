"""External providers: language models and scholarly APIs.

Two rules shape this module.

1. **BYOK.** The user brings their own keys. Nothing here ships a credential, and
   no credential is ever written to an artifact, a log or the quota ledger.

2. **An unavailable provider is a run condition, not an empty result.** The most
   dangerous failure in this system is a literature search that returns nothing
   because the quota ran out, and a novelty verifier that reads that emptiness as
   evidence of novelty. Every provider therefore reports *why* it returned what
   it returned, and exhaustion raises rather than returning `[]`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .errors import ProviderUnavailable


# --------------------------------------------------------------------------
# quota
# --------------------------------------------------------------------------
@dataclass
class QuotaState:
    provider: str
    calls: int = 0
    max_calls: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    max_usd: float | None = None
    throttled: bool = False
    last_error: str | None = None

    def exhausted(self) -> bool:
        return ((self.max_calls is not None and self.calls >= self.max_calls)
                or (self.max_usd is not None and self.usd >= self.max_usd))


class QuotaLedger:
    """Per-run record of what was spent where. Never contains credentials."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, QuotaState] = {}

    def budget(self, provider: str, *, max_calls: int | None = None,
               max_usd: float | None = None) -> None:
        s = self.state.setdefault(provider, QuotaState(provider))
        s.max_calls, s.max_usd = max_calls, max_usd

    def record(self, provider: str, *, tokens_in: int = 0, tokens_out: int = 0,
               usd: float = 0.0, endpoint: str = "", error: str | None = None) -> None:
        s = self.state.setdefault(provider, QuotaState(provider))
        s.calls += 1
        s.tokens_in += tokens_in
        s.tokens_out += tokens_out
        s.usd += usd
        if error:
            s.last_error = error
            if "429" in error or "rate" in error.lower():
                s.throttled = True
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "provider": provider, "endpoint": endpoint,
                                "tokens_in": tokens_in, "tokens_out": tokens_out, "usd": usd,
                                "error": error}, ensure_ascii=False) + "\n")

    def check(self, provider: str) -> None:
        s = self.state.get(provider)
        if s and s.exhausted():
            raise ProviderUnavailable(
                f"quota exhausted for '{provider}' after {s.calls} calls / ${s.usd:.2f}. "
                f"This is a run condition, not an empty result set: any novelty judgment "
                f"made from here on must be marked UNKNOWN_COVERAGE."
            )

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(provider=s.provider, calls=s.calls, tokens_in=s.tokens_in,
                     tokens_out=s.tokens_out, usd=round(s.usd, 4),
                     throttled=s.throttled, exhausted=s.exhausted(),
                     last_error=s.last_error) for s in self.state.values()]


# --------------------------------------------------------------------------
# language models
# --------------------------------------------------------------------------
@dataclass
class ModelResponse:
    text: str
    model: str
    synthetic: bool = False          # true => output is not from a real model
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0


class ModelProvider(Protocol):
    name: str
    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 json_mode: bool = False) -> ModelResponse: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-4-6", api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderUnavailable(
                "ANTHROPIC_API_KEY is not set. ResearchForge is BYOK: it never ships a key. "
                "Export one, or run with --offline to exercise the pipeline with clearly "
                "marked synthetic output."
            )

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 json_mode: bool = False) -> ModelResponse:
        import httpx
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": self.api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json=body, timeout=180.0)
        if r.status_code != 200:
            raise ProviderUnavailable(f"anthropic {r.status_code}: {r.text[:300]}")
        d = r.json()
        u = d.get("usage", {})
        return ModelResponse("".join(b.get("text", "") for b in d.get("content", [])),
                             self.model, False,
                             u.get("input_tokens", 0), u.get("output_tokens", 0))


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "gpt-5", api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ProviderUnavailable("OPENAI_API_KEY is not set (BYOK).")

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 json_mode: bool = False) -> ModelResponse:
        import httpx
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {"model": self.model, "messages": msgs,
                                "max_completion_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post("https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json=body, timeout=180.0)
        if r.status_code != 200:
            raise ProviderUnavailable(f"openai {r.status_code}: {r.text[:300]}")
        d = r.json()
        u = d.get("usage", {})
        return ModelResponse(d["choices"][0]["message"]["content"], self.model, False,
                             u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


class OfflineStubProvider:
    """Structurally valid output that is not research.

    This exists so the state machine, the artifact contract and the human gate can
    be exercised without a key. It is not a fallback and must never be reached by
    accident: it has to be turned on explicitly, and every artifact produced under
    it is stamped `synthetic: true`, which the release gate treats as a blocker.

    The temptation this guards against is real. A stub that returned plausible
    prose would make a demo look like a working research system, which is exactly
    the claim this project exists to stop anyone from making.
    """

    name = "offline-stub"

    def __init__(self, fixtures: Path | None = None) -> None:
        self.fixtures = Path(fixtures) if fixtures else None

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096,
                 json_mode: bool = False) -> ModelResponse:
        return ModelResponse(
            json.dumps({"_synthetic": True,
                        "_warning": "OfflineStubProvider output. Not model-generated. "
                                    "Any artifact derived from this is not research.",
                        "_prompt_sha": __import__("hashlib").sha256(prompt.encode()).hexdigest()[:12]}),
            "offline-stub", synthetic=True)


def build_model_provider(spec: str, *, offline: bool = False) -> ModelProvider:
    if offline or spec == "offline":
        return OfflineStubProvider()
    if spec.startswith("anthropic"):
        return AnthropicProvider(*( [spec.split(":", 1)[1]] if ":" in spec else [] ))
    if spec.startswith("openai"):
        return OpenAIProvider(*( [spec.split(":", 1)[1]] if ":" in spec else [] ))
    raise ProviderUnavailable(f"unknown model provider '{spec}'")


# --------------------------------------------------------------------------
# scholarly providers
# --------------------------------------------------------------------------
@dataclass
class ScholarlyCapabilities:
    """What a provider can actually do — used to name coverage blind spots.

    These flags are the difference between a coverage report that means something
    and one that reports hit counts. A provider set with `full_text=False`
    everywhere cannot support a mechanism-level novelty search, and the run needs
    to say so rather than return a confident NOVEL_ENOUGH.
    """
    full_text: bool = False
    abstracts: bool = True
    citations: bool = False
    code_search: bool = False
    preprints: bool = False
    non_english: bool = False
    last_90_days: bool = True
    requires_key: bool = False
    documented_rate: str = "unknown"


@dataclass
class ScholarlyProvider:
    name: str
    base_url: str
    capabilities: ScholarlyCapabilities
    api_key_env: str | None = None
    mailto_env: str | None = None
    probe_ok: bool | None = None
    probe_detail: str = ""
    _transport: Any = field(default=None, repr=False)

    def available(self) -> bool:
        if self.api_key_env and not os.environ.get(self.api_key_env):
            return False
        return self.probe_ok is not False

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        if self._transport is None:
            raise ProviderUnavailable(
                f"provider '{self.name}' has no transport configured. Live search requires "
                f"network access; tests and demos use FixtureTransport."
            )
        return self._transport.search(self, query, limit)


class FixtureTransport:
    """Deterministic replay from disk.

    Tests that hit the live network are not tests, and a demo whose result changes
    with the weather is not evidence. Fixtures are recorded once and replayed.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def search(self, provider: ScholarlyProvider, query: str, limit: int) -> list[dict[str, Any]]:
        import hashlib
        key = hashlib.sha256(f"{provider.name}|{query}".encode()).hexdigest()[:16]
        p = self.root / provider.name / f"{key}.json"
        if not p.exists():
            raise ProviderUnavailable(
                f"no fixture for provider '{provider.name}' query {query!r} (expected {p}). "
                f"Record it with `researchforge record`, or run live with credentials."
            )
        return json.loads(p.read_text())[:limit]


DEFAULT_PROVIDERS = [
    ScholarlyProvider("openalex", "https://api.openalex.org",
                      ScholarlyCapabilities(abstracts=True, citations=True, preprints=True,
                                            non_english=True, documented_rate="100k/day polite pool"),
                      mailto_env="RESEARCHFORGE_CONTACT_EMAIL"),
    ScholarlyProvider("crossref", "https://api.crossref.org",
                      ScholarlyCapabilities(abstracts=False, citations=True,
                                            documented_rate="polite pool, UA with mailto"),
                      mailto_env="RESEARCHFORGE_CONTACT_EMAIL"),
    ScholarlyProvider("arxiv", "https://export.arxiv.org/api",
                      ScholarlyCapabilities(abstracts=True, preprints=True,
                                            documented_rate="~1 req/3s"),),
    ScholarlyProvider("semantic_scholar", "https://api.semanticscholar.org/graph/v1",
                      ScholarlyCapabilities(abstracts=True, citations=True, preprints=True,
                                            requires_key=True,
                                            documented_rate="100 req/5min unauthenticated"),
                      api_key_env="SEMANTIC_SCHOLAR_API_KEY"),
]
