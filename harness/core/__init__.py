"""Reusable core of the prompt workbench (2026-11-08, bench/ROADMAP_own_harness.md section 2).

Parts that other apps (the planned internal chatbot) can use without the workbench:
  convert  file -> text with MarkItDown, scanned pages listed for OCR
  ocr      read scanned pages and pictures with a local vision model
  files    files sent from Claude: text now, scans parked and read when a run starts
  store    SQLite: short connections, read-only opener, journal setting, column migrations
  qa       QA rules for prompts (contract, invented facts and placeholders, limits, ...)
  mcp      MCP call log and per-client success stats
  llm      model calls: Ollama, llama.cpp or any OpenAI-compatible server, paid APIs, spend cap

Modules take their settings as arguments rather than reading the workbench's globals, so they
work in any app. Moved here without behaviour changes; the workbench's tests are the gate.
"""
