import json, os, re, sqlite3, threading, time, uuid, urllib.request, urllib.error
import try_inputs
import try_judge
import saved_library
from core import convert as core_convert, files as source_files, mcp as core_mcp, ocr, store as core_store
import xml.etree.ElementTree as ET
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from contextlib import contextmanager

# Chat tab (2026-11-10): the chatbot in chat/ runs on the host (127.0.0.1:8791, it needs your indexed
# documents and the GPU). The workbench passes the tab's requests through, so one page and one
# tailnet address serve both. Empty CHAT_URL hides the tab.
CHAT_URL=os.environ.get('CHAT_URL','http://host.docker.internal:8791').rstrip('/')


def chat_proxy(method,path,body=None):
    """Forward a Chat tab request to the chatbot. Returns (status, json)."""
    if not CHAT_URL:return 404,{'error':'Chat is turned off (CHAT_URL is empty).'}
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(CHAT_URL+path,data=data,method=method,
                               headers={'Host':'127.0.0.1:8791','Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=600) as r:return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:return e.code,json.loads(e.read())
        except ValueError:return e.code,{'error':'Chat error %d'%e.code}
    except (urllib.error.URLError,OSError):
        return 503,{'error':'The chat is not running. Start it on this PC with: python chat/web.py'}
import pipeline

BASE = Path(__file__).parent
DATA = Path(os.environ.get('HARNESS_DATA', str(BASE / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'history.sqlite'
N8N_DB = os.environ.get('N8N_DB', '/n8n-data/database.sqlite')
MCP_URL = os.environ.get('MCP_URL', 'http://host.docker.internal:8081/mcp')
MODES = ['iterate-prompt', 'optimize-user-prompt', 'optimize-system-prompt']
# Reported by /api/status. Keep in step with the n8n Ollama nodes and the
# optimizer compose file; overridable without rebuilding the image.
ASSISTANT_MODEL = os.environ.get('ASSISTANT_MODEL', 'qwen3.5-assistant-fast:4b')
OPTIMIZER_MODEL = os.environ.get('OPTIMIZER_MODEL', 'qwen3.5-assistant-fast:4b')
NUM_CTX = int(os.environ.get('NUM_CTX', '65536'))
NUM_PREDICT = int(os.environ.get('NUM_PREDICT', '8192'))
# 150,000 characters is about 40-45k tokens of real text: with the system message, request,
# optimizer draft and an 8K-token answer it fits the 64K context (measured 2026-10-13:
# 5.6 GB VRAM, ~2,400 tokens/s prefill, facts early/middle/late recalled). Each attempt runs in
# its own n8n memory session, so the material is sent once per attempt, never replayed.
SOURCE_LIMIT = int(os.environ.get('SOURCE_LIMIT', '150000'))
CHARS_PER_TOKEN = 3.5   # conservative: English prose is ~4 characters per token, tables nearer 3
CONVERT_TYPES = core_convert.TYPES
CONVERT_MAX_BYTES = core_convert.MAX_BYTES
# Content judge (QA_JUDGE=on): a model review of whether the delivered prompt does what was
# asked. Off unless validated; see bench/judge_eval.py.
QA_JUDGE = os.environ.get('QA_JUDGE', 'off').lower() == 'on'
# Extra names this page answers on, e.g. the PC's Tailscale name (served by `tailscale serve`).
# n8n (default) or native: harness/pipeline.py, the same workflow without n8n.
PIPELINE = os.environ.get('PIPELINE', 'n8n').strip().lower()
TAILNET_HOSTS = [h.strip().lower() for h in os.environ.get('TAILNET_HOSTS', '').split(',') if h.strip()]
LOCAL_HOSTS = ['127.0.0.1:8789', 'localhost:8789'] + TAILNET_HOSTS
LOCAL_ORIGINS = ['http://127.0.0.1:8789', 'http://localhost:8789'] + ['https://' + h for h in TAILNET_HOSTS]
JUDGE_URL = os.environ.get('JUDGE_URL', 'http://host.docker.internal:11434/api/generate')

db = core_store.opener(DB)          # one short connection per use (core/store.py)

def init():
    with db() as c:
        core_store.prepare(c)          # rollback journal, not WAL (see core/store.py)
        c.execute('''CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, created REAL, title TEXT, prompt TEXT, mode TEXT, requirements TEXT,
          session_id TEXT, parent_id TEXT, source TEXT, status TEXT, xml TEXT DEFAULT '',
          result TEXT DEFAULT '', optimizer_draft TEXT DEFAULT '', execution_id TEXT DEFAULT '',
          optimizer_ms INTEGER DEFAULT 0, elapsed REAL DEFAULT 0, error TEXT DEFAULT '',
          pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0, started REAL DEFAULT 0)''')
        core_store.add_columns(c, 'runs', [('questions',"'[]'"),('response_kind',"'prompt'"),('context_prompt',"''"),('qa',"''"),
                                           ('source_material',"''"),('stage',"''"),('destination',"''"),('assumptions',"'[]'"),
                                           ('open_questions',"'[]'"),('pack_id',"''"),('pack_mode',"''"),('pack',"''"),('quality',"''")])
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        try_inputs.init(c)
        saved_library.init(c)
        core_mcp.init_log(c)           # MCP call log table (core/mcp.py)
        # A prompt run cut off by a restart (Docker Desktop restarting, an update) has no side
        # effects, so a recent one is re-queued once. Library commands (they save into n8n) and
        # packs still stop as "interrupted" for a person to review.
        for r in c.execute("SELECT id,prompt,error FROM runs WHERE status='running' AND started>? "
                           "AND IFNULL(pack_id,'')='' AND IFNULL(response_kind,'')!='pack'", (time.time() - 3600,)).fetchall():
            if not LIBRARY_COMMAND.search((r['prompt'] or '').strip()) and 'Re-queued' not in (r['error'] or ''):
                c.execute("UPDATE runs SET status='queued',stage='',error='Re-queued after a server restart.' WHERE id=?", (r['id'],))
        c.execute("UPDATE runs SET status='interrupted',error='Server restarted during this run. Review the n8n execution before retrying.' WHERE status='running'")

def n8n_read():
    return core_store.read_only(N8N_DB)

def parse_xml(xml):
    root = ET.fromstring(xml)
    if root.tag != 'prompt_assistant': raise ValueError('Unexpected XML response')
    opt = root.find('optimizer'); execution = root.find('execution')
    if opt is None or opt.get('status') != 'success': raise ValueError('Missing successful MCP evidence')
    result = root.findtext('result', '')
    if not result.strip(): raise ValueError('Empty final answer')
    questions=[q.text.strip() for q in root.findall('questions/question') if q.text and q.text.strip()]
    assumptions=[a.text.strip() for a in root.findall('assumptions/assumption') if a.text and a.text.strip()]
    open_questions=[q.text.strip() for q in root.findall('open_questions/question') if q.text and q.text.strip()]
    return dict(result=result, optimizer_draft=opt.findtext('draft', ''), assumptions=assumptions, open_questions=open_questions,
                questions=questions,response_kind='questions' if questions else root.get('kind','prompt'),
                execution_id=execution.get('id', '') if execution is not None else '',
                optimizer_ms=int(opt.get('duration_ms', 0)))

def unflatten(text):
    values = json.loads(text); memo = {}
    def deref(i):
        if i in memo: return memo[i]
        v = values[i]
        if isinstance(v, dict):
            out = {}; memo[i] = out
            out.update({k: resolve(x) for k,x in v.items()}); return out
        if isinstance(v, list):
            out = []; memo[i] = out; out.extend(resolve(x) for x in v); return out
        return v
    def resolve(v): return deref(int(v)) if isinstance(v, str) and v.isdigit() else v
    return deref(0)

def import_history():
    """Copy finished n8n executions into the history (only while n8n's database is mounted)."""
    if not os.path.exists(N8N_DB):
        return 0
    imported = 0
    with n8n_read() as nc, db() as c:
        rows = nc.execute('''SELECT e.id,e.startedAt,d.data FROM execution_entity e JOIN execution_data d
          ON d.executionId=e.id WHERE e.workflowId=? AND e.status='success' ORDER BY e.id DESC LIMIT 200''', ('PromptAssistant20260909',)).fetchall()
        known = {r[0] for r in c.execute("SELECT execution_id FROM runs WHERE execution_id != ''")}
        for row in rows:
            if str(row['id']) in known: continue
            try:
                runs = unflatten(row['data'])['resultData']['runData']
                original = runs['Prompt Assistant Chat'][0]['data']['main'][0][0]['json']
                prompt = original.get('chatInput', '')
                try:
                    envelope = json.loads(prompt)
                    if envelope.get('harnessVersion') == 1: continue
                except (ValueError, AttributeError): pass
                output = runs['Display Complete Prompt'][0]['data']['main'][0][0]['json']['output']
                if not prompt or not output: continue
                try:
                    details = parse_xml(output)
                    stored_xml = output
                except (ValueError, ET.ParseError):
                    details = {'result': output}
                    stored_xml = ''
                created = runs['Prompt Assistant Chat'][0]['startTime'] / 1000
                c.execute('''INSERT OR IGNORE INTO runs (id,created,title,prompt,mode,requirements,session_id,parent_id,source,status,xml,result,execution_id,questions,response_kind)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', ('n8n-'+str(row['id']),created,prompt[:85],prompt,'iterate-prompt','',original.get('sessionId',''),None,'n8n history','success',stored_xml,details['result'],str(row['id']),json.dumps(details.get('questions',[])),details.get('response_kind','prompt')))
                imported += 1
            except (KeyError, IndexError, TypeError, ValueError, ET.ParseError): continue
    return imported

SETTINGS_DEFAULTS = {'qa_default': True, 'defaults_text': ''}


def settings():
    """The user's standing preferences, added to every request (see harness_notes)."""
    out = dict(SETTINGS_DEFAULTS)
    with db() as c:
        for key, value in c.execute('SELECT key, value FROM settings'):
            if key in out:
                out[key] = json.loads(value)
    return out


def save_settings(body):
    with db() as c:
        if 'qa_default' in body:
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('qa_default', json.dumps(bool(body['qa_default']))))
        if 'defaults_text' in body:
            text = str(body['defaults_text'] or '').strip()[:2000]
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('defaults_text', json.dumps(text)))
    return settings()


def library():
    return saved_library.rows(db)

def request_json(url, body=None, timeout=10, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data=response.read().decode()
        if 'text/event-stream' in response.headers.get('Content-Type',''):
            data=[line[6:] for line in data.splitlines() if line.startswith('data: ')][-1]
        return json.loads(data), dict(response.headers)

def mcp_status():
    try:
        headers={'Accept':'application/json, text/event-stream'}
        result, h = request_json(MCP_URL, {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'prompt-workbench','version':'1.0'}}}, headers=headers)
        session=next((v for k,v in h.items() if k.lower()=='mcp-session-id'),None)
        if session: headers['mcp-session-id']=session
        # The server supports stateless requests; initialize and tools/list are read-only.
        tools,_=request_json(MCP_URL,{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},headers=headers)
        return {'connected':True,'server':result['result']['serverInfo'],'tools':[{'name':t['name'],'description':t.get('description','')} for t in tools['result']['tools']]}
    except Exception as e: return {'connected':False,'error':str(e),'tools':[]}

def create_run(body, source):
    prompt=body.get('prompt'); mode=body.get('mode','iterate-prompt')
    if not isinstance(prompt,str) or not prompt.strip(): raise ValueError('Enter a prompt')
    if mode not in MODES: raise ValueError('Unknown MCP tool')
    requirements=body.get('requirements','')
    if not isinstance(requirements,str): raise ValueError('Requirements must be text')
    source_material=body.get('source_material','') or ''
    if not isinstance(source_material,str): raise ValueError('Source material must be text')
    source_material=source_material.strip()[:SOURCE_LIMIT]
    rid=body.get('request_id') or str(uuid.uuid4())
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',rid): raise ValueError('Invalid request ID')
    destination=body.get('destination') or 'auto'
    if destination!='auto' and destination not in DESTINATIONS: raise ValueError('Unknown destination. Use: auto, '+', '.join(DESTINATIONS))
    pack_mode=body.get('pack') or 'auto'
    if pack_mode not in ('auto','on','off'): raise ValueError('pack must be auto, on or off')
    quality=body.get('quality') or ''
    if quality not in ('','local','api'): raise ValueError('quality must be local or api')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
        if old:
            if (old['prompt'],old['mode'],old['requirements']) != (prompt,mode,requirements): raise ValueError('Request ID already used with different input')
            return dict(old)
        if c.execute("SELECT count(*) FROM runs WHERE status IN ('running','queued')").fetchone()[0]>=20: raise OverflowError('Queue is full; try again later')
        parent=body.get('parent_id')
        previous=c.execute('SELECT * FROM runs WHERE id=?',(parent,)).fetchone() if parent else None
        if parent and not previous: raise ValueError('Parent prompt not found')
        session=previous['session_id'] if previous else str(uuid.uuid4())
        context_prompt=prompt
        # Revisions and answers keep the parent's material unless new material is given.
        if not source_material and previous: source_material=previous['source_material'] or ''
        # Follow-ups run where their parent runs; new requests are detected from their text.
        if destination=='auto':
            destination=(previous['destination'] if previous and previous['destination'] else '') or detect_destination(prompt+'\n'+requirements)
        if previous and previous['response_kind']=='questions':
            context_prompt='Original request:\n'+(previous['context_prompt'] or previous['prompt'])+'\n\nQuestions you asked:\n'+previous['questions']+'\n\nUser answers:\n'+prompt+'\n\nNow write the complete final prompt using these answers. Do not repeat answered questions.'
        # Follow-ups keep the parent's model choice unless one is given.
        if not quality and previous: quality=previous['quality'] or ''
        c.execute('''INSERT INTO runs (id,created,title,prompt,mode,requirements,session_id,parent_id,source,status,context_prompt,source_material,destination,pack_mode,quality)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(rid,time.time(),prompt[:85],prompt,mode,requirements,session,parent,source,'queued',context_prompt,source_material,destination,pack_mode,quality))
        return dict(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

def answer_run(rid, answers, request_id, source):
    """Queue the follow-up run that carries the user's answers to a questions reply."""
    with db() as c:
        previous = c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
    open_qs = json.loads(previous['open_questions'] or '[]') if previous and previous['status'] == 'success' else []
    if previous and previous['response_kind'] == 'prompt' and open_qs:
        # The prompt is already written; answers to the questions it would have asked refine it.
        # Answer any of them: a blank answer skips that question.
        if not isinstance(answers, list) or len(answers) != len(open_qs) or any(not isinstance(a, str) for a in answers):
            raise ValueError('Send one answer per question; leave an answer blank to skip that question')
        pairs = [(q, a.strip()) for q, a in zip(open_qs, answers) if a.strip()]
        if not pairs:
            raise ValueError('Answer at least one question')
        req = 'Apply the user\'s answers to these questions:\n' + '\n'.join('Question: %s\nAnswer: %s' % p for p in pairs)
        return create_run({'prompt': previous['result'], 'mode': 'iterate-prompt', 'requirements': req,
                           'parent_id': rid, 'request_id': request_id}, source)
    if not previous or previous['status'] != 'success' or previous['response_kind'] != 'questions':
        raise ValueError('This run has no pending questions')
    questions = json.loads(previous['questions'])
    if (not isinstance(answers, list) or len(answers) != len(questions)
            or any(not isinstance(a, str) or not a.strip() for a in answers)):
        raise ValueError('Answer each question or enter "Use your best judgment"')
    prompt = '\n\n'.join('Question: ' + q + '\nAnswer: ' + a.strip() for q, a in zip(questions, answers))
    return create_run({'prompt': prompt, 'mode': 'iterate-prompt', 'requirements': previous['requirements'],
                       'parent_id': rid, 'request_id': request_id}, source)


def execution_failure(request_id, fallback):
    """Find only this request's failed execution; never expose another run's data."""
    details = dict(error=str(fallback), execution_id='', optimizer_draft='', optimizer_ms=0)
    if PIPELINE == 'native':            # n8n is retired: there is no execution history to look in
        return details
    try:
        with n8n_read() as c:
            rows = c.execute('''SELECT e.id,d.data FROM execution_entity e JOIN execution_data d
              ON d.executionId=e.id WHERE e.workflowId=? AND e.status='error'
              ORDER BY e.id DESC LIMIT 50''', ('PromptAssistant20260909',)).fetchall()
        for row in rows:
            result = unflatten(row['data']).get('resultData', {})
            runs = result.get('runData', {})
            try:
                original = runs['Prompt Assistant Chat'][0]['data']['main'][0][0]['json']
                envelope = json.loads(original['chatInput'])
                if envelope.get('harnessVersion') != 1 or envelope.get('requestId') != request_id: continue
            except (KeyError, IndexError, TypeError, ValueError): continue
            node = result.get('lastNodeExecuted', 'Workflow')
            message = result.get('error', {}).get('message') or str(fallback)
            details.update(error=f'{node}: {message}', execution_id=str(row['id']))
            opt = runs.get('Required Prompt Optimizer MCP', [{}])[0]
            if opt.get('executionStatus') == 'success':
                output = opt.get('data', {}).get('main', [[{}]])[0][0].get('json', {})
                details.update(optimizer_draft='\n'.join(x['text'] for x in output.get('content', []) if x.get('type') == 'text' and isinstance(x.get('text'), str)), optimizer_ms=opt.get('executionTime', 0))
            break
    except (sqlite3.Error, KeyError, IndexError, TypeError, ValueError): pass
    return details

# ---------------------------------------------------------------------------
# Quality assurance runs on every result before it is stored. A failing result
# gets one revision pass, with the exact issues sent to the drafting agent only
# (the optimizer would echo them). The review is stored with the run, embedded
# in its XML, and shown to the user whether it passed or not.
# ---------------------------------------------------------------------------
# QA rules and request analysis live in core/qa.py (moved 2026-11-08, unchanged).
from core.qa import *  # noqa: F401,F403 (PLACEHOLDER, LIBRARY_COMMAND, qa_review, ... as before)
from core.qa import _field, _fact_keys  # noqa: F401 (underscore names are not star-imported)


def convert_document(filename, data):
    """Turn an uploaded file into text (core/convert.py) with this workbench's source limit."""
    return core_convert.convert_document(filename, data, SOURCE_LIMIT, CONVERT_MAX_BYTES)


def ocr_document(filename, data, page):
    """Transcribe one scanned page or picture (core/convert.py) with the assistant model."""
    return core_convert.ocr_document(filename, data, page, JUDGE_URL, ASSISTANT_MODEL, NUM_CTX, CONVERT_MAX_BYTES)


# v3: only omissions and contradictions. Echoes and leaked internal text are caught by
# deterministic checks in qa_review; asking a 4B model for them (v2) flagged 9 of 23
# good prompts. The model lists what was asked, then checks each item; the verdict is
# derived in code. v1 (direct verdict) caught 3 of 6 omissions.
JUDGE_SCHEMA = {
    'type': 'object',
    'properties': {
        'asked_for': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 6},
        'missing': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3},
    },
    'required': ['asked_for', 'missing'],
}
JUDGE_SYSTEM = """You review a prompt that was written for a user. The user asked for a prompt for some task (or asked for their prompt to be improved). The DELIVERED PROMPT will be given to another AI model as-is.

Step 1 - asked_for: list what the user explicitly asked the prompt's task to do or include. Use only things the user actually wrote. Do not add best practices or things you think would be good.
Leave out instructions about this tool rather than about the task, for example: use the optimizer, display or return the result, return XML, do not save, no questions, do not optimize.

Step 2 - missing: for each asked_for item, check the DELIVERED PROMPT. Put an item in missing only if the delivered prompt clearly leaves it out or contradicts it. A paraphrase or a more specific version counts as covered. Extra detail the user did not ask for is fine.

When unsure, leave missing empty."""


WORKFLOW_ITEM = re.compile(r'optimi[sz]e|optimizer|\bdisplay\b|\breturn (?:its|the|complete)|\bxml\b|harness'
                           r'|\bsave\b|library|no questions|clarification questions', re.I)


def judge_content(request, requirements, material, delivered, timeout=180):
    """Ask the model whether the delivered prompt does what was asked. Returns verdict, problems, ms."""
    parts = ['USER REQUEST:\n' + request]
    if requirements:
        parts.append('USER REFINEMENTS:\n' + requirements)
    if material:
        parts.append('SOURCE MATERIAL THE USER ATTACHED (excerpt):\n' + material[:3000])
    parts.append('DELIVERED PROMPT:\n' + delivered)
    body = {'model': ASSISTANT_MODEL, 'system': JUDGE_SYSTEM, 'prompt': '\n\n'.join(parts), 'stream': False,
            'think': False, 'format': JUDGE_SCHEMA, 'keep_alive': -1,
            # Same context size as the pipeline, so Ollama reuses the loaded model instead of reloading it.
            'options': {'num_ctx': NUM_CTX, 'num_predict': 400, 'temperature': 0}}
    started = time.time()
    answer, _ = request_json(JUDGE_URL, body, timeout=timeout)
    review = json.loads(answer.get('response') or '{}')
    # The prompt tells the model to skip tool/workflow instructions; a 4B model still lists
    # them sometimes ("use the optimizer iteration tool"), so enforce it in code.
    missing = [str(m).strip() for m in review.get('missing', []) if str(m).strip() and not WORKFLOW_ITEM.search(str(m))]
    problems = ['Leaves out or contradicts: %s' % m for m in missing][:3]
    return {'verdict': 'fail' if problems else 'ok', 'problems': problems, 'asked_for': review.get('asked_for', []),
            'ms': int((time.time() - started) * 1000)}


def set_stage(rid, stage):
    """Record which step a running request is on, for the live progress line in the UI."""
    with db() as c:
        c.execute('UPDATE runs SET stage=? WHERE id=?', (stage, rid))


def content_review(row, parsed, qa):
    """Add the content judge's verdict to a QA report (only when QA_JUDGE is on).

    Runs only on prompts that already passed the deterministic checks: a failing prompt
    is revised anyway. A judge error never fails the run; the check is marked skipped.
    """
    if not QA_JUDGE or parsed['response_kind'] != 'prompt' or not qa['passed']:
        return
    request = (row['context_prompt'] or row['prompt'] or '').replace(HARNESS_FOLLOWUP, '').strip()
    try:
        j = judge_content(request, row['requirements'] or '', row['source_material'] or '', parsed['result'])
    except Exception as e:
        qa['checks'].append({'check': 'covers_request', 'passed': True, 'skipped': str(e)[:120]})
        return
    qa['checks'].append({'check': 'covers_request', 'passed': j['verdict'] == 'ok', 'ms': j['ms']})
    if j['verdict'] != 'ok':
        qa['issues'].extend(j['problems'])
        qa['passed'] = False


def qa_xml(xml, report):
    """Embed the review in the run's XML so every consumer receives it."""
    from xml.sax.saxutils import escape, quoteattr
    if '</prompt_assistant>' not in xml:
        return xml
    body = ''.join('\n    <check name=%s passed="%s" />' % (quoteattr(c['check']), str(c['passed']).lower())
                   for c in report['checks'])
    body += ''.join('\n    <issue>%s</issue>' % escape(i) for i in report['issues'])
    block = '  <qa passed="%s" revised="%s" attempts="%d">%s\n  </qa>\n' % (
        str(report['passed']).lower(), str(report['revised']).lower(), len(report['attempts']), body)
    return xml.replace('</prompt_assistant>', block + '</prompt_assistant>')


def repair_placeholders(text, names):
    """Turn placeholders the user never wrote into plain [labels]: "{{key_skills}}" -> "[key skills]".

    Used only after the revision, and only when invented placeholders are all that is left.
    Writing first, the 4B model sometimes fills a gap with a new slot even after being told
    not to; a plain label reads as "fill this in" without posing as a template variable.
    The QA report lists every conversion (check placeholders_repaired), so nothing is hidden.
    """
    for ph in names:
        label = re.sub(r'[_\s]+', ' ', ph.strip('{} ')).strip() or 'details'
        text = text.replace(ph, '[' + label + ']')
    return text


PACK_SCHEMA = {'type': 'object', 'required': ['separate_prompts', 'prompts'], 'properties': {
    'separate_prompts': {'type': 'boolean'},
    'prompts': {'type': 'array', 'maxItems': 5, 'items': {'type': 'object', 'required': ['title', 'request'],
                'properties': {'title': {'type': 'string'}, 'request': {'type': 'string'}}}}}}
PACK_SYSTEM = (
    'You decide whether a user wants several separate prompts or one prompt. Split only when the user asks for '
    'prompts that will be used separately (for example one prompt for a slide deck and another for a speaking '
    'script). A single prompt whose output has several parts (a summary plus questions, an agenda plus notes) is '
    'one prompt. When splitting, write one self-contained request per prompt: keep the user\'s wording, every '
    '{{placeholder}} that belongs to it, and the shared context (topic, audience, constraints, how the prompts fit '
    'together) in each one. Titles are 2 to 5 words.')


def plan_pack(row, timeout=120):
    """Ask the model whether the request is several prompts. Returns [(title, request)] or []."""
    request = '\n'.join(x for x in (_field(row, 'prompt'), _field(row, 'requirements')) if x)[:6000]
    body = {'model': ASSISTANT_MODEL, 'system': PACK_SYSTEM, 'prompt': 'USER REQUEST:\n' + request, 'stream': False,
            'think': False, 'format': PACK_SCHEMA, 'keep_alive': -1,
            'options': {'num_ctx': NUM_CTX, 'num_predict': 1500, 'temperature': 0}}
    answer, _ = request_json(JUDGE_URL, body, timeout=timeout)
    plan = json.loads(answer.get('response') or '{}')
    items = [(str(i.get('title', '')).strip()[:60], str(i.get('request', '')).strip())
             for i in plan.get('prompts', []) if isinstance(i, dict)]
    items = [(t or 'Prompt %d' % n, r) for n, (t, r) in enumerate(items, 1) if len(r) >= 20]
    return items if plan.get('separate_prompts') and 2 <= len(items) <= 5 else []


def start_pack(row, items):
    """Queue one child run per prompt; the parent waits and is assembled by finish_pack."""
    now = time.time()
    with db() as c:
        for n, (title, request) in enumerate(items, 1):
            c.execute('''INSERT INTO runs (id,created,title,prompt,mode,requirements,session_id,parent_id,source,status,
                         context_prompt,source_material,destination,pack_mode,pack_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      ('%s-%d' % (row['id'], n), now + n * 0.001, title, request, row['mode'], row['requirements'] or '',
                       str(uuid.uuid4()), None, row['source'], 'queued', request, row['source_material'] or '',
                       row['destination'] or detect_destination(request), 'off', row['id']))
        c.execute("UPDATE runs SET stage='pack', response_kind='pack', pack=? WHERE id=?",
                  (json.dumps([{'title': t, 'run_id': '%s-%d' % (row['id'], n)} for n, (t, _) in enumerate(items, 1)]),
                   row['id']))


def pack_items(c, parent):
    items = json.loads(parent['pack'] or '[]')
    rows = {r['id']: r for r in c.execute('SELECT * FROM runs WHERE pack_id=?', (parent['id'],))}
    return [(i['title'], rows.get(i['run_id'])) for i in items]


def finish_pack(pack_id):
    """When every prompt of a pack has finished, assemble the parent: titled prompts and a combined QA."""
    from xml.sax.saxutils import escape, quoteattr
    with db() as c:
        parent = c.execute('SELECT * FROM runs WHERE id=?', (pack_id,)).fetchone()
        if not parent:
            return
        items = pack_items(c, parent)
        if any(r is None or r['status'] in ('queued', 'running') for _, r in items):
            return
        parts, checks, issues, revised = [], [], [], False
        for title, r in items:
            qa = json.loads(r['qa']) if r['qa'] else {}
            ok = r['status'] == 'success' and r['response_kind'] == 'prompt'
            text = r['result'] if ok else '(no prompt: %s)' % (r['error'] or r['response_kind'] or r['status'])
            parts.append('## %s\n\n%s' % (title, text))
            passed = ok and bool(qa.get('passed'))
            checks.append({'check': 'pack: ' + title, 'passed': passed})
            issues += ['%s: %s' % (title, i) for i in qa.get('issues', [])] if ok else ['%s: no prompt was written' % title]
            revised = revised or bool(qa.get('revised'))
        report = {'passed': not issues, 'checks': checks, 'issues': issues, 'revised': revised, 'attempts': []}
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<prompt_assistant kind="pack">\n'
               + ''.join('  <prompt title=%s run_id=%s>%s</prompt>\n' % (quoteattr(t), quoteattr(r['id']), escape(r['result'] or ''))
                         for t, r in items)
               + '</prompt_assistant>')
        xml = qa_xml(xml, report)
        any_ok = any(r['status'] == 'success' for _, r in items)
        c.execute("UPDATE runs SET status=?, result=?, xml=?, qa=?, response_kind='pack', stage='', elapsed=?, error=? WHERE id=?",
                  ('success' if any_ok else 'error', '\n\n'.join(parts), xml, json.dumps(report),
                   time.time() - (parent['started'] or parent['created']), '' if any_ok else 'No prompt in the pack was written',
                   pack_id))


def harness_notes(row, prefs=None):
    """Guidance for the drafting agent only (never the optimizer): destination and the user's defaults."""
    prefs = prefs or settings()
    dest = _field(row, 'destination', 'chat')
    notes = [DESTINATION_NOTES[dest]] if dest in DESTINATION_NOTES else []
    request = '\n'.join(x for x in (_field(row, 'prompt'), _field(row, 'requirements')) if x)
    material = _field(row, 'source_material').strip()
    if not material and not _field(row, 'parent_id') and is_dangling(_field(row, 'prompt')):
        notes.append('This request refers to a prompt or text that is not included here. Reply with kind '
                     '"questions" and ask the user to paste it.')
    elif not material and not _field(row, 'parent_id') and len(re.findall(r'\w+', request)) < 4:
        # "test", "hello": writing a prompt anyway produced leaked template text (2026-10-04 gate).
        notes.append('This request does not say what the prompt should do. Reply with kind "questions" and ask '
                     'what the prompt is for.')
    if len(material) > FACT_SOURCE_MAX:
        found = key_lines(material)
        if found:
            notes.append('The source material is long (%s characters). These lines from it hold its dates, figures '
                         'and codes, in document order from start to end:\n- %s\nWhen the request asks for such details, '
                         'write them into the prompt itself. Where the downstream model needs the full text, mark it '
                         'with {{source_document}}.' % (format(len(material), ','), '\n- '.join(found)))
    refs = sorted({m.group(0).strip()[:80] for m in UNREADABLE_REF.finditer(request)}) if not material else []
    refs = refs if dest != 'agent' else []
    if refs:
        # Writing first, the model otherwise drops the reference silently (2026-10-04 gate).
        notes.append('The request mentions content you cannot see (%s). Keep any link exactly as written, put a '
                     'clearly named placeholder such as {{source_document}} where that content must be pasted, and '
                     'list it in assumptions.' % ', '.join(refs))
    if prefs.get('qa_default') and not MINIMAL_REQUEST.search(request):
        notes.append(QA_DEFAULT_NOTES.get(dest, QA_DEFAULT_NOTE))
    if prefs.get('defaults_text'):
        notes.append('The user\'s standing preferences for every prompt: ' + prefs['defaults_text'])
    return notes


def fit_material(row, reserve_chars=0):
    """Source material that fits the model's context with everything else in the request.

    Returns (material, trimmed). Space is kept for the answer (NUM_PREDICT), the system message,
    the optimizer draft and the revision feedback (reserve_chars). SOURCE_LIMIT normally keeps
    material well inside this, so trimming is a safety net, reported in the QA panel.
    """
    material = _field(row, 'source_material')
    other = sum(len(_field(row, k)) for k in ('context_prompt', 'prompt', 'requirements'))
    other += 5000 + 8000 + reserve_chars          # system message, optimizer draft, feedback
    room = int((NUM_CTX - NUM_PREDICT - 2000) * CHARS_PER_TOKEN) - other
    if len(material) <= room:
        return material, False
    return material[:max(0, room)] + '\n[... the rest was cut to fit the model\'s context]', True


def attempt(row, qa_feedback='', session=None, material=None):
    """One full pass through n8n, or the native pipeline when PIPELINE=native.
    Raises on transport or contract failure."""
    envelope = {'harnessVersion': 1, 'prompt': row['context_prompt'] or row['prompt'], 'mode': row['mode'],
                'requirements': row['requirements'], 'requestId': row['id'], 'qaFeedback': qa_feedback,
                'sourceMaterial': (row['source_material'] or '') if material is None else material,
                'harnessNotes': harness_notes(row), 'quality': row['quality'] or ''}
    if PIPELINE == 'native':
        xml, timings = pipeline.attempt(envelope)
        print('native pipeline %s: %s' % (row['id'][:8], timings), flush=True)
        return xml, parse_xml(xml)
    # n8n was retired on 2026-10-28. Fail at once with the way back instead of waiting on a
    # webhook that no longer answers.
    raise RuntimeError('PIPELINE=%s is not supported: n8n was retired on 2026-10-28. Use PIPELINE=native, '
                       'or check out the n8n-legacy branch (tag n8n-final) and restore '
                       'backups/n8n_data-20261028-retired.tgz to run the n8n workflow.' % PIPELINE)


def process_next():
    """Claim and run one queued request. Returns False when the queue is empty."""
    with db() as c:
        row = c.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if row:
            c.execute("UPDATE runs SET status='running',started=? WHERE id=?", (time.time(), row['id']))
    if not row:
        return False
    try:
        run_claimed(row)
    except Exception as e:
        # Never leave a run stuck on "running": record why it stopped.
        try:
            with db() as c:
                c.execute("UPDATE runs SET status='error', error=? WHERE id=?",
                          ('Internal error while processing this run: %s' % e, row['id']))
        except Exception:
            pass
        raise
    return True


def run_claimed(row):
    if source_files.has_pending(row['source_material']):
        # Scanned pages of files sent with create_prompt: read them now, before anything uses the material.
        # Not cut to SOURCE_LIMIT here: fit_material trims an oversize request and says so in the QA panel.
        set_stage(row['id'], 'reading files')
        material = source_files.read_pending(row['source_material'], ocr_document, DATA)
        with db() as c:
            c.execute('UPDATE runs SET source_material=? WHERE id=?', (material, row['id']))
            row = c.execute('SELECT * FROM runs WHERE id=?', (row['id'],)).fetchone()
    if not _field(row, 'pack_id') and not _field(row, 'parent_id'):
        mode = _field(row, 'pack_mode', 'auto')
        hinted = PACK_HINT.search('\n'.join(x for x in (row['prompt'], row['requirements']) if x))
        if mode == 'on' or (mode == 'auto' and hinted):
            set_stage(row['id'], 'planning')
            try:
                items = plan_pack(row)
            except Exception:
                items = []          # planning is an extra; on any failure the request runs as one prompt
            if items:
                start_pack(row, items)
                return
    try:
        _run_single(row)
    finally:
        if _field(row, 'pack_id'):
            finish_pack(row['pack_id'])


def _run_single(row):
    started = time.time()
    if PIPELINE == 'native' and LIBRARY_COMMAND.search((row['prompt'] or '').strip()):
        # The library lives in this database now (it was an n8n tool): answer the command here.
        parent = None
        if _field(row, 'parent_id'):
            with db() as c:
                p = c.execute('SELECT title,result FROM runs WHERE id=?', (row['parent_id'],)).fetchone()
            parent = dict(p) if p else None
        reply = saved_library.command(db, row['prompt'], parent)
        with db() as c:
            c.execute("UPDATE runs SET status='success',result=?,xml='',response_kind='message',questions='[]',qa='',"
                      "elapsed=?,error='',stage='' WHERE id=?", (reply, time.time() - started, row['id']))
        return
    may_retry = not LIBRARY_COMMAND.search((row['prompt'] or '').strip())
    feedback, best, history, last_error = '', None, [], None
    material, trimmed = fit_material(row, reserve_chars=8000)
    for pass_no in (1, 2):
        # Prompt runs get a fresh n8n memory session per attempt: the request already carries
        # everything (original request, answers, previous prompt), and n8n's memory would
        # otherwise replay the earlier attempt with its full source material. Library commands
        # keep the shared session, because "save it" looks back through it.
        session = ('run-%s-%d' % (row['id'], pass_no)) if may_retry else None
        try:
            set_stage(row['id'], 'drafting' if pass_no == 1 else 'revising')
            xml, parsed = attempt(row, feedback, session, material)
        except Exception as e:
            last_error = e
            history.append({'attempt': pass_no, 'error': str(e)[:300]})
            if pass_no == 1 and may_retry and TRANSIENT.search(str(e)):
                feedback = QA_CONTRACT_NOTE
                time.sleep(2)
                continue
            break
        qa = qa_review(row, parsed, settings())
        set_stage(row['id'], 'checking')
        content_review(row, parsed, qa)
        history.append({'attempt': pass_no, 'passed': qa['passed'], 'issues': qa['issues']})
        # Keep the better attempt; a tie goes to the revision.
        if best is None or len(qa['issues']) <= len(best[2]['issues']):
            best = (xml, parsed, qa)
        if qa['passed'] or not may_retry:
            break
        # The previous attempt goes along explicitly (memory no longer carries it), so the
        # revision corrects it instead of starting over.
        feedback = ('\n'.join('- ' + i for i in qa['issues']) +
                    '\n\nYour previous attempt, to correct (keep what is right):\n' + parsed['result'][:6000])
    if best:
        xml, parsed, qa = best
        if parsed['response_kind'] == 'prompt' and qa.get('invented') and all(
                c['passed'] or c['check'] in ('no_invented_placeholders', 'covers_request') for c in qa['checks']):
            names = qa['invented']
            parsed = dict(parsed, result=repair_placeholders(parsed['result'], names))
            xml = repair_placeholders(xml, names)
            qa = qa_review(row, parsed, settings())
            content_review(row, parsed, qa)   # judge the text actually delivered
            qa['checks'].append({'check': 'placeholders_repaired', 'passed': True,
                                 'note': 'Converted %s to plain [labels]' % ', '.join(names)})
            history.append({'attempt': 'repair', 'passed': qa['passed'], 'issues': qa['issues'],
                            'converted': names})
        wanted = requested_facts(_field(row, 'source_material'), '\n'.join(
            x for x in (row['context_prompt'], row['prompt'], row['requirements']) if x))
        missing = [f for f in wanted if f.lower() not in parsed['result'].lower()]
        if parsed['response_kind'] == 'prompt' and missing and any(
                c['check'] == 'requested_facts_included' and not c['passed'] for c in qa['checks']):
            from xml.sax.saxutils import escape
            fixed = append_requested_facts(parsed['result'], _field(row, 'source_material'), missing)
            if fixed != parsed['result']:
                added = fixed[len(parsed['result'].rstrip()):]
                xml = xml.replace('</result>', escape(added) + '</result>', 1)
                parsed = dict(parsed, result=fixed)
                qa = qa_review(row, parsed, settings())
                content_review(row, parsed, qa)
                qa['checks'].append({'check': 'facts_appended', 'passed': True,
                                     'note': 'Added the requested details from the material: %s' % ', '.join(missing[:12])})
                history.append({'attempt': 'facts', 'passed': qa['passed'], 'issues': qa['issues'], 'added': missing[:12]})
        # Assumptions are written with the prompt; keep them in step with what was finally delivered.
        stale = sorted({ph for a in parsed.get('assumptions', []) for ph in PLACEHOLDER.findall(a)}
                       - set(PLACEHOLDER.findall(parsed['result'])))
        if stale:
            parsed = dict(parsed, assumptions=[repair_placeholders(a, stale) for a in parsed['assumptions']])
        if trimmed:
            qa['checks'].append({'check': 'material_fits', 'passed': True,
                                 'note': 'Source material was cut to %s characters to fit the model\'s context'
                                         % format(len(material), ',')})
        report = {'passed': qa['passed'], 'checks': qa['checks'], 'issues': qa['issues'],
                  'revised': len(history) > 1, 'attempts': history}
        xml = qa_xml(xml, report)
        with db() as c:
            c.execute("UPDATE runs SET status='success',xml=?,result=?,optimizer_draft=?,execution_id=?,optimizer_ms=?,"
                      "elapsed=?,questions=?,response_kind=?,qa=?,assumptions=?,open_questions=?,error='' WHERE id=?",
                      (xml, parsed['result'], parsed['optimizer_draft'], parsed['execution_id'], parsed['optimizer_ms'],
                       time.time() - started, json.dumps(parsed['questions']), parsed['response_kind'],
                       json.dumps(report), json.dumps(parsed.get('assumptions', [])),
                       json.dumps(parsed.get('open_questions', [])), row['id']))
    else:
        failure = execution_failure(row['id'], last_error)
        report = {'passed': False, 'checks': [], 'issues': ['No usable result was produced.'],
                  'revised': len(history) > 1, 'attempts': history}
        with db() as c:
            c.execute("UPDATE runs SET status='error',error=?,execution_id=?,optimizer_draft=?,optimizer_ms=?,elapsed=?,qa=? WHERE id=?",
                      (failure['error'], failure['execution_id'], failure['optimizer_draft'], failure['optimizer_ms'],
                       time.time() - started, json.dumps(report), row['id']))


def worker():
    """Process the queue forever. One failed run must never stop the whole queue."""
    import traceback
    while True:
        try:
            if not process_next():
                time.sleep(1)
        except Exception:
            traceback.print_exc()
            time.sleep(2)


# ---------------------------------------------------------------------------
# MCP server: the workbench as tools for any MCP client (Claude Code, Claude
# Desktop, ...). Streamable HTTP, plain JSON responses, local only. Every tool
# goes through the same queue, optimizer, drafting agent and QA as the UI.
# ---------------------------------------------------------------------------
MCP_PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
MCP_WAIT_SECONDS = 150          # MCP clients time out; longer runs return a run id to poll
MCP_MODES = {'refine': 'iterate-prompt', 'user': 'optimize-user-prompt', 'system': 'optimize-system-prompt'}
_STR = {'type': 'string'}
# Kept short on purpose: the tool list is loaded into every Claude Code session (2026-10-14:
# 4,499 characters for 7 tools -> about half). Run ids may be shortened to 8 characters.
MCP_TOOLS = [
    {'name': 'create_prompt',
     'description': 'Write a QA-checked prompt locally. Returns it (show verbatim) or questions for answer_questions.',
     'inputSchema': {'type': 'object', 'required': ['request'], 'properties': {
         'request': {'type': 'string', 'description': 'The task, or a prompt to improve. Keep {{placeholders}}.'},
         'mode': {'enum': list(MCP_MODES), 'description': 'refine (default), user (one task), system'},
         'refinements': _STR,
         'source_material': {'type': 'string', 'description': 'Text to base it on'},
         'destination': {'enum': ['auto'] + list(DESTINATIONS)},
         'pack': {'enum': ['auto', 'on', 'off']},
         'quality': {'enum': ['local', 'api'], 'description': 'api = paid model'}}}},
    {'name': 'answer_questions',
     'description': 'Answer a run\'s questions (all, or any optional ones; "" skips).',
     'inputSchema': {'type': 'object', 'required': ['run_id', 'answers'], 'properties': {
         'run_id': _STR, 'answers': {'type': 'array', 'items': _STR}}}},
    {'name': 'revise_prompt',
     'description': 'Revise a finished prompt with new instructions.',
     'inputSchema': {'type': 'object', 'required': ['run_id', 'instructions'], 'properties': {
         'run_id': _STR, 'instructions': _STR}}},
    {'name': 'find_runs',
     'description': 'One run by run_id, or recent runs (optional query).',
     'inputSchema': {'type': 'object', 'properties': {
         'run_id': _STR, 'query': _STR, 'limit': {'type': 'integer', 'maximum': 25},
         'verbose': {'type': 'boolean'}}}},
    {'name': 'find_saved_prompts',
     'description': 'Search the saved prompt library.',
     'inputSchema': {'type': 'object', 'properties': {'query': _STR}}},
    {'name': 'pin_prompt',
     'description': 'Pin a prompt (optional title) as a slash command; pinned=false unpins.',
     'inputSchema': {'type': 'object', 'required': ['run_id'], 'properties': {
         'run_id': _STR, 'title': _STR, 'pinned': {'type': 'boolean'}}}},
]
# Older clients may still call the tools find_runs replaced; they work but are not listed.
MCP_ALIASES = {'get_prompt_run': 'find_runs', 'list_prompt_runs': 'find_runs'}
# Behaviour hints (MCP 2025-06-18) so clients can auto-approve the read-only tools. Nothing here
# reaches outside this computer, and nothing deletes: runs are only added or pinned.
_HINTS = {'create_prompt': (False, False), 'answer_questions': (False, False), 'revise_prompt': (False, False),
          'find_runs': (True, True), 'find_saved_prompts': (True, True), 'pin_prompt': (False, True)}
for _t in MCP_TOOLS:
    _ro, _idem = _HINTS[_t['name']]
    # Only hints that differ from the MCP defaults (readOnly/idempotent default to false;
    # destructive/openWorld default to true, so their false values must be sent).
    _t['annotations'] = dict({'destructiveHint': False, 'openWorldHint': False},
                             **({'readOnlyHint': True} if _ro else {}), **({'idempotentHint': True} if _idem else {}))


def _resolve(rid):
    """Full run id from a full id or a unique prefix of at least 6 characters."""
    rid = str(rid or '').strip()
    with db() as c:
        if c.execute('SELECT 1 FROM runs WHERE id=?', (rid,)).fetchone():
            return rid
        if len(rid) >= 6 and re.fullmatch(r'[A-Za-z0-9_-]+', rid):
            hits = [r[0] for r in c.execute('SELECT id FROM runs WHERE id LIKE ? LIMIT 2', (rid + '%',))]
            if len(hits) == 1:
                return hits[0]
            if hits:
                raise ValueError('Run id %s is ambiguous; give more characters' % rid)
    raise ValueError('No run with id %s' % rid)


def _wait_for(rid):
    deadline = time.time() + MCP_WAIT_SECONDS
    while True:
        with db() as c:
            row = c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
        if not row or row['status'] not in ('queued', 'running') or time.time() > deadline:
            return row
        time.sleep(1)


def _describe(row, verbose=False):
    """Compact tool output for one run: a header line, then only what is present."""
    if row is None:
        return 'Run not found.'
    rid = row['id'][:8]
    if row['status'] in ('queued', 'running'):
        return 'run %s: still running. Call find_runs with run_id %s in a moment.' % (rid, rid)
    if row['status'] != 'success':
        return 'run %s: error: %s' % (rid, row['error'] or 'unknown')
    if row['response_kind'] == 'pack':
        qa = json.loads(row['qa']) if row['qa'] else {}
        with db() as c:
            items = pack_items(c, row)
        head = 'run %s | pack of %d prompts | %s' % (rid, len(items), 'QA passed' if qa.get('passed') else
                                                      'QA FLAGGED, review before use: ' + '; '.join(qa.get('issues', [])))
        return head + '\n' + '\n\n'.join(
            '## %s (run %s, %s)\n%s' % (t, r['id'][:8] if r else '?', ('QA ok' if r and r['qa'] and json.loads(r['qa']).get('passed')
                                                                     else 'QA FLAGGED' if r and r['qa'] else (r['status'] if r else 'queued')),
                                       (r['result'] if r else '') or '') for t, r in items)
    if row['response_kind'] == 'questions':
        qs = json.loads(row['questions'] or '[]')
        return ('run %s needs answers before it writes the prompt. Ask the user, then call answer_questions '
                '(run_id %s, one answer per question):\n' % (rid, rid)
                + '\n'.join('%d. %s' % (i + 1, q) for i, q in enumerate(qs)))
    qa = json.loads(row['qa']) if row['qa'] else None
    if not qa:
        verdict = 'QA not run'
    elif qa['passed']:
        verdict = 'QA passed' + (' after revision' if qa.get('revised') else '')
    else:
        verdict = 'QA FLAGGED, review before use: ' + '; '.join(qa['issues'])
    head = ['run %s' % rid, verdict]
    dest = _field(row, 'destination')
    if dest in DESTINATIONS and dest != 'chat':
        head.append('runs in ' + DESTINATIONS[dest]['label'])
    lines = [' | '.join(head)]
    if verbose and qa:
        lines.append('checks: ' + ', '.join('%s %s' % (c['check'], 'ok' if c['passed'] else 'FAIL') for c in qa['checks']))
    assumptions = json.loads(_field(row, 'assumptions', '[]'))
    if assumptions:
        lines.append('Assumptions: ' + '; '.join(assumptions))
    open_qs = json.loads(_field(row, 'open_questions', '[]'))
    if open_qs:
        lines.append('Optional questions (answer_questions, "" skips): '
                     + ' '.join('%d. %s' % (i + 1, q) for i, q in enumerate(open_qs)))
    lines.append('---\n' + (row['result'] or ''))
    return '\n'.join(lines)


def _run_line(r):
    qa = json.loads(r['qa']) if r['qa'] else None
    badge = r['status'] if r['status'] != 'success' else (
        'questions' if r['response_kind'] == 'questions' else ('QA ok' if qa and qa['passed'] else 'QA FLAGGED' if qa else 'done'))
    mode = {'iterate-prompt': 'refine', 'optimize-user-prompt': 'user', 'optimize-system-prompt': 'system'}.get(r['mode'], r['mode'])
    dest = r['destination'] if r['destination'] and r['destination'] != 'chat' else ''
    title = ' '.join((r['title'] or '').split())[:60]
    return ' '.join(x for x in (r['id'][:8], time.strftime('%m-%d', time.localtime(r['created'])), mode, badge, dest, '-', title) if x)


def mcp_call(name, args, source='MCP'):
    name = MCP_ALIASES.get(name, name)
    verbose = bool(args.get('verbose'))
    if name == 'create_prompt':
        mode = MCP_MODES.get(args.get('mode') or 'refine')
        if not mode:
            raise ValueError('mode must be one of: ' + ', '.join(MCP_MODES))
        material = args.get('source_material') or ''
        if args.get('file_data'):
            # Files the desktop-app bridge read on the PC (source_files.py): text now, scans at run start.
            material = (source_files.from_uploads(args['file_data'], convert_document, DATA) + '\n\n' + material).strip()
        elif args.get('files'):
            raise ValueError('files works through the Claude desktop app, which reads them on your PC. '
                             'From here, read the file yourself and pass its text as source_material.')
        row = create_run({'prompt': args.get('request', ''), 'mode': mode, 'requirements': args.get('refinements') or '',
                          'source_material': material,
                          'destination': args.get('destination') or 'auto', 'pack': args.get('pack') or 'auto',
                          'quality': args.get('quality') or ''}, source)
        return _describe(_wait_for(row['id']), verbose)
    if name == 'answer_questions':
        row = answer_run(_resolve(args.get('run_id')), args.get('answers'), None, source)
        return _describe(_wait_for(row['id']), verbose)
    if name == 'revise_prompt':
        with db() as c:
            prev = c.execute('SELECT * FROM runs WHERE id=?', (_resolve(args.get('run_id')),)).fetchone()
        if not prev or prev['status'] != 'success' or not prev['result']:
            raise ValueError('No finished prompt with that run id')
        row = create_run({'prompt': prev['result'], 'mode': 'iterate-prompt', 'requirements': args.get('instructions') or '',
                          'parent_id': prev['id']}, source)
        return _describe(_wait_for(row['id']), verbose)
    if name == 'find_runs':
        if args.get('run_id'):
            with db() as c:
                return _describe(c.execute('SELECT * FROM runs WHERE id=?', (_resolve(args['run_id']),)).fetchone(), verbose)
        limit = max(1, min(25, int(args.get('limit') or 10)))
        query = ("SELECT id,created,title,mode,status,response_kind,qa,destination FROM runs "
                 "WHERE archived=0 AND IFNULL(source,'')!='Test' AND IFNULL(pack_id,'')=''")
        params = []
        if args.get('query'):
            query += ' AND (instr(lower(prompt),lower(?))>0 OR instr(lower(result),lower(?))>0)'
            params += [args['query']] * 2
        with db() as c:
            rows = c.execute(query + ' ORDER BY created DESC LIMIT ?', params + [limit]).fetchall()
        return '\n'.join(_run_line(r) for r in rows) or 'No runs found.'
    if name == 'find_saved_prompts':
        rows = library()
        q = (args.get('query') or '').lower()
        rows = [r for r in rows if not q or q in (r['title'] or '').lower() or q in (r['prompt'] or '').lower()]
        return '\n\n'.join('[%s] %s\n%s' % (r['id'], r['title'], r['prompt']) for r in rows[:10]) or 'No saved prompts found.'
    if name == 'pin_prompt':
        rid, pinned = _resolve(args.get('run_id')), args.get('pinned', True) is not False
        title = str(args.get('title') or '').strip()[:200]
        with db() as c:
            row = c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
            if not row or row['status'] != 'success' or row['response_kind'] != 'prompt':
                raise ValueError('No finished prompt with that run id')
            c.execute("UPDATE runs SET pinned=?, title=CASE WHEN ?<>'' THEN ? ELSE title END WHERE id=?",
                      (int(pinned), title, title, rid))
        if not pinned:
            return 'Unpinned %s.' % rid[:8]
        command = next((n for n, e in mcp_prompt_catalog().items() if e['kind'] == 'pinned' and e['text'] == row['result']), None)
        return 'Pinned %s%s.' % (rid[:8], ' as the slash command "%s"' % command if command else '')
    raise KeyError(name)


def mcp_prompt_catalog():
    """Saved library prompts plus pinned, finished runs, keyed by a slash-command-safe name."""
    entries = []
    try:
        for r in library():
            entries.append(('library', str(r['id']), r['title'] or 'saved prompt', r['prompt'] or ''))
    except Exception:
        pass   # n8n database unavailable: still offer pinned runs
    with db() as c:
        for r in c.execute("SELECT id,title,result FROM runs WHERE pinned=1 AND status='success' "
                           "AND response_kind='prompt' AND archived=0 ORDER BY created DESC LIMIT 50"):
            entries.append(('pinned', r['id'], r['title'] or 'pinned prompt', r['result'] or ''))
    catalog, used = {}, set()
    for kind, key, title, text in entries:
        base = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40] or kind
        name, n = base, 2
        while name in used:
            name, n = '%s-%d' % (base, n), n + 1
        used.add(name)
        args = []
        for ph in PLACEHOLDER.findall(text):
            arg = ph.strip('{} ').strip()
            if arg and arg not in args:
                args.append(arg)
        catalog[name] = {'kind': kind, 'title': title, 'text': text, 'args': args}
    return catalog


RECENT_RESOURCES = 15


def mcp_resources():
    """Prompts Claude Code can @-mention: pinned prompts, the saved library, recent finished prompts."""
    out = []
    with db() as c:
        rows = c.execute("SELECT id,title,result,pinned,qa,destination FROM runs WHERE status='success' AND response_kind='prompt' "
                         "AND archived=0 AND IFNULL(source,'')!='Test' AND IFNULL(pack_id,'')='' "
                         "ORDER BY pinned DESC, created DESC LIMIT ?", (RECENT_RESOURCES + 50,)).fetchall()
    pinned = [r for r in rows if r['pinned']]
    recent = [r for r in rows if not r['pinned']][:RECENT_RESOURCES]
    for r in pinned + recent:
        qa = json.loads(r['qa']) if r['qa'] else {}
        title = ' '.join((r['title'] or 'prompt').split())[:70]
        out.append({'uri': 'prompt-workbench://run/' + r['id'][:8], 'name': title, 'mimeType': 'text/markdown',
                    'description': ('Pinned prompt' if r['pinned'] else 'Recent prompt') +
                                   (', QA passed' if qa.get('passed') else ', QA flagged' if qa else ''),
                    '_text': r['result'] or ''})
    try:
        for r in library():
            out.append({'uri': 'prompt-workbench://library/%s' % r['id'], 'name': (r['title'] or 'saved prompt')[:70],
                        'mimeType': 'text/markdown', 'description': 'Saved in your prompt library', '_text': r['prompt'] or ''})
    except Exception:
        pass   # n8n database unavailable: still offer the workbench's own prompts
    return out


# MCP call log and per-client stats live in core/mcp.py (moved 2026-11-08, unchanged).
def mcp_client_label(agent, msg):
    return core_mcp.client_label(agent, msg)


def mcp_logged(msg, agent=''):
    """mcp_handle plus a record of the call. Logging never breaks the call."""
    return core_mcp.logged(mcp_handle, msg, agent, db)


def connector_stats(days=30):
    """Per client: calls, success rate, median time, last seen, last error (for /api/connectors)."""
    return core_mcp.stats(db, days)


def try_prompt(text, values, timeout=300):
    """Run a prompt on the local model with its {{placeholders}} filled in ("Try it" in the page)."""
    values = {str(k): str(v) for k, v in (values or {}).items()}
    filled = PLACEHOLDER.sub(lambda m: values.get(m.group(0).strip('{} ').strip()) or m.group(0), text or '')
    if not filled.strip():
        raise ValueError('Nothing to run')
    body = {'model': ASSISTANT_MODEL, 'prompt': filled, 'stream': False, 'think': False, 'keep_alive': -1,
            'options': {'num_ctx': NUM_CTX, 'num_predict': 3000, 'temperature': 0.3}}
    started = time.time()
    answer, _ = request_json(JUDGE_URL, body, timeout=timeout)
    return {'output': (answer.get('response') or '').strip(), 'ms': int((time.time() - started) * 1000),
            'unfilled': sorted(set(PLACEHOLDER.findall(filled))), 'model': ASSISTANT_MODEL}


# MCP App (2026-11-07): the Claude desktop app shows create_prompt's result in a small panel with
# Copy and Pin (prompt_view.html, MCP Apps 2026-01-26). Declared only to the desktop bridge: Claude
# Code does not render apps, and the declaration would push its tools/list past the size budget.
UI_URI = 'ui://pw/p'
UI_MIME = 'text/html;profile=mcp-app'
UI_HTML = (BASE / 'prompt_view.html').read_text(encoding='utf-8') if (BASE / 'prompt_view.html').exists() else ''


def mcp_tools_for(agent):
    if not UI_HTML or 'prompt-workbench-bridge' not in (agent or '').lower():
        return MCP_TOOLS
    return [dict(t, _meta={'ui': {'resourceUri': UI_URI}}) if t['name'] == 'create_prompt' else t for t in MCP_TOOLS]


def mcp_handle(msg, agent=''):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
    if mid is None:
        return None
    def ok(result): return {'jsonrpc': '2.0', 'id': mid, 'result': result}
    def err(code, text): return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': text}}
    if method == 'initialize':
        wanted = params.get('protocolVersion')
        return ok({'protocolVersion': wanted if wanted in MCP_PROTOCOLS else MCP_PROTOCOLS[0],
                   'capabilities': {'tools': {'listChanged': False}, 'prompts': {'listChanged': False},
                                    'resources': {'listChanged': False, 'subscribe': False}},
                   'serverInfo': {'name': 'prompt-workbench', 'version': '1.0.0'},
                   'instructions': 'Local prompt workbench. create_prompt returns a QA-checked prompt: show it to the user '
                                   'exactly as returned, with its QA line, and never say it passed QA unless the result '
                                   'says so. If it returns questions, ask the user and call answer_questions. Run ids '
                                   'can be shortened to 8 characters. pin_prompt keeps a prompt as a slash command.'})
    if method == 'ping':
        return ok({})
    if method == 'tools/list':
        return ok({'tools': mcp_tools_for(agent)})
    if method == 'prompts/list':
        return ok({'prompts': [
            {'name': name, 'title': e['title'],
             'description': ('Saved in your prompt library' if e['kind'] == 'library' else 'Pinned in the workbench')
                            + ': ' + e['title'],
             'arguments': [{'name': a, 'description': 'Value for {{%s}}' % a, 'required': True} for a in e['args']]}
            for name, e in mcp_prompt_catalog().items()]})
    if method == 'prompts/get':
        entry = mcp_prompt_catalog().get(params.get('name'))
        if not entry:
            return err(-32602, 'Unknown prompt: %s' % params.get('name'))
        values = params.get('arguments') or {}
        text = PLACEHOLDER.sub(lambda m: str(values.get(m.group(0).strip('{} ').strip(), m.group(0))), entry['text'])
        return ok({'description': entry['title'],
                   'messages': [{'role': 'user', 'content': {'type': 'text', 'text': text}}]})
    if method == 'resources/list':
        return ok({'resources': [{k: v for k, v in r.items() if not k.startswith('_')} for r in mcp_resources()]})
    if method == 'resources/templates/list':
        return ok({'resourceTemplates': [{'uriTemplate': 'prompt-workbench://run/{run_id}', 'name': 'A workbench run',
                                          'description': 'Any run by id (8 characters are enough)', 'mimeType': 'text/markdown'}]})
    if method == 'resources/read':
        uri = str(params.get('uri') or '')
        if uri == UI_URI and UI_HTML:
            return ok({'contents': [{'uri': UI_URI, 'mimeType': UI_MIME, 'text': UI_HTML,
                                     '_meta': {'ui': {'prefersBorder': False, 'permissions': {'clipboardWrite': {}}}}}]})
        hit = next((r for r in mcp_resources() if r['uri'] == uri), None)
        if not hit and uri.startswith('prompt-workbench://run/'):
            try:
                with db() as c:
                    row = c.execute('SELECT * FROM runs WHERE id=?', (_resolve(uri.rsplit('/', 1)[-1]),)).fetchone()
                hit = row and {'_text': _describe(row)}
            except ValueError:
                hit = None
        if not hit:
            return err(-32002, 'Resource not found: %s' % uri)
        return ok({'contents': [{'uri': uri, 'mimeType': 'text/markdown', 'text': hit['_text']}]})
    if method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        try:
            # Test clients mark their calls with _meta so those runs stay out of the history.
            origin = (params.get('_meta') or {}).get('prompt-workbench/origin')
            return ok({'content': [{'type': 'text', 'text': mcp_call(name, args, 'Test' if origin == 'test' else 'MCP')}],
                       'isError': False})
        except KeyError:
            return err(-32602, 'Unknown tool: %s' % name)
        except (ValueError, OverflowError) as e:
            return ok({'content': [{'type': 'text', 'text': str(e)}], 'isError': True})
    return err(-32601, 'Method not found: %s' % method)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,value,status=200,ctype='application/json'):
        raw=json.dumps(value).encode() if ctype=='application/json' else value.encode() if isinstance(value,str) else value
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(raw)
    def body(self, limit=2_000_000):
        size=int(self.headers.get('Content-Length',0))
        if size>limit: raise ValueError('Request exceeds the %d MB transport limit' % (limit // 1_000_000))
        return json.loads(self.rfile.read(size) or b'{}')
    def allowed(self,path):
        host=self.headers.get('Host','')
        if host.lower() not in LOCAL_HOSTS:self.send({'error':'Invalid host'},403);return False
        if path=='/mcp':
            # MCP clients are local programs. Refuse browser pages from other sites (DNS rebinding, CSRF).
            origin=self.headers.get('Origin')
            if origin and origin not in LOCAL_ORIGINS:
                self.send({'error':'Forbidden origin'},403);return False
            return True
        if self.command!='GET':
            origin=self.headers.get('Origin')
            if (origin and origin not in LOCAL_ORIGINS) or self.headers.get('X-Harness-Client')!='workbench':
                self.send({'error':'Use the local workbench'},403);return False
        return True
    def do_GET(self): self.dispatch()
    def do_POST(self): self.dispatch()
    def dispatch(self):
        url=urlparse(self.path);path=url.path;q=parse_qs(url.query)
        if not self.allowed(path):return
        try:
            if path=='/mcp':
                if self.command!='POST':return self.send({'error':'This MCP endpoint accepts POST only'},405)
                msg=self.body()
                agent=self.headers.get('User-Agent','')
                if isinstance(msg,list):
                    replies=[r for r in (mcp_logged(m,agent) for m in msg) if r is not None]
                    return self.send(replies,200) if replies else self.send('',202,'text/plain')
                reply=mcp_logged(msg,agent)
                return self.send(reply,200) if reply is not None else self.send('',202,'text/plain')
            if path=='/api/chat' and self.command=='POST':
                b=self.body()
                code,out=chat_proxy('POST','/api/chat',{'message':str(b.get('message',''))[:20000],'session':'wb-'+str(b.get('session') or 'main')[:60]})
                return self.send(out,code)
            if path=='/api/chat/history':
                from urllib.parse import quote
                code,out=chat_proxy('GET','/api/history?session='+quote('wb-'+q.get('session',['main'])[0][:60]))
                return self.send(out,code)
            if path=='/api/destination' and self.command=='POST':
                b=self.body();d=detect_destination(str(b.get('prompt',''))+'\n'+str(b.get('requirements','')))
                return self.send({'destination':d,'label':DESTINATIONS[d]['label']})
            if path=='/api/connectors':return self.send(connector_stats(int(q.get('days',['30'])[0])))
            if path=='/api/try' and self.command=='POST':
                b=self.body()
                text=b.get('prompt')
                if not text and b.get('run_id'):
                    with db() as c:
                        r=c.execute('SELECT result FROM runs WHERE id=?',(str(b['run_id']),)).fetchone()
                    text=r['result'] if r else ''
                return self.send(try_prompt(text,b.get('values')))
            if path=='/api/try/judge' and self.command=='POST':
                # Which of two Try it answers better does what the user first asked (try_judge.py).
                b=self.body()
                with db() as c:
                    root=try_inputs.family(c,str(b.get('run_id') or ''))
                    r=c.execute('SELECT prompt,requirements FROM runs WHERE id=?',(root,)).fetchone() if root else None
                if not r:raise ValueError('Unknown run')
                request='\n'.join(x for x in (r['prompt'],r['requirements']) if x)
                return self.send(try_judge.judge(request,b.get('values'),str(b.get('answer_a') or ''),str(b.get('answer_b') or ''),
                                                 JUDGE_URL,ASSISTANT_MODEL,NUM_CTX))
            if path=='/api/try-inputs':
                return self.send(try_inputs.save_input(db,self.body()) if self.command=='POST'
                                 else try_inputs.list_inputs(db,q.get('run_id',[''])[0]))
            if path=='/api/settings':
                return self.send(save_settings(self.body()) if self.command=='POST' else settings())
            if path=='/api/status':return self.send({'destinations':{k:v['label'] for k,v in DESTINATIONS.items()},'source_limit':SOURCE_LIMIT,'mcp':mcp_status(),'model':ASSISTANT_MODEL,'optimizer_model':OPTIMIZER_MODEL,'thinking':False,'context':NUM_CTX,'generation':NUM_PREDICT,'judge':QA_JUDGE,'api_spend':pipeline.spend_summary(),'api_key':pipeline.any_api_key(),'chat':bool(CHAT_URL),'workbench_mcp':{'url':'http://127.0.0.1:8789/mcp','tools':[t['name'] for t in MCP_TOOLS]}})
            if path=='/api/library':return self.send({'rows':library()})
            if path=='/api/convert' and self.command=='POST':
                import base64
                b=self.body(limit=60_000_000)
                return self.send(convert_document(str(b.get('filename','')), base64.b64decode(b.get('data','') or '')))
            if path=='/api/ocr' and self.command=='POST':
                import base64
                b=self.body(limit=60_000_000)
                return self.send(ocr_document(str(b.get('filename','')), base64.b64decode(b.get('data','') or ''), b.get('page',0)))
            if path=='/api/sync' and self.command=='POST':return self.send({'imported':import_history()})
            if path=='/api/runs' and self.command=='POST':
                body=self.body()
                # Benchmarks mark their runs so they stay out of the main history.
                return self.send(create_run(body,'Test' if body.get('origin')=='test' else 'Workbench'),202)
            if path=='/api/runs' and self.command=='GET':
                clauses=[];args=[]
                v=q.get('view',['history'])[0]
                clauses.append('archived=1' if v=='archive' else 'archived=0')
                if v=='pinned':clauses.append('pinned=1')
                clauses.append("source='Test'" if v=='tests' else "IFNULL(source,'')!='Test'")
                clauses.append("IFNULL(pack_id,'')=''")   # a pack's prompts show inside the pack
                search=q.get('q',[''])[0]
                if search:clauses.append('(instr(lower(prompt),lower(?))>0 OR instr(lower(title),lower(?))>0 OR instr(lower(result),lower(?))>0)');args += [search]*3
                f=q.get('f',[''])[0]
                # History filter chips (two-pane UI, 2026-10-14).
                if f=='agent':clauses.append("destination='agent'")
                elif f=='notebooklm':clauses.append("destination LIKE 'notebooklm%'")
                elif f=='instructions':clauses.append("destination IN ('notebooklm-instructions','gpt-instructions')")
                elif f=='flagged':clauses.append("qa LIKE '{\"passed\": false%'")
                elif f=='source':clauses.append("length(source_material)>0")
                offset=max(0,int(q.get('offset',['0'])[0]))
                with db() as c:
                    total=c.execute('SELECT count(*) FROM runs WHERE '+' AND '.join(clauses),args).fetchone()[0]
                    rows=c.execute('SELECT id,created,title,mode,source,status,pinned,archived,execution_id,parent_id,response_kind,qa,destination,length(source_material)>0 AS has_source FROM runs WHERE '+' AND '.join(clauses)+' ORDER BY created DESC LIMIT 50 OFFSET ?',args+[offset]).fetchall()
                return self.send({'rows':[dict(r) for r in rows],'total':total,'offset':offset})
            if path.startswith('/api/runs/'):
                rid=path.split('/')[3]
                if path.endswith('/answers') and self.command=='POST':
                    body=self.body()
                    return self.send(answer_run(rid,body.get('answers'),body.get('request_id'),'Workbench'),202)
                if path.endswith('/versions'):
                    # Every version of this prompt, oldest first: the first run of its revision chain and every
                    # run descended from it through parent_id (revisions, answered questions). Following the links
                    # rather than session_id, which imported n8n history can share between unrelated prompts.
                    with db() as c:
                        root=try_inputs.family(c,rid)
                        if not root:return self.send({'error':'Not found'},404)
                        cols="id,title,created,status,qa,response_kind,source,parent_id,pinned"
                        rows,frontier=[],[root]
                        while frontier and len(rows)<200:
                            marks=','.join('?'*len(frontier))
                            found=c.execute("SELECT "+cols+" FROM runs WHERE id IN ("+marks+") AND IFNULL(pack_id,'')=''",frontier).fetchall()
                            rows+=found
                            frontier=[r['id'] for r in c.execute("SELECT id FROM runs WHERE parent_id IN ("+marks+")",frontier)]
                        rows.sort(key=lambda r:r['created'] or 0)
                    def passed(qa):
                        try:return json.loads(qa).get('passed') if qa else None
                        except ValueError:return None
                    return self.send({'versions':[dict({k:r[k] for k in r.keys() if k!='qa'},qa_passed=passed(r['qa'])) for r in rows]})
                with db() as c:
                    if self.command=='POST':
                        body=self.body()
                        for key in ['pinned','archived','title']:
                            if key in body:
                                value=str(body[key]).strip()[:200] if key=='title' else int(bool(body[key]))
                                c.execute('UPDATE runs SET '+key+'=? WHERE id=?',(value,rid))
                    row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
                    out=dict(row) if row else None
                    if row and row['pack']:
                        out['pack_items']=[{'title':t,'id':r['id'],'status':r['status'],'result':r['result'] or '','error':r['error'] or '',
                                            'qa_passed':(json.loads(r['qa']).get('passed') if r['qa'] else None),'response_kind':r['response_kind']}
                                           if r else {'title':t,'status':'queued'} for t,r in pack_items(c,row)]
                return self.send(out) if out else self.send({'error':'Not found'},404)
            if self.command=='GET':
                routes={'/':'index.html','/app.js':'app.js','/theme.js':'theme.js','/style.css':'style.css',
                        # Installable app window (2026-10-28): manifest, service worker, icons.
                        '/manifest.webmanifest':'manifest.webmanifest','/sw.js':'sw.js','/icon-192.png':'icon-192.png',
                        '/icon-512.png':'icon-512.png','/icon-maskable-512.png':'icon-maskable-512.png','/apple-touch-icon.png':'apple-touch-icon.png'}
                if path in routes:
                    file=BASE/'static'/routes[path]
                    types={'.html':'text/html; charset=utf-8','.js':'application/javascript','.css':'text/css','.txt':'text/plain',
                           '.webmanifest':'application/manifest+json','.png':'image/png'}
                    return self.send(file.read_bytes(),ctype=types[file.suffix])
            self.send({'error':'Not found'},404)
        except ValueError as e:self.send({'error':str(e)},400)
        except OverflowError as e:self.send({'error':str(e)},429)
        except Exception as e:self.send({'error':str(e)},502)

if __name__=='__main__':
    init()
    if source_files.prune(DATA):print('Removed parked scans from runs that never started',flush=True)
    try:import_history()
    except Exception as e:print('History import unavailable:',str(e),flush=True)
    copied=saved_library.import_from_n8n(db,n8n_read)
    if copied:print('Saved library: copied %d prompts from n8n' % copied,flush=True)
    threading.Thread(target=worker,daemon=True).start()
    print('Prompt Workbench ready on 8789',flush=True)
    ThreadingHTTPServer(('0.0.0.0',8789),Handler).serve_forever()
