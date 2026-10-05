import os

import pytest

from conftest import cu, find_python38, record, write_transcript


@pytest.mark.parametrize("host, normalized", [
    ("box", {"name": "box", "ssh": "box", "python": "python3", "projects_dir": None}),
    ({"ssh": "user@box", "name": "b", "python": "python3.8", "projects_dir": "/data/p"},
     {"name": "b", "ssh": "user@box", "python": "python3.8", "projects_dir": "/data/p"}),
    ({"ssh": "box", "name": ""}, {"name": "box", "ssh": "box", "python": "python3", "projects_dir": None}),
])
def test_normalize_host(host, normalized):
    assert cu.normalize_host(host) == normalized


@pytest.mark.parametrize("host", [{"name": "x"}, {"ssh": ""}])
def test_a_host_without_an_ssh_target_stops(host):
    with pytest.raises(SystemExit, match='every host needs an "ssh" target'):
        cu.normalize_host(host)


@pytest.mark.parametrize("config_form", [
    "box",
    {"ssh": "box", "name": "b"},
    {"ssh": "box", "projects_dir": "/p"},
    {"ssh": "box", "python": "python3.8"},
    {"ssh": "box", "name": "b", "projects_dir": "/p", "python": "py"},
])
def test_host_to_config_round_trips(config_form):
    assert cu.host_to_config(cu.normalize_host(config_form)) == config_form


def test_parse_collect_output_without_a_summary_line_is_an_error():
    m = cu.parse_collect_output("box", '{"id": "a"}\nnoise\n')
    assert m["error"] == "collector produced no summary line (stdout: 2 lines)"
    assert m["rows"] == []


def test_parse_collect_output_tags_rows_with_the_machine():
    m = cu.parse_collect_output("box", 'noise\n{"id": "a"}\n{"meta": {"files": 1}}\n{"meta": {"files": 2}}\n')
    assert m["rows"] == [{"id": "a", "mach": "box"}]
    assert m["meta"] == {"files": 2}
    assert m["error"] is None


# --------------------------------------------------------------------------
# End to end through the fake ssh in tests/bin
# --------------------------------------------------------------------------

@pytest.fixture
def remote_projects(tmp_path):
    path = tmp_path / "remote box" / "projects"
    path.mkdir(parents=True)
    return path


def test_remote_rows_arrive_tagged_with_the_host_name(report, write_config, fake_ssh, remote_projects):
    write_transcript(remote_projects, "a.jsonl", [record(msg_id="r1", output=1000)])
    write_config({"hosts": [{"ssh": "box", "name": "remote", "projects_dir": str(remote_projects)}]})
    code, doc, _ = report("--no-local", "--no-warehouse")
    assert code == 0
    assert doc["machines"][0]["name"] == "remote"
    assert doc["machines"][0]["error"] is None
    assert doc["by_machine"]["remote"]["calls"] == 1


def test_ssh_gets_the_configured_options_and_the_quoted_projects_dir(report, write_config, fake_ssh, remote_projects):
    write_config({"hosts": [{"ssh": "box", "projects_dir": str(remote_projects)}],
                  "ssh_options": ["-o", "BatchMode=yes", "-p", "2222"]})
    report("--no-local", "--no-warehouse")
    assert fake_ssh() == [["-o", "BatchMode=yes", "-p", "2222", "box",
                           "python3 - --collect --projects-dir '{}'".format(remote_projects)]]


def test_host_flag_replaces_the_configured_hosts(report, write_config, fake_ssh):
    write_config({"hosts": ["configured"]})
    report("--no-local", "--no-warehouse", "--host", "flagged")
    assert [call[-2] for call in fake_ssh()] == ["flagged"]


def test_a_response_on_two_machines_is_counted_once(report, write_config, fake_ssh, projects_dir, remote_projects):
    shared = record(msg_id="same", output=1000)
    write_transcript(projects_dir, "a.jsonl", [shared])
    write_transcript(remote_projects, "a.jsonl", [shared])
    write_config({"hosts": [{"ssh": "box", "projects_dir": str(remote_projects)}]})
    _, doc, _ = report("--no-warehouse")
    assert doc["totals"]["calls"] == 1
    assert doc["cross_machine_duplicates"] == 1


@pytest.mark.parametrize("host, error", [
    ({"ssh": "unreachable"}, "ssh: connect to host unreachable port 22: Connection refused"),
    ({"ssh": "box", "python": "echo"}, "collector produced no summary line (stdout: 1 lines)"),
    ({"ssh": "box", "python": "exit 3 #"}, "ssh exited with 3"),
], ids=["connection-refused", "no-summary", "silent-failure"])
def test_an_unreadable_host_is_reported_and_exits_2(run, write_config, fake_ssh, projects_dir, host, error):
    write_transcript(projects_dir, "a.jsonl", [record()])
    path = write_config({"hosts": [host]})
    code, out, err = run("--config", str(path), "--projects-dir", str(projects_dir), "--no-warehouse")
    assert code == 2
    assert "{:<14} FAILED: {}".format(host["ssh"], error) in out
    assert "1 machine could not be read: {}. Totals above are incomplete.".format(host["ssh"]) in err


def test_a_host_that_hangs_times_out(report, write_config, fake_ssh):
    write_config({"hosts": [{"ssh": "box", "python": "sleep 3 #"}], "timeout": 1})
    code, doc, _ = report("--no-local", "--no-warehouse")
    assert code == 2
    assert doc["machines"][0]["error"] == "timed out after 1s"


def test_collector_runs_under_python_3_8(report, write_config, fake_ssh, remote_projects):
    """The README promises remote machines need only Python 3.8."""
    python38 = find_python38()
    if python38 is None:
        if os.environ.get("CLAUDE_USAGE_REQUIRE_PY38"):
            pytest.fail("no Python 3.8 found, and CLAUDE_USAGE_REQUIRE_PY38 is set")
        pytest.skip("no Python 3.8 found (uv python install 3.8)")
    write_transcript(remote_projects, "a.jsonl", [record(msg_id="r1")])
    write_config({"hosts": [{"ssh": "old", "python": python38, "projects_dir": str(remote_projects)}]})
    code, doc, _ = report("--no-local", "--no-warehouse")
    assert code == 0, doc["machines"][0]["error"]
    assert doc["machines"][0]["python"].startswith("3.8.")
    assert doc["totals"]["calls"] == 1


def test_text_report_says_when_responses_were_on_two_machines(run, write_config, fake_ssh, projects_dir,
                                                               remote_projects):
    shared = record(msg_id="same", output=1000)
    write_transcript(projects_dir, "a.jsonl", [shared])
    write_transcript(remote_projects, "a.jsonl", [shared])
    path = write_config({"hosts": [{"ssh": "box", "projects_dir": str(remote_projects)}]})
    _, out, _ = run("--config", str(path), "--projects-dir", str(projects_dir), "--no-warehouse")
    assert "  1 responses were present on more than one machine and are counted once." in out


def test_a_missing_ssh_binary_is_reported_as_a_failed_host(report, write_config, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    write_config({"hosts": ["box"]})
    code, doc, _ = report("--no-local", "--no-warehouse")
    assert code == 2
    assert "No such file or directory" in doc["machines"][0]["error"]
