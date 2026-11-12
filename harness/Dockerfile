# syntax=docker/dockerfile:1
# Base pinned by digest: an unannounced update of python:3.12-slim invalidated the cache on
# 2026-10-13 and the rebuild re-downloaded every dependency at ~70 KB/s (20+ minutes).
# bench/versions.py reports when newer components exist; update the pin deliberately.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
# MarkItDown converts attached source documents to text. The pip cache mount keeps downloaded
# wheels between builds, so a version bump only fetches what changed.
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 10 "markitdown[pdf,docx,pptx,xlsx]==0.1.9"
COPY server.py pipeline.py try_inputs.py try_judge.py saved_library.py prompt_view.html ./
COPY core ./core
COPY templates ./templates
COPY static ./static
CMD ["python", "-u", "server.py"]
