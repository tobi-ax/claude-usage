import json
import platform

import pytest

from conftest import cu, record, write_transcript


@pytest.fixture
def mixed_usage(projects_dir):
    """Three responses: Opus 5 standard and fast, plus one on a model with no price."""
    write_transcript(projects_dir, "-work-alpha/s1.jsonl", [
        record(model="claude-opus-5", msg_id="m1", output=1_000_000, read=2_000_000,
               session="s1", cwd="/work/alpha", ts="2026-09-01T09:00:00.000Z"),
        record(model="claude-opus-5", msg_id="m2", output=1_000_000, speed="fast",
               session="s1", cwd="/work/alpha", ts="2026-09-02T09:00:00.000Z", entrypoint="sdk-ts"),
    ])
    write_transcript(projects_dir, "-work-beta/s2.jsonl", [
        record(model="claude-future-9", msg_id="m3", input=5, output=7,
               session="s2", cwd="/work/beta", ts="2026-09-02T10:00:00.000Z"),
    ])


def test_totals_and_cost(report, mixed_usage):
    code, doc, _ = report("--no-warehouse")
    assert code == 0
    assert doc["totals"] == {
        "Input (uncached)": 5, "Cache write, 5 min TTL": 0, "Cache write, 1 h TTL": 0,
        "Cache read": 2_000_000, "Output": 2_000_007, "total": 4_000_012,
        "calls": 3, "cost_usd": 25.0 + 1.0 + 50.0, "unpriced_tokens": 12,
    }


def test_by_model_separates_fast_mode(report, mixed_usage):
    _, doc, _ = report("--no-warehouse")
    assert sorted(doc["by_model"]) == ["claude-future-9", "claude-opus-5", "claude-opus-5 (fast)"]
    assert doc["by_model"]["claude-opus-5 (fast)"]["cost_usd"] == 50.0
    assert doc["unpriced_models"] == ["claude-future-9"]


def test_local_machine_is_named_after_this_host(report, mixed_usage):
    _, doc, _ = report("--no-warehouse")
    assert list(doc["by_machine"]) == [platform.node()]
    assert doc["machines"][0]["files"] == 2


@pytest.mark.parametrize("by, keys", [
    ("month", ["2026-09"]),
    ("entrypoint", ["cli", "sdk-ts"]),
    ("project", ["/work/alpha", "/work/beta"]),
])
def test_breakdowns(report, mixed_usage, local_tz, by, keys):
    local_tz("UTC")
    _, doc, _ = report("--no-warehouse", "--by", by)
    assert sorted(doc["by"][by]) == keys


def test_session_breakdown_is_keyed_by_machine_and_session(report, mixed_usage):
    _, doc, _ = report("--no-warehouse", "--by", "session")
    assert sorted(doc["by"]["session"]) == ["{}/s1".format(platform.node()), "{}/s2".format(platform.node())]
    assert doc["by"]["session"]["{}/s1".format(platform.node())]["calls"] == 2


def test_setup_done_follows_the_config_file(report, mixed_usage, write_config):
    assert report("--no-warehouse")[1]["setup_done"] is False
    write_config({})
    assert report("--no-warehouse")[1]["setup_done"] is True


def test_nothing_to_scan_stops(run, config_path):
    with pytest.raises(SystemExit, match="nothing to scan"):
        run("--config", str(config_path), "--no-local", "--no-warehouse")


def test_invalid_config_json_stops(run, config_path):
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{")
    with pytest.raises(SystemExit, match="is not valid JSON"):
        run("--config", str(config_path))


def test_empty_report(report):
    code, doc, _ = report("--no-warehouse")
    assert code == 0
    assert doc["totals"]["calls"] == 0
    assert doc["by_model"] == {}


# --------------------------------------------------------------------------
# Text report: the lines a reader acts on, not the layout
# --------------------------------------------------------------------------

@pytest.fixture
def text_report(run, config_path, projects_dir):
    def call(*argv):
        return run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse", *argv)[1]
    return call


def test_text_report_warns_about_unpriced_models(text_report, mixed_usage):
    assert "  WARNING: no pricing for claude-future-9 -> 12 tokens excluded from the cost." in text_report()


def test_text_report_shows_the_cost(text_report, mixed_usage):
    assert "  Estimated cost at Anthropic API list prices: $76.00" in text_report()


def test_text_report_hints_at_setup_without_a_config(text_report, mixed_usage, write_config):
    hint = "  No config yet: run 'claude-usage setup' to add SSH hosts and fetch current prices."
    assert hint in text_report()
    write_config({})
    assert hint not in text_report()


def test_exact_prints_full_token_counts(text_report, mixed_usage):
    assert "2,000,007" in text_report("--exact")
    assert "2.0M" in text_report()


def test_text_report_lists_requested_breakdowns(text_report, mixed_usage):
    out = text_report("--by", "day", "--by", "session")
    assert "\nBy day\n" in out
    assert "\nTop 15 sessions by cost\n" in out


@pytest.mark.parametrize("n, exact, text", [
    (999, False, "999"),
    (1000, False, "1.0k"),
    (1_250_000, False, "1.2M"),
    (3_400_000_000, False, "3.4B"),
    (1_250_000, True, "1,250,000"),
])
def test_fmt_tok(n, exact, text):
    assert cu.fmt_tok(n, exact) == text


def test_fmt_pct_of_zero_is_a_dash():
    assert cu.fmt_pct(0, 0) == "-"
    assert cu.fmt_pct(1, 3) == "33.3%"


def test_render_table_aligns_the_first_column_left_and_the_rest_right():
    assert cu.render_table(["Name", "N"], [["a", "1"], ["bbb", "22"]]).splitlines() == [
        "  Name   N", "  ----  --", "  a      1", "  bbb   22"]


def test_render_table_without_rows():
    assert cu.render_table(["Name"], []) == "  (nothing)"


def test_text_report_names_the_fetched_price_source(text_report, mixed_usage, pricing_path):
    pricing_path.parent.mkdir(parents=True)
    pricing_path.write_text(json.dumps({"fetched_at": "2026-10-05T10:00:00+02:00", "source": cu.PRICING_SOURCE_URL,
                                        "models": {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)}}))
    assert "  Prices: fetched 2026-10-05 from platform.claude.com, {} models only in the built-in table".format(
        len(cu.PRICING) - 1) in text_report()


@pytest.mark.parametrize("argv, period", [
    ((), "2026-09-01 to 2026-09-02 (all data)"),
    (("--since", "2026-09-02"), "2026-09-02 to today"),
    (("--until", "2026-09-01"), "start to 2026-09-01"),
    (("--since", "2030-01-01", "--until", "2030-01-31"), "2030-01-01 to 2030-01-31"),
])
def test_text_report_header_names_the_period(text_report, mixed_usage, local_tz, argv, period):
    local_tz("UTC")
    assert text_report(*argv).startswith("Claude Code usage  ·  {}  ·  1 machine\n".format(period))


def test_text_report_of_nothing_says_no_data(text_report):
    assert text_report().startswith("Claude Code usage  ·  no data  ·  1 machine\n")
