import json
import platform
import re

import pytest

from conftest import cu, record, write_transcript

DATA_ELEMENT = re.compile(r'<script type="application/json" id="report-data">(.*?)</script>', re.S)


def embedded(path):
    """The report data embedded in an HTML report file."""
    text = path.read_text(encoding="utf-8")
    found = DATA_ELEMENT.findall(text)
    assert len(found) == 1
    return json.loads(found[0])


@pytest.fixture
def usage(projects_dir):
    write_transcript(projects_dir, "-c-app/s1.jsonl", [
        record(model="claude-opus-5", msg_id="m1", output=1_000_000, read=2_000_000, session="s1",
               cwd="/c/app", ts="2026-09-01T09:00:00.000Z", entrypoint="sdk-ts"),
        record(model="claude-sonnet-5", msg_id="m2", output=1_000_000, write_1h=1_000_000, session="s1",
               cwd="/c/app", ts="2026-09-03T15:00:00.000Z"),
    ])
    write_transcript(projects_dir, "-c-tool/s2.jsonl", [
        record(model="claude-opus-5", msg_id="m3", output=100_000, session="s2",
               cwd="/c/tool", ts="2026-09-03T16:00:00.000Z"),
    ])


@pytest.fixture
def html_report(run, config_path, projects_dir, tmp_path):
    """Run a report with --html into a temp file; returns (exit code, stdout, stderr, embedded data)."""
    def call(*argv):
        target = tmp_path / "out" / "report.html"
        code, out, err = run("--config", str(config_path), "--projects-dir", str(projects_dir),
                             "--html", str(target), *argv)
        return code, out, err, embedded(target)
    return call


def test_html_holds_the_same_totals_as_json(html_report, report, usage):
    _, _, _, data = html_report("--no-warehouse")
    _, doc, _ = report("--no-warehouse")
    assert data["totals"]["cost"] == doc["totals"]["cost_usd"]
    assert data["totals"]["calls"] == doc["totals"]["calls"] == 3
    assert data["totals"]["tokens"] == doc["totals"]["total"]


def test_html_comes_on_top_of_the_normal_output(run, config_path, projects_dir, tmp_path, usage):
    target = tmp_path / "r.html"
    code, out, err = run("--json", "--config", str(config_path), "--projects-dir", str(projects_dir),
                         "--no-warehouse", "--html", str(target))
    assert code == 0
    assert json.loads(out)["totals"]["calls"] == 3
    assert "HTML report written to {}".format(target) in err
    assert target.read_text().startswith("<!doctype html>")


def test_html_without_a_path_goes_to_the_data_dir(run, config_path, projects_dir, home, usage):
    run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse", "--html")
    assert (home / ".local" / "share" / "claude-usage" / "report.html").exists()


def test_token_type_costs_add_up_to_the_total(html_report, usage):
    _, _, _, data = html_report("--no-warehouse")
    assert sum(t["cost"] for t in data["token_types"]) == pytest.approx(data["totals"]["cost"])
    assert [t["key"] for t in data["token_types"]] == list(cu.TOKEN_KEYS)


def test_the_time_chart_gets_one_value_per_day_and_key(html_report, usage, local_tz):
    local_tz("UTC")
    _, _, _, data = html_report("--no-warehouse")
    assert data["days"] == [{"day": "2026-09-01", "imported": False}, {"day": "2026-09-03", "imported": False}]
    assert {(c["day"], c["key"]): c["cost"] for c in data["series"]["family"]} == {
        ("2026-09-01", "opus"): 26.0, ("2026-09-03", "opus"): 2.5, ("2026-09-03", "sonnet"): 14.0}
    assert {(c["day"], c["key"]) for c in data["series"]["machine"]} == {
        ("2026-09-01", platform.node()), ("2026-09-03", platform.node())}


def test_days_use_local_time(html_report, projects_dir, local_tz):
    local_tz("Europe/Berlin")
    write_transcript(projects_dir, "-c-app/s1.jsonl", [record(msg_id="m1", ts="2026-09-13T22:30:00.000Z")])
    assert [d["day"] for d in html_report("--no-warehouse")[3]["days"]] == ["2026-09-14"]


def test_repos_and_hours_leave_out_imported_history(html_report, usage, warehouse_path, local_tz):
    local_tz("UTC")
    imported = {"id": "stats-cache:box:2026-08-20:claude-opus-5", "rq": "", "t": "2026-08-20T12:00:00.000Z",
                "m": "claude-opus-5", "i": 0, "o": 1000, "w5": 0, "w1": 0, "r": 0, "f": 0,
                "e": cu.IMPORTED_ENTRYPOINT, "s": "", "p": "", "n": 0, "mach": "box"}
    warehouse_path.parent.mkdir(parents=True)
    warehouse_path.write_text(json.dumps(imported) + "\n")
    _, _, _, data = html_report()
    assert data["totals"]["imported_rows"] == 1
    assert sorted(r["name"] for r in data["repos"]) == ["app", "tool"]
    assert sum(h["calls"] for h in data["hours"]) == 3
    assert {(h["weekday"], h["hour"]) for h in data["hours"]} == {(2, 9), (4, 15), (4, 16)}
    assert data["days"][0] == {"day": "2026-08-20", "imported": True}
    assert "box" in data["machine_order"]


