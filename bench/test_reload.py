"""Test whether differing request options force Ollama to reload the model.

The optimizer container and the n8n Ollama node send their own option sets.
If a changed num_ctx evicts and reloads the model, every pipeline run pays two
cold loads -- which would explain the gap between 0.8s warm inference and the
14.3s production optimizer stage.
"""
import json, time, urllib.request

OLLAMA = 'http://127.0.0.1:11434'
MODEL = 'qwen3.5-assistant-fast:4b'
NS = 1_000_000_000


def gen(num_ctx, keep_alive='5m', label=''):
    body = {'model': MODEL, 'prompt': 'Say OK.', 'stream': False, 'think': False,
            'keep_alive': keep_alive,
            'options': {'num_ctx': num_ctx, 'num_predict': 8, 'temperature': 0.2}}
    req = urllib.request.Request(OLLAMA + '/api/generate', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    wall = time.time() - t
    load = d.get('load_duration', 0) / NS
    verdict = 'RELOADED' if load > 1.0 else 'reused'
    print(f'  num_ctx={num_ctx:<6} {label:<22} wall {wall:6.2f}s  load {load:5.2f}s  -> {verdict}')
    return load


print('=== does changing num_ctx force a reload? ===')
gen(8192, label='(warm up)')
gen(8192, label='(same ctx again)')
gen(4096, label='(DIFFERENT ctx)')
gen(8192, label='(back to original)')
gen(8192, label='(same ctx again)')

print('\n=== running processes / loaded models ===')
req = urllib.request.Request(OLLAMA + '/api/ps')
with urllib.request.urlopen(req, timeout=30) as r:
    ps = json.loads(r.read().decode())
for m in ps.get('models', []):
    size_gb = m.get('size', 0) / 1e9
    vram_gb = m.get('size_vram', 0) / 1e9
    print(f"  {m.get('name')}: {size_gb:.2f}GB total, {vram_gb:.2f}GB VRAM, expires {m.get('expires_at','?')}")
    print(f"    context: {m.get('context_length','?')}")
