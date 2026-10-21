"""Tiled in nrw.toml: where an experiment's data and catalog may be on Tiled.

Step 3 of docs/plan-tiled-catalog.md. Nothing here talks to a server. These are
the rules an address on Tiled is held to before anything could be sent there,
and the person's own TILED_URL, which says which server may have their login.
``nrw.toml`` is committed with the project, so its addresses are written by
anyone who can commit; the person's TILED_URL is not, and the project's own
files setting it is refused outright.

A folder is ``location`` and an address on Tiled is ``url``, each read only
for its own kind: one key with two meanings broke the render back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench import env as env_module
from nr_workbench.cli import main
from nr_workbench.experiment.config import experiment_config_for
from nr_workbench.project.config import load_config
from nr_workbench.project.settings import (
    CHANGE_KEYS,
    NeedsConfirmation,
    SettingsError,
    experiment_block,
    save,
    validate,
)
from nr_workbench.tiled.address import (
    DEFAULT_URL,
    Server,
    address_problem,
    resolved,
    server,
    server_problem,
    shown,
)

CONTEXT = "https://tiled.ornl.gov/projects/isaac/{ipts}/context"
DATA = "https://tiled.ornl.gov/projects/isaac/{ipts}/autoreduction"
SHARED = "https://tiled.ornl.gov/projects/isaac/{ipts}/shared"

#: What stands for a secret someone pasted into an address.
SECRET = "SECRET-7f3a"


def own_settings(text: str) -> None:
    """The person's ``~/.nrw``, where the suite's isolation points it.

    Written as only its owner may write it: nrw takes no TILED_URL from a
    file others can, and a umask of 002 would otherwise make it one.
    """
    env_module.USER_ENV_PATH.write_text(text, encoding="utf-8")
    env_module.USER_ENV_PATH.chmod(0o600)


def append(project: Path, text: str) -> None:
    """A table written into nrw.toml by hand."""
    with (project / "nrw.toml").open("a", encoding="utf-8") as handle:
        handle.write(text)


def uncomment(project: Path, table: str, line: str, typed: str) -> None:
    """A setting made by hand as the template says: its line and its table's."""
    toml = project / "nrw.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count(f"# [experiment.{table}]") == 1 and text.count(line) == 1
    text = text.replace(f"# [experiment.{table}]", f"[experiment.{table}]")
    toml.write_text(text.replace(line, typed), encoding="utf-8")


def init_check(project: Path):
    return CliRunner().invoke(main, ["init", str(project), "--check"])


def problem(url: str, at: str = DEFAULT_URL) -> str | None:
    """Why *url* is refused, with *at* the person's server."""
    return address_problem(url, Server(at, "a test"), "url")


def nrw(*args: str, **kwargs):
    return CliRunner().invoke(main, list(args), **kwargs)


# --------------------------------------------------------------------------
# The rules an address on Tiled is held to
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "url"),
    [
        (DEFAULT_URL, CONTEXT),
        (DEFAULT_URL, DATA),
        (DEFAULT_URL, "https://tiled.ornl.gov:443/a/{ipts}/b-c_d.e"),
        (DEFAULT_URL, "https://TILED.ornl.gov/projects/{ipts}"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000/projects/{ipts}"),
        ("http://localhost", "http://localhost/{ipts}"),
        ("http://[::1]:9", "http://[::1]:9/{ipts}/a"),
    ],
)
def test_an_address_on_tiled_may_be(at: str, url: str) -> None:
    assert problem(url, at) is None


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("/SNS/REF_L/{ipts}/shared", "not an https:// address"),
        ("ftp://tiled.ornl.gov/x", "not an https:// address"),
        ("https:tiled.ornl.gov/x", "not an https:// address"),
        ("http://tiled.ornl.gov/x", "unencrypted"),
        ("https://me@tiled.ornl.gov/x", "user name"),
        ("https://me:secret@tiled.ornl.gov/x", "user name"),
        ("https://tiled.ornl.gov\\@evil.example/x", "user name"),
        ("https://tiled.ornl.gov/x?page=2", "query or a fragment"),
        ("https://tiled.ornl.gov/x#top", "query or a fragment"),
        ("https://tiled.ornl.gov/x?", "query or a fragment"),
        ("https://tiled.ornl.gov#@evil.example/x", "query or a fragment"),
        ("https://tiled.ornl.gov", "its path"),
        ("https://tiled.ornl.gov/", "its path"),
        ("https://tiled.ornl.gov/a//b", "its path"),
        ("https://tiled.ornl.gov/a/../b", "its path"),
        ("https://tiled.ornl.gov/a/%2e%2e", "its path"),
        ("https://tiled.ornl.gov/projects/x/", "ends with '/'"),
        ("https://tiled.ornl.gov/a b", "a space at position 25"),
        ("https://tiled.ornl.go\tv/x", "a tab"),
        ("https://tiled.ornl.gov\n/x", "a line break"),
        ("\x00https://tiled.ornl.gov/x", "U+0000"),
        ("https://tiled.ornl.gov/café", "U+00E9"),
        ("https://tiled.ornl.gov:99999/x", "not an address nrw can read"),
        ("https://tiled.ornl.gov:abc/x", "not an address nrw can read"),
        ("https://[::1/x", "not an address nrw can read"),
        ("https://tiled.ornl.gov:0/x", "port 0"),
        ("https://ti_led!/x", "does not name a host"),
        ("https://tiled.ornl.gov./x", "does not name a host"),
        ("https://[::1%25en0]/x", "does not name a host"),
        ("https://[v1.x]/x", "does not name a host"),
    ],
)
def test_an_address_is_refused_with_the_rule_it_breaks(url: str, said: str) -> None:
    assert said in (problem(url) or "")


