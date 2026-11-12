"""End-to-end test of the workbench MCP server (http://127.0.0.1:8789/mcp).

Speaks JSON-RPC like an MCP client: handshake, every tool, error paths, and the
security boundaries (foreign browser origin, wrong host, gateway port, GET).
Runs real prompts through the pipeline, so it takes a little while.

  python bench/test_mcp.py
"""
import json
import re
import sys
import urllib.error
import urllib.request

URL = 'http://127.0.0.1:8789/mcp'
BASE_HEADERS = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                # Every call is counted under "test" in the connector log, not as a real client.
                'User-Agent': 'prompt-workbench-test/1'}
_id = [0]


def rpc(method, params=None, url=URL, headers=None, notify=False, http_method='POST'):
    body = {'jsonrpc': '2.0', 'method': method}
    if params is not None:
        body['params'] = params
    if not notify:
        _id[0] += 1
        body['id'] = _id[0]
    req = urllib.request.Request(url, data=json.dumps(body).encode() if http_method == 'POST' else None,
                                 headers=dict(BASE_HEADERS, **(headers or {})), method=http_method)
    try:
        with urllib.request.urlopen(req, timeout=400) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def call(tool, args):
    # _meta marks these as test runs, so they land in the UI's Test runs view, not the history.
    status, reply = rpc('tools/call', {'name': tool, 'arguments': args, '_meta': {'prompt-workbench/origin': 'test'}})
    result = (reply or {}).get('result', {})
    text = ''.join(c.get('text', '') for c in result.get('content', []))
    return status, reply, result, text


