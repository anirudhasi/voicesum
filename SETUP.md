# Getting VoiceSum running

Offline meeting transcription, speaker identification and Record of Meeting
generation. Once set up, it runs with no internet access at all.

## Quick start

```bash
git clone https://github.com/anirudhasi/voicesum.git
cd voicesum
python setup.py
```

`setup.py` installs dependencies and downloads the models. It needs network
access while it runs; nothing afterwards does.

Then, in two terminals:

```bash
cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

Open <http://localhost:8080/signup>. **The first account created becomes the
administrator.**

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.12 recommended |
| Node.js 18+ | For the frontend |
| ffmpeg | Must be on PATH. Audio decoding needs it. |
| Ollama | For the language model. <https://ollama.com> |
| Disk | About 15 GB for models |
| GPU | Optional but strongly recommended; CPU transcription is many times slower |

## Why the models are not in this repository

They cannot be. The speech model alone is 2.9 GB, which exceeds GitHub's
100 MB per-file limit and also Git LFS's 2 GB per-file limit. `setup.py`
fetches them instead, and `python setup.py --check` verifies an existing
install without downloading anything.

| Component | Model | Origin | Size |
|---|---|---|---|
| Speech to text | Whisper large-v3 (CTranslate2) | OpenAI, US | 2.9 GB |
| Word alignment | wav2vec2 | Meta, US | 1.1 GB |
| Diarization | pyannote community-1 | pyannote, France | 32 MB |
| Speaker embedding | ECAPA-TDNN | SpeechBrain | 85 MB |
| Text embedding | mxbai-embed-large-v1 | Mixedbread, Germany | 640 MB |
| Language model | Phi-4 via Ollama | Microsoft, US | 9.1 GB |

All are permissively licensed and run locally.

## Offline operation

The application makes no outbound connection at runtime:

- Hugging Face offline variables are set before any library that reads them is
  imported.
- Fonts are self-hosted; there is no CDN request.
- ChromaDB telemetry is disabled explicitly.
- The language model is served from `localhost` by Ollama.

`backend/tests/test_offline_egress.py` asserts this and fails the build if a
reference to an external host reappears.

## Configuration

Copy `backend/.env.example` to `backend/.env` to override defaults. Useful keys:

```bash
EMBEDDING_MODEL=mxbai-embed-large-v1   # must match a directory in runtime/embeddings/
WHISPER_DEVICE=auto                    # cuda | cpu | auto
WHISPER_COMPUTE_TYPE=int8              # int8 | float16
OLLAMA_SERVER_URL=http://localhost:11434
```

Leave `JWT_SECRET` unset. A per-installation key is generated on first run and
stored under `backend/runtime/secrets/`. A shared secret across installs would
be equivalent to no secret, since anyone with a copy of the build could mint
tokens for any installation.

## Tests

```bash
cd backend && python -m pytest tests/ -q
```

## Documentation

| Document | Contents |
|---|---|
| `docs/PROGRESS.md` | What has been built, with test counts |
| `docs/REVAMP-PLAN.md` | Engineering plan, each change with its rationale |
| `docs/IMPLEMENTATION-AND-VALIDATION.md` | Sequencing and how each goal is proven |
| `docs/OBSERVABILITY-AND-AUDIT.md` | Admin observability and audit design |
| `docs/observations/` | Point-in-time findings, including the parameter audit |
