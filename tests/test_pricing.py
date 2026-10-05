import json
import math

import pytest

from conftest import cu, record, write_transcript


def row(model="claude-opus-5", i=0, o=0, w5=0, w1=0, r=0, f=0, **extra):
    return {"m": model, "i": i, "o": o, "w5": w5, "w1": w1, "r": r, "f": f, **extra}


def test_derive_rates_puts_cache_writes_at_1_25x_and_2x_of_input():
    assert cu.derive_rates(4.0, 20.0, 0.2) == {
        "input": 4.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.2, "output": 20.0}


@pytest.mark.parametrize("model", sorted(cu.PRICING))
def test_every_builtin_entry_has_five_finite_rates_with_derived_cache_writes(model):
    rates = cu.PRICING[model]
    assert cu.is_rate_dict(rates)
    assert rates["cache_write_5m"] == pytest.approx(rates["input"] * 1.25)
    assert rates["cache_write_1h"] == pytest.approx(rates["input"] * 2.0)


@pytest.mark.parametrize("model, key", [
    ("claude-opus-5", "claude-opus-5"),
    ("claude-opus-4-1-20260101", "claude-opus-4-1"),
    ("claude-opus-4-20250514", "claude-opus-4"),
    ("claude-opus-4-0", "claude-opus-4"),
    ("claude-opus-4-0-20250514", "claude-opus-4"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ("claude-3-5-haiku-latest", "claude-3-5-haiku"),
    ("claude-3-5-haiku-20241022", "claude-3-5-haiku"),
    ("claude-opus-6-1", None),
    ("claude-opus-5-1", None),
    ("claude-opus-5-latest-preview", None),
    ("gpt-5", None),
])
def test_price_key(model, key):
    pricing = {**cu.PRICING, "claude-opus-4": cu.PRICING["claude-opus-4-1"],
               "claude-3-5-haiku": cu.derive_rates(0.8, 4.0, 0.08)}
    assert cu.price_key(model, pricing) == key


def test_row_cost_prices_each_token_type_at_its_rate():
    pricing = {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)}
    cost = cu.row_cost(row(i=1_000_000, o=1_000_000, w5=1_000_000, w1=1_000_000, r=1_000_000), pricing)
    assert cost == pytest.approx(5.0 + 25.0 + 6.25 + 10.0 + 0.5)


def test_fast_mode_doubles_every_rate_on_fast_mode_models():
    pricing = {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)}
    standard = cu.row_cost(row(i=1000, o=2000, r=3000), pricing)
    assert cu.row_cost(row(i=1000, o=2000, r=3000, f=1), pricing) == pytest.approx(2 * standard)


def test_fast_flag_on_a_model_without_fast_mode_changes_nothing():
    pricing = {"claude-sonnet-5": cu.derive_rates(2.0, 10.0, 0.2)}
    r = row(model="claude-sonnet-5", o=1_000_000)
    assert cu.row_cost({**r, "f": 1}, pricing) == cu.row_cost(r, pricing) == pytest.approx(10.0)


def test_row_cost_is_none_for_an_unpriced_model():
    assert cu.row_cost(row(model="claude-unknown-9", o=10), cu.PRICING) is None


@pytest.mark.parametrize("value, ok", [
    (1, True), (2.5, True), (0, True),
    (True, False), ("1", False), (None, False),
    (math.inf, False), (math.nan, False), (10 ** 400, False),
])
def test_is_finite_number(value, ok):
    assert cu.is_finite_number(value) is ok


def test_validate_pricing_entry_accepts_the_five_rate_shape():
    rates = cu.derive_rates(4.0, 20.0, 0.2)
    assert cu.validate_pricing_entry("cfg", "m", rates) == rates


def test_validate_pricing_entry_derives_cache_writes_from_three_numbers():
    assert cu.validate_pricing_entry("cfg", "m", [4.0, 20.0, 0.2]) == cu.derive_rates(4.0, 20.0, 0.2)


