"""Model calls for every app on the core: local engines (Ollama, llama.cpp or any OpenAI-compatible
server) and paid APIs (Anthropic, OpenAI), JSON-schema output, and the monthly API spend cap.

Settings (environment):
  MODEL_PROVIDER  ollama | llamacpp | anthropic | openai | deepseek   (empty = LOCAL_ENGINE)
  LOCAL_ENGINE    ollama | llamacpp      engine for local runs (default ollama)
  ENGINE_URL      OpenAI-style chat endpoint for llamacpp (llama-server, vLLM, LM Studio)
  DRAFT_MODEL / ENGINE_MODEL / ASSISTANT_MODEL   model names
  API_KEY_FILE    file holding the provider API key (never in git, logs or the browser); a file
                  api-key-<provider>.txt next to it wins for that provider (api-key-deepseek.txt)
  OPENAI_BASE_URL any OpenAI-compatible chat endpoint for provider openai (default api.openai.com)
  API_PROVIDER    anthropic | openai     provider for a request with quality=api (default anthropic)
  SPEND_CAP_USD, PRICE_IN_USD, PRICE_OUT_USD, SPEND_FILE   the cost guard
"""
import json
import os
import time
import urllib.request
from pathlib import Path

OLLAMA_CHAT = os.environ.get('OLLAMA_CHAT_URL', 'http://host.docker.internal:11434/api/chat')
LOCAL_MODEL = os.environ.get('ASSISTANT_MODEL', 'qwen3.5-assistant-fast:4b')
ENGINE_URL = os.environ.get('ENGINE_URL', 'http://host.docker.internal:8090/v1/chat/completions')
LOCAL_PROVIDERS = ('ollama', 'llamacpp')
NUM_CTX = int(os.environ.get('NUM_CTX', '65536'))
NUM_PREDICT = int(os.environ.get('NUM_PREDICT', '8192'))

def setting(name, default):
    return (os.environ.get(name) or default).strip().lower()


def post(url, body, timeout, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read().decode()
        if 'text/event-stream' in r.headers.get('Content-Type', ''):
            data = [line[6:] for line in data.splitlines() if line.startswith('data: ')][-1]
        return json.loads(data), dict(r.headers)


# --- Model call (Write and Refine Prompts) -----------------------------------------------------
def api_key(provider=None):
    path = os.environ.get('API_KEY_FILE', '')
    if not path:
        return ''
    own = Path(path).with_name('api-key-%s.txt' % provider) if provider else None
    for f in ([own] if own else []) + ([] if provider == 'deepseek' else [Path(path)]):
        if f.exists() and f.read_text(encoding='utf-8').strip():
            return f.read_text(encoding='utf-8').strip()
    return ''


def any_api_key():
    return any(api_key(p) for p in ('anthropic', 'openai', 'deepseek'))


def llm(system, user, schema=None, provider=None, model=None, timeout=1800):
    """One chat completion. Returns (text, usage) where usage = {input_tokens, output_tokens, model}."""
    provider = provider or setting('MODEL_PROVIDER', 'ollama')
    if provider == 'ollama':
        model = model or (os.environ.get('DRAFT_MODEL') if setting('MODEL_PROVIDER', 'ollama') == 'ollama' else '') or LOCAL_MODEL
        body = {'model': model, 'stream': False, 'think': False, 'keep_alive': -1,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'options': {'temperature': 0.2, 'num_ctx': NUM_CTX, 'num_predict': NUM_PREDICT}}
        if schema:
            body['format'] = schema
        out, _ = post(OLLAMA_CHAT, body, timeout)
        return out['message']['content'], {'input_tokens': out.get('prompt_eval_count', 0),
                                           'output_tokens': out.get('eval_count', 0), 'model': model}
    if provider == 'llamacpp':
        # Any local OpenAI-compatible server; llama-server enforces the JSON schema with a grammar.
        body = {'model': model or os.environ.get('ENGINE_MODEL') or 'local', 'temperature': 0.2,
                'max_tokens': NUM_PREDICT,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'chat_template_kwargs': {'enable_thinking': False}}
        if schema:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'reply', 'schema': schema}}
        out, _ = post(ENGINE_URL, body, timeout)
        u = out.get('usage', {})
        return out['choices'][0]['message']['content'], {'input_tokens': u.get('prompt_tokens', 0),
                                                         'output_tokens': u.get('completion_tokens', 0),
                                                         'model': out.get('model') or body['model']}
    key = api_key(provider)
    if not key:
        raise RuntimeError('MODEL_PROVIDER=%s needs an API key in API_KEY_FILE' % provider)
    instruction = '' if not schema else ('\n\nReply with only one JSON object matching this schema:\n'
                                         + json.dumps(schema))
    if provider == 'anthropic':
        model = model or os.environ.get('DRAFT_MODEL') or 'claude-haiku-5-5'
        out, _ = post('https://api.anthropic.com/v1/messages',
                      {'model': model, 'max_tokens': NUM_PREDICT, 'system': system + instruction,  # current models reject temperature
                       'messages': [{'role': 'user', 'content': user}]}, timeout,
                      {'x-api-key': key, 'anthropic-version': '2023-06-01'})
        text = ''.join(b.get('text', '') for b in out.get('content', []) if b.get('type') == 'text')
        u = out.get('usage', {})
        return text, {'input_tokens': u.get('input_tokens', 0), 'output_tokens': u.get('output_tokens', 0), 'model': model}
    if provider in ('openai', 'deepseek'):
        if provider == 'deepseek':
            url, model = 'https://api.deepseek.com/chat/completions', model or os.environ.get('DRAFT_MODEL') or 'deepseek-flash'
        else:
            url = os.environ.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1/chat/completions'
            model = model or os.environ.get('DRAFT_MODEL') or 'gpt-5-mini'
        body = {'model': model, 'messages': [{'role': 'system', 'content': system + instruction},
                                             {'role': 'user', 'content': user}]}
        if schema:
            body['response_format'] = {'type': 'json_object'}
        out, _ = post(url, body, timeout, {'Authorization': 'Bearer ' + key})
        u = out.get('usage', {})
        return out['choices'][0]['message']['content'], {'input_tokens': u.get('prompt_tokens', 0),
                                                         'output_tokens': u.get('completion_tokens', 0), 'model': model}
    raise ValueError('Unknown MODEL_PROVIDER: %s' % provider)


