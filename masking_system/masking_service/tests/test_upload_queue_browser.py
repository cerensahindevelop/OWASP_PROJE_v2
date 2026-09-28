"""Browser regressions for Streamlit's scoped XHR upload queue.

Optional development dependency: playwright and its Chromium browser. Neither is
needed in the offline production package. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE
when using an existing browser installation.
"""
import os
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
SCRIPT = (Path(__file__).parents[1] / 'app/webapp/assets/upload_queue.js').read_text()


@pytest.fixture
def browser_page():
    with playwright.sync_playwright() as p:
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        if not executable and not Path(p.chromium.executable_path).exists():
            pytest.skip('Playwright Chromium is not installed')
        browser = p.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page()
        held = []
        def route_handler(route):
            if '/_stcore/upload_file/' in route.request.url:
                held.append(route)
            else:
                route.fulfill(status=200, body='<html></html>', content_type='text/html')
        page.route('**/*', route_handler)
        page.goto('http://upload-queue.test/')
        page.add_script_tag(content=SCRIPT)
        yield page, held
        browser.close()


def settle(page, predicate, timeout=5000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate():
        assert time.monotonic() < deadline, 'Browser queue did not settle'
        page.wait_for_timeout(20)


def test_scoped_queue_cancellation_failure_and_rerun(browser_page):
    page, held = browser_page
    page.evaluate('''() => {
        window.items = [];
        window.finished = [];
        for (let i=0; i<10; i++) {
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', '/app/_stcore/upload_file/session/' + i);
            xhr.onloadend = () => finished.push(i);
            xhr.send(new Blob(['synthetic']));
            items.push(xhr);
        }
        window.sendBeforeRerun = XMLHttpRequest.prototype.send;
    }''')
    settle(page, lambda: len(held) == 4)
    assert page.evaluate('({...window.__maskingUploadQueue})') == {'version':1,'limit':4,'active':4,'queued':6,'peak':4}
    page.add_script_tag(content=SCRIPT)
    assert page.evaluate('sendBeforeRerun === XMLHttpRequest.prototype.send')
    # Other requests must remain live even when all upload slots are occupied.
    assert page.evaluate('''async () => await new Promise(resolve => {
        const x = new XMLHttpRequest(); x.open('GET', '/health');
        x.onload = () => resolve(x.status); x.send();
    })''') == 200
    page.evaluate('items[9].abort(); items[0].abort()')
    settle(page, lambda: len(held) == 5)
    assert page.evaluate('window.__maskingUploadQueue.queued') == 4
    # One active upload fails at the transport layer; queued uploads still proceed.
    held[1].abort('connectionreset')
    settle(page, lambda: len(held) == 6)
    handled = 2
    deadline = time.monotonic() + 5
    while len(held) < 9 or handled < len(held):
        assert time.monotonic() < deadline, 'Queue stopped after a failed upload'
        if handled < len(held):
            held[handled].fulfill(status=503 if handled == 2 else 204)
            handled += 1
        page.wait_for_timeout(20)
    page.wait_for_function('window.__maskingUploadQueue.active === 0')
    assert page.evaluate('window.__maskingUploadQueue.queued') == 0
    assert page.evaluate('window.__maskingUploadQueue.peak') == 4
    assert all(not route.request.url.endswith('/9') for route in held)
    assert page.evaluate('items[2].status') == 503  # HTTP errors must not become success.


def test_queued_send_exception_releases_slot(browser_page):
    page, held = browser_page
    # A separate realm lets us simulate a native send exception before installation.
    page.reload()
    page.evaluate('''() => {
        const send = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(body) {
            if (body === 'throw-sync') throw new Error('simulated native send failure');
            return send.call(this, body);
        };
    }''')
    page.add_script_tag(content=SCRIPT)
    page.evaluate('''() => {
        window.failed = 0;
        for (let i=0; i<6; i++) {
            const x = new XMLHttpRequest(); x.open('PUT', '/_stcore/upload_file/session/' + i);
            x.onerror = () => failed++;
            x.send(i === 4 ? 'throw-sync' : 'synthetic');
        }
    }''')
    settle(page, lambda: len(held) == 4)
    held[0].fulfill(status=204)
    settle(page, lambda: len(held) == 5)
    for route in held[1:]:
        route.fulfill(status=204)
    page.wait_for_function('window.__maskingUploadQueue.active === 0')
    assert page.evaluate('failed') == 1
    assert page.evaluate('window.__maskingUploadQueue.queued') == 0
