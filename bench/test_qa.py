"""Unit tests for the harness QA rules, loaded from harness/server.py itself.

The rules are exec'd from the file rather than imported or retyped: an earlier
"sanity check" tested a retyped regex while the real one in the file had been
corrupted, so it proved nothing.

  python bench/test_qa.py
"""
import re
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / 'harness'
SRC = (HARNESS / 'server.py').read_text(encoding='utf-8')
QA_SRC = (HARNESS / 'core' / 'qa.py').read_text(encoding='utf-8')     # the rules live here since 2026-11-08
if '\x08' in SRC or '\x08' in QA_SRC:
    sys.exit('server.py or core/qa.py contains backspace characters (corrupted regex)')
ns = {'re': re}
exec(QA_SRC, ns)
qa, facts, LIB, TR, UR = ns['qa_review'], ns['source_facts'], ns['LIBRARY_COMMAND'], ns['TRANSIENT'], ns['UNREADABLE_REF']


def row(prompt, material=''):
    return {'context_prompt': '', 'prompt': prompt, 'requirements': '', 'source_material': material}


def prompt(text):
    return {'result': text, 'response_kind': 'prompt', 'questions': []}


def passed(r, p):
    return qa(r, p)['passed']


SYLLABUS = ('ACCT 210 Intermediate Accounting I - Prof. Adaeze Okafor. Midterm October 14, final December 9. '
            'No late work accepted. Office hours Wednesdays 2-4pm, Room B214. Textbook: Kieso 18th edition.')
