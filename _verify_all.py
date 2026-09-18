"""Run behavioral regression suites; no source-string checks count as acceptance.

Usage: python -X utf8 _verify_all.py [--browser]
Browser mode requires an isolated QA server and the runtime variables documented
in tests/browser/README.md. Tests isolate opponent data through conftest.py.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    commands = [
        [sys.executable, "-m", "pytest", "tests", "-q"],
        ["node", "--test", *[str(p.relative_to(root)) for p in sorted((root / "tests/frontend").glob("*.test.mjs"))]],
    ]
    if args.browser:
        commands.append(["node", "tests/browser/session.e2e.mjs"])
        commands.append(["node", "tests/browser/acceptance.e2e.mjs"])
    failed = []
    for command in commands:
        print("\nRUN " + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            failed.append(command)
    print("\nBehavioral suites: " + ("FAIL" if failed else "PASS"), flush=True)
    if not args.browser:
        print("Browser suite was not run. This is not a complete UI acceptance result.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