@pytest.mark.parametrize(
    ("at", "url"),
    [
        (DEFAULT_URL, "https://evil.example[tiled.ornl.gov]/projects/x"),
        (DEFAULT_URL, "https://evil.example:8443[tiled.ornl.gov]/projects/x"),
        ("http://127.0.0.1:8000", "http://evil.example[127.0.0.1]:8000/projects/x"),
    ],
)
def test_a_host_is_read_whole_never_from_inside_brackets(at: str, url: str) -> None:
    """Older Pythons take the bracketed part for the host: evil.example is it."""
    assert problem(url, at) is not None


def test_an_address_on_another_server_is_refused_naming_both() -> None:
    said = problem("https://elsewhere.example/x") or ""

    assert "elsewhere.example" in said and "https://tiled.ornl.gov" in said
    assert "whoever can commit" in said


def test_nrws_own_default_on_another_server_is_no_one_elses_choice() -> None:
    """The address nobody wrote is not one a committer chose."""
    said = problem(CONTEXT, "https://tiled-test.ornl.gov") or ""

    assert "tiled-test.ornl.gov" in said and "remove TILED_URL" in said
    assert "whoever can commit" not in said


@pytest.mark.parametrize(
    "url",
    [
        CONTEXT,
        "https://TILED.ornl.gov:443/projects/{ipts}",
        "https://tiled.ornl.gov:0443/a/{ipts}",
    ],
)
def test_the_same_server_is_the_same_in_any_case_and_on_its_default_port(
    url: str,
) -> None:
    assert problem(url) is None


@pytest.mark.parametrize(
    "path",
    [
        "/projects/isaac/IPTS-99999/autoreduction",
        "/projects/isaac/ipts-99999/context",
        "/projects/isaac/IPTS-00001/context",  # the project's own, written in
        "/projects/isaac/{ipts}/IPTS-2",
        "/projects/isaac/{ipts}/notIPTS-2",
    ],
)
def test_an_address_naming_an_ipts_itself_is_refused(path: str) -> None:
    """On the person's server, with their permissions: another proposal's data,
    or this one's left behind when the project's IPTS changes."""
    said = problem(f"https://tiled.ornl.gov{path}") or ""

    assert "itself" in said and "Write {ipts} where the IPTS goes" in said


@pytest.mark.parametrize(
    "path",
    [
        "/projects/isaac",
        "/projects",
        "/projects/isaac/IPTS_99999/context",
        "/projects/isaac/99999/context",
        "/projects/isaac/data-{ipts}/context",
    ],
)
def test_an_address_naming_no_experiment_is_refused(path: str) -> None:
    """A container above every experiment would be read, or written, whole."""
    said = problem(f"https://tiled.ornl.gov{path}") or ""

    assert "does not name the project's experiment" in said


def test_the_ipts_filled_in_is_only_ever_one_nrw_made() -> None:
    assert resolved(CONTEXT, "IPTS-34347") == CONTEXT.replace("{ipts}", "IPTS-34347")
    assert resolved("https://tiled.ornl.gov/a", None) == "https://tiled.ornl.gov/a"
    for bad in (None, "IPTS-1/../x", "IPTS-1?x", "34347", "ipts-1"):
        with pytest.raises(ValueError):
            resolved(CONTEXT, bad)


@pytest.mark.parametrize(
    "url",
    [
        f"https://me:{SECRET}@tiled.ornl.gov/x",
        f"https://{SECRET}@elsewhere.example/x",
        f"https://tiled.ornl.gov/x?api_key={SECRET}",
        f"https://tiled.ornl.gov/x#{SECRET}",
        f"https://tiled.ornl.gov:99999/x?api_key={SECRET}",
        f"https://tiled.ornl.gov/x\t?api_key={SECRET}",
        f"https://me:{SECRET}@tiled.ornl.gov:0/x",
        f"https://tiled.ornl.gov/a;{SECRET}",
        f"https://tiled.ornl.gov;api_key={SECRET}/x",
        f"https://tiled.ornl.gov:{SECRET}/x",
    ],
)
def test_no_reason_ever_repeats_a_secret_in_an_address(url: str) -> None:
    """Reasons are shown on pages anyone logged in to the machine can read."""
    assert SECRET not in (problem(url) or "")
    assert SECRET not in (problem("https://tiled.ornl.gov/x", url) or "")
    assert SECRET not in (server_problem(url) or "")
    assert SECRET not in shown(url)


