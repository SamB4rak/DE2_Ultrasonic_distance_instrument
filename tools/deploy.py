"""Copy ``src/`` to the Pico's root file system with mpremote.

Usage::

    python tools/deploy.py             # copy, then print the device's file tree
    python tools/deploy.py --dry-run   # print what would be copied; touch nothing
    python tools/deploy.py --clean     # remove every file on the device first

Each entry of ``src/`` goes to the device root: ``src/main.py`` becomes
``/main.py`` and ``src/lib/`` becomes ``/lib``. (``mpremote cp -r src :``
would create ``/src`` instead.) Folders are copied file by file, so
``__pycache__/``, ``*.mpy`` and ``.gitkeep`` never reach the device.

``src/config.py`` (the Wi-Fi and ThingSpeak secrets) is simply one of those
entries: it is copied if it exists. Otherwise a warning says the cloud logger
won't start. This script only passes the path to mpremote; it never opens or
prints the file.

Without ``--clean`` nothing on the device is ever deleted. Close any other
program that holds the serial port (e.g. MicroPico in VS Code) first.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {".gitkeep"}

# Runs on the Pico: create one folder if it doesn't exist yet.
_MKDIR = "import os\ntry:\n    os.mkdir(%r)\nexcept OSError:\n    pass\n"

# Runs on the Pico: delete every file and folder (for --clean).
_CLEAN = """import os
def _rm(p):
    for e in os.ilistdir(p):
        q = (p.rstrip('/') + '/' + e[0])
        if e[1] == 0x4000:
            _rm(q)
            os.rmdir(q)
        else:
            os.remove(q)
_rm('/')
"""

# Runs on the Pico: print the file tree with sizes.
_TREE = """import os
def _tree(p, d):
    for e in sorted(os.ilistdir(p)):
        q = (p.rstrip('/') + '/' + e[0])
        if e[1] == 0x4000:
            print('  ' * d + e[0] + '/')
            _tree(q, d + 1)
        else:
            print('  ' * d + e[0] + '  ' + str(os.stat(q)[6]) + ' B')
_tree('/', 0)
"""


def _skip_file(name):
    return name in SKIP_FILES or name.endswith(".mpy")


def plan(src=SRC):
    """Work out what to copy.

    Returns:
        tuple: ``(entries, dirs, copies)``: the top-level entries of ``src``
        (sorted), the device folders to create, and ``(local path, device
        path)`` pairs for every file.
    """
    entries = []
    dirs = []
    copies = []
    for name in sorted(os.listdir(src)):
        path = os.path.join(src, name)
        if os.path.isdir(path):
            if name in SKIP_DIRS:
                continue
            entries.append(name + "/")
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                rel_dir = os.path.relpath(dirpath, src).replace(os.sep, "/")
                dirs.append("/" + rel_dir)
                for fname in sorted(filenames):
                    if not _skip_file(fname):
                        copies.append((os.path.join(dirpath, fname), "/" + rel_dir + "/" + fname))
        elif not _skip_file(name):
            entries.append(name)
            copies.append((path, "/" + name))
    return entries, dirs, copies


def _mpremote(*args):
    return [sys.executable, "-m", "mpremote", "connect", "auto"] + list(args)


def command(dirs, copies, clean=False):
    """Build one chained mpremote command line (``a + b + ...``)."""
    steps = []
    if clean:
        steps.append(["exec", _CLEAN])
    for d in dirs:
        steps.append(["exec", _MKDIR % d])
    for local, remote in copies:
        steps.append(["cp", local, ":" + remote])
    args = []
    for step in steps:
        if args:
            args.append("+")
        args.extend(step)
    return _mpremote(*args)


def main(argv=None, src=SRC):
    """Command-line entry point. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Copy src/ to the Pico's root with mpremote.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    parser.add_argument("--clean", action="store_true", help="remove every file on the device first")
    args = parser.parse_args(argv)

    entries, dirs, copies = plan(src)
    has_config = os.path.isfile(os.path.join(src, "config.py"))
    prefix = "would " if args.dry_run else ""

    if args.clean:
        print("%sremove every file on the device" % prefix)
    for name in entries:
        print("%scopy src/%s -> :/%s" % (prefix, name, name))
    for local, remote in copies:
        print("    %s -> :%s" % (os.path.relpath(local, src).replace(os.sep, "/"), remote))
    if not has_config:
        print("WARNING: src/config.py not found: the cloud logger will not start on the device.")
        if args.clean:
            print("WARNING: --clean also removes config.py from the device, and none will be copied back.")
    if args.dry_run:
        return 0

    if subprocess.run(command(dirs, copies, args.clean)).returncode != 0:
        print("deploy failed (is the Pico plugged in and the serial port free?)")
        return 1
    print("device files:")
    return subprocess.run(_mpremote("exec", _TREE)).returncode


if __name__ == "__main__":
    sys.exit(main())
