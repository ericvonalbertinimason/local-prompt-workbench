"""Native prompt pipeline: the n8n workflow PromptAssistant20260909, ported to plain Python.

Same contract as the n8n chat webhook: attempt(envelope) takes the harnessVersion-1 envelope that
server.attempt() builds and returns the same <prompt_assistant> XML, so parse_xml, QA and the UI
are unchanged. The node-by-node spec is bench/n8n_inventory.md; the system message is copied
verbatim from the published workflow (bench/_work/n8n_final.json).

Settings (environment):
  PIPELINE        n8n | native           which path server.attempt() takes
  OPTIMIZER       on | off               off skips the Prompt Optimizer MCP call
  SYSTEM_OPTIMIZER and the rest of the stage settings are read where they are used.
  Model, engine and API-cost settings live in core/llm.py.

Per request, envelope['quality'] = 'local' | 'api' overrides MODEL_PROVIDER for that run.
"""
import json
import os
import re
import time
import urllib.error
import uuid
from pathlib import Path

from core.llm import (  # noqa: F401  (re-exported: server.py and the tests use pipeline.*)
    ENGINE_URL, LOCAL_MODEL, LOCAL_PROVIDERS, NUM_CTX, NUM_PREDICT, OLLAMA_CHAT, SPEND_FILE,
    any_api_key, api_key, choose_provider, llm, post, price, record_spend, setting, spend_summary)

MCP_URL = os.environ.get('MCP_URL', 'http://host.docker.internal:8081/mcp')
MODES = ('iterate-prompt', 'optimize-user-prompt', 'optimize-system-prompt')
TEMPLATES = Path(__file__).with_name('templates')

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'kind': {'type': 'string', 'enum': ['questions', 'prompt', 'message']},
        'questions': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3},
        'result': {'type': 'string'},
        'assumptions': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6},
        'open_questions': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3},
    },
    'required': ['kind', 'questions', 'result', 'assumptions', 'open_questions'],
}
POLICY = ('Preserve original intent, placeholders, and explicit constraints. Do not execute the task. Do not '
          'invent requirements or word, page, sentence, character or token limits. Remove limits when requested. '
          'Return English. Preserve save and retrieval commands exactly.')
WANTS_PROMPT = re.compile(r'\b(?:write|create|make|generate|draft|build|design|craft|produce)\b[^.\n]{0,50}\bprompts?\b', re.I)
OPTIMIZER_FAILED = re.compile(r'LLM service returned empty result|Tool execution error', re.I)


def system_message():
    return (TEMPLATES / 'draft_system.txt').read_text(encoding='utf-8')


# --- Prepare Harness Request -------------------------------------------------------------------
def prepare(envelope):
    prompt = envelope.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('A prompt is required')
    mode = envelope.get('mode') or 'iterate-prompt'
    if mode not in MODES:
        raise ValueError('Unknown optimizer mode')
    refinements = str(envelope.get('requirements') or '').strip()
    tool = mode
    if mode == 'iterate-prompt':
        args = {'prompt': prompt, 'requirements': refinements or 'Improve clarity and completeness.'}
    else:
        args = {'prompt': prompt + '\n\n' + refinements if refinements else prompt}
    if mode == 'optimize-system-prompt' and setting('SYSTEM_OPTIMIZER', 'light') != 'full':
        # The system template makes the 4B write ~1,000 tokens of outline (~12 s) that the drafter is told
        # to ignore; the basic user template gives a short draft in 1-3 s (2026-11-07 measurements).
        # The run stays in system mode everywhere else (drafting notes, XML, QA). full = old behaviour.
        tool, args = 'optimize-user-prompt', dict(args, template='user-prompt-basic')
    notes = envelope.get('harnessNotes')
    return {'prompt': prompt, 'mode': mode, 'tool': tool, 'args': args, 'request_id': str(envelope.get('requestId') or ''),
            'refinements': refinements, 'qa_feedback': str(envelope.get('qaFeedback') or ''),
            'material': str(envelope.get('sourceMaterial') or ''),
            'notes': [str(n) for n in notes if n][:6] if isinstance(notes, list) else []}