DOC = ''.join('Fact %d: the deadline for part %d is week %d of term. ' % (i, i, i) for i in range(300))
CASES = [
    # Retry guards
    ('library command is never retried', bool(LIB.search('save this prompt to my library')), True),
    ('library lookup is never retried', bool(LIB.search('please find my saved prompts')), True),
    ('ordinary request can be retried', bool(LIB.search('Write a prompt to help students remember vocab')), False),
    # 2026-11-07: requests that merely start with find/list/show are prompt requests, not library lookups.
    ('"Find a prompt that..." is a request', bool(LIB.search('Find a prompt that writes cover letters for internships')), False),
    ('"List 5 prompts..." is a request', bool(LIB.search('List 5 prompts I could use to brainstorm essay topics')), False),
    ('"Show me a prompt for..." is a request', bool(LIB.search('Show me a prompt for summarizing lecture notes')), False),
    ('"open my saved summary prompt" is a lookup', bool(LIB.search('open my saved summary prompt')), True),
    ('"list my prompts" is a lookup', bool(LIB.search('list my prompts')), True),
    ('"save it" is a save', bool(LIB.search('save it')), True),
    ('invalid JSON is transient', bool(TR.search('Assistant response was not valid structured JSON')), True),
    ('HTTP 502 is transient', bool(TR.search('HTTP Error 502: bad gateway')), True),
    # Structure and placeholders
    ('clean prompt passes', passed(row('Summarize {{doc}}'), prompt('Summarize {{doc}} for executives.')), True),
    ('invented placeholder caught', passed(row('Summarize {{doc}}'), prompt('Summarize {{doc}} using {{variable_name}}.')), False),
    ('lost placeholder caught', passed(row('Turn {{notes}} into an agenda'), prompt('Turn the notes into an agenda.')), False),
    ('optimizer scaffolding caught', passed(row('Do X'), prompt('Do X.\nOptimization Requirements:\n1. Preserve the original intent')), False),
    ('invented word limit caught', passed(row('Write about {{t}}'), prompt('Write about {{t}} in no more than 200 words.')), False),
    ('user-requested limit allowed', passed(row('Write about {{t}} in no more than 200 words'), prompt('Write about {{t}} in no more than 200 words.')), True),
    ('one question is fine', passed(row('x'), {'result': 'need info', 'response_kind': 'questions', 'questions': ['Who?']}), True),
    ('zero questions flagged', passed(row('x'), {'result': 'need info', 'response_kind': 'questions', 'questions': []}), False),
    # Unreadable references
    ('Drive link detected', bool(UR.search('Create a prompt around https://drive.google.com/file/d/1dn')), True),
    ('"my canvas" detected', bool(UR.search('schedule using my canvas')), True),
    ('ordinary request has no reference', bool(UR.search('Write a cold email about bookkeeping')), False),
    ('unseen link: invented content flagged', passed(row('Summarize https://drive.google.com/x'), prompt('Summarize the Q3 report: revenue up 12%.')), False),
    ('unseen link: {{source_document}} passes', passed(row('Summarize https://drive.google.com/x'), prompt('Summarize {{source_document}}.')), True),
    ('unseen link: random placeholder flagged', passed(row('Summarize https://drive.google.com/x'), prompt('Summarize {{variable_name}}.')), False),
    # Source material
    ('material: {{source_document}} allowed', passed(row('Analyse this case', 'Case text'), prompt('Analyse {{source_document}} with SWOT.')), True),
    ('material: random placeholder flagged', passed(row('Analyse this case', 'Case text'), prompt('Analyse {{variable_name}}.')), False),
    ('no material: {{source_document}} flagged', passed(row('Write a cold email'), prompt('Write to {{source_document}}.')), False),
    ('material: {{job_description}} allowed', passed(row('Cover letter for this job', 'Harlow & Pike, SQL'), prompt('Tailor to {{job_description}} and {{candidate_profile}}.')), True),
    ('material: {{key_skill_1}} still flagged', passed(row('Cover letter for this job', 'Harlow & Pike, SQL'), prompt('Mention {{key_skill_1}}.')), False),
    ('mentions resumes: {{source_document}} allowed', passed(row('This is to aggregate resumes, keep formatting'), prompt('Aggregate {{source_document}} without rewording.')), True),
    ('mentions resumes: {{variable_name}} still flagged', passed(row('This is to aggregate resumes'), prompt('Aggregate {{variable_name}}.')), False),
    ('mention alone does not *require* a placeholder', passed(row('Write a prompt to summarize reports'), prompt('Summarize the report for executives.')), True),
    ('echo: "create a prompt" echoed back is flagged', passed(row('Create a reusable prompt to proofread {{text}}'), prompt('Create a reusable prompt to proofread {{text}} for grammar.')), False),
    ('echo: the real prompt passes', passed(row('Create a reusable prompt to proofread {{text}}'), prompt('Proofread {{text}} for grammar; return the corrected text.')), True),
    ('echo: "refine that prompt" echoed back is flagged', passed(row('Use Prompt Optimizer to refine that prompt.'), prompt('Use Prompt Optimizer to refine that prompt.')), False),
    ('echo: prompt-writing task itself is exempt', passed(row('You are a prompt writing assistant. Create prompts for users.'), prompt('Write prompts that are clear and preserve intent.')), True),
    ('echo: follow-up harness sentence does not trip it', passed({'context_prompt': 'Original request:\nSummarize {{doc}}\n\nNow write the complete final prompt using these answers. Do not repeat answered questions.', 'prompt': 'Question: tone? Answer: formal', 'requirements': '', 'source_material': ''}, prompt('Summarize {{doc}} formally.')), True),
    ('echo: "prompt library" mention does not exempt it', passed(row('Create a reusable prompt to proofread {{text}}. Do not save this to the prompt library.'), prompt('Create a reusable prompt to proofread {{text}}.')), False),
    ('prompt-writing task may show example placeholders', passed(row('You are a prompt writing assistant. Preserve placeholders.'), prompt('Keep variables such as {{variable_name}} unchanged.')), True),
    ('scaffolding: optimizer iteration template caught', passed(row('improve it'), prompt('Iteration Evidence (JSON): {"lastOptimizedPrompt": "improve it"}')), False),
    ('word match: "profile" is not a substring hit', ns['is_content_placeholder']('{{profiled_segment}}'), False),
    ('facts: dates not swallowed into names', 'October 14' in facts(SYLLABUS) and 'Midterm October' not in facts(SYLLABUS), True),
    ('facts: generic prompt flagged', passed(row('Assistant for this course', SYLLABUS), prompt('# Role: Course Q&A Assistant for ACCT 210.')), False),
    ('facts: grounded prompt passes', passed(row('Assistant for this course', SYLLABUS), prompt(
        'Answer questions on ACCT 210 Intermediate Accounting I for Prof. Adaeze Okafor. Midterm October 14, '
        'final December 9, office hours 2-4pm.')), True),
]

