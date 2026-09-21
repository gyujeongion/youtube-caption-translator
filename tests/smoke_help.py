"""CI smoke test: every script must answer --help (exit 0, no traceback) in the CURRENT environment.

Not collected by `unittest discover` (the file name does not start with test_). Run it directly:

    python tests/smoke_help.py

On Windows the workflow runs it once more with PYTHONIOENCODING=cp1252 and PYTHONUTF8=0 so a script
that prints non-ASCII text without going through _common.setup_utf8_io() fails here first.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
# (script, extra args) -- sub-commands have their own parsers
CASES = [(p.name, []) for p in sorted(SCRIPTS.glob("*.py")) if p.name != "_common.py"]
CASES += [("extract_thumbnails.py", ["grab"]), ("extract_thumbnails.py", ["gallery"])]


def main() -> int:
    env = dict(os.environ)
    print(f"python {sys.version.split()[0]} | PYTHONIOENCODING={env.get('PYTHONIOENCODING', '')!r} "
          f"PYTHONUTF8={env.get('PYTHONUTF8', '')!r} | utf8_mode={sys.flags.utf8_mode}", flush=True)
    failed = 0
    for name, extra in CASES:
        r = subprocess.run([sys.executable, str(SCRIPTS / name), *extra, "--help"],
                           capture_output=True, env=env)
        err = r.stderr.decode("utf-8", "replace")
        ok = r.returncode == 0 and b"Traceback" not in r.stderr and bool(r.stdout.strip())
        label = " ".join([name, *extra])
        print(f"{'ok  ' if ok else 'FAIL'} {label} --help", flush=True)
        if not ok:
            failed += 1
            print(f"     exit={r.returncode} stderr tail: {err[-400:]!r}", flush=True)
    print(f"{len(CASES) - failed}/{len(CASES)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
