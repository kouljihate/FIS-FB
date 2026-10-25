"""Helpers shared by the Playwright end-to-end tests."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
REPORT_DIR = TESTS / 'report'
SHOTS_DIR = REPORT_DIR / 'screenshots'
MAPS_DIR = ROOT / 'maps'


def _slug(text: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', text).strip('-')


class Ui:
    """Thin wrapper around a Playwright page with app-specific helpers."""

    def __init__(self, page, base_url: str, prefix: str = 'run'):
        self.page = page
        self.base = base_url.rstrip('/')
        self.prefix = _slug(prefix)
        self.shots: list[Path] = []
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []

    # -- navigation -----------------------------------------------------
    def _settle(self) -> None:
        """Wait for the document, but never for a slow CDN script."""
        try:
            self.page.wait_for_load_state('domcontentloaded', timeout=15_000)
        except Exception:
            pass
        self.page.wait_for_selector('body', state='attached', timeout=20_000)

    def goto(self, path: str, status: int = 200):
        url = self.base + (path if path.startswith('/') else '/' + path)
        response = self.page.goto(url, wait_until='commit', timeout=90_000)
        actual = response.status if response else 'no response'
        assert response is not None, f'{path} returned no response'
        assert response.status == status, f'{path} -> HTTP {actual} (expected {status})'
        self._settle()
        return response

    # -- screenshots ----------------------------------------------------
    def shot(self, label: str) -> Path:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self._reveal_toasts()
        except Exception:
            pass
        target = SHOTS_DIR / f'{self.prefix}-{_slug(label)}.jpg'
        quality = 75
        try:
            self.page.screenshot(path=str(target), full_page=True, timeout=30_000,
                                 type='jpeg', quality=quality)
        except PlaywrightTimeoutError:
            # A page carrying thousands of tree markers can be slow to render
            # fully; the viewport is enough evidence for the report.
            self.page.screenshot(path=str(target), full_page=False, timeout=30_000,
                                 type='jpeg', quality=quality)
        self.shots.append(target)
        return target

    # -- flashes --------------------------------------------------------
    # Flash messages are Bootstrap toasts (bottom right), shown by the CDN
    # bundle. Their text is readable whether the bundle loaded or not.
    def flash(self, kind: str | None = None):
        selector = '.flash-toast' + (f'.text-bg-{kind}' if kind else '')
        return self.page.locator(selector).first

    def _reveal_toasts(self) -> None:
        self.page.evaluate(
            """() => document.querySelectorAll('.flash-toast').forEach((toast) => {
                toast.classList.add('show');
                toast.style.display = 'block';
            })""")

    def expect_flash(self, pattern: str, kind: str | None = None, timeout: int = 8_000) -> str:
        locator = self.flash(kind)
        locator.wait_for(state='attached', timeout=timeout)
        text = locator.inner_text().strip()
        assert re.search(pattern, text, re.IGNORECASE), (
            f'flash did not match {pattern!r}: {text!r}')
        return text

    def assert_no_danger(self, context: str = '') -> None:
        danger = self.page.locator('.flash-toast.text-bg-danger')
        if danger.count():
            raise AssertionError(f'unexpected error toast{(" on " + context) if context else ""}: '
                                 f'{danger.first.inner_text()!r}')

    def assert_no_server_error(self, context: str = '') -> None:
        text = self.page.content()
        assert 'Traceback (most recent call last)' not in text, f'python traceback{(" on " + context) if context else ""}'
        assert 'Internal Server Error' not in text, f'500 page{(" on " + context) if context else ""}'

    # -- forms ----------------------------------------------------------
    def _fill(self, scope, data: dict) -> None:
        for name, value in (data or {}).items():
            field = scope.locator(f'[name="{name}"]').first
            if field.count() == 0:
                raise AssertionError(f'no field named {name!r} in this form')
            kind = field.evaluate('el => el.type || ""')
            if kind == 'hidden':
                continue
            if field.evaluate('el => el.tagName.toLowerCase()') == 'select':
                field.select_option(str(value))
            elif kind == 'checkbox':
                field.set_checked(bool(value))
            elif kind == 'radio':
                scope.locator(f'[name="{name}"][value="{value}"]').first.check()
            else:
                field.fill(str(value))

    def _accept_confirmation(self, timeout: int = 5_000) -> bool:
        """A form with data-confirm waits for the in-page Continue button."""
        dialog = self.page.locator('#confirmActionModal')
        accept = self.page.locator('#confirmActionAccept')
        try:
            accept.wait_for(state='visible', timeout=timeout)
        except Exception:
            return False
        assert dialog.count() == 1
        accept.click()
        return True

    def _submit_click(self, button) -> None:
        """Click a submit button, confirm if asked, and stop at navigation commit.

        Waiting only for the commit keeps a slow CDN script from turning a
        successful submission into a timeout.
        """
        try:
            with self.page.expect_navigation(wait_until='commit', timeout=120_000):
                button.click(no_wait_after=True)
                self._accept_confirmation()
        except PlaywrightTimeoutError:
            pass
        self._settle()

    def submit_modal(self, modal_id: str, data: dict | None = None) -> None:
        modal = self.page.locator(f'#{modal_id}')
        if not modal.is_visible():
            # Click the circle button that opens this modal, like a user would.
            trigger = self.page.locator(f'[data-bs-target="#{modal_id}"]').first
            if trigger.count():
                trigger.click()
                try:
                    modal.wait_for(state='visible', timeout=3_000)
                except Exception:
                    pass
        if not modal.is_visible():
            # Bootstrap/Leaflet come from a CDN. When the network is offline the
            # modal never opens, so make the form visible and submit it anyway:
            # the button under test is still exercised.
            self.page.evaluate(
                """(id) => {
                    const m = document.getElementById(id);
                    if (!m) return;
                    m.classList.add('show');
                    m.style.display = 'block';
                    m.removeAttribute('aria-hidden');
                    m.setAttribute('aria-modal', 'true');
                }""", modal_id)
        modal.wait_for(state='visible', timeout=8_000)
        form = modal.locator('form').first
        self._fill(form, data or {})
        submit = form.locator('button[type=submit], input[type=submit]').first
        assert submit.count(), f'#{modal_id} has no submit button'
        self._submit_click(submit)

    def click(self, selector: str) -> None:
        """Click a button that submits a form built in JavaScript."""
        button = self.page.locator(selector).first
        button.wait_for(state='visible', timeout=10_000)
        try:
            with self.page.expect_navigation(wait_until='commit', timeout=120_000):
                button.click(no_wait_after=True)
                self._accept_confirmation()
        except PlaywrightTimeoutError:
            pass
        self._settle()

    def submit_form(self, action_contains: str, data: dict | None = None):
        form = self.page.locator(f'form[action*="{action_contains}"]').first
        assert form.count(), f'no form whose action contains {action_contains!r}'
        self._fill(form, data or {})
        submit = form.locator('button[type=submit], input[type=submit]').first
        assert submit.count(), f'form {action_contains!r} has no submit button'
        self._submit_click(submit)
        return form

    # -- page queries ---------------------------------------------------
    def body_text(self) -> str:
        return self.page.locator('body').inner_text()
