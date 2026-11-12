# Roadmap: your own harness and an internal chatbot

Drafted 2026-11-07. The workbench is already the harness (prompt building, structured output,
QA, history, MCP, UI, conversion, OCR). Ollama is only the inference engine. The plan is to
make the engine swappable, pull the reusable parts into a core, then build a chatbot on that core.

## 1. Swappable engine: llama.cpp (in progress)
- New provider `openai-compatible` in `pipeline.llm()`, with `ENGINE_URL` set to any local
  OpenAI-style server (llama-server, vLLM, LM Studio).
- First engine: llama.cpp `llama-server` on Windows/CUDA, loading the same GGUF Ollama
  already has, so nothing is downloaded twice.
- Gate: run the full, one-shot and pack suites against both engines. Compare faithful %, silent
  defects (must stay 0), latency and VRAM. Adopt only if quality is equal or better.
- Branch `engine-llamacpp` until the eval decides.

## 2. Core package (after 1)
- `core/` folder:
  - `llm` (providers, structured output, fallback, cost guard)
  - `qa` (the check framework)
  - `ingest` (MarkItDown and OCR)
  - `mcp` (server plumbing)
  - `store` (SQLite history)
- The workbench imports from `core/`, with no behaviour change. The existing tests are the gate:
  test_mcp, test_qa, test_convert and test_cost_guard.
- Once a second app uses it, move `core/` to its own repo and install it as a package.
  Don't fork; a fork drifts.

## 3. Internal chatbot (after 2)
- A new repo, `my-assistant`, on the core:
  - chat sessions in SQLite
  - search over your own documents: ingest, then embeddings, then a vector index (sqlite-vec)
  - a few tools (search the files, workbench prompts, calendar later)
- The UI reuses the installable phone app (PWA) shell, reachable over Tailscale as the workbench is.
- **Hardware:** the 12 GB RTX 3060 fits one 4B at 64K or a 9–14B at smaller context, not both
  together. A multi-user chatbot needs vLLM on Linux/WSL2 or a bigger GPU.
- **Security first:** the chatbot reads your files, so it serves on the tailnet only, never
  public, and uses the same file-path allowlist as the workbench `files` argument.

## Result of step 1 (2026-11-07)
Fast suite, same cases, same optimizer and QA. Only the drafting engine changed:

| Engine | Success | Faithful | QA flagged | Silent | Median | p90 |
|---|---|---|---|---|---|---|
| Ollama 0.40 (baseline) | 14/14 | 13/13 (100%) | 2 | 0 | 7.8s | — |
| llama.cpp b12182, Vulkan, Qwen3.5-4B Q4_K_M | 14/14 | 13/13 (100%) | 2 | 0 | 11.3s | 22.8s |

- Quality is the same, and the engine never fell back to Ollama. Grammar-enforced JSON returned valid
  output every time.
- Slower. Reasons:
  - It uses the Vulkan build, not CUDA.
  - Both engines shared the 12 GB GPU, with Ollama's 4B still pinned for the optimizer.
  - The first few cases overlapped another eval run.
- Decision: Ollama stays the default (LOCAL_ENGINE=ollama). llamacpp is a supported option.
- To retry for speed: use a CUDA build of llama-server, run with the GPU otherwise idle, or let
  llama.cpp do the optimizer step as well, so one model holds the GPU.

## Speed, each engine alone on an idle GPU (2026-11-07, bench/engine_bench.py)
| Engine | Median | Throughput | Valid |
|---|---|---|---|
| Ollama | 7.8s | 22.4 tok/s | 5/5 |
| llama.cpp CUDA 13.5 (64K context) | 9.0s | 20.5 tok/s | 5/5 |

These are close. llama.cpp gives no speed win on this setup, so Ollama stays the default.
Both runs were slower than earlier probes, so the GPU may not have been fully idle.