def test_an_address_is_shown_with_its_host_and_path() -> None:
    assert shown(CONTEXT) == CONTEXT
    assert shown(DEFAULT_URL) == DEFAULT_URL
    assert shown("https://me:pw@h.example:8443/a?b#c") == "https://…@h.example:8443/a"
    assert shown("https://h.example;k=1/a") == "https://…/a"
    assert shown("ftp://h.example/a") == "(not shown)"


# --------------------------------------------------------------------------
# The person's server: TILED_URL
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://tiled.ornl.gov/api/v1",
        "http://tiled.ornl.gov",
        "https://me@tiled.ornl.gov",
        "tiled.ornl.gov",
        "https://tiled.ornl.gov/?api_key=x",
        "https://tiled.ornl.gov:443x",
    ],
)
def test_tiled_url_is_a_server_alone_held_to_the_same_rules(url: str) -> None:
    assert server_problem(url) is not None


def test_tiled_url_comes_from_the_persons_own_settings(project: Path) -> None:
    assert server(project) == Server(DEFAULT_URL, "nrw's default")

    own_settings("TILED_URL=http://127.0.0.1:8000/\n")

    found = server(project)
    assert (found.url, found.problem) == ("http://127.0.0.1:8000", None)
    assert found.source.endswith(".nrw")


@pytest.mark.parametrize(
    ("name", "content", "names"),
    [
        (".env", b"TILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".env", b"TILED_API_KEY=k\n", "TILED_API_KEY"),
        (".env", b"TILED_URL=\n", "TILED_URL"),
        (".flaskenv", b"TILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".env.local", b"TILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".envrc", b"export TILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".env", b'"TILED_URL"=https://elsewhere.example\n', "TILED_URL"),
        (".env", b"  TILED_URL: https://elsewhere.example\n", "TILED_URL"),
        (".env", b"\xef\xbb\xbfTILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".env", b"A=\xff\xfe\nTILED_URL=https://elsewhere.example\n", "TILED_URL"),
        (".env", b'A="one\nTILED_URL=https://elsewhere.example\n"\n', "TILED_URL"),
        (".env", b"HOME=/tmp/elsewhere\nTILED_URL=x\n", "TILED_URL and HOME"),
    ],
    ids=[
        "env",
        "key",
        "set-empty",
        "flaskenv",
        "env-local",
        "envrc-export",
        "quoted-name",
        "colon",
        "byte-order-mark",
        "not-utf8",
        "in-a-quoted-value",
        "home",
    ],
)
def test_a_project_that_sets_a_persons_tiled_setting_gets_no_tiled_at_all(
    project: Path, monkeypatch, name: str, content: bytes, names: str
) -> None:
    """Whatever the value in effect, and read as the loosest loader would: a
    program that loads the project's files puts them in the environment,
    where they look like the person's own."""
    (project / name).write_bytes(content)
    own_settings("TILED_URL=https://tiled.ornl.gov\n")
    monkeypatch.setenv("TILED_URL", "https://elsewhere.example")

    said = server(project).problem or ""

    assert f"This project's {name} sets {names}." in said
    assert "removed" in said
    assert "elsewhere.example" not in said


def test_the_key_is_refused_with_the_server_the_project_sets(project: Path) -> None:
    """A service's settings go together: the project setting one stops both."""
    (project / ".env").write_text("TILED_URL=https://elsewhere.example\n")
    own_settings("TILED_API_KEY=" + "K" * 32 + "\n")

    setting, said = env_module.person_setting("TILED_API_KEY", project)

    assert setting is not None and "TILED_URL" in (said or "")
    assert "K" * 32 not in (said or "")


def test_a_project_file_that_sets_nothing_of_the_persons_trips_nothing(
    project: Path,
) -> None:
    (project / ".env").write_text(
        "# TILED_URL=https://x.example\nMY_TILED_URL=x\nTILED_URL_OLD=x\nA=1\n"
    )
    (project / ".env.example").write_text("TILED_URL=https://x.example\n")

    assert server(project).problem is None


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="no owner and mode to check")
def test_tiled_url_is_not_taken_from_a_file_others_can_write(project: Path) -> None:
    own_settings("TILED_URL=https://tiled.ornl.gov\n")
    env_module.USER_ENV_PATH.chmod(0o664)

    said = server(project).problem or ""

    assert "can be written by others (mode 664)" in said
    assert "chmod go-w" in said


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="no owner and mode to check")
@pytest.mark.parametrize(("mode", "refused"), [(0o777, True), (0o1777, False)])
def test_tiled_url_is_not_taken_through_a_folder_others_can_write(
    tmp_path: Path, monkeypatch, project: Path, mode: int, refused: bool
) -> None:
    """A link to a file in an open folder: anyone could put another there.
    A sticky folder, as /tmp is, keeps each file its owner's."""
    folder = tmp_path / "open"
    folder.mkdir()
    real = folder / "nrw-settings"
    real.write_text("TILED_URL=https://tiled.ornl.gov\n", encoding="utf-8")
    real.chmod(0o600)
    folder.chmod(mode)
    link = tmp_path / "linked-home" / ".nrw"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setattr(env_module, "USER_ENV_PATH", link)

    said = server(project).problem or ""

    assert ("which holds" in said and "can be written by others" in said) is refused


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="no owner and mode to check")
def test_tiled_url_is_not_taken_from_someone_elses_file(
    project: Path, monkeypatch
) -> None:
    own_settings("TILED_URL=https://tiled.ornl.gov\n")
    monkeypatch.setattr(os, "getuid", lambda: os.stat(project).st_uid + 1)

    said = server(project).problem or ""

    # The file's own owner, not a folder's above it ("which holds ...").
    assert "belongs to another user" in said and "which holds" not in said


