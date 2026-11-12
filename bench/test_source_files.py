"""Files from Claude: the desktop bridge's file rules and the workbench's handling of what it sends.

Bridge (harness/mcp_stdio_bridge.py): reads only named files, refuses credentials and the workbench
data folder, rewrites create_prompt's `files` into `file_data`, advertises `files` in tools/list.
Workbench (harness/core/files.py): text now, scanned pages parked and read at run start, marks
outside the uploads folder ignored, parked files deleted after reading. No model or server needed.

  python bench/test_source_files.py
"""
import base64
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / 'harness'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('/', '_'), HARNESS / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = load('mcp_stdio_bridge')
sf = load('core/files')
tmp = Path(tempfile.mkdtemp())
for name, data in {'notes.txt': b'Brightwater regatta on May 3', 'Keynote_lecture.pdf': b'%PDF fake',
                   'Turkey trip itinerary.pdf': b'%PDF fake', '.env': b'TOKEN=x', 'api-key.txt': b'sk-test',
                   'tool.exe': b'MZ'}.items():
    (tmp / name).write_bytes(data)
results = []


def refused(paths):
    try:
        bridge.read_files(paths)
        return False
    except ValueError:
        return True


sent = bridge.read_files([str(tmp / 'notes.txt'), str(tmp / 'Keynote_lecture.pdf'), str(tmp / 'Turkey trip itinerary.pdf')])
results.append(([f['name'] for f in sent] == ['notes.txt', 'Keynote_lecture.pdf', 'Turkey trip itinerary.pdf']
                and base64.b64decode(sent[0]['data']).startswith(b'Brightwater'), 'ordinary files are read (incl. Keynote, Turkey)'))
results.append((refused([str(tmp / '.env')]) and refused([str(tmp / 'api-key.txt')]), 'credential-looking files are refused'))
results.append((refused([str(bridge.DATA_DIR / 'history.sqlite')]), 'the workbench data folder is refused'))
results.append((refused([str(tmp / 'tool.exe')]), 'unsupported file types are refused'))
results.append((refused([str(tmp / 'missing.pdf')]), 'missing files are reported'))
results.append((refused([str(tmp / 'notes.txt')] * 6), 'more than 5 files are refused'))

call = {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
        'params': {'name': 'create_prompt', 'arguments': {'request': 'r', 'files': [str(tmp / 'notes.txt')]}}}
out = json.loads(bridge.outgoing(call, json.dumps(call)))
args = out['params']['arguments']
results.append(('files' not in args and args['file_data'][0]['name'] == 'notes.txt', 'create_prompt files become file_data'))
other = {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call', 'params': {'name': 'find_runs', 'arguments': {}}}
results.append((bridge.outgoing(other, 'unchanged') == 'unchanged', 'other calls pass through untouched'))
listed = bridge.incoming({'method': 'tools/list'}, json.dumps({'result': {'tools': [
    {'name': 'create_prompt', 'inputSchema': {'type': 'object', 'properties': {}}}, {'name': 'pin_prompt', 'inputSchema': {}}]}}))
tools = json.loads(listed)['result']['tools']
results.append(('files' in tools[0]['inputSchema']['properties'] and 'properties' not in tools[1]['inputSchema'],
                'tools/list from the bridge advertises files on create_prompt only'))

data_dir = tmp / 'data'


def fake_convert(name, data):
    if name.endswith('.pdf'):
        return {'text': '', 'ocr_pages': [0, 1], 'ocr_skipped': 0}
    return {'text': data.decode(), 'ocr_pages': [], 'ocr_skipped': 0}


file_data = [{'name': 'notes.txt', 'data': base64.b64encode(b'Regatta on May 3').decode()},
             {'name': 'scan.pdf', 'data': base64.b64encode(b'%PDF scanned').decode()}]
material = sf.from_uploads(file_data, fake_convert, data_dir)
parked = list((data_dir / 'uploads').glob('*/scan.pdf'))
results.append((material.startswith(sf.MARK) and material.count(sf.MARK) == 2 and '--- notes.txt ---' in material and len(parked) == 1,
                'text kept, scanned file parked, OCR marks first (never cut by the size limit)'))
typed = sf.MARK + '0123456789abcdef0123456789abcdef/../../server.py|0'
done = sf.read_pending(material + '\n' + typed, lambda name, data, page: {'text': 'page %d of %s' % (page, data.decode())}, data_dir)
results.append(('[Page 2, read by OCR]\npage 1 of %PDF scanned' in done and 'read by OCR: check names' in done and sf.MARK not in done,
                'marks replaced by OCR text; a typed mark pointing elsewhere is ignored'))
results.append((not any((data_dir / 'uploads').iterdir()), 'parked file deleted after reading'))
old = data_dir / 'uploads' / ('f' * 32)
old.mkdir(parents=True)
(old / 'x.pdf').write_bytes(b'x')
os.utime(old, (time.time() - 2 * 86400,) * 2)
results.append((sf.prune(data_dir) == 1 and not old.exists(), 'parked files older than a day are pruned'))

failed = 0
for ok, line in results:
    failed += not ok
    print('  %s  %s' % ('PASS' if ok else 'FAIL', line))
print('\n%d/%d passed' % (len(results) - failed, len(results)))
sys.exit(failed)
