"""Judge for "Try it" comparisons (2026-11-01): which of two answers better does what the user asked.

Try it can run this version of a prompt and the previous one on the same inputs. The judge reads
the user's original request, the inputs and both answers, and picks one with short reasons. Small
models tend to prefer whichever answer they read first, so it judges twice with the order swapped
and only names a winner when both agree; otherwise the result is a tie.
"""
import json
import time
import urllib.request

SCHEMA = {
    'type': 'object',
    'properties': {
        'better': {'type': 'string', 'enum': ['first', 'second', 'tie']},
        'reasons': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3},
    },
    'required': ['better', 'reasons'],
}
SYSTEM = """You compare two answers that two versions of a prompt produced from the same inputs.
Decide which answer better does what the USER REQUEST asked for: it covers what was asked, uses the
inputs faithfully without inventing facts, and follows any format or length the request set.
Ignore length and style unless the request asked for them. If they are about equally good, say tie.
Give at most three short reasons, each naming a concrete difference."""


def _ask(url, model, num_ctx, request, values, first, second, timeout):
    inputs = '\n'.join('%s: %s' % (k, v[:2000]) for k, v in (values or {}).items()) or '(none)'
    user = ('USER REQUEST:\n%s\n\nINPUTS:\n%s\n\nFIRST ANSWER:\n%s\n\nSECOND ANSWER:\n%s'
            % (request[:6000], inputs, first[:12000], second[:12000]))
    body = {'model': model, 'system': SYSTEM, 'prompt': user, 'format': SCHEMA, 'stream': False, 'think': False,
            'keep_alive': -1, 'options': {'temperature': 0, 'num_ctx': num_ctx, 'num_predict': 400}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(json.load(r).get('response') or '{}')
    return out.get('better', 'tie'), [str(x) for x in out.get('reasons', [])][:3]


def judge(request, values, answer_a, answer_b, url, model, num_ctx, timeout=300):
    """Compare answer_a (this version) with answer_b (previous). Returns winner a|b|tie and reasons."""
    if not (answer_a or '').strip() or not (answer_b or '').strip():
        raise ValueError('Both answers are needed to compare')
    started = time.time()
    pick1, why1 = _ask(url, model, num_ctx, request, values, answer_a, answer_b, timeout)
    pick2, why2 = _ask(url, model, num_ctx, request, values, answer_b, answer_a, timeout)
    one = {'first': 'a', 'second': 'b'}.get(pick1, 'tie')
    two = {'first': 'b', 'second': 'a'}.get(pick2, 'tie')
    winner = one if one == two else 'tie'
    reasons = why1 if winner == one and one != 'tie' else (why1 + why2)[:3]
    return {'winner': winner, 'reasons': reasons, 'agreed': one == two,
            'ms': int((time.time() - started) * 1000), 'model': model}