def test_an_agents_command_does_not_choose_the_tiled_server(
    project: Path, monkeypatch
) -> None:
    """A misled agent's `TILED_URL=... nrw ...` would otherwise have the login."""
    monkeypatch.setenv("TILED_URL", "https://mirror.example")
    assert server(project).problem is None  # a person's shell may

    monkeypatch.setenv("NRW_AGENT", "1")
    said = server(project).problem or ""

    assert "agent" in said and "ESCALATIONS.md" in said
    assert "mirror.example" not in said  # never a value: one may be a key
    own_settings("TILED_URL=https://mirror.example\n")
    assert server(project).problem is None  # the person's own choice


@pytest.mark.parametrize(
    ("files", "environment"),
    [
        ("TILED_URL=${TILED_HOST}\n", {"TILED_HOST": "https://mirror.example"}),
        ("TILED_URL=https://tiled-test.ornl.gov\n", {"TILED_URL": ""}),
    ],
    ids=["through-a-variable", "set-empty"],
)
def test_an_agent_cannot_choose_the_server_by_way_of_the_persons_file(
    project: Path, monkeypatch, files: str, environment: dict[str, str]
) -> None:
    """What is in effect is held to what the files alone say, whatever its source."""
    own_settings(files)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NRW_AGENT", "1")

    assert "agent" in (server(project).problem or "")


# --------------------------------------------------------------------------
# Saving them
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "table", "url"),
    [("catalog.url", "catalog", SHARED), ("source.url", "source", DATA + "-x")],
)
def test_an_address_is_saved_where_init_renders_it_back(
    project: Path, key: str, table: str, url: str
) -> None:
    """A setting nrw writes that init does not render leaves a .nrw-new behind."""
    result = save(project, {key: url})

    assert result.changed
    assert load_config(project).raw["experiment"][table] == {"url": url}
    checked = init_check(project)
    assert checked.exit_code == 0, checked.output
    assert save(project, {key: None}).changed
    assert init_check(project).exit_code == 0


@pytest.mark.parametrize(
    ("key", "said"),
    [
        ("catalog.url", '[experiment.catalog] kind = "tiled"'),
        ("source.url", '[experiment.source] kind = "tiled"'),
    ],
)
def test_an_address_saved_where_its_kind_reads_none_is_said_to_do_nothing(
    project: Path, key: str, said: str
) -> None:
    result = save(project, {key: SHARED})

    assert any(said in w and "changes nothing" in w for w in result.warnings)
    problems = experiment_config_for(project).problems
    assert any("changes nothing" in p.message for p in problems), problems


def test_a_folder_saved_for_a_source_on_tiled_is_said_to_do_nothing(
    project: Path,
) -> None:
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    result = save(project, {"source.location": "/data/x"})

    assert any('kind = "local"' in w for w in result.warnings), result.warnings
    problems = experiment_config_for(project).problems
    assert any("location is read only" in p.message for p in problems), problems


@pytest.mark.parametrize("key", ["catalog.url", "source.url"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "/SNS/REF_L/{ipts}/shared",
        "http://tiled.ornl.gov/x",
        "https://me@tiled.ornl.gov/x",
        "https://tiled.ornl.gov/x?q=1",
        "https://tiled.ornl.gov/a/../b",
        "https://tiled.ornl.gov:99999/x",
        "https://tiled.ornl.gov:abc/x",
        "https://[::1/x",
        "https://tiled.ornl.gov/projects/isaac/IPTS-99999/context",
        "https://elsewhere.example/projects/{ipts}",
    ],
)
def test_every_rule_holds_for_an_address_saved(
    project: Path, key: str, value: str
) -> None:
    """Refused with its reason -- never a crash, which the page shows as a 500."""
    before = (project / "nrw.toml").read_bytes()

    with pytest.raises(SettingsError):
        save(project, {key: value})

    assert (project / "nrw.toml").read_bytes() == before


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("https://tiled.ornl.gov:443x", "not an address nrw can read"),
        ("ftp://tiled.ornl.gov", "not an https:// address"),
        ("https://tiled.ornl.gov/api", "has a path"),
    ],
)
def test_a_bad_tiled_url_refuses_a_save_with_its_reason(
    project: Path, url: str, said: str
) -> None:
    own_settings(f"TILED_URL={url}\n")

    with pytest.raises(SettingsError) as refused:
        save(project, {"catalog.url": CONTEXT})

    assert "TILED_URL" in str(refused.value) and said in str(refused.value)


