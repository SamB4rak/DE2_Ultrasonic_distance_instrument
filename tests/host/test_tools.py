"""Tests for tools/check_secrets.py, tools/deploy.py and tools/check_mpy.py."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools"))
import check_mpy  # noqa: E402
import check_secrets  # noqa: E402
import deploy  # noqa: E402

# Test keys are built at run time, so this file never matches the scan itself.
FAKE_KEY = "Q7" * 8
FAKE_PASSWORD = "not-a-" + "real-password"


def write(root, files):
    for rel, text in files.items():
        path = os.path.join(str(root), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(text, bytes) else "w"
        with open(path, mode) as f:
            f.write(text)


# --- check_secrets -----------------------------------------------------------


def test_parse_secret_values_reads_assignments_without_running_them():
    source = (
        "import os\n"
        "os.remove('x')  # never runs\n"
        "WIFI_SSID = 'my-hotspot'\n"
        "WIFI_PASSWORD = %r\n"
        "THINGSPEAK_WRITE_KEY = %r\n"
        "THINGSPEAK_CHANNEL_ID = '1234567'\n"
        "OTHER = 'ignored-value'\n"
    ) % (FAKE_PASSWORD, FAKE_KEY)
    assert check_secrets.parse_secret_values(source) == {
        "WIFI_SSID": "my-hotspot",
        "WIFI_PASSWORD": FAKE_PASSWORD,
        "THINGSPEAK_WRITE_KEY": FAKE_KEY,
    }


def test_parse_skips_short_and_non_string_values():
    assert check_secrets.parse_secret_values("WIFI_SSID = 'abc'\nWIFI_PASSWORD = 12345678\n") == {}
    assert check_secrets.parse_secret_values("this is not python (") == {}


def test_scan_text_finds_values_and_patterns_by_line():
    text = "ok\nssid my-hotspot here\nurl?api_" + "key=" + FAKE_KEY + "\nK = '" + FAKE_KEY + "'\n"
    hits = list(check_secrets.scan_text(text, {"WIFI_SSID": "my-hotspot"}))
    assert hits == [(2, "WIFI_SSID"), (3, "API_KEY_PARAM"), (4, "QUOTED_KEY")]


@pytest.mark.parametrize("line", [
    "WIFI_PASSWORD = 'test-password'",
    "THINGSPEAK_WRITE_KEY = 'TEST-WRITE-KEY'",
    "x = 'ABCDEFGHIJKLMNO'",                # 15 characters
    "x = 'ABCDEFGHIJKLMNOPQ'",              # 17 characters
    "api_" + "key=SHORT123",
])
def test_scan_text_ignores_harmless_lines(line):
    assert list(check_secrets.scan_text(line, {})) == []


def test_main_prints_only_file_line_and_name(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True)
    write(repo, {
        "docs/a.md": "fine\nsee api_" + "key=" + FAKE_KEY + "\n",
        "img.bin": b"\0\0api_" + b"key=" + FAKE_KEY.encode() + b"\0",
        ".gitignore": "ignored/\n",
        "ignored/b.md": "api_" + "key=" + FAKE_KEY + "\n",
    })
    monkeypatch.setattr(check_secrets, "ROOT", str(repo))
    assert check_secrets.main([]) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == ["LEAK docs/a.md:2 API_KEY_PARAM"]
    assert FAKE_KEY not in out

    write(repo, {"docs/a.md": "fine\n"})
    assert check_secrets.main([]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_history_scan_finds_a_leak_in_an_old_commit(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run(git + ["init", "-q", "--template="], check=True)
    write(repo, {"notes.md": "pw " + FAKE_PASSWORD + "\n"})
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "oops"], check=True)
    write(repo, {"notes.md": "clean now\n"})
    subprocess.run(git + ["commit", "-q", "-am", "fix"], check=True)
    hits = check_secrets.scan_history({"WIFI_PASSWORD": FAKE_PASSWORD}, str(repo))
    assert len(hits) == 1
    commit, path, name = hits[0]
    assert path == "notes.md" and name == "WIFI_PASSWORD" and len(commit) == 12


# --- deploy ------------------------------------------------------------------


@pytest.fixture
def src_tree(tmp_path):
    src = tmp_path / "src"
    write(src, {
        "board.py": "PIN_BUTTON = 17\n",
        "main.py": "run = None\n",
        "old.mpy": b"M",
        "__pycache__/board.cpython-313.pyc": b"\0",
        "lib/.gitkeep": "",
        "lib/sh1106.py": "# driver\n",
        "lib/__pycache__/sh1106.cpython-313.pyc": b"\0",
    })
    return src


def test_deploy_plan_maps_each_entry_to_the_device_root(src_tree):
    entries, dirs, copies = deploy.plan(str(src_tree))
    assert entries == ["board.py", "lib/", "main.py"]
    assert dirs == ["/lib"]
    assert [remote for _, remote in copies] == ["/board.py", "/lib/sh1106.py", "/main.py"]


def test_deploy_dry_run_prints_the_plan_and_touches_nothing(src_tree, monkeypatch, capsys):
    def no_subprocess(*args, **kwargs):
        raise AssertionError("dry run must not start mpremote")

    monkeypatch.setattr(deploy.subprocess, "run", no_subprocess)
    assert deploy.main(["--dry-run"], src=str(src_tree)) == 0
    out = capsys.readouterr().out
    assert "would copy src/board.py -> :/board.py" in out
    assert "would copy src/lib/ -> :/lib/" in out
    assert "would copy src/main.py -> :/main.py" in out
    assert "WARNING: src/config.py not found" in out
    assert "__pycache__" not in out and ".mpy" not in out and ".gitkeep" not in out


def test_deploy_runs_one_chained_mpremote_command(src_tree, monkeypatch):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(deploy.subprocess, "run", lambda cmd: calls.append(cmd) or Done())
    assert deploy.main([], src=str(src_tree)) == 0
    copy_cmd, tree_cmd = calls
    assert copy_cmd[:5] == [sys.executable, "-m", "mpremote", "connect", "auto"]
    cps = [copy_cmd[i + 1:i + 3] for i, a in enumerate(copy_cmd) if a == "cp"]
    assert [dst for _, dst in cps] == [":/board.py", ":/lib/sh1106.py", ":/main.py"]
    assert deploy._CLEAN not in copy_cmd          # never deletes without --clean
    assert tree_cmd[-1] == deploy._TREE


def test_deploy_clean_removes_first(src_tree, monkeypatch):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(deploy.subprocess, "run", lambda cmd: calls.append(cmd) or Done())
    assert deploy.main(["--clean"], src=str(src_tree)) == 0
    assert calls[0][5:7] == ["exec", deploy._CLEAN]


def test_deploy_reports_a_failed_copy(src_tree, monkeypatch, capsys):
    class Failed:
        returncode = 1

    monkeypatch.setattr(deploy.subprocess, "run", lambda cmd: Failed())
    assert deploy.main([], src=str(src_tree)) == 1
    assert "deploy failed" in capsys.readouterr().out


# --- check_mpy ---------------------------------------------------------------


def test_check_mpy_passes_good_and_fails_bad_files(tmp_path, capsys):
    src = tmp_path / "src"
    write(src, {"good.py": "x = 1\n", "lib/bad.py": "def f(:\n    pass\n"})
    assert check_mpy.source_files(str(src)) == ["good.py", os.path.join("lib", "bad.py")]
    assert check_mpy.main(str(src)) == 1
    out = capsys.readouterr().out
    assert "ok   good.py" in out
    assert "FAIL " + os.path.join("lib", "bad.py") in out
    assert "2 file(s), 1 failed" in out


# --- review additions --------------------------------------------------------


def test_parse_handles_a_bom_and_annotated_assignments():
    source = "﻿WIFI_SSID: str = 'my-hotspot'\nWIFI_PASSWORD = %r\n" % FAKE_PASSWORD
    # load_config reads with utf-8-sig, which strips the BOM; this is the text it passes on
    text = source.encode("utf-8").decode("utf-8-sig")
    assert check_secrets.parse_secret_values(text) == {
        "WIFI_SSID": "my-hotspot", "WIFI_PASSWORD": FAKE_PASSWORD}


def test_parse_assignments_keeps_short_values_so_they_are_not_missing():
    assigned = check_secrets.parse_assignments("WIFI_SSID = 'abc'\n")
    assert assigned == {"WIFI_SSID": "abc"}
    assert check_secrets.parse_secret_values("WIFI_SSID = 'abc'\n") == {}


def _fake_repo(tmp_path, monkeypatch, files):
    """A temporary git repo with ``files``; check_secrets points at it."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--template=", str(repo)], check=True)
    write(repo, files)
    monkeypatch.setattr(check_secrets, "ROOT", str(repo))
    return repo


