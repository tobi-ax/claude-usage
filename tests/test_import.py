import json

import pytest

from conftest import cu, record, write_transcript


def cache(model_usage, daily, last="2026-06-22"):
    return {"modelUsage": model_usage, "dailyModelTokens": daily, "lastComputedDate": last}


def usage(i=0, o=0, w=0, r=0):
    return {"inputTokens": i, "outputTokens": o, "cacheCreationInputTokens": w, "cacheReadInputTokens": r}


@pytest.mark.parametrize("total, shares, parts", [
    (10, [0.5, 0.5], [5, 5]),
    (10, [1 / 3, 1 / 3, 1 / 3], [3, 3, 4]),
    (7, [1.0], [7]),
    (0, [0.2, 0.8], [0, 0]),
    (5, [], []),
])
def test_split_by_share_sums_to_the_total(total, shares, parts):
    assert cu.split_by_share(total, shares) == parts


def test_stats_cache_rows_spread_exact_totals_over_days_by_share():
    c = cache({"claude-opus-4-8": usage(i=100, o=1000, w=400, r=10_000)},
              [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 300}},
               {"date": "2026-06-02", "tokensByModel": {"claude-opus-4-8": 100}}])
    rows = cu.stats_cache_rows(c, "box")
    assert [(r["t"], r["i"], r["o"], r["w5"], r["r"]) for r in rows] == [
        ("2026-06-01T12:00:00.000Z", 75, 750, 300, 7500),
        ("2026-06-02T12:00:00.000Z", 25, 250, 100, 2500),
    ]
    assert {(r["n"], r["e"], r["w1"], r["mach"], r["f"]) for r in rows} == {(0, "stats-cache", 0, "box", 0)}
    assert rows[0]["id"] == "stats-cache:box:2026-06-01:claude-opus-4-8"


def test_a_model_without_daily_entries_lands_on_the_last_computed_date():
    rows = cu.stats_cache_rows(cache({"claude-sonnet-4-6": usage(o=50)}, []), "box")
    assert [(r["t"][:10], r["o"]) for r in rows] == [("2026-06-22", 50)]


def test_rows_without_tokens_are_dropped():
    c = cache({"claude-opus-4-8": usage(o=1)},
              [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 1}},
               {"date": "2026-06-02", "tokensByModel": {"claude-opus-4-8": 0}}])
    assert [r["t"][:10] for r in cu.stats_cache_rows(c, "box")] == ["2026-06-01"]


@pytest.fixture
def stats_cache(tmp_path):
    def write(doc):
        path = tmp_path / "stats-cache.json"
        path.write_text(json.dumps(doc))
        return path
    return write


def import_into(run, config_path, projects_dir, path, machine="old-laptop"):
    return run("--config", str(config_path), "--projects-dir", str(projects_dir),
               "--import-stats-cache", str(path), "--import-machine", machine)


def test_import_only_adds_days_before_the_machines_first_transcript(run, report, stats_cache, warehouse_path,
                                                                     config_path, projects_dir):
    warehouse_path.parent.mkdir(parents=True)
    warehouse_path.write_text(json.dumps({
        "id": "m1", "rq": "r1", "t": "2026-06-02T08:00:00.000Z", "m": "claude-opus-4-8", "i": 0, "o": 10,
        "w5": 0, "w1": 0, "r": 0, "f": 0, "e": "cli", "s": "", "p": "", "mach": "old-laptop"}) + "\n")
    path = stats_cache(cache({"claude-opus-4-8": usage(o=300)},
                             [{"date": d, "tokensByModel": {"claude-opus-4-8": 100}}
                              for d in ("2026-06-01", "2026-06-02", "2026-06-03")]))
    code, out, _ = import_into(run, config_path, projects_dir, path)
    assert code == 0
    assert "Imported 1 approximate model-day rows for old-laptop" in out
    assert "skipped 2 rows on or after 2026-06-02" in out
    _, doc, _ = report("--no-local")
    assert doc["approximate_rows"] == 1
    assert doc["totals"]["calls"] == 1
    assert doc["totals"]["Output"] == 110


def test_import_of_a_missing_file_stops(run, config_path, projects_dir, tmp_path):
    with pytest.raises(SystemExit, match="stats cache not found"):
        import_into(run, config_path, projects_dir, tmp_path / "nope.json")


def test_reimporting_the_same_cache_adds_nothing(run, stats_cache, warehouse_path, config_path, projects_dir):
    path = stats_cache(cache({"claude-opus-4-8": usage(o=300)},
                             [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 1}}]))
    import_into(run, config_path, projects_dir, path)
    import_into(run, config_path, projects_dir, path)
    assert len(warehouse_path.read_text().splitlines()) == 1


def test_reimport_replaces_the_earlier_import(run, stats_cache, warehouse_path, config_path, projects_dir):
    day = [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 1}}]
    import_into(run, config_path, projects_dir, stats_cache(cache({"claude-opus-4-8": usage(o=300)}, day)))
    import_into(run, config_path, projects_dir, stats_cache(cache({"claude-opus-4-8": usage(o=200)}, day)))
    assert [json.loads(l)["o"] for l in warehouse_path.read_text().splitlines()] == [200]


def test_text_report_marks_imported_rows_as_approximate(run, stats_cache, config_path, projects_dir):
    path = stats_cache(cache({"claude-opus-4-8": usage(o=2000)},
                             [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 1}}]))
    import_into(run, config_path, projects_dir, path)
    write_transcript(projects_dir, "a.jsonl", [record()])
    _, out, _ = run("--config", str(config_path), "--projects-dir", str(projects_dir))
    assert ("  Approximate: 1 model-day rows imported from Claude's stats cache cover 2026-06-01 to 2026-06-01 "
            "on old-laptop (2.0k tokens).") in out


def test_reimport_drops_model_days_the_new_cache_no_longer_has(run, stats_cache, warehouse_path, config_path,
                                                                projects_dir):
    two_days = [{"date": d, "tokensByModel": {"claude-opus-4-8": 1}} for d in ("2026-06-01", "2026-06-02")]
    import_into(run, config_path, projects_dir, stats_cache(cache({"claude-opus-4-8": usage(o=300)}, two_days)))
    import_into(run, config_path, projects_dir, stats_cache(cache({"claude-opus-4-8": usage(o=300)}, two_days[:1])))
    assert [json.loads(l)["t"][:10] for l in warehouse_path.read_text().splitlines()] == ["2026-06-01"]


def test_reimport_keeps_real_rows_and_other_machines_imports(run, stats_cache, warehouse_path, config_path,
                                                             projects_dir):
    real = {"id": "m1", "rq": "r1", "t": "2026-06-05T08:00:00.000Z", "m": "claude-opus-4-8", "i": 0, "o": 10,
            "w5": 0, "w1": 0, "r": 0, "f": 0, "e": "cli", "s": "", "p": "", "mach": "old-laptop"}
    warehouse_path.parent.mkdir(parents=True)
    warehouse_path.write_text(json.dumps(real) + "\n")
    day = [{"date": "2026-06-01", "tokensByModel": {"claude-opus-4-8": 1}}]
    path = stats_cache(cache({"claude-opus-4-8": usage(o=300)}, day))
    import_into(run, config_path, projects_dir, path, machine="other-box")
    import_into(run, config_path, projects_dir, path)
    import_into(run, config_path, projects_dir, path)
    rows = [json.loads(l) for l in warehouse_path.read_text().splitlines()]
    assert sorted((r["mach"], r["e"]) for r in rows) == [
        ("old-laptop", "cli"), ("old-laptop", "stats-cache"), ("other-box", "stats-cache")]
