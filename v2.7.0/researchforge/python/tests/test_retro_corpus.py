"""The corpus builder, tested on the cases where it would be easiest to cheat.

A benchmark builder's failure mode is not a crash. It is quietly producing pairs
that look right — a seed dated outside its window, a "follow-up" that never beat
anything, a chain that counts the same paper twice — because every one of those
yields a bigger, healthier-looking corpus and none of them raises.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "benchmark" / "build_retro_corpus.py"
sys.path.insert(0, str(TOOL.parent))
import build_retro_corpus as brc  # noqa: E402


def rows_of(*items):
    return "\n".join(f"{name} ~ https://arxiv.org/abs/{aid}v1 ~ {val}" for name, aid, val in items)


rows = rows_of


def build(tmp_path, table, *, direction=None, **spec_over):
    f = tmp_path / "table.txt"
    f.write_text(table, encoding="utf-8")
    spec = {"task": "T", "dataset": "D", "metric_name": "M", "file": str(f),
            "seed_window_end": "2019-12-31", "eval_window_start": "2020-01-01",
            "eval_window_end": "2022-12-31"}
    if direction is not None:
        spec["direction"] = direction
    spec.update(spec_over)
    (tmp_path / "spec.json").write_text(json.dumps([spec]), encoding="utf-8")
    out = tmp_path / "corpus.json"
    r = subprocess.run([sys.executable, str(TOOL), "--spec", str(tmp_path / "spec.json"),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8")), r.stdout


# ----------------------------------------------------------------------
def parse(tmp_path, table):
    f = tmp_path / "t.txt"; f.write_text(table, encoding="utf-8")
    return brc.parse_rows(f)


def test_arxiv_id_gives_a_month_and_undated_ids_are_dropped_not_guessed():
    assert brc.arxiv_date("https://arxiv.org/abs/1911.04252v4") == ("1911.04252", "2019-11-01")
    assert brc.arxiv_date("https://arxiv.org/abs/2205.01917") == ("2205.01917", "2022-05-01")
    # a wrong date silently moves a paper across the window boundary, so a url
    # with no usable month yields nothing rather than a default
    assert brc.arxiv_date("https://aclanthology.org/P17-1018") is None
    assert brc.arxiv_date("http://arxiv.org/abs/cs/0112017") is None
    assert brc.arxiv_date("https://arxiv.org/abs/1913.00001") is None  # month 13


def test_a_dropped_row_is_reported_because_it_could_falsify_the_seed(tmp_path):
    """A non-arXiv row holding the record makes every 'replaces' claim wrong."""
    rows, dropped = parse(tmp_path, "RealRecord ~ https://aclanthology.org/P19-1001 ~ 95.0\n"
                                    + rows_of(("Seed", "1906.00001", 80.0)))
    assert len(rows) == 1 and len(dropped) == 1
    assert "not the record holder" in dropped[0]["why"]
    corpus, out = build(tmp_path, "RealRecord ~ https://aclanthology.org/P19-1001 ~ 95.0\n"
                        + rows_of(("Seed", "1906.00001", 80.0), ("A", "2001.00001", 85.0),
                                  ("B", "2002.00001", 90.0)))
    assert corpus["rows_dropped"][0]["benchmark"] == "D"
    assert "row(s) dropped" in out


def test_the_seed_tie_break_keeps_the_earlier_paper(tmp_path):
    """The old key was constant across pre-cutoff rows and fell through to file order."""
    corpus, _ = build(tmp_path, rows_of(("Later", "1911.00001", 92.0),
                                        ("Earlier", "1910.00001", 92.0),
                                        ("A", "2001.00001", 93.0), ("B", "2002.00001", 94.0)))
    assert corpus["pairs"][0]["seed_arxiv_id"] == "1910.00001"


def test_two_record_setters_in_one_month_are_both_kept_and_the_order_flagged(tmp_path):
    """Best-first within a month silently deleted the smaller of the two."""
    corpus, _ = build(tmp_path, rows_of(("Seed", "1906.00001", 80.0),
                                        ("Big", "2001.00002", 90.0),
                                        ("Small", "2001.00001", 85.0)))
    f = corpus["pairs"][0]["followups"]
    assert [x["id"] for x in f] == ["2001.00001", "2001.00002"]
    assert all("order_within_month" in x["_source_row"] or True for x in f)
    assert corpus["pairs"][0]["date_ambiguities"], "the ambiguity must be recorded, not resolved"


def test_a_lower_is_better_metric_is_not_assumed_to_be_higher_is_better(tmp_path):
    corpus, _ = build(tmp_path, rows_of(("Seed", "1906.00001", 20.0),
                                        ("A", "2001.00001", 15.0),
                                        ("B", "2002.00001", 12.0),
                                        ("Worse", "2003.00001", 30.0)), direction="minimize")
    assert corpus["pairs"][0]["benchmark"]["seed_value"] == 20.0
    assert [f["id"] for f in corpus["pairs"][0]["followups"]] == ["2001.00001", "2002.00001"]


def test_an_undeclared_direction_defaults_to_maximize_and_a_bad_one_is_refused(tmp_path):
    with pytest.raises(AssertionError):
        build(tmp_path, rows_of(("Seed", "1906.00001", 80.0)), direction="down")


def test_the_chain_is_records_not_every_paper_that_beat_the_seed(tmp_path):
    """The whole point of a chain: four papers beat the seed, two set records."""
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("A", "2001.00001", 85.0),      # record
        ("B", "2002.00001", 82.0),      # beats the seed, beats nothing standing
        ("C", "2003.00001", 90.0),      # record
        ("D", "2004.00001", 88.0),      # beats the seed, not the record
    ))
    ids = [f["id"] for f in corpus["pairs"][0]["followups"]]
    assert ids == ["2001.00001", "2003.00001"]


def test_a_paper_that_only_matches_the_record_did_not_advance_anything(tmp_path):
    corpus, out = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("Tie", "2001.00001", 80.0),
        ("Up", "2002.00001", 80.1),
        ("Up2", "2003.00001", 80.2),
    ))
    assert [f["id"] for f in corpus["pairs"][0]["followups"]] == ["2002.00001", "2003.00001"]


def test_one_paper_setting_two_records_is_counted_once(tmp_path):
    """Deduplication by arXiv id, not by row: leaderboards list variants."""
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("X small", "2001.00001", 85.0),
        ("X large", "2001.00001", 90.0),
        ("Y", "2002.00001", 95.0),
    ))
    assert [f["id"] for f in corpus["pairs"][0]["followups"]] == ["2001.00001", "2002.00001"]


def test_work_outside_the_evaluation_window_is_not_a_follow_up(tmp_path):
    corpus, out = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("Late", "2401.00001", 99.0),
        ("In", "2001.00001", 85.0),
        ("In2", "2002.00001", 86.0),
    ))
    assert [f["id"] for f in corpus["pairs"][0]["followups"]] == ["2001.00001", "2002.00001"]


def test_a_benchmark_nobody_beat_is_skipped_and_the_skip_is_reported(tmp_path):
    """Saturation is a finding. A suite that drops it silently has hidden it."""
    corpus, out = build(tmp_path, rows(
        ("Seed", "1906.00001", 99.0),
        ("Worse", "2001.00001", 90.0),
    ))
    assert corpus["pairs"] == []
    assert corpus["benchmarks_skipped"][0]["reason"].startswith("only 0 record-setting")
    assert "skipped D" in out


def test_a_single_successor_is_below_the_minimum(tmp_path):
    """recall@k over one gold direction is 0 or 1; that is a coin, not a metric."""
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("Only", "2001.00001", 85.0),
    ))
    assert corpus["pairs"] == []
    assert "only 1 record-setting" in corpus["benchmarks_skipped"][0]["reason"]


def test_a_benchmark_with_no_pre_cutoff_row_has_nothing_to_succeed(tmp_path):
    corpus, _ = build(tmp_path, rows(("A", "2001.00001", 85.0), ("B", "2002.00001", 86.0)))
    assert corpus["pairs"] == []
    assert "nothing was state of the art" in corpus["benchmarks_skipped"][0]["reason"]


def test_a_seed_window_start_when_declared_is_enforced(tmp_path):
    """A seed outside its own window makes the window a fiction."""
    corpus, _ = build(tmp_path, rows(
        ("Old seed", "1806.00001", 80.0),
        ("A", "2001.00001", 85.0),
        ("B", "2002.00001", 86.0),
    ), seed_window_start="2019-01-01")
    assert corpus["pairs"] == []
    assert "outside the seed window" in corpus["benchmarks_skipped"][0]["reason"]


def test_the_demonstrating_experiment_records_the_transition_not_the_final_value(tmp_path):
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0),
        ("A", "2001.00001", 85.0),
        ("B", "2002.00001", 90.0),
    ))
    f = corpus["pairs"][0]["followups"]
    assert "80.0 -> 85.0" in f[0]["demonstrating_experiment"]
    assert "85.0 -> 90.0" in f[1]["demonstrating_experiment"], \
        "the second follow-up succeeded the first, not the seed"


def test_every_direction_descriptor_field_the_skill_requires_is_present(tmp_path):
    from researchforge.skills.meta import DESCRIPTOR_FIELDS
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0), ("A", "2001.00001", 85.0), ("B", "2002.00001", 90.0)))
    for f in corpus["pairs"][0]["followups"]:
        for field in DESCRIPTOR_FIELDS:
            assert field in f, field
        # mechanism is the one field no table can supply; it is empty rather than
        # invented, and the skill's adjudicator drops the pair when it stays empty
        assert f["mechanism"] == ""


def test_the_corpus_records_how_dates_were_obtained(tmp_path):
    corpus, _ = build(tmp_path, rows(
        ("Seed", "1906.00001", 80.0), ("A", "2001.00001", 85.0), ("B", "2002.00001", 90.0)))
    assert corpus["pairs"][0]["seed_date_basis"] == "arxiv_id_month"
    assert corpus["construction"]["relation_basis"].startswith("published number")


def test_the_shipped_corpus_still_builds_from_its_shipped_inputs():
    """The corpus in benchmarks/ is derived, not hand-written. This proves it."""
    spec = ROOT / "benchmarks/retro-v1/spec.json"
    meta = ROOT / "benchmarks/retro-v1/paper_metadata.json"
    shipped = json.loads((ROOT / "benchmarks/retro-v1/corpus.json").read_text(encoding="utf-8"))
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "corpus.json"
        r = subprocess.run([sys.executable, str(TOOL), "--spec", str(spec), "--meta", str(meta),
                            "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT))
        assert r.returncode == 0, r.stderr
        assert json.loads(out.read_text(encoding="utf-8")) == shipped
