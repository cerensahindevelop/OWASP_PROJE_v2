import collections
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path('/tmp/owasp-upload-queue-check')
PYTHON = '/home/aipc/Desktop/OWASP_PROJE/masking_system/masking_service/.venv/bin/python'
CHROME = '/home/aipc/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'
overlay = Path('/tmp/owasp-upload-diagnosis/streamlit163')
env = dict(os.environ, PYTHONPATH=str(overlay))
log = open(ROOT / 'streamlit163.log', 'w')
server = subprocess.Popen([PYTHON, '-m', 'streamlit', 'run', str(ROOT / 'app.py'), '--global.developmentMode=false', '--server.address=127.0.0.1', '--server.port=18762', '--server.headless=true', '--browser.gatherUsageStats=false'], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
results = []
try:
    for _ in range(100):
        try:
            urllib.request.urlopen('http://127.0.0.1:18762/_stcore/health', timeout=1)
            break
        except Exception:
            if server.poll() is not None:
                raise RuntimeError((ROOT / 'streamlit163.log').read_text())
            time.sleep(.2)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        upload = {}
        failures = collections.Counter()
        def capture(req):
            if '/_stcore/upload_file/' in req.url and req.method == 'PUT':
                upload.update(url=req.url, headers=req.headers)
        page.on('request', capture)
        page.on('requestfailed', lambda req: failures.update([req.failure]) if '/_stcore/upload_file/' in req.url else None)
        page.goto('http://127.0.0.1:18762')
        page.wait_for_function('window.__maskingUploadQueue && window.__maskingUploadQueue.limit === 4')
        directory = ROOT / 'single'
        directory.mkdir(exist_ok=True)
        (directory / 'probe.txt').write_text('synthetic transport diagnostic\n')
        page.locator('input[type=file]').set_input_files(str(directory))
        for _ in range(100):
            page.wait_for_timeout(100)
            if upload:
                break
        axios = next((overlay / 'streamlit/static/static/js').glob('axios.*.js')).name
        for count, concurrency in [(10000, 10000)]:
            failures.clear()
            args = {'count': count, 'concurrency': concurrency, 'base': upload['url'].rsplit('/',1)[0], 'xsrf': upload['headers'].get('x-xsrftoken'), 'axios': '/static/js/' + axios}
            print(f'Start count={count} concurrency={concurrency}', flush=True)
            result = page.evaluate('''async ({count, concurrency, base, xsrf, axios: modulePath}) => {
                const {default: axios} = await import(modulePath);
                let next = 0, active = 0, peak = 0;
                const statuses = {}, errors = {};
                const start = performance.now();
                async function worker() {
                    while (next < count) {
                        const i = next++;
                        const body = new FormData();
                        body.append('file.txt', new File(['synthetic diagnostic content\\n'.repeat(16)], 'file.txt'), 'synthetic/file-' + i + '.txt');
                        active++; peak = Math.max(peak, active);
                        try {
                            const r = await axios.request({url: base + '/' + crypto.randomUUID(), method:'PUT', data:body, responseType:'text', withCredentials:true, headers:xsrf ? {'X-Xsrftoken':xsrf} : {}});
                            statuses[r.status] = (statuses[r.status] || 0) + 1;
                        } catch(e) {
                            const key = e.code + ': ' + e.message;
                            errors[key] = (errors[key] || 0) + 1;
                        } finally { active--; }
                    }
                }
                await Promise.all(Array.from({length:concurrency}, worker));
                return {count, concurrency, peak, statuses, errors, elapsed: (performance.now()-start)/1000};
            }''', args)
            result['browser_errors'] = dict(failures)
            result['queue'] = page.evaluate('({...window.__maskingUploadQueue})')
            results.append(result)
            (ROOT / 'transport-results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(result), flush=True)
        page.close()
        page = browser.new_page()
        failures.clear()
        received = {'count': 0}
        def count_response(response):
            if '/_stcore/upload_file/' in response.url and response.status == 204:
                received['count'] += 1
                if received['count'] % 500 == 0:
                    print('Widget successful uploads: ' + str(received['count']), flush=True)
        page.on('response', count_response)
        page.on('requestfailed', lambda req: failures.update([req.failure]) if '/_stcore/upload_file/' in req.url else None)
        page.goto('http://127.0.0.1:18762')
        page.wait_for_function('window.__maskingUploadQueue && window.__maskingUploadQueue.limit === 4')
        directory = ROOT / 'folder-5000'
        directory.mkdir(exist_ok=True)
        for i in range(5000):
            (directory / f'file-{i:05d}.txt').write_text('synthetic upload queue check\n')
        started = time.monotonic()
        page.locator('input[type=file]').set_input_files(str(directory), timeout=180000)
        deadline = time.monotonic() + 240
        while received['count'] < 5000 and time.monotonic() < deadline:
            page.wait_for_timeout(500)
        summary = {'widget_files': 5000, 'http_204': received['count'], 'browser_errors': dict(failures), 'elapsed': round(time.monotonic()-started,2)}
        (ROOT / 'widget-results.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
        assert received['count'] == 5000
        page.wait_for_function('window.__maskingUploadQueue.active === 0 && window.__maskingUploadQueue.queued === 0', timeout=60000)
        summary['queue'] = page.evaluate('({...window.__maskingUploadQueue})')
        page.get_by_role('button', name='Start', exact=True).click(timeout=60000)
        page.get_by_text('RECEIVED: 5000', exact=True).wait_for(timeout=60000)
        summary['received_by_python'] = 5000
        summary['queue_after_rerun'] = page.evaluate('({...window.__maskingUploadQueue})')
        (ROOT / 'widget-results.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
    log.close()
