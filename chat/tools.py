"""Chatbot tools, as slash commands. A 4B model is unreliable at deciding when to call a tool, so the
user calls them explicitly; the result is shown and also added to the conversation for follow-ups.

  /help                list commands
  /docs QUERY          search your indexed documents and show the matches
  /prompts [QUERY]     find your saved prompts in the Prompt Workbench
  /prompt REQUEST      have the workbench write a QA-checked prompt (takes a few seconds)
  /use QUERY           chat as one of your saved prompts (its text becomes the assistant's instructions)
  /run                 chat as the prompt /prompt or /promptify just wrote
  /promptify [GOAL]    turn this conversation into a reusable, QA-checked prompt
  /save [TITLE]        pin the last prompt written here to your workbench library
  /use off             back to the plain assistant
"""
import json
import os
import re
import urllib.request
from pathlib import Path

import docs

WORKBENCH = os.environ.get('WORKBENCH_URL', 'http://127.0.0.1:8789')
SAVED = re.compile(r'^\[(\d+)\] (.+)$', re.M)
RUN_ID = re.compile(r'^run (\w+)', re.M)


def workbench(tool, arguments, timeout=600):
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}}
    req = urllib.request.Request(WORKBENCH + '/mcp', data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'Host': '127.0.0.1:8789'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if 'error' in out:
        raise RuntimeError(out['error'].get('message', 'workbench error'))
    return '\n'.join(c.get('text', '') for c in out['result'].get('content', []) if c.get('type') == 'text')


# --- Per-conversation state: the active assistant and the last prompt written here ----------------
def _table(c):
    c.execute('CREATE TABLE IF NOT EXISTS state (session TEXT PRIMARY KEY, title TEXT, prompt TEXT, '
              'last_run TEXT, last_prompt TEXT)')


def state(c, session):
    """{'title', 'prompt', 'last_run', 'last_prompt'} for the conversation (empty strings if unset)."""
    if c is None:
        return {'title': '', 'prompt': '', 'last_run': '', 'last_prompt': ''}
    _table(c)
    row = c.execute('SELECT title, prompt, last_run, last_prompt FROM state WHERE session=?', (session,)).fetchone()
    return dict(zip(('title', 'prompt', 'last_run', 'last_prompt'), row or ('', '', '', '')))


def _set(c, session, **fields):
    s = dict(state(c, session), **fields)
    c.execute('INSERT OR REPLACE INTO state VALUES (?,?,?,?,?)',
              (session, s['title'], s['prompt'], s['last_run'], s['last_prompt']))
    c.commit()


def saved_prompts(text):
    """Split find_saved_prompts output into [(id, title, body)]."""
    marks = list(SAVED.finditer(text))
    return [(m.group(1), m.group(2).strip(), text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip())
            for i, m in enumerate(marks)]


def created(text):
    """(run_id, prompt) from create_prompt output: the prompt follows the first '---' line."""
    run = RUN_ID.search(text)
    _, sep, body = text.partition('\n---\n')
    return (run.group(1) if run else ''), (body.strip() if sep else '')


def _remember(c, session, text):
    run_id, prompt = created(text)
    if prompt:
        _set(c, session, last_run=run_id, last_prompt=prompt)
    return text + ('\n\nNext: /run to chat with it, /save to pin it.' if prompt else '')


def run(line, index, c=None, session=''):
    """Run a slash command. Returns its text, or None if `line` is not a command."""
    if not line.startswith('/'):
        return None
    name, _, arg = line[1:].partition(' ')
    arg = arg.strip()
    if name == 'help':
        return __doc__.split('\n\n', 1)[1].strip()
    if name == 'docs':
        if index is None:
            return 'No documents indexed yet. Run: python chat/chat.py --index FOLDER'
        hits = docs.search(index, arg, k=5)
        return '\n\n'.join('%s (part %d)\n%s' % (Path(p).name, part, t[:400]) for p, part, t in hits) or 'No matches.'
    if name == 'use' and arg.lower() == 'off':
        _set(c, session, title='', prompt='')
        return 'Back to the plain assistant.'
    if name == 'run':
        s = state(c, session)
        if not s['last_prompt']:
            return 'No prompt written in this conversation yet. Use /prompt or /promptify first.'
        _set(c, session, title='prompt run ' + (s['last_run'] or 'new'), prompt=s['last_prompt'])
        return 'Now chatting as the prompt just written. Send its input (or /use off to stop).'
    try:
        if name == 'prompts':
            return workbench('find_saved_prompts', {'query': arg} if arg else {}, timeout=30) or 'No saved prompts.'
        if name == 'use':
            found = saved_prompts(workbench('find_saved_prompts', {'query': arg} if arg else {}, timeout=30))
            if not found:
                return 'No saved prompt matches "%s". Try /prompts to see them.' % arg
            pid, title, body = found[0]
            _set(c, session, title=title, prompt=body)
            others = ', '.join(t for _, t, _ in found[1:4])
            return 'Now chatting as "%s" [%s]. Send its input (or /use off to stop).%s' % (
                title, pid, ('\nOther matches: ' + others) if others else '')
        if name == 'prompt':
            if not arg:
                return 'Usage: /prompt what the prompt should do'
            return _remember(c, session, workbench('create_prompt', {'request': arg}))
        if name == 'promptify':
            rows = [] if c is None else c.execute(
                'SELECT role, content FROM messages WHERE session=? ORDER BY rowid', (session,)).fetchall()
            kept, skip = [], False          # slash commands and their outputs are not the conversation
            for r, t in rows:
                if r == 'user':
                    skip = t.startswith('/')
                if not skip:
                    kept.append(('User: ' if r == 'user' else 'Assistant: ') + t)
            talk = '\n\n'.join(kept[-20:])
            if not talk:
                return 'Nothing to turn into a prompt yet: chat first, then /promptify.'
            request = arg or ('Write a reusable prompt for the task the user described in this conversation. '
                              'Write every requirement they stated (format, sections, length, tone) into the prompt '
                              'itself as fixed rules. Use a {{placeholder}} only for the content that changes each '
                              'time it is used, such as this week\'s notes.')
            return _remember(c, session, workbench('create_prompt', {'request': request, 'source_material': talk}))
        if name == 'save':
            s = state(c, session)
            if not s['last_run']:
                return 'Nothing to save: write a prompt here with /prompt or /promptify first.'
            args = {'run_id': s['last_run'], 'pinned': True}
            if arg:
                args['title'] = arg
            return workbench('pin_prompt', args, timeout=30) or 'Pinned.'
    except OSError as e:
        return 'The Prompt Workbench is not reachable (%s). Is it running?' % str(e)[:100]
    return 'Unknown command. Try /help'
