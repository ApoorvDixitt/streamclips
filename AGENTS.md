# AGENTS.md

This repository is an agent skill. Read `SKILL.md` first; it is the operating manual. Details live in `references/`.

- Run scripts with the repo's venv Python: `.venv/bin/python scripts/<name>.py` (Windows: `.venv\Scripts\python`).
- Every script prints JSON and exits non-zero on failure. Read the `error` field and `references/troubleshooting.md`.
- API keys come from environment variables only (`GROQ_API_KEY`, `YOUTUBE_API_KEY`, `ELEVENLABS_API_KEY`). Never write them to files or print them.
- Campaign data lives in the user's campaign folder, never inside this repo.
- Tests: `.venv/bin/python -m pytest -q tests` (pure unit tests; no network).
- Style: Python 3.10+, standard library first, small dependencies, no frameworks. Keep scripts single-purpose and JSON in/out.
