# Prompt Workbench

A local prompt writer that runs on your own GPU. You describe a task, and it writes a reusable prompt,
checks it with automatic QA rules (missing requirements, invented facts, unsafe agent steps) and revises
it when a check fails. It also includes a small chatbot that can search your own documents.

- **Workbench** (`harness/`): a web page on `http://127.0.0.1:8789`, plus an MCP server so Claude Code
  or the Claude desktop app can call it (`create_prompt`, `revise_prompt`, `find_saved_prompts`, `pin_prompt`, …).
- **Chatbot** (`chat/`): a chat page on `http://127.0.0.1:8791` with document search (keyword plus
  meaning search), OCR for scanned PDFs, and slash commands that use the workbench (`/prompt`, `/use`,
  `/promptify`, `/run`, `/save`). Type `/help` for the list.
- **Models**: local via Ollama (default) or llama.cpp (`bench/llama_server.ps1`). Anthropic or OpenAI can
  be used optionally, with a spending cap.

## Requirements

- Windows 11 with Docker Desktop, plus Ollama or llama.cpp
- An NVIDIA GPU with about 6 GB of free memory for the default 4B model (Qwen 3.5 4B)
- Python 3.12 for the chatbot and the tests

## Start

```
start.cmd
```

`setup.ps1` (which `start.cmd` runs) starts only what isn't running yet. It creates the model from
`outputs/Qwen-Assistant-Fast.Modelfile`, starts the workbench container and checks its health.
Pass `-RegisterClaudeCode` to add the MCP server to Claude Code.

Chatbot:

```
python chat/web.py                       # chat page on 127.0.0.1:8791
python chat/chat.py --index C:\my\notes  # index a folder for document search
```

## Use it from your phone (optional)

Both pages listen only on 127.0.0.1. To reach them from your own devices, use Tailscale (tailnet only):

```
tailscale serve --bg 8789
tailscale serve --bg --https=8791 http://127.0.0.1:8791
```

Then set `TAILNET_HOSTS=<your-pc>.<your-tailnet>.ts.net` so the pages accept that host name. Requests
from any other host are refused.

## Optional API models

Put an API key in `harness/data/api-key.txt`. That folder is git-ignored; never commit a key. Set
`SPEND_CAP_USD` to cap spending; when the cap is reached, requests fall back to the local model.

## Tests

The tests run offline and need no GPU or model:

```
python bench/test_qa.py
python bench/test_chat.py
python bench/test_engine.py
python bench/test_cost_guard.py
```

`bench/health.sh` checks a running stack, and `bench/versions.py` reports newer component versions.
`bench/ROADMAP_own_harness.md` describes where the project is going.

## Privacy

Prompt history, chats, document indexes and keys stay in `harness/data/` and `chat/data/`, which are
git-ignored and never leave your PC.

## License

MIT, see [LICENSE](LICENSE).
