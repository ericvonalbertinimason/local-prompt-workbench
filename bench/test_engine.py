"""Offline checks for the llamacpp engine (any local OpenAI-compatible server). A fake server stands in
for llama-server, so this needs no model, GPU or stack."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

seen = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        body = json.dumps({'model': 'qwen3.5-4b', 'usage': {'prompt_tokens': 11, 'completion_tokens': 7},
                           'choices': [{'message': {'content': '{"kind":"prompt"}'}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
spend = Path(tempfile.mkdtemp()) / 'spend.json'
os.environ.update(ENGINE_URL='http://127.0.0.1:%d/v1/chat/completions' % srv.server_port, SPEND_FILE=str(spend))
for k in ('MODEL_PROVIDER', 'LOCAL_ENGINE', 'API_PROVIDER'):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'harness'))
import pipeline as p  # noqa: E402

checks = []
text, usage = p.llm('sys', 'user', p.RESPONSE_SCHEMA, 'llamacpp')
req = seen[-1]
checks.append(('reply text', text == '{"kind":"prompt"}'))
checks.append(('usage', usage == {'input_tokens': 11, 'output_tokens': 7, 'model': 'qwen3.5-4b'}))
checks.append(('schema sent as json_schema', req['response_format']['json_schema']['schema'] == p.RESPONSE_SCHEMA))
checks.append(('thinking off', req['chat_template_kwargs'] == {'enable_thinking': False}))
checks.append(('no API key needed', 'Authorization' not in str(req)))
checks.append(('default stays ollama', p.choose_provider({}) == 'ollama'))
os.environ['LOCAL_ENGINE'] = 'llamacpp'
checks.append(('LOCAL_ENGINE picks llamacpp', p.choose_provider({}) == 'llamacpp'))
checks.append(('quality=local uses LOCAL_ENGINE', p.choose_provider({'quality': 'local'}) == 'llamacpp'))
checks.append(('quality=api still goes to the API', p.choose_provider({'quality': 'api'}) == 'anthropic'))
os.environ['MODEL_PROVIDER'] = 'llamacpp'
checks.append(('quality=api with MODEL_PROVIDER=llamacpp', p.choose_provider({'quality': 'api'}) == 'anthropic'))
checks.append(('local engine is never capped', 'llamacpp' in p.LOCAL_PROVIDERS and not spend.exists()))

failed = [n for n, ok in checks if not ok]
print('%d/%d passed' % (len(checks) - len(failed), len(checks)), *('FAIL ' + n for n in failed), sep='\n')
sys.exit(1 if failed else 0)
