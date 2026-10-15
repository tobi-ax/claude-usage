import platform

import pytest

from conftest import cu, record, write_transcript


# --------------------------------------------------------------------------
# Keys of the newer breakdowns
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model, family", [
    ("claude-opus-5-5", "opus"),
    ("claude-fable-5-1", "fable"),
    ("claude-haiku-4-5-20251001", "haiku"),
    ("claude-3-5-haiku", "haiku"),
    ("claude-3-5-sonnet-20241022", "sonnet"),
    ("some-other-model", "some-other-model"),
])
def test_model_family(model, family):
    assert cu.model_family(model) == family


def test_hour_and_weekday_use_local_time(local_tz):
    local_tz("Europe/Berlin")
    row = {"t": "2026-09-13T22:30:00.000Z", "e": "cli"}  # Sunday 22:30 UTC, Monday 00:30 in Berlin
    assert cu.GROUPINGS["hour"][1](row) == "00"
    assert cu.GROUPINGS["weekday"][1](row) == "1 Mon"


def test_imported_rows_have_no_hour_but_a_weekday(local_tz):
    local_tz("UTC")
    row = {"t": "2026-09-16T12:00:00.000Z", "e": cu.IMPORTED_ENTRYPOINT}
    assert cu.GROUPINGS["hour"][1](row) == "unknown"
    assert cu.GROUPINGS["weekday"][1](row) == "3 Wed"


# --------------------------------------------------------------------------
# Repository labels
# --------------------------------------------------------------------------

WORKSPACE_PATHS = {
    "/home/me/Code",
    "/home/me/Code/app",
    "/home/me/Code/app/docs/requirements",
    "/home/me/Code/app/node_modules/.pnpm/lib@1.0.0/node_modules/lib",
    "/home/me/Code/app/.claude/worktrees/probe",
    "/home/me/Code/app-fix-login",
    "/home/me/Code/mono",
    "/home/me/Code/mono/services/api",
    "/home/me/Code/tool",
    "/home/me/.t3/worktrees/app/t3code-1a2b3c4d",
    "/home/me/.t3/worktrees/viewer/t3code-5e6f7a8b",
    "/home/me/Code/viewer-demo",
    "/home/me/.local/share/pnpm/store/files",
    "/tmp/t3code-claude-title-abc123",
}


@pytest.mark.parametrize("path, label", [
    ("/home/me/Code/app", "app"),
    ("/home/me/Code/app/docs/requirements", "app"),
    ("/home/me/Code/app/node_modules/.pnpm/lib@1.0.0/node_modules/lib", "app"),
    ("/home/me/Code/app/.claude/worktrees/probe", "app"),
    ("/home/me/Code/app-fix-login", "app"),
    ("/home/me/.t3/worktrees/app/t3code-1a2b3c4d", "app"),
    ("/home/me/Code/mono/services/api", "mono"),
    ("/home/me/Code/tool", "tool"),
    ("/home/me/.t3/worktrees/viewer/t3code-5e6f7a8b", "viewer"),
    ("/home/me/Code/viewer-demo", "viewer"),
    ("/home/me/Code", "/home/me/Code"),
    ("/tmp/t3code-claude-title-abc123", cu.TEMP_LABEL),
])
def test_repo_labels(path, label):
    assert cu.repo_labels(WORKSPACE_PATHS)[path] == label


def test_a_path_deep_inside_a_workspace_is_not_a_repo():
    paths = {"/home/me", "/home/me/Code", "/home/me/Code/a", "/home/me/Code/b", "/home/me/.local/share/pnpm/store/files"}
    assert cu.repo_labels(paths)["/home/me/.local/share/pnpm/store/files"] == "/home/me/.local/share/pnpm/store/files"


def test_one_subdirectory_does_not_make_a_repo_a_workspace():
    labels = cu.repo_labels({"/c/app", "/c/app/src", "/c/tool"})
    assert labels["/c/app/src"] == "app"


def test_a_trailing_slash_does_not_matter():
    labels = cu.repo_labels({"/c/", "/c/app/", "/c/tool", "/c/app/src/"})
    assert labels["/c/app/src/"] == "app"
    assert labels["/c/"] == "/c"


def test_the_filesystem_root_as_a_workspace():
    labels = cu.repo_labels({"/", "/app", "/srv", "/home/u/.local/share/x"})
    assert labels["/app"] == "app"
    assert labels["/home/u/.local/share/x"] == "/home/u/.local/share/x"


def test_the_macos_temp_dir_counts_as_temp():
    assert cu.repo_labels({"/private/var/folders/ab/T/x"}) == {"/private/var/folders/ab/T/x": cu.TEMP_LABEL}


def test_known_limit_two_used_subdirectories_split_a_repo():
    labels = cu.repo_labels({"/h/Code/mono", "/h/Code/mono/frontend", "/h/Code/mono/services", "/h/Code/portal"})
    assert labels["/h/Code/mono"] == "/h/Code/mono"
    assert labels["/h/Code/mono/frontend"] == "frontend"


