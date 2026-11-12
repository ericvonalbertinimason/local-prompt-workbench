"""QA rules and request analysis for prompts (moved verbatim from the workbench server, 2026-11-08).

Self-contained: only the standard library's re. qa_review() checks a delivered prompt against the request
(contract, invented facts and placeholders, scaffolding, limits, links for agents, self-check, ...);
the patterns here also detect destinations, library commands, transient failures and unreadable
references. Tests: bench/test_qa.py; eval.py loads these same rules.
"""
import re

PLACEHOLDER = re.compile(r'\{\{\s*[\w .-]+\s*\}\}')
SCAFFOLDING = re.compile(
    r'optimization requirements|do not execute the task|preserve (?:the )?original intent'
    r'|do not invent requirements|preserve save and retrieval|prompt optimization (?:specialist|expert)'
    r'|optimizerDraft|reference draft|quality review|requested refinements:|input json'
    # The optimizer's iteration template (seen leaking on 2026-09-27).
    r'|iteration evidence|lastoptimizedprompt|iterateinput|raw prompt evidence', re.I)
# "Create a prompt that..." in both the request and the delivered prompt means the
# output repeats the request instead of being the prompt (6 of 33 real outputs).
PROMPT_REQUEST = re.compile(
    r'\b(?:write|create|make|generate|draft|build|design|craft|produce|refine|improve|optimi[sz]e|rewrite)\b'
    r'[^.\n]{0,50}\bprompts?\b', re.I)
# Tasks whose subject is prompt writing. Deliberately excludes "Prompt Optimizer" (a tool
# name) and "prompt library" (save instructions), which let real echoes through.
PROMPT_TOPIC = re.compile(r'prompt[- ]?(?:engineer|writ|design)', re.I)
HARNESS_FOLLOWUP = 'Now write the complete final prompt using these answers.'
INVENTED_LIMIT = re.compile(
    r'\b(?:no more than|at most|under|maximum of|limit(?:ed)? to|not exceed(?:ing)?)\s+'
    r'\d[\d,]*\s*(?:word|sentence|character|token|page|paragraph)s?\b', re.I)
# Library requests (answered by saved_library.py; they can write rows, so they are never re-run).
# Saving needs a save verb; looking up needs an explicit library word, because the n8n-era rule
# ("find|list|show ... prompt") also caught real requests such as "Find a prompt that writes cover
# letters" or "Show me a prompt for lecture notes" (2026-11-07 review).
LIBRARY_COMMAND = re.compile(
    r'^(?:(?:please|can you|could you)\s+)*(?:save|store|remember)\b[\s\S]*\b(?:prompt|library|it|this)\b'
    r'|^(?:(?:please|can you|could you)\s+)*(?:retrieve|find|list|show|search|load|open)\b[\s\S]*'
    r'\b(?:saved|library|my (?:saved |pinned )?prompts?|pinned)\b'
    r'|^(?:save it|save this|find saved prompts)\b', re.I)
TRANSIENT = re.compile(r'not valid structured JSON|Unexpected XML response|Empty final answer'
                       r'|Missing successful MCP evidence|HTTP Error 5\d\d', re.I)
# Things a local model cannot open. When one is mentioned and nothing was attached,
# the prompt must ask for it or mark where it goes, never invent its contents.
UNREADABLE_REF = re.compile(
    r'https?://\S+|\bcanvas\b'
    r'|\b(?:my|the|attached|these|those)\s+(?:files?|folders?|e-?mails?|resumes?|r\u00e9sum\u00e9s?|documents?|attachments?|syllabus|notes)\b',
    re.I)
# Any mention of documents the request did not include. Weaker than UNREADABLE_REF: it
# only *allows* a content placeholder (e.g. "aggregate resumes" -> {{source_document}}),
# it never *requires* one.
CONTENT_MENTION = re.compile(
    r'\b(?:resumes?|résumés?|cvs?|files?|folders?|documents?|docs|attachments?|notes'
    r'|syllabus|transcripts?|articles?|reports?|pdfs?|spreadsheets?|slides?)\b', re.I)