def test_main_warns_about_unparsed_names_and_uses_the_config_values(tmp_path, monkeypatch, capsys):
    _fake_repo(tmp_path, monkeypatch, {"notes.md": "pw " + FAKE_PASSWORD + "\n"})
    # stands in for a config.py that assigns only the password (none is created)
    monkeypatch.setattr(check_secrets, "load_config",
                        lambda repo: ({"WIFI_PASSWORD": FAKE_PASSWORD},
                                      ["WIFI_SSID", "THINGSPEAK_WRITE_KEY"]))
    assert check_secrets.main([]) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "WARN src/config.py: WIFI_SSID not parsed",
        "WARN src/config.py: THINGSPEAK_WRITE_KEY not parsed",
        "LEAK notes.md:1 WIFI_PASSWORD",
    ]
    assert FAKE_PASSWORD not in out


def test_main_history_reports_file_and_message_leaks(tmp_path, monkeypatch, capsys):
    repo = _fake_repo(tmp_path, monkeypatch, {"notes.md": "pw " + FAKE_PASSWORD + "\n"})
    git = ["git", "-C", str(repo)]
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "oops " + FAKE_PASSWORD], check=True)
    write(repo, {"notes.md": "clean now\n"})
    subprocess.run(git + ["commit", "-q", "-am", "fix"], check=True)
    monkeypatch.setattr(check_secrets, "load_config",
                        lambda repo: ({"WIFI_PASSWORD": FAKE_PASSWORD}, []))
    assert check_secrets.main(["--history"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("LEAK ") and lines[0].endswith(":<message> WIFI_PASSWORD")
    assert lines[1].startswith("LEAK ") and lines[1].endswith(":notes.md WIFI_PASSWORD")
    assert all(FAKE_PASSWORD not in line for line in lines)


def test_deploy_copies_config_when_present(src_tree, monkeypatch, capsys):
    # A config.py is never created by a test: pretend one is listed in src/.
    real_listdir = os.listdir
    real_isfile = os.path.isfile
    src = str(src_tree)
    monkeypatch.setattr(deploy.os, "listdir",
                        lambda p: real_listdir(p) + (["config.py"] if p == src else []))
    monkeypatch.setattr(deploy.os.path, "isfile",
                        lambda p: p == os.path.join(src, "config.py") or real_isfile(p))
    assert deploy.main(["--dry-run"], src=src) == 0
    out = capsys.readouterr().out
    assert "would copy src/config.py -> :/config.py" in out
    assert "WARNING" not in out


def test_deploy_clean_without_config_warns(src_tree, capsys):
    assert deploy.main(["--dry-run", "--clean"], src=str(src_tree)) == 0
    out = capsys.readouterr().out
    assert "would remove every file on the device" in out
    assert "--clean also removes config.py from the device" in out