@pytest.mark.parametrize("value", [
    [4.0, 20.0],
    [4.0, 20.0, 0.2, 1.0],
    ["4", 20.0, 0.2],
    [4.0, 20.0, math.inf],
    {k: 1.0 for k in cu.RATE_KEYS if k != "output"},
    {**cu.derive_rates(4.0, 20.0, 0.2), "extra": 1.0},
    {**cu.derive_rates(4.0, 20.0, 0.2), "input": True},
    "4/20/0.2",
], ids=["two-numbers", "four-numbers", "string-number", "infinite", "missing-key", "extra-key", "bool-rate", "string"])
def test_validate_pricing_entry_rejects_other_shapes(value):
    with pytest.raises(SystemExit, match='pricing entry for "claude-x" is not valid'):
        cu.validate_pricing_entry("cfg.json", "claude-x", value)


def test_summarize_counts_calls_cost_and_unpriced_tokens():
    pricing = {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)}
    rows = [row(o=1_000_000), row(o=1_000_000, n=0), row(model="claude-new", i=3, o=4)]
    s = cu.summarize(rows, pricing)
    assert s["calls"] == 2
    assert s["cost"] == pytest.approx(50.0)
    assert s["unpriced"] == 7
    assert s["o"] == 2_000_004


# --------------------------------------------------------------------------
# Which price source a report uses: built-in < fetched pricing.json < config
# --------------------------------------------------------------------------

@pytest.fixture
def one_opus_response(projects_dir):
    write_transcript(projects_dir, "a.jsonl", [record(model="claude-opus-5", output=1_000_000)])


def write_fetched(pricing_path, models):
    pricing_path.parent.mkdir(parents=True, exist_ok=True)
    pricing_path.write_text(json.dumps({
        "fetched_at": "2026-10-05T10:00:00+02:00",
        "source": "https://platform.claude.com/docs/en/about-claude/pricing.md",
        "models": models,
    }))


def test_report_uses_the_builtin_table_without_a_fetched_file(report, one_opus_response):
    _, doc, _ = report("--no-warehouse")
    assert doc["pricing"] == {"source": "built-in", "as_of": cu.BUILTIN_PRICING_AS_OF, "config_overrides": []}
    assert doc["totals"]["cost_usd"] == 25.0


def test_fetched_prices_replace_builtin_ones(report, one_opus_response, pricing_path):
    write_fetched(pricing_path, {"claude-opus-5": cu.derive_rates(5.0, 30.0, 0.5)})
    _, doc, _ = report("--no-warehouse")
    assert doc["pricing"]["source"] == "fetched"
    assert doc["pricing"]["fetched_at"] == "2026-10-05T10:00:00+02:00"
    assert doc["totals"]["cost_usd"] == 30.0


def test_report_names_builtin_models_missing_from_the_fetched_file(report, one_opus_response, pricing_path):
    write_fetched(pricing_path, {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)})
    _, doc, _ = report("--no-warehouse")
    assert doc["pricing"]["builtin_only"] == sorted(k for k in cu.PRICING if k != "claude-opus-5")


def test_config_pricing_overrides_fetched_and_builtin(report, one_opus_response, pricing_path, write_config):
    write_fetched(pricing_path, {"claude-opus-5": cu.derive_rates(5.0, 30.0, 0.5)})
    write_config({"pricing": {"claude-opus-5": [5.0, 40.0, 0.5]}})
    _, doc, _ = report("--no-warehouse")
    assert doc["pricing"]["config_overrides"] == ["claude-opus-5"]
    assert doc["totals"]["cost_usd"] == 40.0


def test_text_report_states_the_price_source(run, one_opus_response, projects_dir, write_config):
    path = write_config({"pricing": {"claude-opus-5": [5.0, 40.0, 0.5]}})
    _, out, _ = run("--config", str(path), "--projects-dir", str(projects_dir), "--no-warehouse")
    assert "  Prices: built-in table ({}), 1 model overridden in config; run 'claude-usage update' for current ones".format(
        cu.BUILTIN_PRICING_AS_OF) in out


def test_malformed_config_pricing_stops_the_report(report, one_opus_response, write_config):
    write_config({"pricing": {"claude-opus-5": [5.0, 40.0]}})
    with pytest.raises(SystemExit, match='pricing entry for "claude-opus-5" is not valid'):
        report("--no-warehouse")
