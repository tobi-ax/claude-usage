import json

import pytest

from conftest import cu, record, write_transcript


def row(id_, t="2026-09-15T10:00:00.000Z", rq="req", o=1, **extra):
    return {"id": id_, "rq": rq, "t": t, "m": "claude-opus-5", "i": 0, "o": o, "w5": 0, "w1": 0, "r": 0,
            "f": 0, "e": "cli", "s": "", "p": "", "mach": "box", **extra}


def test_missing_warehouse_loads_empty(tmp_path):
    assert cu.load_warehouse(str(tmp_path / "rows.jsonl")) == []


def test_load_skips_garbage_and_rows_without_an_id(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join([json.dumps(row("a")), "{broken", json.dumps({"t": "x"}), "[1]", ""]))
    assert [r["id"] for r in cu.load_warehouse(str(path))] == ["a"]


def test_save_sorts_by_time_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "data" / "rows.jsonl"
    cu.save_warehouse(str(path), [row("b", t="2026-09-16"), row("a", t="2026-09-16"), row("c", t="2026-09-01")])
    assert [json.loads(l)["id"] for l in path.read_text().splitlines()] == ["c", "a", "b"]
    assert [p.name for p in path.parent.iterdir()] == ["rows.jsonl"]


def test_merge_counts_only_new_responses(tmp_path):
    path = str(tmp_path / "rows.jsonl")
    merged, new = cu.merge_into_warehouse(path, [row("a"), row("b")], [row("b"), row("c")])
    assert sorted(r["id"] for r in merged) == ["a", "b", "c"]
    assert new == 1


def test_a_fresh_scan_updates_a_row_stored_mid_stream(tmp_path):
    path = str(tmp_path / "rows.jsonl")
    merged, new = cu.merge_into_warehouse(path, [row("a", o=5)], [row("a", o=90)])
    assert [r["o"] for r in merged] == [90]
    assert new == 0
    assert [r["o"] for r in cu.load_warehouse(path)] == [90]


def test_merge_without_live_rows_writes_nothing(tmp_path):
    path = tmp_path / "rows.jsonl"
    cu.merge_into_warehouse(str(path), [row("a")], [])
    assert not path.exists()


def test_report_still_counts_responses_after_their_transcripts_are_pruned(report, projects_dir):
    transcript = write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1", output=500),
                                                            record(msg_id="m2", output=700)])
    _, first, _ = report()
    transcript.unlink()
    _, second, _ = report()
    assert first["totals"] == second["totals"]
    assert first["warehouse"]["new"] == 2
    assert second["warehouse"]["new"] == 0
    assert second["warehouse"]["rows"] == 2
    assert second["warehouse"]["oldest"] == "2026-09-15"


def test_no_warehouse_neither_reads_nor_writes_it(report, projects_dir, warehouse_path):
    write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1")])
    _, doc, _ = report("--no-warehouse")
    assert doc["warehouse"] is None
    assert not warehouse_path.exists()


def test_warehouse_path_comes_from_the_flag_then_the_config(report, projects_dir, write_config, tmp_path):
    write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1")])
    from_config = tmp_path / "config-wh" / "rows.jsonl"
    from_flag = tmp_path / "flag-wh" / "rows.jsonl"
    write_config({"warehouse": str(from_config)})
    report()
    assert from_config.exists()
    report("--warehouse", str(from_flag))
    assert from_flag.exists()


@pytest.fixture
def retired_box(projects_dir, warehouse_path):
    """This machine has one transcript; the warehouse also holds rows of a box no longer read."""
    write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1")])
    cu.save_warehouse(str(warehouse_path), [
        row("old1", t="2026-09-10T10:00:00.000Z", mach="retired-box"),
        row("old2", t="2026-09-12T10:00:00.000Z", mach="retired-box"),
        row("stats-cache:retired-box:2026-08-01:claude-opus-5", t="2026-08-01T12:00:00.000Z",
            mach="retired-box", e=cu.IMPORTED_ENTRYPOINT, n=0),
    ])


def test_a_machine_only_in_the_warehouse_is_named_and_still_counted(report, retired_box):
    code, doc, _ = report()
    assert code == 0
    assert doc["warehouse_only_machines"] == [{"name": "retired-box", "calls": 2, "last_day": "2026-09-12"}]
    assert doc["by_machine"]["retired-box"]["calls"] == 2
    assert doc["totals"]["calls"] == 3


def test_the_text_header_lists_and_counts_warehouse_only_machines(run, config_path, projects_dir, retired_box):
    _, out, _ = run("--config", str(config_path), "--projects-dir", str(projects_dir))
    lines = out.splitlines()
    assert lines[0].endswith("·  2 machines")
    assert any(l.split() == ["retired-box", "2", "API", "calls", "warehouse", "only,", "not", "read", "this",
                             "run,", "latest", "2026-09-12"] for l in lines)


def test_machines_read_this_run_are_not_warehouse_only(report, projects_dir):
    write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1")])
    report()
    _, doc, _ = report()
    assert doc["warehouse_only_machines"] == []


def test_no_warehouse_has_no_warehouse_only_machines(report, retired_box):
    _, doc, _ = report("--no-warehouse")
    assert doc["warehouse_only_machines"] == []
    assert "retired-box" not in doc["by_machine"]