# --- Cost guard ---------------------------------------------------------------------------------
SPEND_FILE = Path(os.environ.get('SPEND_FILE', '/data/api-spend.json'))


def _spend():
    try:
        return json.loads(SPEND_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def spend_summary():
    """{'today', 'month', 'cap'} in USD (estimates from token counts)."""
    days = _spend()
    today, month = time.strftime('%Y-%m-%d'), time.strftime('%Y-%m')
    return {'today': round(days.get(today, 0), 4),
            'month': round(sum(v for k, v in days.items() if k.startswith(month)), 4),
            'cap': float(os.environ.get('SPEND_CAP_USD') or 5)}


# USD per million tokens (input, output), checked 2026-11-11: Claude API pricing table and
# api-docs.deepseek.com (DeepSeek doubles both during peak hours: weekdays 01-04 and 06-10 UTC).
PRICES = {'claude-haiku-5-5': (0.10, 0.50), 'claude-sonnet-5-5': (2.0, 10.0), 'claude-opus-5-5': (4.0, 20.0),
          'deepseek-flash': (0.15, 0.60), 'deepseek-v4-pro': (0.66, 1.98)}


def price(model, when=None):
    t = time.gmtime(when)
    pin, pout = PRICES.get(model, (float(os.environ.get('PRICE_IN_USD') or 1), float(os.environ.get('PRICE_OUT_USD') or 5)))
    if model.startswith('deepseek') and t.tm_wday < 5 and (1 <= t.tm_hour < 4 or 6 <= t.tm_hour < 10):
        pin, pout = pin * 2, pout * 2
    return pin, pout


def record_spend(usage):
    pin, pout = price(usage.get('model', ''))
    cost = (usage.get('input_tokens', 0) * pin + usage.get('output_tokens', 0) * pout) / 1e6
    days = _spend()
    today = time.strftime('%Y-%m-%d')
    days[today] = days.get(today, 0) + cost
    try:
        SPEND_FILE.write_text(json.dumps(days), encoding='utf-8')
    except OSError as e:
        print('pipeline: could not record spend (%s)' % e, flush=True)
    return cost


def choose_provider(envelope, provider=None):
    if provider:
        return provider
    quality = str(envelope.get('quality') or '').strip().lower()
    if quality == 'local':
        return setting('LOCAL_ENGINE', 'ollama')
    if quality == 'api':
        default = setting('MODEL_PROVIDER', 'ollama')
        provider = default if default not in LOCAL_PROVIDERS else setting('API_PROVIDER', 'anthropic')
    else:
        provider = setting('MODEL_PROVIDER', '') or setting('LOCAL_ENGINE', 'ollama')
    if provider not in LOCAL_PROVIDERS:
        s = spend_summary()
        if s['month'] >= s['cap']:
            print('pipeline: monthly API cap $%.2f reached; using the local model' % s['cap'], flush=True)
            return 'ollama'
    return provider