@pytest.mark.parametrize(
    "where",
    [
        f"TILED_URL=https://me:{SECRET}@tiled.ornl.gov\n",
        f"TILED_URL=https://tiled.ornl.gov/?api_key={SECRET}\n",
    ],
)
def test_a_secret_in_tiled_url_is_never_repeated_by_a_save(
    project: Path, where: str
) -> None:
    own_settings(where)

    with pytest.raises(SettingsError) as refused:
        save(project, {"catalog.url": CONTEXT})

    assert SECRET not in str(refused.value)


def test_an_address_on_another_server_is_refused_on_saving(project: Path) -> None:
    own_settings("TILED_URL=https://tiled-test.ornl.gov\n")

    with pytest.raises(SettingsError) as refused:
        save(project, {"catalog.url": CONTEXT})

    assert "tiled-test.ornl.gov" in str(refused.value)
    assert "https://tiled.ornl.gov/projects" in str(refused.value)


def test_a_test_server_on_this_machine_may_be_used_once_it_is_tiled_url(
    project: Path,
) -> None:
    own_settings("TILED_URL=http://127.0.0.1:8000\n")

    result = save(project, {"catalog.url": "http://127.0.0.1:8000/a/{ipts}"})

    assert result.changed


def test_an_address_names_its_experiment_as_ipts_whatever_the_ipts(
    project: Path,
) -> None:
    """Written in, the IPTS would stay behind when [beamtime] ipts changed."""
    named = "https://tiled.ornl.gov/projects/isaac/IPTS-2/context"
    with pytest.raises(SettingsError, match="names IPTS-2 itself"):
        validate(project, {"ipts": "IPTS-2", "catalog.url": named})

    edits, _ = validate(project, {"ipts": "", "catalog.url": CONTEXT})

    assert edits["experiment.catalog"]["url"].value == CONTEXT


def test_the_catalog_cannot_be_kept_on_tiled_until_it_can_be_read(
    project: Path,
) -> None:
    with pytest.raises(SettingsError, match="not available yet"):
        save(project, {"catalog.kind": "tiled"})


def test_choosing_the_catalog_in_files_follows_the_default(project: Path) -> None:
    assert not save(project, {"catalog.kind": "parquet"}).changed


def test_a_url_is_an_address_and_a_location_a_folder(project: Path) -> None:
    with pytest.raises(SettingsError, match="not an https:// address"):
        save(project, {"source.url": "/SNS/REF_L/{ipts}/shared"})
    with pytest.raises(SettingsError, match="not a full path"):
        save(project, {"source.location": DATA})


def test_a_kind_that_cannot_be_used_is_said_before_the_address_it_reads(
    project: Path,
) -> None:
    with pytest.raises(SettingsError, match="not available yet"):
        validate(project, {"source.url": "ftp://x", "source.kind": "tiled"})


def test_every_setting_there_is_has_a_check(project: Path) -> None:
    """One with none would pass as a setting, then be dropped without a word."""
    values = {
        "ipts": "IPTS-2",
        "label": "x",
        "source.kind": "local",
        "source.location": "/data/x",
        "source.url": DATA,
        "source.settle_seconds": 60,
        "feed.kind": "directory",
        "feed.poll_seconds": 15,
        "catalog.kind": "parquet",
        "catalog.url": CONTEXT,
    }
    assert set(values) == set(CHANGE_KEYS)

    edits, _ = validate(project, values)

    made = {
        key if table == "beamtime" else f"{table.removeprefix('experiment.')}.{key}"
        for table, keys in edits.items()
        for key in keys
    }
    assert made == set(CHANGE_KEYS)


def test_with_the_catalog_on_tiled_an_ipts_change_says_it_moves_the_catalog(
    project: Path,
) -> None:
    """On Tiled the catalog is the experiment's own, beside its data."""
    uncomment(project, "catalog", '# kind = "parquet"', 'kind = "tiled"')

    with pytest.raises(NeedsConfirmation) as asked:
        save(project, {"ipts": "IPTS-2"}, catalogued_runs=3)

    assert "its catalog on Tiled" in str(asked.value)
    assert "IPTS-2's" in str(asked.value)


def test_an_ipts_change_is_worded_for_the_catalog_the_change_leaves(
    project: Path,
) -> None:
    """The catalog taken back into the project's files in the same save."""
    uncomment(project, "catalog", '# kind = "parquet"', 'kind = "tiled"')

    with pytest.raises(NeedsConfirmation) as asked:
        save(project, {"ipts": "IPTS-2", "catalog.kind": "parquet"}, catalogued_runs=3)

    assert "runs already catalogued stay as they are" in str(asked.value)


