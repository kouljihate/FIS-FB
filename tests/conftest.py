"""Fixtures for the Playwright end-to-end suite.

The suite runs against a dedicated database (``FIS_playwright``) and a private
uploads folder, so it never touches the data or the files of a real install.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import seed
from support import REPORT_DIR, ROOT, SHOTS_DIR, TESTS, Ui

DEFAULT_PORT = 7099


def _free_port(preferred: int = DEFAULT_PORT) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError('no free TCP port for the test server')


def _wait_for_server(url: str, process: subprocess.Popen, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'test server exited with code {process.returncode}; '
                               f'see {REPORT_DIR / "server.log"}')
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f'test server did not answer {url}: {last_error}')


@pytest.fixture(scope='session')
def settings():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Stale screenshots from an earlier run would look like part of this one.
    for old in SHOTS_DIR.glob('*.jpg'):
        old.unlink()
    for old in SHOTS_DIR.glob('*.png'):
        old.unlink()
    port = _free_port()
    return {
        'port': port,
        'base_url': f'http://127.0.0.1:{port}',
        'db_name': seed.DB_NAME,
        'uploads': REPORT_DIR / 'uploads',
    }


@pytest.fixture(scope='session')
def server(settings):
    """Live Flask app on the test database, started once for the whole run."""
    try:
        seed.reset_database()
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.exit(f'MongoDB is not reachable, the suite needs it: {exc}')

    uploads = Path(settings['uploads'])
    if uploads.exists():
        shutil.rmtree(uploads, ignore_errors=True)
    uploads.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({
        'MONGO_URI': f'mongodb://127.0.0.1:27017/{settings["db_name"]}',
        'FIS_TEST_UPLOADS': str(uploads),
        'SECRET_KEY': 'playwright-test-secret',
        'PYTHONIOENCODING': 'utf-8',
        # Never send a real message from a test run: an empty value wins over .env.
        'SMTP_HOST': '',
        'BLUETICKS_API_KEY': '',
        'WHATSAPP_TIMEOUT': '1',
    })

    log_path = REPORT_DIR / 'server.log'
    log = open(log_path, 'w', encoding='utf-8')
    process = subprocess.Popen(
        [sys.executable, str(TESTS / 'launch_app.py'), '--port', str(settings['port'])],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_server(settings['base_url'] + '/login', process)
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        log.close()


@pytest.fixture(scope='session')
def dataset(server, settings):
    """Three Project Admins, three owners each, one project per owner."""
    data = seed.build_dataset(settings['base_url'])
    (REPORT_DIR / 'dataset.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


@pytest.fixture(scope='session')
def browser():
    with sync_playwright() as playwright:
        try:
            # The suite drives the installed Chrome; the bundled Chromium is the
            # fallback when Chrome is not installed.
            launched = playwright.chromium.launch(channel='chrome', headless=True)
        except Exception:
            launched = playwright.chromium.launch(headless=True)
        yield launched
        launched.close()


@pytest.fixture()
def ui(browser, dataset, request):
    """Logged-in (Admin) page for one test, with screenshots and console capture."""
    context = browser.new_context(viewport={'width': 1440, 'height': 900},
                                  accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(20_000)

    helper = Ui(page, dataset['base_url'], prefix=request.node.name)
    request.node._ui = helper

    page.on('dialog', lambda dialog: dialog.accept())
    page.on('console', lambda message: helper.console_errors.append(message.text)
            if message.type == 'error' else None)
    page.on('pageerror', lambda error: helper.page_errors.append(str(error)))

    base = dataset['base_url']
    page.goto(base + '/login', wait_until='commit')
    page.wait_for_selector('input[name="username"]', timeout=30_000)
    page.fill('input[name="username"]', dataset['admin']['username'])
    page.fill('input[name="password"]', dataset['admin']['password'])
    page.click('form button[type="submit"]', no_wait_after=True)
    page.wait_for_url(base.rstrip('/') + '/', timeout=60_000)
    page.wait_for_selector('body', state='attached')
    assert 'Invalid credentials' not in page.content(), 'Admin login failed'

    yield helper

    request.node._pw_shots = list(helper.shots)
    request.node._pw_console = helper.console_errors + helper.page_errors
    context.close()


def pytest_configure(config):
    config.addinivalue_line('markers', 'end_to_end: full application flow through the UI')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach the screenshots and the browser console to the HTML report."""
    outcome = yield
    report = outcome.get_result()

    if report.when == 'call' and report.failed:
        ui = getattr(item, '_ui', None)
        if ui is not None:
            try:
                ui.shot('failure')
            except Exception:  # pragma: no cover - browser already gone
                pass

    _attach_to_report(report, item)


def _attach_to_report(report, node) -> None:
    import base64

    from pytest_html import extras

    extra = list(getattr(report, 'extras', []))
    for path in getattr(node, '_pw_shots', []) or []:
        path = Path(path)
        if path.exists():
            # pytest-html inlines base64 content; a plain path would only be a
            # link that breaks as soon as the report is moved.
            encoded = base64.b64encode(path.read_bytes()).decode('ascii')
            mime = 'image/jpeg' if path.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
            extra.append(extras.image(encoded, name=path.stem, mime_type=mime,
                                      extension=path.suffix.lstrip('.').lower()))
    console = getattr(node, '_pw_console', None)
    if console:
        extra.append(extras.text('\n'.join(console[-40:]), name='browser console'))
    report.extras = extra
