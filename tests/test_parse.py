import itertools
import json

import pytest

from conftest import cu, record, write_transcript


def test_parse_line_maps_a_record_to_a_row():
    rec = record(model="claude-fable-5-1", msg_id="msg_a", request_id="req_a", ts="2026-09-16T08:00:00.000Z",
                 input=10, output=20, write_5m=30, write_1h=40, read=50,
                 entrypoint="sdk-ts", session="s-1", cwd="/work/x")
    assert cu.parse_line(json.dumps(rec)) == {
        "id": "msg_a", "rq": "req_a", "t": "2026-09-16T08:00:00.000Z", "m": "claude-fable-5-1",
        "i": 10, "o": 20, "w5": 30, "w1": 40, "r": 50, "f": 0,
        "e": "sdk-ts", "s": "s-1", "p": "/work/x",
    }


def test_parse_line_flags_fast_mode():
    assert cu.parse_line(json.dumps(record(speed="fast")))["f"] == 1
    assert cu.parse_line(json.dumps(record(speed="standard")))["f"] == 0


def test_cache_writes_without_a_breakdown_count_as_5_minute_writes():
    row = cu.parse_line(json.dumps(record(write_5m=70, breakdown=False)))
    assert (row["w5"], row["w1"]) == (70, 0)


def test_message_without_id_falls_back_to_the_record_uuid():
    assert cu.parse_line(json.dumps(record(msg_id=None, uuid="u-123")))["id"] == "u-123"


def test_missing_optional_fields_get_defaults():
    rec = record()
    for key in ("requestId", "entrypoint", "sessionId", "cwd"):
        del rec[key]
    row = cu.parse_line(json.dumps(rec))
    assert (row["rq"], row["e"], row["s"], row["p"]) == ("", "unknown", "", "")


def without_usage():
    rec = record()
    del rec["message"]["usage"]
    return rec


@pytest.mark.parametrize("line", [
    "not json",
    "[1, 2, 3]",
    json.dumps({**record(), "type": "user"}),
    json.dumps(without_usage()),
    json.dumps(record(model="")),
    json.dumps(record(model="<synthetic>")),
    json.dumps(record(output=0)),
], ids=["invalid-json", "not-an-object", "user-record", "no-usage", "no-model", "synthetic", "all-zero"])
def test_parse_line_skips_lines_that_are_not_billable_responses(line):
    assert cu.parse_line(line) is None


def stream(msg_id, request_id, outputs):
    """The records Claude Code writes for one streamed response, one per content block."""
    return [cu.parse_line(json.dumps(record(msg_id=msg_id, request_id=request_id, output=o))) for o in outputs]


def test_dedup_keeps_the_record_with_the_final_output_count():
    rows = stream("msg_1", "req_1", [5, 80, 30])
    assert [r["o"] for r in cu.dedup(rows)] == [80]


def test_dedup_result_does_not_depend_on_record_order():
    rows = stream("msg_1", "req_1", [5, 80, 30]) + stream("msg_2", "req_2", [7, 9])
    results = {tuple(sorted((r["id"], r["o"]) for r in cu.dedup(list(p)))) for p in itertools.permutations(rows)}
    assert results == {(("msg_1", 80), ("msg_2", 9))}


def test_dedup_keys_on_message_id_and_request_id_together():
    rows = stream("msg_1", "req_1", [5]) + stream("msg_1", "req_2", [5]) + stream("msg_2", "req_1", [5])
    assert len(cu.dedup(rows)) == 3


def test_scan_reads_every_jsonl_file_below_the_projects_dir(projects_dir):
    write_transcript(projects_dir, "-work-a/s1.jsonl", [record(msg_id="m1")])
    write_transcript(projects_dir, "-work-b/nested/s2.jsonl", [record(msg_id="m2")])
    (projects_dir / "-work-a" / "notes.txt").write_text(json.dumps(record(msg_id="m3")) + "\n")
    rows, meta = cu.scan(str(projects_dir))
    assert sorted(r["id"] for r in rows) == ["m1", "m2"]
    assert meta == {"files": 2, "projects_dir": str(projects_dir), "exists": True}


def test_scan_folds_a_response_found_in_two_files(projects_dir):
    write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1", output=10)])
    write_transcript(projects_dir, "b.jsonl", [record(msg_id="m1", output=40)])
    rows, _ = cu.scan(str(projects_dir))
    assert [r["o"] for r in rows] == [40]


def test_scan_of_a_missing_dir_reports_it_missing(tmp_path):
    rows, meta = cu.scan(str(tmp_path / "nope"))
    assert rows == []
    assert meta["exists"] is False and meta["files"] == 0


def test_scan_survives_invalid_utf8_and_garbage_lines(projects_dir):
    path = write_transcript(projects_dir, "a.jsonl", [record(msg_id="m1")], extra_lines=["{truncated"])
    path.write_bytes(path.read_bytes() + b"\xff\xfe broken\n")
    rows, _ = cu.scan(str(projects_dir))
    assert [r["id"] for r in rows] == ["m1"]
