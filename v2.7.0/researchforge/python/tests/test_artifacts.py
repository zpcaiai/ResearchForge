import json
import pytest
from pathlib import Path

from researchforge.artifacts import ArtifactStore
from researchforge.errors import ContractViolation, SchemaViolation

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def store(tmp_path):
    return ArtifactStore(tmp_path, SCHEMAS, run_id="test")


def test_producer_may_write(tmp_path):
    s = store(tmp_path)
    s.write("idea-ranker", "ranked_ideas", {"ideas": []})
    assert s.exists("ranked_ideas")


def test_non_producer_cannot_write(tmp_path):
    s = store(tmp_path)
    with pytest.raises(ContractViolation, match="produced by"):
        s.write("idea-ranker", "paper_model", {"paper_id": "x"})


def test_undeclared_artifact_rejected(tmp_path):
    s = store(tmp_path)
    with pytest.raises(ContractViolation, match="not in the contract"):
        s.write("idea-ranker", "idea_portfolio_v2", {})


def test_reader_must_declare_input(tmp_path):
    s = store(tmp_path)
    s.write("paper-model-builder", "paper_model",
            {"paper_id": "p", "title": "t", "sections": [], "claims": []})
    # citation-resolver does not declare paper_model as an input
    with pytest.raises(ContractViolation, match="not in its declared inputs"):
        s.read("citation-resolver", "paper_model")


def test_schema_is_enforced(tmp_path):
    s = store(tmp_path)
    with pytest.raises(SchemaViolation, match="PaperModel"):
        s.write("paper-model-builder", "paper_model", {"title": "missing required fields"})


def test_missing_upstream_names_its_producer(tmp_path):
    s = store(tmp_path)
    with pytest.raises(ContractViolation, match="producer is 'paper-model-builder'"):
        s.read("idea-seed-miner", "paper_model")


def test_write_is_recorded_in_provenance(tmp_path):
    s = store(tmp_path)
    s.write("idea-ranker", "ranked_ideas", {"ideas": [1]})
    evs = s.prov.events_for("ranked_ideas")
    assert len(evs) == 1 and evs[0].kind == "artifact_write" and evs[0].digest
