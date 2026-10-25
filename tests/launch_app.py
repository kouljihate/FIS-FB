"""Run the irrigation planner for the Playwright suite.

Only two things differ from ``app.py``: the upload folder is redirected to the
test report directory (so a test upload can never overwrite a real project's
file) and the reloader/debugger are off so the server behaves deterministically
under automation.
"""

import argparse
import os
import sys
from pathlib import Path

# pytest runs this file as a script, so the project root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from irrigation import create_app

app = create_app()

uploads = os.environ.get('FIS_TEST_UPLOADS')
if uploads:
    os.makedirs(uploads, exist_ok=True)
    app.config['UPLOAD_FOLDER'] = uploads

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the app for Playwright tests')
    parser.add_argument('--port', type=int, default=7099)
    args = parser.parse_args()
    app.run(host='127.0.0.1', port=args.port, debug=False,
            use_reloader=False, threaded=True)
