"""The artifact store — where the contract stops being a document.

v0.1.0's central defect was that its 62 skills communicated through prose: one
skill produced `idea_portfolio.json`, another consumed "idea portfolio", and
nothing could tell they were the same thing or notice when they weren't. The fix
is not better documentation. It is that a skill physically cannot write an
artifact it does not own, and cannot read one it did not declare.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _JSValidationError

from .errors import ContractViolation, SchemaViolation
from .generated import ARTIFACTS, INTERNAL_ARTIFACT_SPECS, INTERNAL_ARTIFACTS, SKILLS
from .provenance import Event, ProvenanceLog, sha256_file


class ArtifactStore:
    #: set by the runner so internal artifacts resolve for the executing skill only
    current_skill: str | None = None

    def __init__(self, project: Path, schemas_dir: Path, run_id: str,
                 provenance: ProvenanceLog | None = None) -> None:
        self.project = Path(project)
        self.schemas_dir = Path(schemas_dir)
        self.run_id = run_id
        self.prov = provenance or ProvenanceLog(self.project)
        self._validators: dict[str, Draft202012Validator] = {}

    # ---------- contract ----------
    def _spec(self, artifact_id: str, skill: str | None = None):
        spec = ARTIFACTS.get(artifact_id)
        if spec is None:
            owner = skill or self.current_skill
            if owner:
                spec = INTERNAL_ARTIFACT_SPECS.get(owner, {}).get(artifact_id)
            if spec is None:
                for m in INTERNAL_ARTIFACT_SPECS.values():
                    if artifact_id in m:
                        spec = m[artifact_id]
                        break
        if spec is None:
            raise ContractViolation(
                f"unknown artifact '{artifact_id}'. It is not in the contract. "
                f"Add it to manifests/artifact-graph.json and re-run codegen; do not "
                f"write undeclared artifacts."
            )
        return spec

    def _may_write(self, skill: str, artifact_id: str) -> None:
        if artifact_id in INTERNAL_ARTIFACTS.get(skill, ()):
            return
        spec = self._spec(artifact_id, skill)
        if spec.producer != skill:
            raise ContractViolation(
                f"skill '{skill}' tried to write '{artifact_id}', which is produced by "
                f"'{spec.producer}'. Every artifact has exactly one producer; two writers "
                f"means no one owns whether it is correct."
            )

    def _may_read(self, skill: str, artifact_id: str) -> None:
        c = SKILLS.get(skill)
        if c is None:
            raise ContractViolation(f"unknown skill '{skill}'")
        if artifact_id in c.consumes or artifact_id in c.feedback:
            return
        if artifact_id in INTERNAL_ARTIFACTS.get(skill, ()):
            return
        spec = ARTIFACTS.get(artifact_id)
        if spec is not None and spec.producer == skill:
            return
        raise ContractViolation(
            f"skill '{skill}' tried to read '{artifact_id}', which is not in its declared "
            f"inputs. Declared: {sorted(c.consumes)}. If this read is legitimate, it is a "
            f"change to the contract, not to the code."
        )

    # ---------- schema ----------
    def _validator(self, schema_name: str) -> Draft202012Validator:
        if schema_name not in self._validators:
            p = self.schemas_dir / f"{schema_name}.schema.json"
            if not p.exists():
                raise SchemaViolation(f"schema '{schema_name}' declared but {p} is missing")
            self._validators[schema_name] = Draft202012Validator(json.loads(p.read_text()))
        return self._validators[schema_name]

    def _validate(self, artifact_id: str, payload: Any, skill: str | None = None) -> None:
        spec = self._spec(artifact_id, skill)
        if not spec.schema:
            return
        v = self._validator(spec.schema)
        items = payload if isinstance(payload, list) else [payload]
        for i, item in enumerate(items):
            try:
                v.validate(item)
            except _JSValidationError as e:
                where = f"[{i}]" if isinstance(payload, list) else ""
                raise SchemaViolation(
                    f"'{artifact_id}'{where} does not validate against {spec.schema}: "
                    f"{e.message} (at {'/'.join(str(x) for x in e.absolute_path) or '<root>'})"
                ) from e

    # ---------- paths ----------
    def path_for(self, artifact_id: str) -> Path:
        spec = self._spec(artifact_id)
        rel = spec.path.split("|")[0]
        return self.project / rel

    def exists(self, artifact_id: str) -> bool:
        p = self.path_for(artifact_id)
        if self._spec(artifact_id).path.endswith("/"):
            return (p / "_manifest.json").exists()
        return p.exists()

    # ---------- io ----------
    def write(self, skill: str, artifact_id: str, payload: Any, *,
              detail: dict[str, Any] | None = None) -> Path:
        self._may_write(skill, artifact_id)
        self._validate(artifact_id, payload, skill)
        self.current_skill = skill
        p = self.path_for(artifact_id)
        if self._spec(artifact_id).path.endswith("/"):
            # directory-valued artifact: the manifest inside it is what carries provenance
            p.mkdir(parents=True, exist_ok=True)
            p = p / "_manifest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".jsonl":
            if not isinstance(payload, list):
                raise ContractViolation(f"'{artifact_id}' is .jsonl; payload must be a list")
            p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                 for r in payload), encoding="utf-8")
        elif p.suffix in (".json",):
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                         encoding="utf-8")
        elif isinstance(payload, (str, bytes)):
            p.write_bytes(payload.encode() if isinstance(payload, str) else payload)
        else:
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        self.prov.append(Event(self.prov.now(), self.run_id, skill, "artifact_write",
                               artifact_id, str(p.relative_to(self.project)),
                               sha256_file(p), detail or {}))
        return p

    def append_jsonl(self, skill: str, artifact_id: str, records: list[Any]) -> Path:
        """Append to a ledger without rewriting it. Ledgers are append-only by design."""
        self._may_write(skill, artifact_id)
        self._validate(artifact_id, records, skill)
        self.current_skill = skill
        p = self.path_for(artifact_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        self.prov.append(Event(self.prov.now(), self.run_id, skill, "artifact_write",
                               artifact_id, str(p.relative_to(self.project)),
                               sha256_file(p), {"appended": len(records)}))
        return p

    def read(self, skill: str, artifact_id: str, *, default: Any = None) -> Any:
        self._may_read(skill, artifact_id)
        p = self.path_for(artifact_id)
        if not p.exists():
            if default is not None:
                return default
            raise ContractViolation(
                f"skill '{skill}' requires '{artifact_id}' but it has not been produced. "
                f"Its producer is '{self._spec(artifact_id).producer}', which has not run."
            )
        self.prov.append(Event(self.prov.now(), self.run_id, skill, "artifact_read",
                               artifact_id, str(p.relative_to(self.project)), None, {}))
        if p.suffix == ".jsonl":
            return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        if p.suffix == ".json":
            return json.loads(p.read_text(encoding="utf-8"))
        return p.read_text(encoding="utf-8", errors="replace")