# ("email" and "case" are left out: in "write a cold email" or "use case" they name the
# output or a concept, not supplied material. UNREADABLE_REF still catches "my emails".)
# Words that mark a placeholder as standing in for supplied material. Matched as whole
# words of the placeholder name: substring matching let {{candidate_profile}} through
# only because "profile" contains "file".
CONTENT_WORDS = {'source', 'document', 'doc', 'file', 'content', 'material', 'resume', 'cv', 'notes', 'text',
                 'attachment', 'case', 'data', 'email', 'folder', 'syllabus', 'schedule', 'link', 'page',
                 'description', 'profile', 'posting', 'background', 'brief', 'transcript', 'report', 'article', 'input'}


# Placeholder words used to talk ABOUT placeholders ("keep {{placeholders}} style variables for
# length and setting"): not a variable to keep, and a sign the user wants new variables added.
# Real request on 2026-10-21 failed QA twice on this.
META_PLACEHOLDER_WORDS = {'placeholder', 'placeholders', 'variable', 'variables', 'var', 'vars', '...'}


def is_meta_placeholder(p):
    return p.strip('{} ').strip().lower() in META_PLACEHOLDER_WORDS


def limit_is_invented(result, source, material=''):
    """A length/count limit in the prompt that neither the request nor the material contains."""
    if INVENTED_LIMIT.search(source):
        return False
    for m in INVENTED_LIMIT.finditer(result):
        nums = [n.replace(',', '') for n in re.findall(r'\d[\d,]*', m.group(0))]
        known = (source + '\n' + material).replace(',', '')
        if not all(n in known for n in nums):
            return True
    return False


def is_content_placeholder(p):
    return any(w in CONTENT_WORDS or (w.endswith('s') and w[:-1] in CONTENT_WORDS) for w in re.findall(r'[a-z]+', p.lower()))   # plurals too


# Where the delivered prompt will run. It decides what a good prompt looks like: an agent
# opens the links itself, NotebookLM already holds the sources, instructions have a size cap.
# Budgets: NotebookLM chat 2,000 (the user's own revision on 2026-09-22), NotebookLM
# "Configure chat" 10,000 (Google, Dec 2025), custom GPT instructions 8,000.
DESTINATIONS = {
    'chat': {'label': 'ChatGPT / Claude chat', 'budget': None},
    'agent': {'label': 'AI agent (browser or tools)', 'budget': None},
    'notebooklm-chat': {'label': 'NotebookLM chat', 'budget': 2000},
    'notebooklm-instructions': {'label': 'NotebookLM instructions', 'budget': 10000},
    'gpt-instructions': {'label': 'GPT / Project instructions', 'budget': 8000},
}
AGENT_WORDS = re.compile(r'\bai agents?\b|\bagentic\b|\bbrowser agents?\b|\bclaude code\b|\bclaude in chrome\b'
                         r'|\bcomputer[- ]use\b|\b(?:chrome|chorome)\b', re.I)
AGENT_ACTION = re.compile(r'\b(?:download|upload|navigate to|scrape|crawl|log ?in to|sign in to)\w*', re.I)
AGENT_TARGET = re.compile(r'https?://|\bcanvas\b|\bdrive\b|\bwebsite\b|\bsite\b|\bfolders?\b|\bnotebook', re.I)
NOTEBOOK = re.compile(r'\bnotebook\s*l+\s*m\b', re.I)          # NotebookLM, "notebook lm", "notebook lllm"
NOTEBOOK_SETUP = re.compile(r'\b(?:custom(?:ize)? instructions?|configure(?: chat)?|chat settings|notebook instructions)\b', re.I)
GPT_INSTRUCTIONS = re.compile(r'\bcustom gpts?\b|\bgpt instructions\b|\bproject instructions\b|\bclaude projects?\b'
                              r'|\binstructions for (?:a|my) (?:custom )?(?:gpt|project)\b', re.I)
URL = re.compile(r'https?://[^\s<>"\]\)]+')
AGENT_STOP = re.compile(r'\bask (?:the user|me|for (?:confirmation|approval|permission))|\bconfirm|\bcheck with (?:the user|me)'
                        r'|\bapproval\b|\bstop and\b|\bpause\b|\bbefore (?:you )?(?:download|upload|delet|submit|send|purchas|log)', re.I)
SELF_CHECK = re.compile(r'self[- ]?check|quality[- ](?:assurance|check)|\bQA\b|checklist|\bverif(?:y|ication)\b'
                        r'|double[- ]check|review (?:your|the) (?:output|answer|work|draft|result)', re.I)
CONSTRAINT_PART = re.compile(r'\bconstraints?\b|\brestrictions?\b|\bguardrails?\b|\brules\b|\bmust not\b|\bdo not\b|\bnever\b', re.I)