def test_repos_carry_their_cost_per_machine(html_report, usage):
    _, _, _, data = html_report("--no-warehouse")
    app = next(r for r in data["repos"] if r["name"] == "app")
    # Opus 5: 1M output at $25/M plus 2M cache read at $0.5/M; Sonnet 5: 1M output at $10/M plus
    # 1M one-hour cache write at twice its $2/M input rate.
    assert app["machines"] == {platform.node(): pytest.approx(26.0 + 14.0)}


def write_days(projects_dir, millions_by_day):
    write_transcript(projects_dir, "-c-app/s1.jsonl", [
        record(msg_id="m{}".format(i), output=millions * 1_000_000, ts=day + "T09:00:00.000Z")
        for i, (day, millions) in enumerate(millions_by_day.items())])


def test_recent_compares_the_last_30_days_with_the_30_before(html_report, projects_dir, local_tz):
    local_tz("UTC")
    write_days(projects_dir, {"2026-09-30": 1, "2026-09-01": 2, "2026-08-31": 4, "2026-08-02": 8, "2026-08-01": 16})
    assert html_report("--no-warehouse")[3]["recent"] == {"days": 30, "cost": 75.0, "previous": 300.0}


def test_recent_needs_60_days_of_data(html_report, projects_dir, local_tz):
    local_tz("UTC")
    write_days(projects_dir, {"2026-09-30": 1, "2026-08-03": 1})
    assert html_report("--no-warehouse")[3]["recent"] is None


def test_no_value_can_end_the_data_element(run, config_path, projects_dir, tmp_path):
    hostile = "</script><script>alert(1)</script><!--"
    write_transcript(projects_dir, "-c-x/s1.jsonl", [record(msg_id="m1", entrypoint=hostile)])
    target = tmp_path / "r.html"
    run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse", "--html", str(target))
    assert "<script>alert" not in target.read_text()
    assert [e["name"] for e in embedded(target)["entrypoints"]] == [hostile]


def test_the_page_names_failed_machines(html_report, usage, write_config, fake_ssh):
    write_config({"hosts": [{"ssh": "nope", "python": "/nonexistent/python3"}]})
    code, _, _, data = html_report("--no-warehouse")
    assert code == 2
    assert data["failed"] == ["nope"]
    assert any(n.startswith("nope") and "FAILED" in n for n in data["notes"])


def test_an_empty_report_still_renders(html_report):
    _, _, _, data = html_report("--no-warehouse")
    assert data["days"] == [] and data["repos"] == [] and data["totals"]["first_day"] is None


def test_a_missing_template_stops(run, config_path, projects_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(cu, "html_template_path", lambda: str(tmp_path / "gone.html"))
    with pytest.raises(SystemExit, match="cannot read the HTML template"):
        run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse",
            "--html", str(tmp_path / "r.html"))


def test_a_template_without_the_marker_stops(run, config_path, projects_dir, tmp_path, monkeypatch):
    template = tmp_path / "t.html"
    template.write_text("<!doctype html><p>no data here</p>")
    monkeypatch.setattr(cu, "html_template_path", lambda: str(template))
    with pytest.raises(SystemExit, match="data marker"):
        run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse",
            "--html", str(tmp_path / "r.html"))


def test_the_shipped_template_holds_the_marker_once():
    with open(cu.html_template_path(), encoding="utf-8") as fh:
        assert fh.read().count(cu.HTML_DATA_MARKER) == 1


def test_a_day_with_real_rows_is_not_imported(html_report, usage, warehouse_path, local_tz):
    local_tz("UTC")
    imported = {"id": "stats-cache:box:2026-09-01:claude-opus-5", "rq": "", "t": "2026-09-01T12:00:00.000Z",
                "m": "claude-opus-5", "i": 0, "o": 1000, "w5": 0, "w1": 0, "r": 0, "f": 0,
                "e": cu.IMPORTED_ENTRYPOINT, "s": "", "p": "", "n": 0, "mach": "box"}
    warehouse_path.parent.mkdir(parents=True)
    warehouse_path.write_text(json.dumps(imported) + "\n")
    assert html_report()[3]["days"][0] == {"day": "2026-09-01", "imported": False}
