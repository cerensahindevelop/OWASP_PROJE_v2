import collections
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path('/tmp/owasp-upload-diagnosis')
PYTHON = '/home/aipc/Desktop/OWASP_PROJE/masking_system/masking_service/.venv/bin/python'
CHROME = '/home/aipc/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'
overlay = ROOT / 'streamlit163'
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
        directory = ROOT / 'single'
        directory.mkdir(exist_ok=True)
        (directory / 'probe.txt').write_text('synthetic transport diagnostic\n')
        page.locator('input[type=file]').set_input_files(str(directory))
        for _ in range(100):
            page.wait_for_timeout(100)
            if upload:
                break
        axios = next((overlay / 'streamlit/static/static/js').glob('axios.*.js')).name
        for count, concurrency in [(2000, 2000), (10000, 10000), (10000, 4)]:
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
            results.append(result)
            (ROOT / 'transport-results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(result), flush=True)
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
    log.close()