# A follow-up with nothing to work on ("improve it", "and include a final QA") when there is
# no earlier prompt in this run. Writing first there made the model fill the gap with the
# optimizer's template (4 flagged runs on 2026-10-04); asking is the right reply.
DANGLING = re.compile(r'^(?:and|also|now|then)\b|^(?:please\s+)?(?:improve|make|refine|fix|rewrite|change|update|'
                      r'shorten|expand|edit|adjust|redo|revise|compare)\s+(?:it|this|that|them|both|the (?:previous|last|same|above))\b',
                      re.I)
# The user asked for a bare prompt; the constraints-and-QA default would contradict them.
MINIMAL_REQUEST = re.compile(r'\bone[- ](?:sentence|line)\b|\bminimal\b|\bkeep it simple\b|\bsimple prompt\b'
                             r'|\bno (?:extra |additional )?constraints\b|\bavoid (?:adding )?(?:unnecessary |extra )?constraints\b'
                             # An explicit request to drop the self-check beats the standing preference (2026-10-21).
                             r'|\b(?:delete|remove|drop|omit|cut|leave out|without|no)\b[^.\n]{0,40}\bself[- ]?check', re.I)


def is_dangling(text):
    t = (text or '').strip()
    if not t or len(t) > 220 or PLACEHOLDER.search(t) or '\n' in t:
        return False
    head, _, tail = t.partition(':')
    if len(tail.split()) > 5:          # "Improve this prompt: You are a ..." carries its prompt
        return False
    return bool(DANGLING.search(t))


# Requests that may want several separate prompts ("two prompts: one for the deck and one for
# the script", "a prompt pack", "the next one for a PPTX and a Word script"). Only a hint: a
# short planning call decides. Matched none of the 65 eval and one-shot requests (2026-10-18).
PACK_HINT = re.compile(
    r'\b(?:two|three|four|five|2|3|4|5|several|multiple|separate)\s+(?:separate\s+|different\s+|distinct\s+)?prompts\b'
    r'|\bprompt\s+(?:pack|set|series)\b|\b(?:set|series|pack)\s+of\s+prompts\b|\bprompts?\s+for\s+(?:each|both)\b'
    r'|\b(?:a|one)\s+prompt\s+(?:for|that)\b.{3,160}?\b(?:and|plus)\s+(?:another|a second|one)\b'
    r'|\bnext\s+(?:one|prompts?)\s+for\b.{3,160}?\band\b'
    r'|\bprompts\s*:\s*(?:one|1)\b', re.I | re.S)


def detect_destination(text):
    t = text or ''
    if AGENT_WORDS.search(t) or (AGENT_ACTION.search(t) and AGENT_TARGET.search(t)):
        return 'agent'
    if NOTEBOOK.search(t):
        return 'notebooklm-instructions' if NOTEBOOK_SETUP.search(t) else 'notebooklm-chat'
    if GPT_INSTRUCTIONS.search(t):
        return 'gpt-instructions'
    return 'chat'


DESTINATION_NOTES = {
    'agent': ('This prompt is for an AI agent that can open web pages and use tools (for example Claude in Chrome '
              'or Claude Code). Links, sites and folders in the request are places the agent goes: keep every link '
              'exactly as written and tell the agent to open it. Never ask the user to paste their content and never '
              'use a placeholder for it. Call apps by their real names (for example NotebookLM). Structure the prompt '
              'as: goal; scope (what to do and what not to do); steps; when to stop and ask the user before acting '
              '(signing in, downloading or uploading many files, deleting, submitting or sending anything); how to '
              'check the result; and what to report back.'),
    'notebooklm-chat': ('This prompt will be pasted into the NotebookLM chat box. NotebookLM already has the user\'s '
                        'documents as sources: refer to them (for example "using the case in your sources") instead of '
                        'pasting their contents, tables or details. The finished prompt must be at most 2,000 characters '
                        'long; this limits your prompt, it is not something to write into it. Keep every part the user '
                        'asked for, compressed to a line or two each.'),
    'notebooklm-instructions': ('This prompt is the standing instructions of a NotebookLM notebook (Configure chat). '
                                'Write instructions that apply to every answer, referring to the notebook\'s sources '
                                'instead of pasting them. The finished prompt must be at most 10,000 characters long.'),
    'gpt-instructions': ('This prompt is the standing instructions of a custom GPT or a Claude Project: the assistant\'s '
                         'role, scope, rules and how it responds, applying to every conversation. The finished prompt '
                         'must be at most 8,000 characters long.'),
}
QA_DEFAULT_NOTE = ('The user\'s standing preference for every prompt: include a short Constraints section (the rules '
                   'the task must respect: what the user asked for, plus guardrails such as not inventing facts. They '
                   'come from the task, not from how prompts are written, so leave out rules about placeholders or '
                   'length limits), and end with a self-check step: before giving '
                   'the final answer, the model checks its work against each constraint and adds a brief QA checklist '
                   'at the end of its answer showing which constraints are met.')
