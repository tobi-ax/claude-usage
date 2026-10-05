import builtins
import json
import urllib.error

import pytest

from conftest import cu


@pytest.fixture
def answers(monkeypatch):
    """Feed the wizard's input() calls from a list; input() raises EOFError once it is used up.

    Returns the list of prompts the wizard showed.
    """
    prompts = []

    def feed(*replies):
        queue = list(replies)

        def fake_input(prompt=""):
            prompts.append(prompt)
            if not queue:
                raise EOFError
            return queue.pop(0)
        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts
    return feed


@pytest.fixture
def setup(run, config_path, fake_ssh):
    def call(*argv):
        return run("setup", "--config", str(config_path), *argv)
    return call


def saved(config_path):
    return json.loads(config_path.read_text())


def test_first_setup_adds_a_host_verifies_it_and_fetches_prices(setup, answers, config_path, pricing_page,
                                                                 pricing_path):
    answers("y", "", "box", "", "", "", "n")
    code, out, _ = setup()
    assert code == 0
    assert "  required." in out
    assert saved(config_path) == {"hosts": ["box"]}
    assert "  box: ok: 0 responses, 0 files, python " in out
    assert "No prior config, fetching current model prices now." in out
    assert pricing_path.exists()


def test_rerun_keeps_removes_and_edits_hosts_and_other_keys(setup, answers, write_config, config_path, pricing_path):
    write_config({"hosts": ["keep-me", {"ssh": "drop-me"}, "edit-me"], "timeout": 30, "warehouse": "/w"})
    answers("k", "r", "e", "edited", "Edited", "", "", "n", "n")
    code, _, _ = setup()
    assert code == 0
    assert saved(config_path) == {"hosts": ["keep-me", {"ssh": "edited", "name": "Edited"}],
                                  "timeout": 30, "warehouse": "/w"}
    assert not pricing_path.exists()


def test_custom_projects_dir_and_python_are_saved(setup, answers, config_path, pricing_page):
    answers("y", "box", "", "/data/claude", "python3", "n")
    setup()
    assert saved(config_path)["hosts"] == [{"ssh": "box", "projects_dir": "/data/claude"}]


@pytest.mark.parametrize("choice, hosts", [("r", []), ("k", ["unreachable"])])
def test_a_host_that_fails_verification_can_be_removed_or_kept(setup, answers, write_config, config_path,
                                                               choice, hosts):
    write_config({"hosts": ["unreachable"]})
    answers("k", "n", choice, "n")
    _, out, _ = setup()
    assert "  unreachable: FAILED: ssh: connect to host unreachable port 22: Connection refused" in out
    assert saved(config_path)["hosts"] == hosts


def test_an_edited_host_is_verified_again(setup, answers, write_config, config_path):
    write_config({"hosts": ["unreachable"]})
    answers("k", "n", "e", "box", "box", "", "", "n")
    _, out, _ = setup()
    assert "  box: ok: " in out
    assert saved(config_path)["hosts"] == ["box"]


def test_running_out_of_input_aborts_without_writing(setup, answers, config_path):
    answers()
    code, _, err = setup()
    assert code == 1
    assert "setup aborted: no input, nothing was written." in err
    assert not config_path.exists()


def test_no_input_at_the_price_question_skips_the_update(setup, answers, write_config, pricing_path):
    write_config({"hosts": []})
    answers("n")
    code, _, err = setup()
    assert code == 0
    assert "skipping the price update" in err
    assert not pricing_path.exists()


def test_a_failed_price_update_still_keeps_the_config(setup, answers, monkeypatch, config_path):
    def fail(url, timeout):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(cu, "fetch_pricing_markdown", fail)
    answers("n")
    code, _, err = setup()
    assert code == 1
    assert "price update failed;" in err
    assert saved(config_path) == {"hosts": []}


def test_price_update_from_setup_reports_overrides_in_its_config(run, answers, tmp_path, pricing_page):
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"hosts": [], "pricing": {"claude-opus-5": [5.0, 25.0, 0.5]}}))
    answers("n", "y")
    _, out, _ = run("setup", "--config", str(path))
    assert "1 model is overridden in your config" in out


def test_price_update_from_setup_ignores_overrides_in_the_default_config(run, answers, tmp_path, write_config,
                                                                          pricing_page):
    write_config({"pricing": {"claude-opus-5": [5.0, 25.0, 0.5]}})
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"hosts": []}))
    answers("n", "y")
    _, out, _ = run("setup", "--config", str(path))
    assert "overridden in your config" not in out


def test_retry_hint_names_a_non_default_config(run, answers, monkeypatch, tmp_path):
    def fail(url, timeout):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(cu, "fetch_pricing_markdown", fail)
    path = tmp_path / "my config.json"
    answers("n")
    _, _, err = run("setup", "--config", str(path))
    assert "Retry with 'claude-usage update --config '{}''.".format(path) in err


def test_retry_hint_for_the_default_config_has_no_flag(setup, answers, monkeypatch):
    def fail(url, timeout):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(cu, "fetch_pricing_markdown", fail)
    answers("n")
    _, _, err = setup()
    assert "Retry with 'claude-usage update'." in err


def test_a_config_path_starting_with_a_dash_reaches_the_price_update(run, answers, pricing_page, tmp_path,
                                                                     monkeypatch):
    monkeypatch.chdir(tmp_path)
    answers("n")
    code, _, _ = run("setup", "--config=-dash.json")
    assert code == 0
    assert (tmp_path / "-dash.json").exists()


def test_no_input_hint_names_a_non_default_config(run, answers, tmp_path):
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"hosts": []}))
    answers("n")
    _, _, err = run("setup", "--config", str(path))
    assert "Run 'claude-usage update --config {}' to fetch prices later.".format(path) in err