def test_a_kind_is_read_alike_by_the_save_and_the_reader(project: Path) -> None:
    """Written with spaces around it by hand, it is one kind to both."""
    append(project, '\n[experiment.source]\nkind = " tiled "\n')

    _, warnings = validate(project, {"source.url": DATA})

    assert warnings == []
    assert experiment_config_for(project).source.address is not None


# --------------------------------------------------------------------------
# Set by hand, as the template says, and set back
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "line"),
    [("source", '# kind = "local"'), ("catalog", '# kind = "parquet"')],
)
def test_a_kind_set_by_hand_as_the_template_says_renders_back(
    project: Path, table: str, line: str
) -> None:
    """A placeholder that followed the kind went stale here: init left a
    .nrw-new, and the old folder's placeholder read as a setting typed into a
    comment."""
    uncomment(project, table, line, 'kind = "tiled"')

    checked = init_check(project)
    problems = experiment_config_for(project).problems

    assert checked.exit_code == 0, checked.output
    assert not any("in a comment" in p.message for p in problems), problems


def test_a_kind_set_by_hand_in_a_table_of_its_own_reads_no_comment_as_set(
    project: Path,
) -> None:
    append(project, '\n[experiment.source]\nkind = "tiled"\n')

    problems = experiment_config_for(project).problems

    assert not any("in a comment" in p.message for p in problems), problems


def test_a_kind_set_by_hand_and_reset_on_the_page_leaves_no_empty_table(
    project: Path,
) -> None:
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    save(project, {"source.kind": "local"})

    text = (project / "nrw.toml").read_text(encoding="utf-8")
    assert "# [experiment.source]" in text and "\n[experiment.source]" not in text
    assert init_check(project).exit_code == 0


def test_an_older_nrws_project_takes_an_address_and_init_brings_it_up(
    project: Path,
) -> None:
    """nrw.toml as an nrw before the url keys wrote it: no url placeholders."""
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.project.scaffold import load_lock, sha256_bytes, write_lock

    # As one rendered it: no url placeholders, and no catalog table at all.
    toml = project / "nrw.toml"
    dropped = ("# [experiment.catalog]", '# kind = "parquet"')
    older = (
        "\n".join(
            line
            for line in toml.read_text(encoding="utf-8").splitlines()
            if "url =" not in line and line not in dropped
        ).rstrip("\n")
        + "\n"
    )
    assert "experiment.catalog" not in older
    toml.write_text(older, encoding="utf-8")
    lock = ProjectLayout(root=project).scaffold_lock
    entries = load_lock(lock)
    entries["nrw.toml"]["sha256_at_install"] = sha256_bytes(older.encode())
    entries["nrw.toml"]["template_version"] = "older"
    write_lock(lock, entries)

    result = save(project, {"catalog.url": SHARED})
    upgraded = nrw("init", str(project))

    assert any("older nrw" in note for note in result.notes), result.notes
    assert upgraded.exit_code == 0, upgraded.output
    assert not (project / "nrw.toml.nrw-new").exists()
    assert load_config(project).raw["experiment"]["catalog"] == {"url": SHARED}
    assert init_check(project).exit_code == 0


# --------------------------------------------------------------------------
# Reading them
# --------------------------------------------------------------------------


def test_a_tiled_source_written_by_hand_is_resolved_with_the_ipts(
    project: Path,
) -> None:
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    config = experiment_config_for(project)

    assert config.source.address == DATA.replace("{ipts}", "IPTS-00001")
    assert config.source.path is None
    assert config.problems == ()


@pytest.mark.parametrize(
    ("table", "url", "said"),
    [
        ("source", "/SNS/REF_L/{ipts}/x", "not an https:// address"),
        ("source", "https://elsewhere.example/projects/x", "not on your Tiled"),
        ("source", "https://tiled.ornl.gov/projects/isaac/IPTS-9/x", "names IPTS-9"),
        ("catalog", "https://tiled.ornl.gov/a/../b", "its path"),
        ("catalog", "http://tiled.ornl.gov/a", "unencrypted"),
        ("catalog", "https://tiled.ornl.gov/auto\\treduction", "a tab"),
        ("catalog", "https://tiled.ornl.gov/projects/isaac/context", "experiment"),
    ],
)
def test_an_address_written_by_hand_is_held_to_the_rules(
    project: Path, table: str, url: str, said: str
) -> None:
    """Whoever can commit wrote it: read, it meets every rule a save does."""
    append(project, f'\n[experiment.{table}]\nkind = "tiled"\nurl = "{url}"\n')

    config = experiment_config_for(project)

    assert any(said in p.message for p in config.problems), config.problems
    assert getattr(config, table).address is None


def test_an_address_needs_the_ipts_it_names(project: Path) -> None:
    save(project, {"ipts": ""})
    append(project, '\n[experiment.catalog]\nkind = "tiled"\n')

    config = experiment_config_for(project)

    assert config.catalog.address is None
    # The catalog's own: the folder source's says the same of the folder.
    assert any(
        p.scope == "catalog" and "needs the project's IPTS" in p.message
        for p in config.problems
    ), config.problems