QA_DEFAULT_NOTES = {
    'agent': ('The user\'s standing preference for every prompt: include a short Constraints section (what the '
              'agent must and must not do), and end with a verification step: before finishing, the agent checks the '
              'result against each constraint and reports what was done, what was skipped and anything that needs the '
              'user\'s attention.'),
    'notebooklm-chat': ('The user\'s standing preference for every prompt: include the key constraints and end with a '
                        'self-check (the model confirms its answer meets each constraint and adds a short QA line). '
                        'Keep both to two or three short lines.'),
}


def _field(row, key, default=''):
    """Read a column from a sqlite3.Row or a dict (tests), tolerating rows from before the column existed."""
    try:
        return row[key] or default
    except (KeyError, IndexError):
        return default
# Distinctive facts in short source material: figures, dates, codes and multi-word
# names. A prompt built from the material should carry a reasonable share of them.
# Month names match in any case; the rest stays case-sensitive so only real names count.
MONTH = r'(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?'
# A capitalized word that is not a month, so "Midterm October 14" yields the date, not a fake name.
NAME_WORD = (r'(?!(?:January|February|March|April|May|June|July|August|September|October|November|December)\b)'
             r'[A-Z][a-z]+')
SALIENT_FACT = re.compile(
    r'\b' + MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?\b'          # May 17, October 14
    r'|\b\d{1,2}\s+' + MONTH + r'\b'                          # 14 November
    r'|\b[A-Z]{2,}\s?\d{2,}\b'                                # ACCT 210, B214
    r'|\b\d[\d,.:]*(?:\s?(?:am|pm|%|m|km|k|th))?(?:-\d[\d,.:]*(?:am|pm)?)?\b'  # 6:48, 2,000 m, 2-4pm
    r'|\b' + NAME_WORD + r'(?:\s+(?:&\s+)?' + NAME_WORD + r')+\b')  # Brightwater Rowing Club, Harlow & Pike
FACT_SOURCE_MAX = 4000   # longer material is summarized, so per-fact coverage stops being meaningful
FACT_MIN_SHARE = 0.4


def source_facts(material):
    facts, seen = [], set()
    for m in SALIENT_FACT.finditer(material):
        f = m.group(0).strip(' .,')
        key = f.lower()
        if len(f) < 3 or key in seen or re.fullmatch(r'\d{1,2}', f):
            continue
        seen.add(key)
        facts.append(f)
    return facts


# Long material: the 4B model reads 60-140k characters but picks facts mostly from the start
# (2026-10-14: 0/4 and 2/4 deadlines carried into the prompt). An index of the lines that hold
# dates, figures and codes, sampled evenly across the whole document, puts the middle and the
# end within easy reach.
KEY_LINE_FACT = re.compile(
    r'\b' + MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?\b|\b\d{1,2}\s+' + MONTH + r'\b'
    r'|\b[A-Z]{2,}\s?\d{2,}\b|\$\s?\d|\b\d[\d,.]*\s?(?:%|percent|dollars|euros|words|pages|minutes|hours|am|pm)\b'
    r'|\b(?:due|deadline|exam|quiz|midterm|final|submit)\b', re.I)


# "The prompt itself must list every deadline": with long material the 4B model tends to write
# "extract every deadline" instead (2026-10-14). When the request asks for the details in the
# prompt, the dates and codes from the key lines must appear in it.
LIST_REQUEST = re.compile(r'prompt (?:itself )?(?:must|should) (?:list|include|contain|name)'
                          r'|\b(?:list|include|name)\s+(?:every|all)(?: of)?(?: the)?\s+(?:deadlines?|dates?|due dates?|'
                          r'assignments?|requirements?|figures?|names?)', re.I)
