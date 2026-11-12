"""Add the workbench MCP bridge to a Claude desktop config file (used by add_to_claude_app.ps1).

  python claude_app_config.py <claude_desktop_config.json> [--dry-run]

Only the mcpServers.prompt-workbench entry is added; every other key is kept as it was.
A timestamped backup is written next to the file first.
"""
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    path = Path(sys.argv[1])
    dry = '--dry-run' in sys.argv
    data = json.loads(path.read_text(encoding='utf-8'))
    servers = data.setdefault('mcpServers', {})
    if 'prompt-workbench' in servers:
        print('already configured:', ', '.join(servers))
        return 0
    servers['prompt-workbench'] = {'command': sys.executable, 'args': [str(HERE / 'mcp_stdio_bridge.py')]}
    if dry:
        print('dry run, would add:', json.dumps(servers['prompt-workbench']))
        return 0
    backup = path.with_name(path.stem + '.backup-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    check = json.loads(path.read_text(encoding='utf-8'))
    print('added; servers now:', ', '.join(check['mcpServers']))
    print('backup:', backup)
    return 0


if __name__ == '__main__':
    sys.exit(main())
