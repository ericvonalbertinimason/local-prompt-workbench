"""Offline checks for chat/chat.py: storage, transcript building, and that it calls core.llm. No model needed."""
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ['CHAT_EMBED'] = 'off'  # offline: keyword search only; meaning search is checked live
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'chat'))
import chat  # noqa: E402

calls = []
chat.llm.llm = lambda system, user, provider=None: (calls.append(user) or ('  reply %d  ' % len(calls)),
                                                    {'model': 'fake', 'output_tokens': 3})
c = sqlite3.connect(':memory:')
c.execute('CREATE TABLE messages (session TEXT, at REAL, role TEXT, content TEXT)')

checks = []
text, _ = chat.reply(c, 's1', 'My name is Sam.', provider='ollama')
checks.append(('first turn is the bare message', calls[0] == 'My name is Sam.'))
checks.append(('reply stripped', text == 'reply 1'))
chat.reply(c, 's1', 'What is my name?', provider='ollama')
checks.append(('second turn carries history', 'User: My name is Sam.' in calls[1] and 'Assistant: reply 1' in calls[1]))
checks.append(('stored in order', [r for r, _ in chat.history(c, 's1')] == ['user', 'assistant', 'user', 'assistant']))
chat.reply(c, 's2', 'hello', provider='ollama')
checks.append(('sessions are separate', calls[2] == 'hello'))
for i in range(50):
    c.execute('INSERT INTO messages VALUES (?,?,?,?)', ('s3', i, 'user' if i % 2 == 0 else 'assistant', str(i)))
checks.append(('context window capped', len(chat.history(c, 's3')) == chat.KEEP_TURNS * 2))

# Document search
import tempfile  # noqa: E402
import docs  # noqa: E402
d = Path(tempfile.mkdtemp())
(d / 'trip.md').write_text('The team offsite is in Lisbon on 14 March. Budget is 900 euros per person.', encoding='utf-8')
(d / 'notes.txt').write_text('Grocery list: apples, oats, coffee.', encoding='utf-8')
(d / '.env').write_text('API_KEY=secret', encoding='utf-8')
(d / 'api-key.txt').write_text('sk-secret', encoding='utf-8')
ix = docs.open_index(':memory:')
stats = docs.index(ix, d, log=lambda *a: None)
checks.append(('indexes text files, skips secrets', stats['indexed'] == 2))
checks.append(('unchanged files skipped on refresh', docs.index(ix, d, log=lambda *a: None)['unchanged'] == 2))
hits = docs.search(ix, 'Where is the offsite and what is the budget?')
checks.append(('finds the right file first', hits and hits[0][0].endswith('trip.md')))
checks.append(('no secret text indexed', not docs.search(ix, 'secret')))
calls.clear()
text, usage = chat.reply(c, 's4', 'Where is the offsite?', provider='ollama', index=ix)
checks.append(('excerpts reach the model', 'Lisbon' in calls[0] and '[1] trip.md' in calls[0]))
checks.append(('sources returned', usage['sources'] and usage['sources'][0].startswith('[1] ')))
# Tools (slash commands): no model call, stored for follow-ups
import tools  # noqa: E402
calls.clear()
text, usage = chat.reply(c, 's5', '/docs offsite budget', index=ix)
checks.append(('/docs runs without the model', not calls and 'trip.md' in text and usage['model'] == 'tool'))
checks.append(('/help lists commands', '/prompts' in chat.reply(c, 's5', '/help')[0]))
tools.workbench = lambda tool, args, timeout=0: 'called %s %s' % (tool, json.dumps(args))
checks.append(('/prompts calls the workbench', chat.reply(c, 's5', '/prompts resume')[0] == 'called find_saved_prompts {"query": "resume"}'))
checks.append(('unknown command', chat.reply(c, 's5', '/nope')[0].startswith('Unknown')))
checks.append(('tool results kept in history', sum(1 for r, _ in chat.history(c, 's5') if r == 'assistant') == 4))
# Integration with the Prompt Workbench: /use, /prompt + /run, /promptify, /save
SAVED_OUT = ('[3] Master Resume Generator\nAct as a resume architect. Use {{files}}.\n\n'
             '[1] Three-bullet article summary\nSummarize {{article}} in exactly three bullets.')
CREATED_OUT = 'run ab12cd34 | QA passed\nAssumptions: none\n---\n# Task\nTurn {{notes}} into a 3-bullet summary.'
wb = []
def fake_wb(tool, args, timeout=0):
    wb.append((tool, args))
    return {'find_saved_prompts': SAVED_OUT, 'create_prompt': CREATED_OUT, 'pin_prompt': 'Pinned as /summary'}[tool]
tools.workbench = fake_wb
checks.append(('saved prompts parsed', [t for _, t, _ in tools.saved_prompts(SAVED_OUT)] == ['Master Resume Generator', 'Three-bullet article summary']))
checks.append(('created prompt parsed', tools.created(CREATED_OUT) == ('ab12cd34', '# Task' + chr(10) + 'Turn {{notes}} into a 3-bullet summary.')))
out, usage = chat.reply(c, 's6', '/use resume')
checks.append(('/use picks the best match', 'Master Resume Generator' in out and usage['as'] == 'Master Resume Generator'))
calls.clear()
chat.reply(c, 's6', 'Here are my files', provider='ollama')
sys_used = []
chat.llm.llm = lambda system, user, provider=None: (sys_used.append(system) or calls.append(user) or 'ok', {'model': 'fake', 'output_tokens': 1})
_, usage = chat.reply(c, 's6', 'Make it one page', provider='ollama')
checks.append(('/use makes the saved prompt the instructions', 'Act as a resume architect' in sys_used[-1] and usage['as']))
checks.append(('command output kept out of the model view', 'Now chatting as' not in calls[-1]))
chat.reply(c, 's6', '/use off')
chat.reply(c, 's6', 'plain again', provider='ollama')
checks.append(('/use off restores the plain assistant', sys_used[-1] == chat.SYSTEM))
checks.append(('/run needs a prompt first', 'No prompt' in chat.reply(c, 's7', '/run')[0]))
out, _ = chat.reply(c, 's7', '/prompt summarize meeting notes')
checks.append(('/prompt offers /run and /save', '/run' in out and '/save' in out))
chat.reply(c, 's7', '/run')
chat.reply(c, 's7', 'notes: we ship Friday', provider='ollama')
checks.append(('/run chats as the new prompt', 'Turn {{notes}} into a 3-bullet summary.' in sys_used[-1]))
out, _ = chat.reply(c, 's7', '/save Meeting summary')
checks.append(('/save pins the last run', wb[-1] == ('pin_prompt', {'run_id': 'ab12cd34', 'pinned': True, 'title': 'Meeting summary'})))
checks.append(('/promptify needs a conversation', 'Nothing' in chat.reply(c, 's8', '/promptify')[0]))
chat.reply(c, 's8', 'I want weekly updates in three bullets, no jargon', provider='ollama')
chat.reply(c, 's8', '/promptify')
tool, args = wb[-1]
checks.append(('/promptify sends the conversation as source material', tool == 'create_prompt' and 'no jargon' in args['source_material'] and '/promptify' not in args['source_material']))
checks.append(('long text is chunked', len(docs.chunks('x' * 5000)) > 1))

failed = [n for n, ok in checks if not ok]
print('%d/%d passed' % (len(checks) - len(failed), len(checks)), *('FAIL ' + n for n in failed), sep='\n')
sys.exit(1 if failed else 0)