## Step 3 status (2026-11-08): first version built, in `chat/`
- `chat/chat.py`: multi-turn chat on `core.llm`, with conversations in `chat/data/chat.sqlite`. It uses the same
  engines and spend cap as the workbench.
- `chat/docs.py`: `--index FOLDER` makes documents searchable through core.convert and SQLite FTS5
  (BM25). Each reply cites the excerpts it used. Secrets (.env, keys, api-key files, sqlite) are never
  indexed.
- `chat/tools.py`: tools as slash commands. A 4B model is unreliable at deciding to call tools itself.
  - `/docs QUERY` searches your documents.
  - `/prompts [QUERY]` finds your saved workbench prompts.
  - `/prompt REQUEST` writes a QA-checked prompt.
- `chat/web.py` + `page.html`: a phone chat page on 127.0.0.1:8791, with a Host allowlist and dark mode.
  To use it on the tailnet: `tailscale serve --bg --https=8791 http://127.0.0.1:8791` (the user runs this).
- Tests: `bench/test_chat.py` passes 18/18 offline. Real runs worked: remembered context across turns, gave
  correct cited answers from `bench/`, a web reply at phone width, and returned real saved prompts.
- Next, when wanted:
  - ~~semantic search with an embedding model~~ done: hybrid keyword + meaning (nomic-embed-text in Ollama, rank fusion); CHAT_EMBED=off for keyword only
  - ~~OCR for scanned files at index time~~ done (chat/docs.py, local vision model; CHAT_OCR=off skips)
  - ~~moving chat storage onto `core.store`~~ done (same journal setting as the workbench)

## Status (2026-11-08): roadmap steps 1-3 done
- Step 1: engine is swappable; llamacpp supported, Ollama the default.
- Step 2: harness/core holds convert, ocr, files, store, llm, qa and mcp. The workbench is rebuilt on it (ca8e946).
- Step 3: chatbot in chat/ (chat, hybrid document search, OCR, tools, phone page).

## Verification (2026-11-10)
- Live workbench runs on harness/core (llm, qa, mcp, store and others inside the container). test_mcp passes 40/40.
- Search by meaning: on 8 reworded questions over 8 files, the right file ranked first 8/8 with hybrid search, versus 2/8 with keyword alone.
- Offline tests: test_chat 18/18, test_engine 11/11, test_cost_guard 9/9, test_qa 80/80.

## Chatbot and workbench integration (2026-11-10)
- `/use QUERY`: chat as a saved prompt. It becomes the assistant's instructions, and {{placeholders}} are filled from your messages.
- `/prompt` then `/run`: chat as a prompt the workbench just wrote. `/save TITLE` pins it.
- `/promptify`: the conversation, without commands, goes to the workbench as source material. Your stated rules become fixed rules in the prompt.
- Tests: test_chat 30/30. Live run: `/use` gave a correct 3-bullet summary, and `/promptify` + `/run` gave a correct Done/Next/Blocked update.

## Chat tab inside the workbench (2026-11-10)
- The workbench has a **Chat** tab: in the sidebar on a computer, the fifth button of the bottom tab bar on the phone.
  The chatbot keeps running on this PC (`python chat/web.py`, port 8791, where your document index and GPU are).
  The workbench passes `/api/chat` and `/api/chat/history` through to it (`CHAT_URL`, default
  `http://host.docker.internal:8791`; empty hides the tab). So the workbench address and phone app now include chat.
- Quick-command chips: /help, /use, /prompt, /run, /promptify, /docs. The conversation is kept per browser,
  and New chat starts a fresh one. Workbench chat sessions are stored as `wb-...` in chat/data/chat.sqlite.
- If the chatbot isn't running, the tab says so and gives the command to start it. The chat routes follow the
  same rules as the rest of the workbench: local hosts only, and POST requests must come from the workbench page.
- Checked: proxy round trip (0.9 s reply), history after a reload, desktop and 375 px phone layouts, test_mcp 40/40.