def main():
    results = []
    status, reply = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                       'clientInfo': {'name': 'test', 'version': '1'}})
    info = (reply or {}).get('result', {})
    results.append((status == 200 and info.get('serverInfo', {}).get('name') == 'prompt-workbench'
                    and info.get('protocolVersion') == '2025-06-18', 'initialize -> %s %s' % (status, info.get('serverInfo'))))
    status, _ = rpc('notifications/initialized', notify=True)
    results.append((status == 202, 'initialized notification -> %s' % status))
    status, reply = rpc('tools/list')
    tools = (reply or {}).get('result', {}).get('tools', [])
    names = [t['name'] for t in tools]
    results.append((sorted(names) == ['answer_questions', 'create_prompt', 'find_runs', 'find_saved_prompts',
                                      'pin_prompt', 'revise_prompt'], 'tools/list -> %s' % names))
    # Token budget: the tool list is loaded into every Claude Code session (was 4,499 chars).
    size = len(json.dumps((reply or {}).get('result', {})))
    results.append((size <= 2600, 'tools/list is %d characters (budget 2,600; was 4,499)' % size))
    # MCP App panel (2026-11-07): declared only to the desktop-app bridge, which can render it.
    results.append((not any(t.get('_meta') for t in tools), 'no app declaration for other clients'))
    _, bridge = rpc('tools/list', headers={'User-Agent': 'prompt-workbench-bridge/1 (Claude desktop app)'})
    meta = next((t.get('_meta') for t in (bridge or {}).get('result', {}).get('tools', []) if t['name'] == 'create_prompt'), None)
    uri = ((meta or {}).get('ui') or {}).get('resourceUri')
    results.append((bool(uri), 'desktop bridge sees create_prompt app panel %s' % uri))
    _, view = rpc('resources/read', {'uri': uri or 'ui://none'})
    content = ((view or {}).get('result', {}).get('contents') or [{}])[0]
    results.append((content.get('mimeType') == 'text/html;profile=mcp-app' and 'ui/initialize' in content.get('text', ''),
                    'app panel resource is served (%s)' % content.get('mimeType')))
    ro = sorted(t['name'] for t in tools if t.get('annotations', {}).get('readOnlyHint'))
    results.append((ro == ['find_runs', 'find_saved_prompts']
                    and not any(t['annotations']['destructiveHint'] for t in tools), 'read-only hints -> %s' % ro))

    status, _, result, text = call('create_prompt', {'request': 'Write a prompt to summarize {{article}} in three bullet points.', 'mode': 'user'})
    run = re.match(r'run (\w{8})\b', text)
    results.append((status == 200 and not result.get('isError') and run and ('\n---\n' in text or 'answer_questions' in text),
                    'create_prompt -> %s' % text.splitlines()[0][:120]))
    rid = run.group(1) if run else ''
    results.append(('{{article}}' in text if '\n---\n' in text else True, 'create_prompt kept {{article}}'))
    results.append((re.search(r'\| QA (passed|FLAGGED)', text.splitlines()[0]) is not None, 'header states the QA verdict'))
    # Header and prompt are split at the first '---'; the prompt itself may contain markdown rules.
    body = text.split('\n---\n', 1)[-1]
    results.append((len(text) < 400 + len(body), 'compact output: %d chars around a %d-char prompt'
                    % (len(text) - len(body), len(body))))

    _, _, _, text = call('find_runs', {'run_id': rid})
    results.append((text.startswith('run ' + rid), 'find_runs by short id -> %s' % text.splitlines()[0][:80]))
    _, _, _, vtext = call('find_runs', {'run_id': rid, 'verbose': True})
    results.append(('checks: ' in vtext and 'checks: ' not in text, 'verbose adds the QA checks only on request'))
    _, _, _, text = call('get_prompt_run', {'run_id': rid})
    results.append((text.startswith('run ' + rid), 'old name get_prompt_run still works'))
    _, _, _, text = call('find_runs', {'limit': 25})
    results.append((rid and rid not in text and '\n' not in text.split('\n')[0][9:], 'find_runs list: one line per run, test runs left out'))
    _, _, result, text = call('find_runs', {'run_id': 'zzzzzzzz'})
    results.append((result.get('isError') is True, 'unknown short id refused: %s' % text[:50]))
    _, _, _, text = call('revise_prompt', {'run_id': rid, 'instructions': 'Make each bullet start with a verb.'})
    results.append((re.match(r'run \w{8} \| QA', text) is not None, 'revise_prompt -> %s' % text.splitlines()[0][:80]))
    # Destination + write-first: an agent task comes back as a prompt that keeps its link,
    # with its assumptions and optional questions; answering one refines it.
    agent_req = ('Write a prompt for Claude in Chrome to open https://example.edu/course/42, download the lecture '
                 'slides and add them to NotebookLM.')
    _, _, result, text = call('create_prompt', {'request': agent_req, 'mode': 'user', 'destination': 'auto'})
    results.append(('runs in AI agent' in text.splitlines()[0] and 'https://example.edu/course/42' in text,
                    'create_prompt agent task -> %s' % text.splitlines()[0][:100]))
    arid = (re.match(r'run (\w{8})\b', text) or [None, ''])[1]
    oq = next((l for l in text.split('\n---\n')[0].splitlines() if l.startswith('Optional questions')), '')
    nq = len(re.findall(r'\b\d\. ', oq))
    if nq:
        answers = ['Only the lecture slides, as PDFs.'] + [''] * (nq - 1)
        _, _, result, text = call('answer_questions', {'run_id': arid, 'answers': answers})
        results.append((re.match(r'run \w{8} \| QA', text) is not None and not result.get('isError'),
                        'answer one optional question -> %s' % text.splitlines()[0][:80]))
    else:
        results.append((True, 'no optional questions this time (answering skipped)'))
    _, _, result, text = call('create_prompt', {'request': 'x', 'destination': 'moon'})
    results.append((result.get('isError') is True and 'Unknown destination' in text, 'unknown destination refused'))

    status, _, result, text = call('find_saved_prompts', {})
    results.append((status == 200 and not result.get('isError'), 'find_saved_prompts -> %d characters' % len(text)))

    # MCP prompts: saved library + pinned runs become slash commands. Pin this test's own
    # run through the UI API, check it is published with {{article}} as an argument, unpin.
    status, reply = rpc('prompts/list')
    listed = (reply or {}).get('result', {}).get('prompts', [])
    results.append((status == 200 and any('library' in p['description'].lower() for p in listed),
                    'prompts/list publishes the saved library (%d prompts)' % len(listed)))
    ui = {'Content-Type': 'application/json', 'Host': '127.0.0.1:8789', 'Origin': 'http://127.0.0.1:8789',
          'X-Harness-Client': 'workbench'}
    def set_pin(v):
        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8789/api/runs/' + full_rid,
                               data=json.dumps({'pinned': v}).encode(), headers=ui), timeout=30).read()
    full_rid = next(r['id'] for r in json.loads(urllib.request.urlopen(urllib.request.Request(
        'http://127.0.0.1:8789/api/runs?view=tests', headers={'Host': '127.0.0.1:8789'}), timeout=30).read())['rows']
        if r['id'].startswith(rid))
    set_pin(True)
    try:
        _, reply = rpc('prompts/list')
        pinned = [p for p in (reply or {}).get('result', {}).get('prompts', []) if p['description'].startswith('Pinned')]
        mine = next((p for p in pinned if any(a['name'] == 'article' for a in p['arguments'])), None)
        results.append((mine is not None, 'pinned run appears as a prompt with argument "article"'))
        if mine:
            _, reply = rpc('prompts/get', {'name': mine['name'], 'arguments': {'article': 'THE-TEST-ARTICLE'}})
            got = ((reply or {}).get('result', {}).get('messages') or [{}])[0].get('content', {}).get('text', '')
            results.append(('THE-TEST-ARTICLE' in got and '{{article}}' not in got, 'prompts/get fills in {{article}}'))
    finally:
        set_pin(False)
    # pin_prompt: the MCP way to do the same, with a title that becomes the command name.
    _, _, result, text = call('pin_prompt', {'run_id': rid, 'title': 'mcp test article summary'})
    _, reply = rpc('prompts/list')
    listed = [p['name'] for p in (reply or {}).get('result', {}).get('prompts', [])]
    results.append((not result.get('isError') and 'mcp-test-article-summary' in listed,
                    'pin_prompt -> %s' % text))
    _, _, result, text = call('pin_prompt', {'run_id': rid, 'pinned': False})
    _, reply = rpc('prompts/list')
    listed = [p['name'] for p in (reply or {}).get('result', {}).get('prompts', [])]
    results.append((not result.get('isError') and 'mcp-test-article-summary' not in listed, 'pin_prompt unpin -> %s' % text))
    _, _, result, _ = call('pin_prompt', {'run_id': 'no-such-run'})
    results.append((result.get('isError') is True, 'pin_prompt on an unknown run is refused'))
    # Resources: prompts Claude Code can @-mention (pinned, recent, library); any run by short id.
    status, reply = rpc('resources/list')
    res = (reply or {}).get('result', {}).get('resources', [])
    results.append((status == 200 and res and all(r['uri'].startswith('prompt-workbench://') for r in res)
                    and not any('_text' in r for r in res), 'resources/list -> %d resources' % len(res)))
    if res:
        _, reply = rpc('resources/read', {'uri': res[0]['uri']})
        got = ((reply or {}).get('result', {}).get('contents') or [{}])[0].get('text', '')
        results.append((len(got) > 20, 'resources/read %s -> %d chars' % (res[0]['uri'], len(got))))
    _, reply = rpc('resources/read', {'uri': 'prompt-workbench://run/' + rid})
    got = ((reply or {}).get('result', {}).get('contents') or [{}])[0].get('text', '')
    results.append((got.startswith('run ' + rid), 'resources/read of a run by short id'))
    _, reply = rpc('resources/read', {'uri': 'prompt-workbench://run/zzzzzzzz'})
    results.append(((reply or {}).get('error', {}).get('code') == -32002, 'unknown resource -> JSON-RPC error'))
    # Connector log: this test's calls are recorded under the 'test' client.
    stats = json.loads(urllib.request.urlopen(urllib.request.Request(
        'http://127.0.0.1:8789/api/connectors?days=1', headers={'Host': '127.0.0.1:8789'}), timeout=30).read())
    mine = next((c for c in stats['clients'] if c['client'] == 'test'), None)
    results.append((mine is not None and mine['calls'] >= 5 and mine['ok'] >= 4,
                    'connector log records test calls -> %s' % (mine and {k: mine[k] for k in ('calls', 'ok', 'median_ms')})))
    status, reply = rpc('prompts/get', {'name': 'no-such-prompt'})
    results.append(((reply or {}).get('error', {}).get('code') == -32602, 'unknown prompt -> JSON-RPC error'))

    _, _, result, text = call('answer_questions', {'run_id': rid, 'answers': ['x', 'y', 'z', 'w']})
    results.append((result.get('isError') is True, 'answers that do not match the questions are refused: %s' % text[:60]))
    _, reply, _, _ = call('no_such_tool', {})
    results.append(((reply or {}).get('error', {}).get('code') == -32602, 'unknown tool -> JSON-RPC error'))

    status, _ = rpc('ping', headers={'Origin': 'http://evil.example'})
    results.append((status == 403, 'browser page from another site refused -> %s' % status))
    status, _ = rpc('ping', headers={'Host': 'evil.example:8789'})
    results.append((status == 403, 'wrong Host (DNS rebinding) refused -> %s' % status))
    try:
        status, _ = rpc('ping', url='http://127.0.0.1:8790/mcp')
    except OSError:
        status = 'closed'
    results.append((status == 'closed', 'retired gateway port 8790 is closed -> %s' % status))
    status, _ = rpc('ping', http_method='GET')
    results.append((status == 405, 'GET refused (no SSE stream) -> %s' % status))

    failed = 0
    for ok, line in results:
        failed += not ok
        print('  %s  %s' % ('PASS' if ok else 'FAIL', line.encode('ascii', 'replace').decode()))
    print('\n%d/%d passed' % (len(results) - failed, len(results)))
    sys.exit(failed)


if __name__ == '__main__':
    main()
