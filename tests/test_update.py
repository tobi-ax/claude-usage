import json
import urllib.error

import pytest

from conftest import PRICING_PAGE, cu


@pytest.fixture(scope="module")
def page_models():
    return cu.parse_pricing_table(PRICING_PAGE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Parsing the pricing page
# --------------------------------------------------------------------------

def test_saved_page_parses_into_every_model_row(page_models):
    assert len(page_models) == 19


@pytest.mark.parametrize("model, rates", [
    ("claude-fable-5-1", (10.0, 12.5, 20.0, 0.25, 50.0)),
    ("claude-opus-5-5", (4.0, 5.0, 8.0, 0.2, 20.0)),
    ("claude-opus-5", (5.0, 6.25, 10.0, 0.5, 25.0)),
    ("claude-sonnet-5", (2.0, 2.5, 4.0, 0.2, 10.0)),
    ("claude-opus-4-1", (15.0, 18.75, 30.0, 1.5, 75.0)),
    ("claude-3-5-haiku", (0.8, 1.0, 1.6, 0.08, 4.0)),
])
def test_saved_page_rows_carry_all_five_rates(page_models, model, rates):
    keys = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
    assert tuple(page_models[model][k] for k in keys) == rates


def test_only_the_first_table_is_read(page_models):
    """The fast mode and batch tables further down list the same models at other prices."""
    assert page_models["claude-opus-5"]["input"] == 5.0


@pytest.mark.parametrize("model", sorted(cu.PRICING))
def test_builtin_table_matches_the_saved_page(page_models, model):
    assert cu.PRICING[model] == pytest.approx(page_models[model])


@pytest.mark.parametrize("cell, price", [
    ("$10 / MTok", 10.0),
    ("$12.50 / MTok", 12.5),
    ("$0.25 / MTok<sup>1</sup>", 0.25),
    ("$2 / MTok<sup>3</sup>", 2.0),
    ("$ 3 / MTok", 3.0),
    ("n/a", None),
    ("", None),
])
def test_extract_price(cell, price):
    assert cu.extract_price(cell) == price


@pytest.mark.parametrize("name, model_id", [
    ("Claude Opus 5.5", "claude-opus-5-5"),
    ("Claude Opus 4", "claude-opus-4"),
    ("Claude Haiku 4.5", "claude-haiku-4-5"),
    ("Claude Haiku 3.5 ([retired, except on Bedrock](https://example.com))", "claude-3-5-haiku"),
    ("Claude Mythos 5.1 ([limited availability](https://anthropic.com/glasswing))", "claude-mythos-5-1"),
    ("Claude Something Else", "claude-something-else"),
])
def test_model_id_from_name(name, model_id):
    assert cu.model_id_from_name(name) == model_id


def test_table_rows_without_the_header_is_empty():
    assert cu.table_rows("| Model | Input |\n| --- | --- |\n| Claude X 1 | $1 |\n") == []


def test_table_rows_stops_at_the_first_line_outside_the_table():
    text = ("| Model | Base input tokens |\n| --- | --- |\n| Claude A 1 | $1 |\n\n"
            "| Claude B 1 | $2 |\n")
    assert cu.table_rows(text) == [["Claude A 1", "$1"]]


@pytest.mark.parametrize("cells", [
    ["Tokenizer note", "$1", "$1", "$1", "$1", "$1"],
    ["Claude X 1", "$1", "$1", "$1", "$1"],
    ["Claude X 1", "$1", "$1", "-", "$1", "$1"],
    [],
], ids=["not-a-model", "too-few-cells", "missing-price", "empty"])
def test_price_row_skips_rows_without_a_complete_model_price(cells):
    assert cu.price_row(cells) is None


def test_pricing_diff():
    old = {"a": {"input": 1}, "b": {"input": 2}, "c": {"input": 3}}
    new = {"b": {"input": 2}, "c": {"input": 4}, "d": {"input": 5}}
    assert cu.pricing_diff(old, new) == (["d"], ["a"], ["c"])


# --------------------------------------------------------------------------
# pricing.json: validation, loading, saving
# --------------------------------------------------------------------------

def good_doc(**changes):
    return {"fetched_at": "2026-10-05T10:00:00+02:00", "source": cu.PRICING_SOURCE_URL,
            "models": {"claude-opus-5": cu.derive_rates(5.0, 25.0, 0.5)}, **changes}


@pytest.mark.parametrize("doc, error", [
    ([], "top level is not an object"),
    (good_doc(models=None), '"models" is missing or not an object'),
    (good_doc(models={}), '"models" is empty'),
    (good_doc(models={"b": {"input": 1}, "a": "x"}), "model(s) without all five numeric rates: a, b"),
    (good_doc(source=""), '"source" is missing or empty'),
    (good_doc(fetched_at=None), '"fetched_at" is missing or empty'),
    (good_doc(), None),
])
def test_pricing_doc_error(doc, error):
    assert cu.pricing_doc_error(doc) == error


def test_load_fetched_pricing_of_a_missing_file_is_silent(tmp_path, capsys):
    assert cu.load_fetched_pricing(str(tmp_path / "pricing.json")) is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("content, message", [
    (b"{not json", "not valid JSON"),
    (b"\xff\xfe", "not valid UTF-8"),
    (json.dumps(good_doc(models={})).encode(), '"models" is empty'),
])
def test_load_fetched_pricing_reports_and_ignores_a_bad_file(tmp_path, capsys, content, message):
    path = tmp_path / "pricing.json"
    path.write_bytes(content)
    assert cu.load_fetched_pricing(str(path)) is None
    assert message in capsys.readouterr().err


def test_save_pricing_file_writes_sorted_json_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "new-dir" / "pricing.json"
    cu.save_pricing_file(str(path), good_doc())
    assert json.loads(path.read_text()) == good_doc()
    assert path.read_text().endswith("}\n")
    assert [p.name for p in path.parent.iterdir()] == ["pricing.json"]


def test_failed_save_keeps_the_old_file_and_removes_its_temp_file(tmp_path):
    path = tmp_path / "out" / "pricing.json"
    path.parent.mkdir()
    path.write_text("old")
    with pytest.raises(TypeError):
        cu.save_pricing_file(str(path), {"models": {1, 2}})
    assert path.read_text() == "old"
    assert [p.name for p in path.parent.iterdir()] == ["pricing.json"]


# --------------------------------------------------------------------------
# claude-usage update
# --------------------------------------------------------------------------

def test_update_writes_the_fetched_prices(run, pricing_page, pricing_path, page_models):
    code, out, _ = run("update")
    assert code == 0
    assert pricing_page == [cu.PRICING_SOURCE_URL]
    doc = json.loads(pricing_path.read_text())
    assert doc["source"] == cu.PRICING_SOURCE_URL
    assert doc["models"] == page_models
    assert cu.pricing_doc_error(doc) is None
    assert "Fetched 19 models from {}".format(cu.PRICING_SOURCE_URL) in out


def test_first_update_compares_with_the_builtin_table(run, pricing_page):
    _, out, _ = run("update")
    new = sorted(set(cu.parse_pricing_table(cu.fetch_pricing_markdown(None, None))) - set(cu.PRICING))
    assert "  new (compared with the built-in table): {}".format(", ".join(new)) in out


def test_second_update_reports_no_changes(run, pricing_page):
    run("update")
    _, out, _ = run("update")
    assert "  no changes compared with the previously fetched table" in out


def test_update_reports_changed_and_removed_models(run, pricing_page, pricing_path):
    run("update")
    doc = json.loads(pricing_path.read_text())
    doc["models"]["claude-opus-5"]["output"] = 99.0
    doc["models"]["claude-retired-1"] = cu.derive_rates(1.0, 1.0, 0.1)
    pricing_path.write_text(json.dumps(doc))
    _, out, _ = run("update")
    assert "  changed (compared with the previously fetched table): claude-opus-5" in out
    assert "  removed (compared with the previously fetched table): claude-retired-1" in out


def test_update_names_config_overrides(run, pricing_page, write_config):
    path = write_config({"pricing": {"claude-opus-5": [5.0, 25.0, 0.5], "claude-x": [1, 1, 1]}})
    _, out, _ = run("update", "--config", str(path))
    assert "  2 models are overridden in your config and keep their configured rates: claude-opus-5, claude-x" in out


def failing_fetch(error):
    def fetch(url, timeout):
        raise error
    return fetch


@pytest.mark.parametrize("error, message", [
    (urllib.error.HTTPError(cu.PRICING_SOURCE_URL, 503, "Service Unavailable", {}, None), "HTTP 503 Service Unavailable"),
    (urllib.error.URLError("Name or service not known"), "Name or service not known"),
    (OSError("timed out"), "timed out"),
], ids=["http-error", "url-error", "os-error"])
def test_update_fails_without_writing_when_the_fetch_fails(run, monkeypatch, pricing_path, error, message):
    monkeypatch.setattr(cu, "fetch_pricing_markdown", failing_fetch(error))
    code, _, err = run("update")
    assert code == 1
    assert "could not fetch {}: {}".format(cu.PRICING_SOURCE_URL, message) in err
    assert not pricing_path.exists()


def test_update_refuses_a_page_with_too_few_models(run, monkeypatch, pricing_path):
    pricing_path.parent.mkdir(parents=True)
    pricing_path.write_text("previous")
    page = "| Model | Base input tokens | a | b | c | d |\n| - | - | - | - | - | - |\n| Claude X 1 | $1 | $1 | $1 | $1 | $1 |\n"
    monkeypatch.setattr(cu, "fetch_pricing_markdown", lambda url, timeout: page)
    code, _, err = run("update")
    assert code == 1
    assert "only 1 model(s) parsed" in err
    assert pricing_path.read_text() == "previous"


def test_update_names_a_single_config_override(run, pricing_page, write_config):
    path = write_config({"pricing": {"claude-opus-5": [5.0, 25.0, 0.5]}})
    _, out, _ = run("update", "--config", str(path))
    assert "  1 model is overridden in your config and keeps its configured rate: claude-opus-5" in out
