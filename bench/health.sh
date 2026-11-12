#!/usr/bin/env bash
# One-line-per-check health report for the prompt workbench. Read-only.
#   bash bench/health.sh
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
fails=0
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s  ->  %s\n' "$1" "$2"; fails=$((fails+1)); }
code() { curl -s -m "${2:-6}" -o /dev/null -w '%{http_code}' -H 'Host: 127.0.0.1:8789' "$1" 2>/dev/null; }

echo "Prompt workbench health"

if docker info >/dev/null 2>&1; then ok "Docker engine"; else bad "Docker engine" "start Docker Desktop"; fi

# n8n was retired on 2026-10-28 (the native pipeline and the saved library no longer need it).
for c in prompt-optimizer prompt-workbench; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null) || st="not running (Docker down or container missing)"
  [ "$st" = running ] && ok "container $c" || bad "container $c" "$st"
done

if [ "$(code http://127.0.0.1:11434/api/tags)" = 200 ]; then
  pinned=$(curl -s -m 6 http://127.0.0.1:11434/api/ps | python -c "import json,sys; ms=json.load(sys.stdin).get('models',[]); print(','.join(m['name']+'@'+str(m.get('context_length')) for m in ms) or 'none')" 2>/dev/null)
  ok "Ollama serving (resident: ${pinned:-none})"
else
  bad "Ollama serving" "nothing on 11434 - see README.md 'If it does not load'"
fi

status=$(curl -s -m 20 -H 'Host: 127.0.0.1:8789' http://127.0.0.1:8789/api/status 2>/dev/null)
if [ -n "$status" ]; then
  echo "$status" | python -c "
import json,sys
d=json.load(sys.stdin)
print(('  OK    ' if d['mcp']['connected'] else '  FAIL  ')+'MCP optimizer (%d tools)' % len(d['mcp']['tools']))
print('  OK    workbench UI http://127.0.0.1:8789 (%s, ctx %s)' % (d['model'], d['context']))
" || bad "workbench status" "unreadable /api/status"
  echo "$status" | python -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['mcp']['connected'] else 1)" || fails=$((fails+1))
else
  bad "workbench UI" "no answer on 8789"
fi

# The workbench's own MCP server (what Claude Code connects to).
mcp=$(curl -s -m 8 -H 'Host: 127.0.0.1:8789' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:8789/mcp 2>/dev/null   | python -c "import json,sys; print(len(json.load(sys.stdin)['result']['tools']))" 2>/dev/null)
[ -n "$mcp" ] && ok "workbench MCP server http://127.0.0.1:8789/mcp ($mcp tools)" || bad "workbench MCP server" "no tools/list answer on /mcp"

# Connector activity (informational): calls per Claude client over the last 7 days.
conn=$(curl -s -m 8 -H 'Host: 127.0.0.1:8789' 'http://127.0.0.1:8789/api/connectors?days=7' 2>/dev/null | python -c "
import json,sys
d=json.load(sys.stdin); parts=[]
for c in d['clients']:
    if c['client']=='test': continue
    rate='' if c['success_rate'] is None else ', %d%% ok' % round(100*c['success_rate'])
    parts.append('%s %d calls%s' % (c['client'], c['calls'], rate))
print('; '.join(parts) or 'no calls from Claude in 7 days')" 2>/dev/null)
[ -n "$conn" ] && printf '  INFO  Claude connectors (7 days): %s\n' "$conn"

lib=$(curl -s -m 8 -H 'Host: 127.0.0.1:8789' http://127.0.0.1:8789/api/library 2>/dev/null | python -c "import json,sys; print(len(json.load(sys.stdin)['rows']))" 2>/dev/null)
[ -n "$lib" ] && ok "saved library ($lib prompts, in the workbench database)" || bad "saved library" "no answer on /api/library"
# The retired n8n container may still exist (stopped) as a rollback; say so only if it is running.
if [ "$(docker inspect -f '{{.State.Status}}' n8n 2>/dev/null)" = running ]; then
  printf '  INFO  n8n is running again (retired; only a PIPELINE=n8n rollback uses it)\n'
fi

echo
[ "$fails" -eq 0 ] && echo "All checks passed." || echo "$fails check(s) failed."
exit "$fails"
