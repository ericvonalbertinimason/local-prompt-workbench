"""MCP connection log: who called, whether it worked, how long it took (moved from the workbench server, 2026-11-08).

Wrap any JSON-RPC handler with logged(); connect, tool calls, prompt and resource reads are recorded in
the mcp_calls table, labelled by client (tests, the Claude desktop app's bridge, Claude Code, other).
Logging never breaks a call. stats() gives per-client success rates for a dashboard or health check.
"""
import time

LOGGED_METHODS = ('initialize', 'tools/call', 'prompts/get', 'resources/read')


def init_log(c):
    # One row per MCP call that matters (connect, tool call, prompt or resource read), so
    # connector success can be measured per client (Claude Code, the chat app, tests).
    c.execute('''CREATE TABLE IF NOT EXISTS mcp_calls (ts REAL, client TEXT, agent TEXT, method TEXT,
                 tool TEXT, ok INTEGER, ms INTEGER, error TEXT)''')


def client_label(agent, msg):
    """Which client made a call: tests mark themselves; the desktop-app bridge sends its own User-Agent.

    The bridge serves the whole Claude desktop app (its chat and its Code tab), so it is labelled
    as the app; Claude Code in a terminal connects over HTTP directly (2026-10-28).
    """
    meta = (msg.get('params') or {}).get('_meta') or {}
    a = (agent or '').lower()
    if meta.get('prompt-workbench/origin') == 'test' or 'prompt-workbench-test' in a:
        return 'test'
    if 'prompt-workbench-bridge' in a:
        return 'Claude desktop app'
    if 'claude' in a or 'node' in a or 'undici' in a or 'axios' in a:
        return 'Claude Code'
    return 'other'


def logged(handle, msg, agent, db):
    """handle(msg, agent) plus a record of the call in db's mcp_calls table."""
    started = time.time()
    reply, failure = None, None
    try:
        reply = handle(msg, agent)
    except Exception as e:
        failure = e
    method = msg.get('method') if isinstance(msg, dict) else None
    if method in LOGGED_METHODS:
        try:
            params = msg.get('params') or {}
            result = (reply or {}).get('result') or {}
            ok = failure is None and reply is not None and 'error' not in reply and not result.get('isError')
            if failure is not None:
                error = str(failure)
            elif 'error' in (reply or {}):
                error = str(reply['error'].get('message', ''))
            elif result.get('isError'):
                error = ''.join(c.get('text', '') for c in result.get('content', []))
            else:
                error = ''
            tool = params.get('name') or params.get('uri') or (params.get('clientInfo') or {}).get('name') or ''
            with db() as c:
                c.execute('INSERT INTO mcp_calls VALUES (?,?,?,?,?,?,?,?)',
                          (started, client_label(agent, msg), (agent or '')[:120], method, str(tool)[:80],
                           int(ok), int((time.time() - started) * 1000), error[:300]))
        except Exception:
            pass
    if failure is not None:
        raise failure
    return reply


def stats(db, days=30):
    """Per client: calls, success rate, median time, last seen, last error."""
    since = time.time() - days * 86400
    with db() as c:
        rows = c.execute('SELECT * FROM mcp_calls WHERE ts>=? ORDER BY ts', (since,)).fetchall()
    out = {}
    for r in rows:
        s = out.setdefault(r['client'], {'client': r['client'], 'connects': 0, 'calls': 0, 'ok': 0, 'ms': [],
                                         'last_seen': 0, 'last_error': '', 'last_error_at': 0, 'agent': r['agent']})
        s['last_seen'] = r['ts']
        s['agent'] = r['agent'] or s['agent']
        if r['method'] == 'initialize':
            s['connects'] += 1
            continue
        s['calls'] += 1
        s['ok'] += r['ok']
        s['ms'].append(r['ms'])
        if not r['ok']:
            s['last_error'], s['last_error_at'] = r['error'], r['ts']
    for s in out.values():
        ms = sorted(s.pop('ms'))
        s['median_ms'] = ms[len(ms) // 2] if ms else None
        s['success_rate'] = round(s['ok'] / s['calls'], 3) if s['calls'] else None
    return {'days': days, 'clients': sorted(out.values(), key=lambda s: -s['last_seen'])}
