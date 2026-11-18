"""Check that no Wi-Fi password or ThingSpeak key ends up in git.

Usage::

    python tools/check_secrets.py            # the files git would commit
    python tools/check_secrets.py --history  # also every past commit

It looks for the values from ``src/config.py`` (if the file exists) and for
anything that looks like an API key. It prints only where it found something,
never the secret itself, or ``OK``. The exit code is 1 on any finding, or if
it couldn't read one of the values from ``config.py``.
"""

import argparse
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_NAMES = ("WIFI_SSID", "WIFI_PASSWORD", "THINGSPEAK_WRITE_KEY")
MIN_SECRET_LEN = 6
PATTERNS = (
    ("API_KEY_PARAM", re.compile(r"api_key=[A-Za-z0-9]{16}(?![A-Za-z0-9])", re.IGNORECASE)),
    ("QUOTED_KEY", re.compile(r"""(["'])[A-Z0-9]{16}\1""")),
)


def parse_assignments(source):
    """Return ``{name: value}`` for every secret name assigned a string.

    Plain (``NAME = "..."``) and annotated (``NAME: str = "..."``) assignments
    count.

    Args:
        source (str): the text of a ``config.py``. It is parsed, never run.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in SECRET_NAMES:
                found[target.id] = value.value
    return found


def parse_secret_values(source):
    """Return the secret values worth scanning for in a ``config.py`` text.

    Args:
        source (str): the text of a ``config.py``. It is parsed, never run.

    Returns:
        dict: ``{name: value}`` for values of at least ``MIN_SECRET_LEN``
        characters (shorter ones would match too much harmless text).
    """
    return {k: v for k, v in parse_assignments(source).items() if len(v) >= MIN_SECRET_LEN}


def load_config(repo=ROOT):
    """Parse ``<repo>/src/config.py`` if it exists.

    Returns:
        tuple: ``(secrets, missing)``: the values to scan for, and the secret
        names the file doesn't assign a string to (both empty without a file).
    """
    path = os.path.join(repo, "src", "config.py")
    if not os.path.isfile(path):
        return {}, []
    # utf-8-sig: Windows editors may save the file with a byte-order mark.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        source = f.read()
    assigned = parse_assignments(source)
    missing = [name for name in SECRET_NAMES if name not in assigned]
    return parse_secret_values(source), missing


def load_secrets(repo=ROOT):
    """The secret values from ``<repo>/src/config.py`` (empty if none)."""
    return load_config(repo)[0]


def scan_text(text, secrets):
    """Yield ``(line_number, NAME)`` for every hit in ``text`` (1-based lines)."""
    for number, line in enumerate(text.splitlines(), 1):
        for name, value in secrets.items():
            if value in line:
                yield number, name
        for name, pattern in PATTERNS:
            if pattern.search(line):
                yield number, name


def read_text(path):
    """Return the file's text, or None for a binary or unreadable file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_files(root, relpaths, secrets):
    """Scan files under ``root``; return a list of ``(relpath, line, NAME)``."""
    hits = []
    for rel in relpaths:
        text = read_text(os.path.join(root, rel))
        if text is None:
            continue
        for number, name in scan_text(text, secrets):
            hits.append((rel, number, name))
    return hits


def git_files(repo=ROOT):
    """Every file git would commit: tracked plus untracked, not ignored."""
    out = subprocess.run(
        ["git", "-C", repo, "ls-files", "-co", "--exclude-standard", "-z"],
        check=True, capture_output=True,
    ).stdout
    return sorted({p.decode("utf-8") for p in out.split(b"\0") if p})


def scan_history(secrets, repo=ROOT):
    """Scan every commit's message and added lines.

    Returns:
        list: ``(commit, file, NAME)`` tuples; ``file`` is ``<message>`` for a
        hit in the commit message.
    """
    proc = subprocess.run(
        ["git", "-C", repo, "log", "-p", "--all", "--no-color", "--no-ext-diff"],
        check=True, capture_output=True,
    )
    hits = []
    seen = set()
    commit = path = None

    def record(where, line):
        for _, name in scan_text(line, secrets):
            key = (commit, where, name)
            if key not in seen:
                seen.add(key)
                hits.append(key)

    for raw in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if raw.startswith("commit "):
            commit = raw.split()[1][:12]
            path = None
        elif path is None and commit and raw.startswith("    "):
            record("<message>", raw[4:])      # message lines are indented
        elif raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("+") and commit and path:
            record(path, raw[1:])
    return hits


def main(argv=None):
    """Command-line entry point. Returns the exit code (0 OK, 1 leak)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", action="store_true",
                        help="also scan every commit (git log -p --all)")
    args = parser.parse_args(argv)

    secrets, missing = load_config(ROOT)
    leaks = 0
    for name in missing:
        print("WARN src/config.py: %s not parsed" % name)
        leaks += 1
    for rel, number, name in scan_files(ROOT, git_files(ROOT), secrets):
        print("LEAK %s:%d %s" % (rel, number, name))
        leaks += 1
    if args.history:
        for commit, rel, name in scan_history(secrets, ROOT):
            print("LEAK %s:%s %s" % (commit, rel, name))
            leaks += 1
    if leaks:
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
