"""Internal chatbot, first slice (roadmap step 3): multi-turn chat on the workbench core.

  python chat/chat.py                  # new conversation
  python chat/chat.py --session NAME   # continue one
  python chat/chat.py --list           # list conversations
  python chat/chat.py --index FOLDER   # make a folder of your documents searchable (rerun to refresh)

Once a folder is indexed, each message searches it and the reply cites the excerpts it used.

Uses harness/core/llm.py, so the same engines apply: Ollama by default, LOCAL_ENGINE=llamacpp for
llama-server, and MODEL_PROVIDER / quality settings for a paid API under the same spend cap.
Conversations are kept in chat/data/chat.sqlite (git-ignored). Also:
the phone page is chat/web.py; type /help for the tools.
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault('OLLAMA_CHAT_URL', 'http://127.0.0.1:11434/api/chat')
os.environ.setdefault('ENGINE_URL', 'http://127.0.0.1:8090/v1/chat/completions')
os.environ.setdefault('SPEND_FILE', str(HERE / 'data' / 'api-spend.json'))
sys.path.insert(0, str(HERE.parent / 'harness'))
from core import llm, store  # noqa: E402
import docs  # noqa: E402
import tools  # noqa: E402

SYSTEM = ('You are a helpful, concise assistant running on the user\'s own computer. '
          'Say so plainly when you do not know something; never invent facts, names or numbers.')
KEEP_TURNS = 20  # earlier turns are dropped from the model's context (still stored)


def db():
    (HERE / 'data').mkdir(exist_ok=True)
    c = sqlite3.connect(HERE / 'data' / 'chat.sqlite', timeout=15)
    store.prepare(c)  # the journal setting the workbench uses on this machine
    c.execute('CREATE TABLE IF NOT EXISTS messages (session TEXT, at REAL, role TEXT, content TEXT)')
    return c


def history(c, session):
    rows = c.execute('SELECT role, content FROM messages WHERE session=? ORDER BY rowid', (session,)).fetchall()
    return rows[-KEEP_TURNS * 2:]


def transcript(rows, user):
    """The core's llm() takes one system and one user message, so earlier turns go in as a transcript."""
    if not rows:
        return user
    past = '\n\n'.join(('User: ' if r == 'user' else 'Assistant: ') + t for r, t in rows)
    return 'Conversation so far:\n\n' + past + '\n\nUser: ' + user + '\n\nReply to the last user message.'


DOCS_NOTE = ('\n\nExcerpts from the user\'s own documents that may help:\n\n%s\n\n'
             'Use them when they answer the question and cite them as [1], [2]. If they do not answer it, '
             'say so instead of guessing.')


def save(c, session, user, text):
    now = time.time()
    c.executemany('INSERT INTO messages VALUES (?,?,?,?)',
                  [(session, now, 'user', user), (session, now + 1e-6, 'assistant', text)])
    c.commit()


AS_PROMPT = ('Follow these instructions, written by the user in their Prompt Workbench. The user\'s messages '
             'supply the inputs; fill any {{placeholders}} from them, and ask for an input only if it is missing.\n\n'
             '%s\n\nNever invent facts, names or numbers.')


def system_for(c, session):
    """The plain assistant, or the saved/new prompt chosen with /use or /run."""
    active = tools.state(c, session)
    return (AS_PROMPT % active['prompt'], active['title']) if active['prompt'] else (SYSTEM, '')


def reply(c, session, user, provider=None, index=None):
    result = tools.run(user, index, c, session)
    if result is not None:          # a slash command: no model call; kept in history for follow-ups
        save(c, session, user, result)
        return result, {'model': 'tool', 'output_tokens': 0, 'sources': [], 'as': system_for(c, session)[1]}
    rows, skip = [], False          # leave slash commands and their outputs out of the model's view
    for r, t in history(c, session):
        if r == 'user':
            skip = t.startswith('/')
        if not skip:
            rows.append((r, t))
    prompt, sources = transcript(rows, user), []
    hits = docs.search(index, user) if index is not None else []
    if hits:
        excerpts, sources = docs.context(hits)
        prompt += DOCS_NOTE % excerpts
    system, title = system_for(c, session)
    text, usage = llm.llm(system, prompt, provider=provider or llm.choose_provider({}))
    text = text.strip()
    save(c, session, user, text)
    usage['sources'] = sources
    usage['as'] = title
    return text, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', default=time.strftime('chat-%Y%m%d-%H%M%S'))
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--index', metavar='FOLDER', help='add or refresh a folder of documents, then exit')
    ap.add_argument('--no-docs', action='store_true', help='chat without searching your documents')
    args = ap.parse_args()
    c = db()
    index = docs.open_index(HERE / 'data' / 'docs.sqlite')
    if args.index:
        print(docs.index(index, args.index))
        return
    if args.no_docs or not index.execute('SELECT count(*) FROM files').fetchone()[0]:
        index = None
    if args.list:
        for s, n, last in c.execute('SELECT session, count(*), max(at) FROM messages GROUP BY session ORDER BY 3 DESC'):
            print('%-28s %3d messages  %s' % (s, n, time.strftime('%Y-%m-%d %H:%M', time.localtime(last))))
        return
    print('Session %s. Empty line or Ctrl+C to quit.' % args.session)
    while True:
        try:
            user = input('\nyou> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            break
        text, usage = reply(c, args.session, user, index=index)
        print('\nbot> ' + text)
        for s in usage['sources']:
            print('     ' + s)
        print('     [%s, %d tokens%s]' % (usage['model'], usage['output_tokens'],
                                          ', as "%s"' % usage['as'] if usage['as'] else ''))


if __name__ == '__main__':
    main()