def test_a_folder_written_for_a_source_on_tiled_is_said_to_do_nothing(
    project: Path,
) -> None:
    append(project, '\n[experiment.source]\nkind = "tiled"\nlocation = "/data/x"\n')

    problems = experiment_config_for(project).problems

    assert any(
        'location is read only when kind = "local"' in p.message for p in problems
    ), problems


def test_the_documented_defaults_uncommented_change_nothing(project: Path) -> None:
    """What nrw.toml shows as each default must be its default."""
    toml = project / "nrw.toml"
    block = experiment_block({})
    uncommented = "\n".join(
        line.removeprefix("#").removeprefix(" ") for line in block.splitlines()
    )
    toml.write_text(toml.read_text().replace(block, uncommented), encoding="utf-8")

    assert experiment_config_for(project).problems == ()


def test_an_address_typed_into_a_comment_is_named(project: Path) -> None:
    toml = project / "nrw.toml"
    text = toml.read_text(encoding="utf-8")
    placeholder = f'# url = "{CONTEXT}"'
    assert text.count(placeholder) == 1
    typed = f"https://me:{SECRET}@tiled.ornl.gov/projects/isaac/{{ipts}}/shared"
    toml.write_text(text.replace(placeholder, f'# url = "{typed}"'), encoding="utf-8")

    problems = experiment_config_for(project).problems

    assert any("`url` in a comment" in p.message for p in problems), problems
    assert not any(SECRET in p.message for p in problems)  # named, not repeated


def test_text_from_nrw_toml_reaches_a_message_only_as_text(project: Path) -> None:
    """An escape sequence in a folder or a key could rewrite the terminal."""
    esc = chr(92) + "u001b"  # TOML's own escape for ESC, which the file then holds
    append(
        project,
        f'\n[experiment.source]\nlocation = "relative/{esc}]0;x"\n"{esc}[2J" = 1\n',
    )

    problems = experiment_config_for(project).problems

    assert any("not an absolute path" in p.message for p in problems), problems
    assert any("is not a setting" in p.message for p in problems), problems
    assert not any(chr(0x1B) in p.message for p in problems)


@pytest.mark.parametrize("value", ["5", '""', '["https://tiled.ornl.gov/mine"]'])
def test_an_address_that_is_not_text_reads_nothing(project: Path, value: str) -> None:
    """Not nrw's default in its place: that would read a container nobody chose."""
    append(project, f'\n[experiment.catalog]\nkind = "tiled"\nurl = {value}\n')

    config = experiment_config_for(project)

    assert config.catalog.address is None
    assert any("url must be an address" in p.message for p in config.problems)


def test_a_secret_in_tiled_url_never_reaches_a_page_or_the_terminal(
    project: Path, monkeypatch
) -> None:
    """TILED_URL is from a private file; the pages are open to the machine."""
    from .test_experiment_web import make_app

    own_settings(f"TILED_URL=https://me:{SECRET}@tiled.ornl.gov/?api_key={SECRET}\n")
    uncomment(project, "catalog", '# kind = "parquet"', 'kind = "tiled"')
    app = make_app(project)
    client = app.test_client()
    monkeypatch.chdir(project)

    said = [
        nrw("experiment", "status", "--root", str(project)).output,
        nrw("experiment", "settings", "--root", str(project)).output,
        nrw("doctor").output,
        client.get("/api/experiment").get_data(as_text=True),
        client.get("/api/experiment/settings").get_data(as_text=True),
        client.get("/settings").get_data(as_text=True),
    ]

    assert any("TILED_URL" in text for text in said)
    for text in said:
        assert SECRET not in text


def test_a_secret_written_into_an_address_never_reaches_a_page_or_the_terminal(
    project: Path, monkeypatch
) -> None:
    """In nrw.toml, set or typed into a comment: either is repeated on pages
    the machine can read, and in output that is often logged."""
    from .test_experiment_web import _serve, make_app

    toml = project / "nrw.toml"
    text = toml.read_text(encoding="utf-8")
    placeholder = f'# url = "{DATA}"'
    commented = f'# url = "https://me:{SECRET}@tiled.ornl.gov/a/{{ipts}}"'
    toml.write_text(text.replace(placeholder, commented), encoding="utf-8")
    uncomment(project, "catalog", '# kind = "parquet"', 'kind = "tiled"')
    append_url = f'url = "https://tiled.ornl.gov/projects/{{ipts}}?api_key={SECRET}"'
    text = toml.read_text(encoding="utf-8")
    toml.write_text(
        text.replace('kind = "tiled"', f'kind = "tiled"\n{append_url}', 1),
        encoding="utf-8",
    )
    assert SECRET in toml.read_text(encoding="utf-8")
    app = make_app(project)
    client = app.test_client()

    said = {
        "status": nrw("experiment", "status", "--root", str(project)).output,
        "settings": nrw("experiment", "settings", "--root", str(project)).output,
        "json": nrw("experiment", "settings", "--json", "--root", str(project)).output,
        "serve": _serve(project, monkeypatch=monkeypatch).output,
        "/api/experiment": client.get("/api/experiment").get_data(as_text=True),
        "/api/experiment/settings": client.get("/api/experiment/settings").get_data(
            as_text=True
        ),
    }

    assert "has a query" in said["status"]
    assert "`url` in a comment" in said["status"]
    for where, text in said.items():
        assert SECRET not in text, where


