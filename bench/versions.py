"""Installed vs latest versions of the workbench's components. Read-only: it never upgrades.

  python bench/versions.py           # one line per component
  python bench/versions.py --json    # for the health report
  python bench/versions.py --fresh   # ignore the one-day cache

Latest versions come from the public release feeds (npm for n8n, GitHub releases for Ollama
and Prompt Optimizer, PyPI for MarkItDown), each with a 5-second timeout. Results are cached
for a day in harness/data/versions.json; offline, the check is skipped quietly. Upgrades go
through the gated procedure in README.md, never from here.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'harness' / 'data' / 'versions.json'
OPTIMIZER_IMAGE = 'linshen/prompt-optimizer'


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'prompt-workbench-version-check',
                                               'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return ''


def latest():
    out = {}
    feeds = {
        'n8n': lambda: fetch('https://registry.npmjs.org/n8n/latest')['version'],
        'ollama': lambda: fetch('https://api.github.com/repos/ollama/ollama/releases/latest')['tag_name'].lstrip('v'),
        'markitdown': lambda: fetch('https://pypi.org/pypi/markitdown/json')['info']['version'],
        'prompt-optimizer': lambda: (lambda r: '%s (%s)' % (r['tag_name'].lstrip('v'), r['published_at'][:10]))(
            fetch('https://api.github.com/repos/linshenkx/prompt-optimizer/releases/latest')),
    }
    for name, get in feeds.items():
        try:
            out[name] = get()
        except Exception:
            out[name] = None
    return out


def installed():
    out = {'n8n': run(['docker', 'exec', 'n8n', 'n8n', '--version']) or None}
    try:
        out['ollama'] = fetch('http://127.0.0.1:11434/api/version')['version']
    except Exception:
        out['ollama'] = None
    pip = run(['docker', 'exec', 'prompt-workbench', 'pip', 'show', 'markitdown'])
    m = re.search(r'^Version:\s*(\S+)', pip, re.M)
    out['markitdown'] = m.group(1) if m else None
    image = run(['docker', 'inspect', '-f', '{{.Config.Image}}', 'prompt-optimizer'])
    created = run(['docker', 'image', 'inspect', '-f', '{{.Created}}', image]) if image else ''
    out['prompt-optimizer'] = ('image of %s' % created[:10]) if created else None
    return out


def newer(name, have, want):
    """True when the latest release is newer than what is installed."""
    if not have or not want:
        return False
    if name == 'prompt-optimizer':      # compare dates: the image carries no version number
        h, w = re.search(r'\d{4}-\d\d-\d\d', have), re.search(r'\d{4}-\d\d-\d\d', want)
        return bool(h and w and w.group(0) > h.group(0))
    parse = lambda v: tuple(int(x) for x in re.findall(r'\d+', v)[:3])
    return parse(want) > parse(have)


def main():
    fresh = '--fresh' in sys.argv
    cached = {}
    if CACHE.exists() and not fresh:
        try:
            cached = json.loads(CACHE.read_text(encoding='utf-8'))
        except ValueError:
            cached = {}
    # Reuse the day's cache only when every feed answered; otherwise ask again.
    if cached.get('checked', 0) > time.time() - 86400 and all(cached.get('latest', {}).values()):
        want = cached['latest']
    else:
        want = latest()
        if any(want.values()):
            try:
                CACHE.write_text(json.dumps({'checked': time.time(), 'latest': want}), encoding='utf-8')
            except OSError:
                pass
    have = installed()
    # Releases checked and deliberately not applied, with the reason (bench/versions_skip.json).
    try:
        skip = json.loads((Path(__file__).parent / 'versions_skip.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        skip = {}
    rows = []
    # n8n is retired (2026-10-28); it is only reported while its container exists again.
    names = ['ollama', 'prompt-optimizer', 'markitdown'] + (['n8n'] if have.get('n8n') else [])
    for n in names:
        latest_v = want.get(n)
        version = (re.match(r'[\d.]+', latest_v or '') or [None])[0]
        reason = skip.get(n, {}).get(version) if version else None
        rows.append({'component': n, 'installed': have.get(n), 'latest': latest_v, 'skipped': reason,
                     'update': newer(n, have.get(n), latest_v) and not reason})
    if '--json' in sys.argv:
        print(json.dumps(rows, indent=1))
        return
    if not any(r['latest'] for r in rows):
        print('   versions: release feeds unreachable (offline?); skipped')
        return
    for r in rows:
        # An unreachable feed is "unknown", never "ok": the weekly check on 2026-10-20 reported two
        # components as ok while their release feeds had not answered.
        state = ('update available: %s -> %s' % (r['installed'], r['latest'])) if r['update'] else \
                ('ok (%s)' % (r['installed'] or 'not running') if r['latest'] else
                 'unknown: %s installed, release feed did not answer (check by hand)' % (r['installed'] or 'nothing'))
        if r.get('skipped'):
            state = 'skipped %s: %s' % (r['latest'], r['skipped'])
        print('   %-17s %s' % (r['component'], state))
    if any(r['update'] for r in rows):
        print('   Updates are applied one at a time behind the quality gate: see README.md "Updating components".')


if __name__ == '__main__':
    main()