KEY_FACT = re.compile(r'\b' + MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?\b|\b\d{1,2}\s+' + MONTH + r'\b|\b[A-Z]{2,}\s?\d{2,}\b')


def requested_facts(material, request):
    """Dates and codes from a long document that the request asks to be listed in the prompt."""
    if len(material) <= FACT_SOURCE_MAX or not LIST_REQUEST.search(request or ''):
        return []
    out = []
    for line in key_lines(material):
        for m in KEY_FACT.finditer(line):
            f = m.group(0).strip()
            if f.lower() not in (x.lower() for x in out):
                out.append(f)
    return out[:40]


def append_requested_facts(result, material, missing):
    """Add the requested details the model left out, as verbatim lines from the material.

    Used only after the revision, when the request asked the prompt itself to list details
    (dates, codes) from long material and they are still missing. Quoting the source lines
    cannot invent anything; the QA panel reports it (check facts_appended).
    """
    lines, seen = [], set()
    for line in key_lines(material, limit_chars=20000):
        hit = [f for f in missing if f.lower() in line.lower()]
        if hit and line not in seen:
            seen.add(line)
            lines.append(line)
    if not lines:
        return result
    return result.rstrip() + '\n\n## Details from the material\n' + '\n'.join('- ' + l for l in lines)


def key_lines(material, limit_chars=6000, max_line=220):
    lines = []
    for raw in re.split(r'(?<=[.!?])\s+|\n+', material):
        line = ' '.join(raw.split())
        if 12 <= len(line) and KEY_LINE_FACT.search(line):
            lines.append(line[:max_line])
    seen, unique = set(), []
    for line in lines:
        if line.lower() not in seen:
            seen.add(line.lower())
            unique.append(line)
    if sum(len(l) + 3 for l in unique) <= limit_chars:
        return unique
    keep = max(1, limit_chars // (sum(len(l) for l in unique) // len(unique) + 3))
    step = len(unique) / keep
    return [unique[int(i * step)] for i in range(keep)]


# Hard facts a prompt must not make up: dates, years, course-style codes, money and percentages.
# (Plain numbers and capitalized phrases are left alone: prompts legitimately say "list 5
# examples" or "Output Format".) Seen 2026-10-05: "Regatta: May 17, 2024" when the notes said
# only "May 17".
# Explicit month names: the shared MONTH pattern also matches "decisions" or "novices".
_MON = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?'
        r'|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?')
HARD_FACT = re.compile(
    r'(?i:\b' + _MON + r'[ \t]+\d{1,2}(?:st|nd|rd|th)?\b(?:,?[ \t]*(?:19|20)\d\d\b)?'
    r'|\b\d{1,2}(?:st|nd|rd|th)?[ \t]+' + _MON + r'(?:[ \t]+(?:19|20)\d\d)?\b)'
    r'|\b(?:19|20)\d\d\b|\b[A-Z]{2,5}[ \t]?\d{3,4}[A-Z]?\b'
    r'|[$\u20ac\u00a3][ \t]?\d[\d,]*(?:\.\d+)?(?:[ \t]?[kKmM]\b)?|\b\d[\d,]*(?:\.\d+)?[ \t]?%')
EXAMPLE_CUE = re.compile(r'(?:e\.g\.|for example|for instance|such as|example)[^\n]{0,60}$', re.I)


def _fact_keys(text):
    """Normalized forms of the hard facts in a text: (kind, value) pairs."""
    keys = set()
    low = text.lower()
    for m in re.finditer(r'\b' + _MON + r'[ \t]+(\d{1,2})(?:st|nd|rd|th)?\b|\b(\d{1,2})(?:st|nd|rd|th)?[ \t]+' + _MON, low):
        mon = re.search(r'[a-z]{3}', m.group(0)).group(0)
        keys.add(('date', mon, int(m.group(1) or m.group(2))))
    for m in re.finditer(r'\d[\d,]*(?:\.\d+)?', low):
        keys.add(('num', m.group(0).replace(',', '').rstrip('.')))
    for m in re.finditer(r'\b[a-z]{2,5}[ \t]?\d{3,4}[a-z]?\b', low):
        keys.add(('code', re.sub(r'\s', '', m.group(0))))
    return keys


def invented_facts(result, source, assumptions=()):
    """Hard facts in the prompt that appear in neither the user's text nor the stated assumptions.

    Compared by value, not spelling: "Oct 14" matches "October 14", "$42,000" matches
    "42,000 dollars". Facts introduced as examples ("e.g., 80%") are not claims and are skipped.
    """
    known = _fact_keys(source + '\n' + '\n'.join(assumptions))
    out = []
    for m in HARD_FACT.finditer(result):
        f = m.group(0).strip()
        if EXAMPLE_CUE.search(result[max(0, m.start() - 80):m.start()]):
            continue
        keys = _fact_keys(f)
        date = [k for k in keys if k[0] == 'date']
        if date:
            # The date is known; a year attached to it must be known too ("May 17, 2024").
            year = re.search(r'(?:19|20)\d\d', f)
            if all(k in known for k in date) and (not year or ('num', year.group(0)) in known):
                continue
        elif f.replace(' ', '') == '100%':
            continue          # "100% of the requirements" is wording, not a claim
        elif keys and all(k in known or (k[0] == 'code' and any(c[0] == 'code' and c[1].startswith(k[1]) for c in known))
                          for k in keys if k[0] in ('num', 'code')):
            continue          # a code may be cited without its section letter: ENGN 1931 for ENGN 1931Q
        if f not in out:
            out.append(f)
    return out


QA_CONTRACT_NOTE = ('Your previous reply could not be used: it was not one valid JSON object. '
                    'Return exactly one JSON object with the keys kind, questions and result, and nothing else.')


KEEP_EXACT = re.compile(r'\b(verbatim|word[- ]for[- ]word|exactly as (written|given)|keep (all|every|each)|'
                        r'quote|copy (it|them|this))\b', re.I)


def pasted_wholesale(material, result, request=''):
    """The material dumped into the prompt: at least half of its 300-character stretches appear
    verbatim. Quoting the parts a request names (an objection list, case numbers) stays allowed,
    and a request that asks to keep or quote text exactly is never flagged. (2026-11-11: the
    first version flagged any one stretch, which hit a run that did what the user asked.)"""
    if len(material) <= 600 or KEEP_EXACT.search(request or ''):
        return False
    starts = range(0, len(material) - 300, 150)
    copied = sum(material[i:i + 300] in result for i in starts)
    return copied >= 0.5 * len(starts)


def qa_review(row, parsed, prefs=None):
    """Check one result against the rules the workbench promises."""
    source = '\n'.join(x for x in (row['context_prompt'], row['prompt'], row['requirements']) if x)
    dest = _field(row, 'destination', 'chat')
    prefs = prefs or {}
    material = row['source_material'] or ''
    result, kind = parsed['result'], parsed['response_kind']
    checks, issues = [], []
    invented = []

    def check(name, passed, issue):
        checks.append({'check': name, 'passed': bool(passed)})
        if not passed:
            issues.append(issue)

    check('contract', result.strip() and kind in ('prompt', 'questions', 'message'),
          'The reply was empty or not in the required structure.')
    if kind == 'questions':
        check('question_count', 1 <= len(parsed['questions']) <= 3, 'Ask between one and three questions.')
    if kind == 'prompt':
        given, got = set(PLACEHOLDER.findall(source)), set(PLACEHOLDER.findall(result))
        meta_ph = {p for p in given if is_meta_placeholder(p)}
        given -= meta_ph
        lost = sorted(given - got)
        invented = sorted(got - given - set(PLACEHOLDER.findall(material)))
        refs = [] if material.strip() else sorted({m.group(0).strip()[:60] for m in UNREADABLE_REF.finditer(source)})
        if dest == 'agent':
            refs = []   # an agent opens links and folders itself; they are targets, not missing material
        marker = []
        if refs or material.strip() or CONTENT_MENTION.search(source):
            # A clearly named placeholder standing in for material (missing, or too long
            # to paste into the prompt) is the right move, so it does not count as invented.
            marker = [p for p in invented if is_content_placeholder(p)]
            invented = [p for p in invented if p not in marker]
        if PROMPT_TOPIC.search(source) or meta_ph:
            # A prompt about writing prompts legitimately shows example {{placeholders}}; a request
            # that asks for "{{placeholders}} style variables" wants new ones.
            invented = []
        if refs:
            check('unreadable_material_handled', marker or got & given,
                  'The request refers to material you cannot see (%s). Do not invent it: mark where it goes '
                  'with a clearly named placeholder such as {{source_document}}, or ask the user to provide it.'
                  % ', '.join(refs))
        if not PROMPT_TOPIC.search(source):
            made_up = invented_facts(result, source + '\n' + material, parsed.get('assumptions') or ())
            check('no_invented_facts', not made_up,
                  'Remove details the user never gave: %s. Use only dates, codes, amounts and figures from the request '
                  'or the source material; describe anything else in words (for example "the due date").'
                  % ', '.join(made_up[:8]))
        wanted = requested_facts(material, source)
        if wanted:
            missing = [f for f in wanted if f.lower() not in result.lower()]
            check('requested_facts_included', len(missing) <= len(wanted) // 2,
                  'The request asks the prompt itself to list these details from the material. Write them into the '
                  'prompt (with what each one is for): %s.' % ', '.join(missing))
        # NotebookLM already holds the sources, so its prompts refer to them instead of restating facts.
        if material.strip() and len(material) <= FACT_SOURCE_MAX and not dest.startswith('notebooklm'):
            facts = source_facts(material)
            if len(facts) >= 3:
                low = result.lower()
                missing = [f for f in facts if f.lower() not in low]
                used = len(facts) - len(missing)
                # A content placeholder means the full material is supplied at run time.
                check('source_facts_used', used >= max(2, round(FACT_MIN_SHARE * len(facts))) or marker,
                      'Build the prompt on the source material the user attached. It leaves out key facts '
                      'such as: %s.' % ', '.join(missing[:8]))
        if len(material) > 600:
            check('no_pasted_material', not pasted_wholesale(material, result, source),
                  'Most of the source material was copied into the prompt. Refer to it instead (for example '
                  '"the attached case") and write in only the facts the task needs; keep exact wording only '
                  'where the user asked for it.')
        asked = source.replace(HARNESS_FOLLOWUP, '')
        if PROMPT_REQUEST.search(asked) and not PROMPT_TOPIC.search(asked):
            opening = ' '.join(result.split())[:70]
            check('is_the_prompt_itself', not PROMPT_REQUEST.search(result[:250]),
                  'Your reply starts like the request ("%s...") instead of being the prompt. Write the prompt '
                  'itself: the instructions a model follows to do the task. For example, for "Write a prompt to '
                  'proofread {{text}}" the answer is "Proofread {{text}} for grammar and spelling...". Do not add '
                  'new placeholders.' % opening)
        check('no_scaffolding', not SCAFFOLDING.search(result),
              'Remove text copied from the optimizer (requirement lists, role or profile cards, rules about '
              'preserving intent or placeholders). It is not part of the prompt the user asked for.')
        check('no_invented_placeholders', not invented,
              'Remove placeholders the user never wrote: %s. Use only placeholders from the user request.'
              % ', '.join(invented))
        check('placeholders_preserved', not lost,
              'Keep every placeholder from the user request exactly as written: %s.' % ', '.join(lost))
        check('no_invented_limits', not limit_is_invented(result, source, material),
              'Remove word, sentence, character, token or page limits the user did not ask for.')
        budget = (DESTINATIONS.get(dest) or {}).get('budget')
        if budget:
            check('fits_destination', len(result) <= budget,
                  'The prompt is %d characters, but %s takes at most %d. Shorten it: keep every part the user asked '
                  'for, compress each to a line or two, and refer to the sources instead of repeating them.'
                  % (len(result), DESTINATIONS[dest]['label'], budget))
        if dest == 'agent':
            links = sorted({u.rstrip('.,;:') for u in URL.findall(source)})
            dropped = [u for u in links if u not in result]
            check('agent_links_kept', not dropped,
                  'Keep every link from the request exactly as written so the agent can open it: %s.' % ', '.join(dropped))
            check('agent_confirms_first', AGENT_STOP.search(result),
                  'Tell the agent when to stop and ask the user first: before signing in, downloading or uploading '
                  'many files, deleting, submitting or sending anything.')
        if prefs.get('qa_default') and not MINIMAL_REQUEST.search(source):
            check('has_self_check', SELF_CHECK.search(result) and CONSTRAINT_PART.search(result),
                  'Add a short Constraints section and end the prompt with a self-check step: the model checks its '
                  'work against each constraint and ends its answer with a brief QA checklist.')
    return {'passed': not issues, 'checks': checks, 'issues': issues,
            'invented': invented if kind == 'prompt' else []}
