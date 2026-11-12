"""Try it judge (harness/try_judge.py): a winner only when both orders agree.

The model call is replaced by scripted answers, so this runs without Ollama.

  python bench/test_try_judge.py
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location('try_judge', Path(__file__).resolve().parents[1] / 'harness' / 'try_judge.py')
tj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tj)


def scripted(*answers):
    """The judge sees (A, B) first, then (B, A); answers are what the model says each time."""
    replies = iter(answers)
    tj._ask = lambda url, model, ctx, request, values, first, second, timeout: next(replies)


def run():
    return tj.judge('Summarize {{article}}', {'article': 'text'}, 'answer A', 'answer B', 'u', 'm', 1)


results = []
scripted(('first', ['A keeps the three bullets']), ('second', ['A keeps the three bullets']))
r = run()
results.append((r['winner'] == 'a' and r['agreed'] and r['reasons'] == ['A keeps the three bullets'], 'A wins in both orders -> a'))
scripted(('second', ['B cites the article']), ('first', ['B cites the article']))
results.append((run()['winner'] == 'b', 'B wins in both orders -> b'))
scripted(('first', ['first is better']), ('first', ['first is better']))
r = run()
results.append((r['winner'] == 'tie' and not r['agreed'], 'position bias (always "first") -> tie, flagged as disagreement'))
scripted(('tie', ['same']), ('tie', ['same']))
results.append((run()['winner'] == 'tie', 'tie both times -> tie'))
try:
    tj.judge('x', {}, 'answer', '  ', 'u', 'm', 1)
    results.append((False, 'empty answer refused'))
except ValueError:
    results.append((True, 'empty answer refused'))

failed = 0
for ok, line in results:
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
print('\n%d/%d passed' % (len(results) - failed, len(results)))
sys.exit(failed)
