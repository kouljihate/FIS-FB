"""Run the whole Playwright suite and write the report under tests/report/.

    python tests/run_tests.py                # everything
    python tests/run_tests.py -k water       # a subset, standard pytest options
    python tests/run_tests.py -x --lf        # stop at the first failure, rerun those

The Flask app is started by the suite itself on a free port, against the
``FIS_playwright`` database, so nothing else needs to be running but MongoDB.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPORT = TESTS / 'report'


def main(argv: list[str]) -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'pytest', str(TESTS), *argv]
    print('running:', ' '.join(command))
    result = subprocess.call(command, cwd=str(TESTS))
    print()
    print('report :', REPORT / 'index.html')
    print('shots  :', REPORT / 'screenshots')
    print('server :', REPORT / 'server.log')
    return result


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
