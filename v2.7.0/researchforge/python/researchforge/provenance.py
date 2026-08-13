"""Append-only provenance. Nothing that isn't in here happened."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Event:
    ts: float
    run_id: str
    skill: str
    kind: str                       # artifact_write | artifact_read | skill_start | skill_end | gate | decision | provider_call
    artifact_id: str | None = None
    path: str | None = None
    digest: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ProvenanceLog:
    """One file, opened append-only, fsynced on write.

    Fsync matters here: a run killed mid-experiment must not lose the record of
    what it had already done, or resume will re-run work whose results already
    exist and quietly produce two ledger entries for one experiment.
    """

    def __init__(self, project: Path) -> None:
        self.path = project / "provenance.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, ev: Event) -> None:
        line = json.dumps(asdict(ev), ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self) -> list[Event]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Event(**json.loads(line)))
        return out

    def events_for(self, artifact_id: str) -> list[Event]:
        return [e for e in self.read() if e.artifact_id == artifact_id]

    def now(self) -> float:
        return time.time()