def test_known_limit_a_dashed_name_folds_into_its_prefix():
    labels = cu.repo_labels({"/h/Code/platform", "/h/Code/platform-api"})
    assert labels["/h/Code/platform-api"] == "platform"


def test_a_lone_project_is_a_repo():
    assert cu.repo_labels({"/work/project"}) == {"/work/project": "project"}


def test_a_suffix_only_folds_into_a_known_repo():
    labels = cu.repo_labels({"/c", "/c/app-fix", "/c/other"})
    assert labels["/c/app-fix"] == "app-fix"


def test_the_longest_known_prefix_wins():
    labels = cu.repo_labels({"/c", "/c/dots", "/c/dots-files", "/c/dots-files-apt"})
    assert labels["/c/dots-files-apt"] == "dots-files"


# --------------------------------------------------------------------------
# Single and pair breakdowns in the report
# --------------------------------------------------------------------------

@pytest.fixture
def two_repos(projects_dir):
    write_transcript(projects_dir, "-c-app/s1.jsonl", [
        record(model="claude-opus-5", msg_id="m1", output=1_000_000, session="s1",
               cwd="/c/app", ts="2026-09-01T09:00:00.000Z"),
        record(model="claude-sonnet-5", msg_id="m2", output=1_000_000, session="s1",
               cwd="/c/app/src", ts="2026-09-08T09:00:00.000Z"),
    ])
    write_transcript(projects_dir, "-c-tool/s2.jsonl", [
        record(model="claude-opus-5", msg_id="m3", output=100_000, session="s2",
               cwd="/c/tool", ts="2026-09-08T10:00:00.000Z"),
    ])
    write_transcript(projects_dir, "-c/s3.jsonl", [
        record(model="claude-haiku-4-5", msg_id="m4", output=1000, session="s3",
               cwd="/c", ts="2026-09-08T11:00:00.000Z"),
    ])


def test_by_repo(report, two_repos):
    _, doc, _ = report("--no-warehouse", "--by", "repo")
    assert sorted(doc["by"]["repo"]) == ["/c", "app", "tool"]
    assert doc["by"]["repo"]["app"]["calls"] == 2


def test_by_family(report, two_repos):
    _, doc, _ = report("--no-warehouse", "--by", "family")
    assert sorted(doc["by"]["family"]) == ["haiku", "opus", "sonnet"]


def test_a_pair_nests_in_json(report, two_repos, local_tz):
    local_tz("UTC")
    _, doc, _ = report("--no-warehouse", "--by", "week,family")
    pairs = doc["by"]["week,family"]
    assert {week: sorted(families) for week, families in pairs.items()} == {
        "2026-W36": ["opus"],
        "2026-W37": ["haiku", "opus", "sonnet"],
    }
    assert doc["by"]["week,family"]["2026-W36"]["opus"]["cost_usd"] == 25.0
    assert doc["by"]["week,family"]["2026-W37"]["opus"]["cost_usd"] == 2.5


def test_a_pair_keeps_both_names_as_columns(text_report, two_repos, local_tz):
    local_tz("UTC")
    out = text_report("--by", "week,machine")
    assert "\nBy week and machine\n" in out
    table = out.split("\nBy week and machine\n")[1].splitlines()
    assert table[0].split()[:3] == ["Week", "Machine", "Calls"]
    assert table[2].split()[:2] == ["2026-W36", platform.node()]


def test_a_pair_by_cost_orders_by_the_first_name_and_limits_it(text_report, two_repos):
    out = text_report("--by", "repo,family", "--top", "1")
    table = out.split("\nBy repo and family\n")[1].splitlines()
    assert [line.split()[:2] for line in table[2:]] == [["app", "opus"], ["app", "sonnet"]]


@pytest.mark.parametrize("top, firsts", [("2", ["app", "tool"]), ("0", ["app", "tool", "/c"])])
def test_top_limits_the_first_name_of_a_pair(text_report, two_repos, top, firsts):
    out = text_report("--by", "repo,family", "--top", top)
    table = out.split("\nBy repo and family\n")[1].split("\n\n")[0].splitlines()
    assert list(dict.fromkeys(line.split()[0] for line in table[2:])) == firsts


@pytest.fixture
def text_report(run, config_path, projects_dir):
    def call(*argv):
        return run("--config", str(config_path), "--projects-dir", str(projects_dir), "--no-warehouse", *argv)[1]
    return call


@pytest.mark.parametrize("value", ["nope", "day,day", "day,machine,model", "day,session", "session,day", ""])
def test_invalid_by_values_stop(run, value, capsys):
    with pytest.raises(SystemExit) as exc:
        run("--by", value)
    assert exc.value.code == 2
    assert "--by: expected one of" in capsys.readouterr().err