# --- Required Prompt Optimizer MCP -------------------------------------------------------------
def optimize(req, timeout=900):
    """Returns (draft, ms). With OPTIMIZER=off the draft is empty and the stage is skipped."""
    if setting('OPTIMIZER', 'on') == 'off':
        return '', 0
    started = time.time()
    headers = {'Accept': 'application/json, text/event-stream'}
    init, h = post(MCP_URL, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2024-11-05', 'capabilities': {},
        'clientInfo': {'name': 'prompt-workbench-pipeline', 'version': '1.0'}}}, 30, headers)
    session = next((v for k, v in h.items() if k.lower() == 'mcp-session-id'), None)
    if session:
        headers['mcp-session-id'] = session
    try:
        post(MCP_URL, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, 10, headers)
    except (urllib.error.URLError, ValueError, IndexError):
        pass                               # notifications get an empty 202; nothing to parse
    reply, _ = post(MCP_URL, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                              'params': {'name': req.get('tool', req['mode']), 'arguments': req['args']}}, timeout, headers)
    result = reply.get('result') or {}
    draft = '\n'.join(c['text'] if isinstance(c.get('text'), str) else json.dumps(c.get('text'))
                      for c in result.get('content', []) if c.get('type') == 'text').strip()
    if 'error' in reply or result.get('isError') or not draft or OPTIMIZER_FAILED.search(draft):
        raise RuntimeError('Required Prompt Optimizer MCP: Required optimizer returned no valid prompt')
    return draft, int((time.time() - started) * 1000)


# --- Preserve Original Chat and Optimizer Result -----------------------------------------------
def build_input(req, draft):
    parts = ['USER REQUEST (authoritative):\n' + req['prompt']]
    if req['refinements']:
        parts.append('USER REFINEMENTS:\n' + req['refinements'])
    if req['material']:
        parts.append('SOURCE MATERIAL (supplied by the user for this prompt; use its facts, do not paste it '
                     'wholesale):\n' + req['material'])
    if draft:
        parts.append('REFERENCE DRAFT (written by an optimizer for itself; reuse good wording only):\n' + draft)
    if req['qa_feedback']:
        parts.append('QUALITY REVIEW OF YOUR PREVIOUS ATTEMPT (fix every item; never mention this review):\n'
                     + req['qa_feedback'])
    if req['material']:
        parts.append('NOTE: The user attached SOURCE MATERIAL so you can write the prompt now. Use it to answer '
                     'your own questions. Reply with kind "prompt" unless something essential is missing from both '
                     'the request and the material.')
    if WANTS_PROMPT.search(req['prompt']):
        parts.append('NOTE: The user wants the prompt itself. Start the result with the instructions for the task '
                     '(for example "Proofread {{text}} ..."), not with "Write a prompt" or "Create a prompt".')
    if req['mode'] == 'optimize-system-prompt':
        draft_note = ('The reference draft follows a long generic template; do not copy its sections one by one, '
                      'and include a section only when it carries something specific to this assistant.'
                      if req.get('tool') == 'optimize-system-prompt' else
                      'The reference draft is a short general rewrite of the request, not a system prompt; take '
                      'useful wording from it, but the result must be standing instructions for the assistant.')
        parts.append('NOTE: Write a focused system prompt: the role, everything the user asked for, and the rules '
                     'that matter for this assistant. ' + draft_note)
    parts.extend('NOTE: ' + n for n in req['notes'])
    return '\n\n'.join(parts)


# --- Display Complete Prompt -------------------------------------------------------------------
def parse_response(raw):
    """JSON, tolerating code fences and trailing structural commas (never alters quoted text)."""
    source = re.sub(r'\s*```$', '', re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.I))
    try:
        return json.loads(source)
    except ValueError:
        pass
    out, quoted, escaped = [], False, False
    for i, ch in enumerate(source):
        if quoted:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            out.append(ch)
        elif ch == ',' and re.match(r'\s*[}\]]', source[i + 1:]):
            continue
        else:
            out.append(ch)
    return json.loads(''.join(out))


