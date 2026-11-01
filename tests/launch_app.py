"""Run the irrigation planner for the Playwright suite.

The application is started exactly as ``app.py`` starts it - same configuration,
same ``MONGO_URI``, same uploads folder - so the accounts, projects and files the
tests create are visible in the installed app. Only the reloader and the
debugger are off, so the server behaves deterministically under automation.
"""

import argparse
import sys
from pathlib import Path

# pytest runs this file as a script, so the project root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from irrigation import create_app

app = create_app()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the app for Playwright tests')
    parser.add_argument('--port', type=int, default=7099)
    args = parser.parse_args()
    app.run(host='127.0.0.1', port=args.port, debug=False,
            use_reloader=False, threaded=True)
