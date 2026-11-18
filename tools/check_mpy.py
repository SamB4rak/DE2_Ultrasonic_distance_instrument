"""Check that every file in ``src/`` compiles with MicroPython's compiler.

Usage::

    python tools/check_mpy.py

This catches code that works on a PC but not on the Pico. The compiled output
is thrown away; the exit code is 1 if any file fails.
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def source_files(src=SRC):
    """Return the relative paths of every ``.py`` under ``src`` (sorted)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(os.path.relpath(os.path.join(dirpath, name), src))
    return found


def compile_one(path, out_dir):
    """Run mpy-cross on ``path``; return ``(ok, message)``."""
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".mpy")
    proc = subprocess.run(
        [sys.executable, "-m", "mpy_cross", "-o", out, path],
        capture_output=True, text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def main(src=SRC):
    """Compile every source file. Returns the exit code."""
    files = source_files(src)
    failed = 0
    with tempfile.TemporaryDirectory() as out_dir:
        for rel in files:
            ok, message = compile_one(os.path.join(src, rel), out_dir)
            if ok:
                print("ok   %s" % rel)
            else:
                failed += 1
                print("FAIL %s\n%s" % (rel, message))
    print("%d file(s), %d failed" % (len(files), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