def strip_internal_policy(text):
    out = text.replace(POLICY, '')
    out = re.sub(r'\n*Optimization requirements \(do not include these as prompt content\):\s*', '', out, flags=re.I)
    out = re.sub(r'^\s*(USER REQUEST \(authoritative\)|USER REFINEMENTS|REFERENCE DRAFT[^:\n]*|QUALITY REVIEW[^:\n]*):\s*$',
                 '', out, flags=re.I | re.M)
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'[ \t]+\n', '\n', out)).strip()


def esc(s):
    s = re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]', '', str(s))
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;'))


def to_xml(raw, req, draft, optimizer_ms, execution_id, model, provider):
    text = (raw or '').strip()
    if not text:
        raise ValueError('Assistant returned an empty answer')
    try:
        response = parse_response(text)
    except ValueError:
        raise ValueError('Assistant response was not valid structured JSON. Retry the prompt.') from None
    if not isinstance(response, dict):
        raise ValueError('Assistant response is missing structured content')

    def str_list(v, n):
        return [q.strip() for q in v if isinstance(q, str) and q.strip()][:n] if isinstance(v, list) else []
    questions, open_questions = str_list(response.get('questions'), 3), str_list(response.get('open_questions'), 3)
    assumptions = str_list(response.get('assumptions'), 6)
    result = response.get('result')
    # Write first: when a full prompt was written, any questions become optional ones.
    if questions and response.get('kind') == 'prompt' and isinstance(result, str) and len(result.strip()) > 200:
        open_questions = open_questions or questions
        questions = []
    kind = 'questions' if questions else ('message' if response.get('kind') == 'message' else 'prompt')
    final = (strip_internal_policy(result) if isinstance(result, str) else '') or \
            ('Please answer these questions so I can tailor your prompt.' if questions else '')
    if not final:
        raise ValueError('Assistant returned an empty result')
    qx = lambda qs: '\n'.join('    <question id="%d">%s</question>' % (i + 1, esc(q)) for i, q in enumerate(qs))
    tool = req['mode'] if draft else 'skipped'
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<prompt_assistant kind="%s">\n'
            '  <execution id="%s" request_id="%s" />\n'
            '  <optimizer status="success" tool="%s" duration_ms="%d">\n    <draft>%s</draft>\n  </optimizer>\n'
            '  <model provider="%s" thinking="false">%s</model>\n'
            '  <questions>\n%s\n  </questions>\n  <assumptions>\n%s\n  </assumptions>\n'
            '  <open_questions>\n%s\n  </open_questions>\n  <result>%s</result>\n</prompt_assistant>') % (
        kind, esc(execution_id), esc(req['request_id']), esc(tool), optimizer_ms, esc(draft), esc(provider), esc(model),
        qx(questions), '\n'.join('    <assumption>%s</assumption>' % esc(a) for a in assumptions),
        qx(open_questions), esc(final))


def attempt(envelope, provider=None, model=None):
    """One full pass. Returns (xml, timings) with per-stage milliseconds and token usage."""
    started = time.time()
    req = prepare(envelope)
    draft, optimizer_ms = optimize(req)
    provider = choose_provider(envelope, provider)
    try:
        raw, usage = llm(system_message(), build_input(req, draft), RESPONSE_SCHEMA, provider, model)
    except (urllib.error.URLError, OSError, RuntimeError, KeyError, ValueError) as e:
        if provider == 'ollama':
            raise
        # The API model and the llama.cpp engine are optional: when one fails (no key, outage,
        # quota, server not running), Ollama answers.
        print('pipeline: %s failed (%s); using the local model' % (provider, str(e)[:200]), flush=True)
        provider, model = 'ollama', LOCAL_MODEL
        raw, usage = llm(system_message(), build_input(req, draft), RESPONSE_SCHEMA, provider, model)
    if provider not in LOCAL_PROVIDERS:
        usage['cost_usd'] = round(record_spend(usage), 5)
    execution_id = 'native-' + uuid.uuid4().hex[:8]
    xml = to_xml(raw, req, draft, optimizer_ms, execution_id, usage['model'], provider)
    total = int((time.time() - started) * 1000)
    return xml, {'optimizer_ms': optimizer_ms, 'draft_ms': total - optimizer_ms, 'total_ms': total, **usage}
