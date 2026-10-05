import json

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
