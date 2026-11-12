"""Raw drafting speed of one engine, with the workbench's real system message and JSON schema.

  python bench/engine_bench.py ollama            # Ollama on 11434 (the model it already runs)
  python bench/engine_bench.py llamacpp          # llama-server on 8090 (bench/llama_server.ps1)

Run it with the GPU otherwise idle and only the engine under test holding a model. One warm-up call,
then the same requests in order. Prints wall time, output tokens and tokens per second.
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault('OLLAMA_CHAT_URL', 'http://127.0.0.1:11434/api/chat')
os.environ.setdefault('ENGINE_URL', 'http://127.0.0.1:8090/v1/chat/completions')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'harness'))
import pipeline as p  # noqa: E402

REQUESTS = [
    'Write a prompt that summarizes a meeting transcript into action items with owners.',
    'Improve this prompt: explain photosynthesis to a 10 year old',
    'Write a system prompt for a customer support bot for a bike shop that never promises refunds.',
    'Create a prompt to turn {{notes}} into a one-page study guide with key terms and practice questions.',
    'Write a prompt for drafting a polite follow-up email to {{recruiter}} after an interview.',
]

engine = sys.argv[1] if len(sys.argv) > 1 else 'ollama'
system = p.system_message()
p.llm(system, 'USER REQUEST (authoritative):\nSay hello.', p.RESPONSE_SCHEMA, engine)  # warm-up
rows = []
for r in REQUESTS:
    t = time.time()
    text, usage = p.llm(system, 'USER REQUEST (authoritative):\n' + r, p.RESPONSE_SCHEMA, engine)
    secs = time.time() - t
    valid = isinstance(json.loads(text), dict)
    rows.append((secs, usage['output_tokens'], valid))
    print('%-9s %5.1fs  %4d tok  %5.1f tok/s  %s' % (engine, secs, usage['output_tokens'],
                                                     usage['output_tokens'] / secs, 'valid' if valid else 'INVALID'))
tps = sum(r[1] for r in rows) / sum(r[0] for r in rows)
print('%s: median %.1fs, %.1f tok/s overall, %d/%d valid' % (
    engine, statistics.median(r[0] for r in rows), tps, sum(r[2] for r in rows), len(rows)))
