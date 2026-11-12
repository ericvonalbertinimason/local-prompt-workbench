"""Offline checks for pipeline.choose_provider and the monthly API spend cap. No network, no stack."""
import os
import sys
import tempfile
from pathlib import Path

spend = Path(tempfile.mkdtemp()) / 'spend.json'
os.environ.update(SPEND_FILE=str(spend), SPEND_CAP_USD='0.01')
os.environ.pop('MODEL_PROVIDER', None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'harness'))
import pipeline as p  # noqa: E402

checks = []
checks.append(('default is local', p.choose_provider({}) == 'ollama'))
checks.append(('quality=local', p.choose_provider({'quality': 'local'}) == 'ollama'))
checks.append(('quality=api picks API_PROVIDER', p.choose_provider({'quality': 'api'}) == 'anthropic'))
os.environ['API_PROVIDER'] = 'openai'
checks.append(('API_PROVIDER respected', p.choose_provider({'quality': 'api'}) == 'openai'))
os.environ.pop('API_PROVIDER')
checks.append(('explicit provider wins', p.choose_provider({'quality': 'local'}, 'anthropic') == 'anthropic'))
cost = p.record_spend({'input_tokens': 5000, 'output_tokens': 2000})
checks.append(('cost estimate', abs(cost - 0.015) < 1e-9))
checks.append(('summary', p.spend_summary()['month'] == 0.015))
checks.append(('cap reached -> local', p.choose_provider({'quality': 'api'}) == 'ollama'))
os.environ['MODEL_PROVIDER'] = 'anthropic'
checks.append(('cap applies to MODEL_PROVIDER too', p.choose_provider({}) == 'ollama'))

failed = [n for n, ok in checks if not ok]
print('%d/%d passed' % (len(checks) - len(failed), len(checks)), *('FAIL ' + n for n in failed), sep='\n')
sys.exit(1 if failed else 0)
