"""Chat page for the internal chatbot: python chat/web.py, then open http://127.0.0.1:8791

Listens on 127.0.0.1 only. To use it from your phone over Tailscale (tailnet only, never public):
  tailscale serve --bg --https=8791 http://127.0.0.1:8791
then open https://<this-pc>.<tailnet>.ts.net:8791 and add it to the home screen.
Requests from any other Host are refused, as in the workbench.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat  # noqa: E402
import docs  # noqa: E402

PORT = int(os.environ.get('CHAT_PORT', '8791'))
TAILNET = [h.strip() for h in os.environ.get('TAILNET_HOSTS', '').split(',') if h.strip()]
HOSTS = {'127.0.0.1:%d' % PORT, 'localhost:%d' % PORT} | {h for t in TAILNET for h in (t, '%s:%d' % (t, PORT))}
PAGE = (Path(__file__).with_name('page.html')).read_text(encoding='utf-8')


def index_or_none():
    ix = docs.open_index(chat.HERE / 'data' / 'docs.sqlite')
    return ix if ix.execute('SELECT count(*) FROM files').fetchone()[0] else None


class Handler(BaseHTTPRequestHandler):
    def send(self, body, code=200, kind='application/json'):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        if (self.headers.get('Host') or '').lower() not in HOSTS:
            self.send({'error': 'Invalid host'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        if self.path == '/':
            return self.send(PAGE, kind='text/html')
        if self.path.startswith('/api/history'):
            session = self.path.partition('session=')[2] or 'web'
            c = chat.db()
            return self.send([{'role': r, 'content': t} for r, t in chat.history(c, session)])
        self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        if not self.allowed():
            return
        if self.path != '/api/chat' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send({'error': 'Not found'}, 404)
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 100_000)))
            message = str(body.get('message', '')).strip()
            session = str(body.get('session') or 'web')[:80]
            if not message:
                return self.send({'error': 'Empty message'}, 400)
            started = time.time()
            text, usage = chat.reply(chat.db(), session, message, index=index_or_none())
            self.send({'reply': text, 'sources': [s.split('\\')[-1].split('/')[-1] for s in usage['sources']],
                       'model': usage['model'], 'as': usage.get('as', ''), 'seconds': round(time.time() - started, 1)})
        except Exception as e:
            self.send({'error': str(e)[:200]}, 500)

    def log_message(self, fmt, *args):
        # Which device asked, without message content: Tailscale Serve passes the client address in
        # X-Forwarded-For (local requests have none). Kept in chat/data/access.log (git-ignored).
        try:
            with open(chat.HERE / 'data' / 'access.log', 'a', encoding='utf-8') as f:
                f.write('%s %s %s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'),
                                           self.headers.get('X-Forwarded-For') or 'local',
                                           self.command, self.path.split('?')[0]))
        except OSError:
            pass


if __name__ == '__main__':
    print('Chat on http://127.0.0.1:%d' % PORT)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