# Destinations (2026-10-04): where the prompt runs changes what a good prompt is.
dest = ns['detect_destination']
CANVAS = ('create a prompt for organizing materials from a class off of this canvas page: '
          'https://canvas.example.edu/courses/12345, and dowload relavant materials and put it into the notebook lllm on chorome.')


# repair_placeholders lives outside the rules segment; load it on its own, from the file.
exec(SRC[SRC.index('def repair_placeholders('):SRC.index('def harness_notes(')], ns)
repair = ns['repair_placeholders']


def drow(prompt_text, destination, material=''):
    return dict(row(prompt_text, material), destination=destination)


AGENT_OK = ('Open https://canvas.example.edu/courses/12345 in Chrome, download every file, and add them to NotebookLM. '
            'Ask the user to confirm before downloading more than 20 files.')
CASES += [
    ('detect: Canvas download task -> agent', dest(CANVAS), 'agent'),
    ('detect: "real estate agent" stays chat', dest('Write a prompt for a real estate agent listing'), 'chat'),
    ('detect: NotebookLM prompt -> notebooklm-chat', dest('Improve this NotebookLM prompt for the case'), 'notebooklm-chat'),
    ('detect: NotebookLM configure -> instructions', dest('Write custom instructions for my NotebookLM notebook'), 'notebooklm-instructions'),
    ('detect: custom GPT -> gpt-instructions', dest('Write the instructions for my custom GPT'), 'gpt-instructions'),
    ('detect: plain request -> chat', dest('Write a cold email to {{prospect}}'), 'chat'),
    ('agent: link is a target, no placeholder needed', passed(drow(CANVAS, 'agent'), prompt(AGENT_OK)), True),
    ('chat: same request still needs a placeholder', passed(drow(CANVAS, 'chat'), prompt(AGENT_OK)), False),
    ('agent: dropped link flagged', passed(drow(CANVAS, 'agent'), prompt('Open the course page, download the files, and ask the user to confirm first.')), False),
    ('agent: no stop-and-confirm rule flagged', passed(drow(CANVAS, 'agent'), prompt('Open https://canvas.example.edu/courses/12345 and download everything into NotebookLM.')), False),
    ('notebooklm-chat: over 2,000 characters flagged', passed(drow('Improve this NotebookLM prompt', 'notebooklm-chat'), prompt('Analyse the case. ' * 120)), False),
    ('notebooklm-chat: short prompt passes', passed(drow('Improve this NotebookLM prompt', 'notebooklm-chat'), prompt('Using the case in your sources, map each stakeholder.')), True),
    ('notebooklm: refers to sources instead of restating facts', passed(drow('NotebookLM chat prompt for this course', 'notebooklm-chat', SYLLABUS), prompt('Using the syllabus in your sources, list every deadline.')), True),
    ('qa default: prompt without self-check flagged', qa(row('Summarize {{doc}}'), prompt('Summarize {{doc}} for executives.'), {'qa_default': True})['passed'], False),
    ('qa default: constraints + self-check pass', qa(row('Summarize {{doc}}'), prompt('Summarize {{doc}} for executives.\nConstraints: do not invent facts.\nSelf-check: end with a QA checklist.'), {'qa_default': True})['passed'], True),
    ('qa default off: no self-check needed', passed(row('Summarize {{doc}}'), prompt('Summarize {{doc}} for executives.')), True),
    ('plural content placeholder allowed', ns['is_content_placeholder']('{{course_materials}}'), True),
    ('facts: invented year on a known date flagged', ns['invented_facts']('Regatta: May 17, 2024.', 'regatta May 17'), ['May 17, 2024']),
    ('facts: same date, other spelling, passes', ns['invented_facts']('Due Oct 14 and Dec 9', 'Midterm October 14, final December 9'), []),
    ('facts: same amount, other spelling, passes', ns['invented_facts']('Budget $42,000.', 'capped at 42,000 dollars'), []),
    ('facts: example values are not claims', ns['invented_facts']('Report progress (e.g., "80%")', 'status update'), []),
    ('facts: made-up date flagged', ns['invented_facts']('The exam is on November 3.', 'Plan for the exam'), ['November 3']),
    ('facts: "decisions" is not a month', ns['invented_facts']('Make good decisions.\n2. Then', 'x'), []),
    ('facts: course code without section letter passes', ns['invented_facts']('Include ENGN 1931', 'ENGN 1931Q override'), []),
    ('facts: QA check flags a made-up deadline', passed(row('Write a prompt for my essay plan'), prompt('Plan the essay; the draft is due October 3.')), False),
    ('facts: a stated assumption is not invented', qa(row('Write a prompt for my essay plan'), dict(prompt('Plan the essay; the draft is due October 3.'), assumptions=['Draft due October 3']))['passed'], True),
    ('facts appended: missing deadlines quoted from the material', ns['append_requested_facts'](
        'Plan the weeks.', 'Week 1. Due this week: Map quiz, due September 9. Week 2. Reading only.', ['September 9']).endswith(
        '- Due this week: Map quiz, due September 9.'), True),
    ('facts appended: nothing found, prompt unchanged', ns['append_requested_facts']('Plan.', 'No dates here.', ['May 3']), 'Plan.'),
    ('meta: "{{placeholders}} style variables" need not be kept', passed(row('Write a case prompt and keep {{placeholders}} style variables for length and setting.'), prompt('Write the dialogue for {{setting}} at {{length}} words.')), True),
    ('meta: real placeholder still must be kept', passed(row('Summarize {{doc}}; keep {{placeholders}} style variables.'), prompt('Summarize the document.')), False),
    ('limits: a limit from the source material is not invented', passed(row('Write the case dialogue prompt', 'LENGTH: 1,400-1,800 words of dialogue. Roles: six.'), prompt('Write the dialogue at no more than 1,800 words, using the six roles.')), True),
    ('limits: a limit in neither request nor material is invented', passed(row('Write the case dialogue prompt', 'Roles: six named people.'), prompt('Write the dialogue in no more than 900 words.')), False),
    ('self-check: an explicit request to delete it overrides the default', bool(ns['MINIMAL_REQUEST'].search("(4) Delete the final '**Self-Check:** ...' paragraph")), True),
    ('self-check: asking to add one keeps the default', bool(ns['MINIMAL_REQUEST'].search('Add a self-check at the end')), False),
    ('pasted: whole material dumped into the prompt is caught', ns['pasted_wholesale'](DOC, 'Use this: ' + DOC, 'Write a prompt for this'), True),
    ('pasted: quoting the lists the user asked to keep is allowed', ns['pasted_wholesale'](DOC, 'Objections: ' + DOC[:2000], 'Write a prompt; address the objections'), False),
    ('pasted: an explicit verbatim request is allowed', ns['pasted_wholesale'](DOC, DOC, 'Keep every line verbatim'), False),
    ('repair: invented slots become labels, own ones kept', repair('Dear {{hiring_manager_name}}, re {{topic}}', ['{{hiring_manager_name}}']), 'Dear [hiring manager name], re {{topic}}'),
]

failed = 0
for name, got, want in CASES:
    ok = got == want
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', name))
print('\n%d/%d passed' % (len(CASES) - failed, len(CASES)))
sys.exit(failed)
