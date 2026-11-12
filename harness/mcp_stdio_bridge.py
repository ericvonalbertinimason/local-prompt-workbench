"""Stdio bridge from the Claude desktop chat app to the workbench MCP server.

The chat app launches local MCP servers as programs that speak JSON-RPC over stdin/stdout;
the workbench serves MCP over HTTP at http://127.0.0.1:8789/mcp (Claude Code connects there
directly). This forwards each message unchanged, with one addition: files from Claude
(2026-11-07). create_prompt gains a `files` argument here; the bridge reads only the files
Claude names, on this PC, and sends their bytes as `file_data`. The workbench container never
gets access to your folders. Standard library only, so nothing to install.

claude_desktop_config.json:
  "prompt-workbench": {"command": "C:\\...\\python.exe",
                       "args": ["C:\\path\\to\\prompt-workbench\\harness\\mcp_stdio_bridge.py"]}
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get('WORKBENCH_MCP_URL', 'http://127.0.0.1:8789/mcp')
# Long enough for a full prompt run (the server itself returns "still running" after 150 s).
TIMEOUT = 240
# What the workbench converter handles (harness/server.py CONVERT_TYPES and ocr.IMAGE_TYPES).
FILE_TYPES = {'.pdf', '.docx', '.pptx', '.xlsx', '.html', '.htm', '.txt', '.md', '.csv', '.json',
              '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
MAX_FILES, MAX_BYTES = 5, 40 * 1024 * 1024
# Never sent, whatever Claude asks for: the workbench's own data folder (history, keys) and files that
# look like credentials. Matched on whole names, so "Keynote lecture.pdf" or "Turkey trip.pdf" are fine.
DATA_DIR = Path(__file__).resolve().parent / 'data'
SECRET_NAME = re.compile(r'^\.env|^id_(rsa|ed25519|ecdsa)|\.(pem|key|kdbx|p12|pfx|sqlite\d?|db)$|'
                         r'(^|[-_ .])(api|access|secret|private)[-_ ]?key(s)?([-_ .]|$)|credentials', re.I)
FILES_PROPERTY = {'type': 'array', 'items': {'type': 'string'},
                  'description': 'Paths of files on this PC to read (PDF, Office, pictures, text)'}


def forward(raw):
    req = urllib.request.Request(URL, data=raw.encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                          # Lets the workbench count the chat app's calls separately.
                                          'User-Agent': 'prompt-workbench-bridge/1 (Claude desktop app)'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode('utf-8')


def read_files(paths):
    """[{name, data}] for the named files, or ValueError saying which one cannot be sent and why."""
    if isinstance(paths, str):
        paths = [paths]
    paths = [str(p).strip().strip('"') for p in (paths or []) if str(p).strip()]
    if len(paths) > MAX_FILES:
        raise ValueError('At most %d files per request' % MAX_FILES)
    out = []
    for raw in paths:
        path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        if path == DATA_DIR or DATA_DIR in path.parents:
            raise ValueError('%s is in the workbench data folder, which is never sent' % raw)
        if SECRET_NAME.search(path.name):
            raise ValueError('%s looks like a key or credentials file; it is not sent' % raw)
        if path.suffix.lower() not in FILE_TYPES:
            raise ValueError('%s: unsupported file type. Use: %s' % (raw, ', '.join(sorted(FILE_TYPES))))
        if not path.is_file():
            raise ValueError('File not found: %s' % raw)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('%s is larger than %d MB' % (raw, MAX_BYTES // 1024 // 1024))
        out.append({'name': path.name, 'data': base64.b64encode(path.read_bytes()).decode('ascii')})
    return out


def outgoing(msg, line):
    """The line to send: create_prompt's `files` become `file_data`. Raises ValueError for a bad file."""
    params = msg.get('params') if isinstance(msg, dict) else None
    if not (msg.get('method') == 'tools/call' and isinstance(params, dict) and params.get('name') == 'create_prompt'):
        return line
    args = dict(params.get('arguments') or {})
    if not args.get('files'):
        return line
    args['file_data'] = read_files(args.pop('files'))
    return json.dumps(dict(msg, params=dict(params, arguments=args)))


def incoming(msg, body):
    """Replies: advertise `files` on create_prompt in tools/list (only the desktop app can use it)."""
    if not (isinstance(msg, dict) and msg.get('method') == 'tools/list'):
        return body
    try:
        reply = json.loads(body)
        for tool in reply['result']['tools']:
            if tool.get('name') == 'create_prompt':
                tool['inputSchema'].setdefault('properties', {})['files'] = FILES_PROPERTY
        return json.dumps(reply)
    except (ValueError, KeyError, TypeError):
        return body


def main():
    # MCP stdio transport: one JSON-RPC message per line on stdin, replies one per line on stdout.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            body = incoming(msg, forward(outgoing(msg, line)))
        except ValueError as e:                 # a named file could not be sent: tell Claude why
            body = json.dumps({'jsonrpc': '2.0', 'id': msg.get('id'),
                               'result': {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}})
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print('prompt-workbench bridge: %s' % e, file=sys.stderr, flush=True)
            if isinstance(msg, dict) and msg.get('id') is not None:
                body = json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'error': {
                    'code': -32000, 'message': 'The Prompt Workbench is not running. Start it with start.cmd '
                                               '(C:\\path\\to\\prompt-workbench), then try again.'}})
            else:
                continue
        if body.strip():                      # notifications get an empty 202 reply: nothing to write
            sys.stdout.write(body.strip() + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