# --------------------------------------------------------------------------
# Shown, and set, from the terminal
# --------------------------------------------------------------------------


def test_the_settings_show_a_tiled_sources_address_not_the_folder(
    project: Path,
) -> None:
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    result = nrw("experiment", "settings", "--root", str(project))

    assert result.exit_code == 0, result.output
    assert f"url        {DATA}  (nrw's default)" in result.output
    assert DATA.replace("{ipts}", "IPTS-00001") in result.output
    assert "/SNS/REF_L/" not in result.output
    assert "Tiled is not available as a source yet" in result.output


def test_an_address_is_set_and_reset_from_the_terminal(project: Path) -> None:
    root = ["--root", str(project)]

    written = nrw("experiment", "settings", "--catalog-url", SHARED, "--write", *root)
    contradicted = nrw(
        "experiment", "settings", "--source-url", DATA, "--default-source-url", *root
    )

    assert written.exit_code == 0, written.output
    assert any("changes nothing" in line for line in written.output.splitlines())
    assert load_config(project).raw["experiment"]["catalog"] == {"url": SHARED}
    assert "contradict" in contradicted.output
    reset = nrw("experiment", "settings", "--default-catalog-url", "--write", *root)
    assert reset.exit_code == 0, reset.output
    assert "catalog" not in load_config(project).raw.get("experiment", {})


@pytest.mark.parametrize("ipts", ["IPTS-00001", ""], ids=["resolved", "no-ipts"])
def test_the_folder_check_names_a_tiled_source_as_not_built_yet(
    project: Path, ipts: str
) -> None:
    """Not "the data location cannot be worked out", nor "set the IPTS": the
    kind is what cannot be used, whether or not its address can be filled in."""
    save(project, {"ipts": ipts})
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    result = nrw("experiment", "settings", "--check", "--root", str(project))

    assert result.exit_code != 0
    assert "planned but not implemented" in result.output


def test_serve_names_a_tiled_sources_address(project: Path, monkeypatch) -> None:
    from .test_experiment_web import _serve

    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')

    result = _serve(project, monkeypatch=monkeypatch)

    expected = DATA.replace("{ipts}", "IPTS-00001")
    assert f"Data on Tiled  {expected}  (nrw's default)" in result.output


# --------------------------------------------------------------------------
# The key, and doctor
# --------------------------------------------------------------------------


def test_the_tiled_key_is_never_printed(project: Path, monkeypatch) -> None:
    from nr_workbench.env import describe

    key = "K" * 32  # the shape of a key, and nothing a scanner takes for one
    monkeypatch.setenv("TILED_API_KEY", key)
    monkeypatch.chdir(project)

    described = describe()
    doctor = nrw("doctor")

    assert "TILED_API_KEY" in described and key not in described["TILED_API_KEY"]
    assert "TILED_API_KEY=set" in doctor.output
    assert key not in doctor.output


def test_doctor_reports_the_tiled_server_nrw_uses_and_where_it_is_set(
    project: Path, monkeypatch
) -> None:
    own_settings("TILED_URL=http://127.0.0.1:8000\n")
    monkeypatch.chdir(project)

    checks = {c["name"]: c for c in json.loads(nrw("doctor", "--json").output)}

    assert checks["tiled"]["status"] == "ok"
    assert checks["tiled"]["detail"].startswith("http://127.0.0.1:8000 (")


def test_doctor_names_a_project_that_sets_tiled_url_and_shows_its_value_nowhere(
    project: Path, monkeypatch
) -> None:
    """doctor loads the project's .env; nrw never takes TILED_URL from it."""
    (project / ".env").write_text("TILED_URL=https://elsewhere.example\n")
    monkeypatch.chdir(project)

    output = nrw("doctor", "--json").output
    checks = {c["name"]: c for c in json.loads(output)}

    assert checks["tiled"]["status"] == "warn"
    assert "This project's .env sets TILED_URL" in checks["tiled"]["detail"]
    assert "elsewhere.example" not in output


def test_reading_the_settings_imports_no_http_client(
    project: Path, tmp_path: Path
) -> None:
    """Nearly every command reads them, and an HTTP client costs ~100 ms."""
    uncomment(project, "source", '# kind = "local"', 'kind = "tiled"')
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        k: v for k, v in os.environ.items() if k not in env_module.KNOWN_VARS
    }
    script = textwrap.dedent(
        f"""
        import sys
        from nr_workbench.experiment.config import experiment_config_for
        from nr_workbench.project.settings import validate

        config = experiment_config_for({str(project)!r})
        assert config.source.address, config.problems
        validate({str(project)!r}, {{"catalog.url": {CONTEXT!r}}})
        leaked = [m for m in ("httpx", "requests", "urllib3") if m in sys.modules]
        print(",".join(leaked))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**environment, "HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
