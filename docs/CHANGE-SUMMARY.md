# Change Summary

Every change made to the application since the original codebase, with the
reason for it. Type is **Fix** (the original was defective), **Changed** (the
original worked but had to change for a stated constraint), or **New** (a
capability that did not exist).

Status as of 2026-09-14. Detail and test names for each item are in
`PROGRESS.md`; plain-language workstream status is in `WORKSTREAMS.md`.

## 1. Speaker identification (reported challenge 1)

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Speaker correction pass | An unmeasured speaker label scored 1.0, so the correction test could never pass for "Speaker N" labels | Unmeasured label scores 0.0; accept 0.82 and minimum 0.20 thresholds are settings, defaults unchanged | The labels most likely to be wrong were the only ones the correction could not touch |
| Fix | Voice enrolment errors | Every failure said "Could not extract embeddings. Please re-record." | Each sample classed as too short, too quiet or empty (user can fix) or server fault (re-recording will not help) | The live error was a missing server library; asking the user to re-record could never work |
| Fix | Audio decoding | torchcodec, which needs separate FFmpeg libraries, failed on Windows | librosa and soundfile decode audio | Enrolment and overlap detection failed outright |

## 2. Short and medium ROM losing information (reported challenge 2)

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Metadata after condensing | Owner, dates, figures and terms reattached by list position after the model reordered points; dates, numbers and terms emptied | Model returns source point ids; metadata merged from every source point | This is the information loss ADA reported |
| New | Coverage record | Dropped points vanished silently | Each version records which points were kept, merged and dropped | A reviewer can see and restore what was cut |
| New | Importance scoring | No notion of which points matter | Seven-signal score shown to the model when condensing | Condensing keeps what matters instead of guessing |
| Fix | Empty AI instructions | Four prompt keys sent an empty prompt to the model | All wired to real templates; an unknown or empty key raises an error | Model output for those steps was unguided |
| Fix | Stray import | Condensing service imported an internal transformers test module | Removed | Could crash on a different library version |

## 3. Action points (reported challenge 3)

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Action owners | Unassigned actions given to every speaker on the point | Owner only from evidence, with its source labelled: named by model, extracted, named in task, inferred from sole speaker, or unassigned | Guessed owners were presented as fact |
| Fix | PDF export | Speaker summary section used values before they were built | Values built first | Export crashed |
| Fix | Training variants | New prompt variants could reuse an existing id and number | Unique id and next free number | One variant silently overwrote another |

## 4. Offline operation (no internet at runtime)

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Diarization telemetry | pyannote sent usage traces to its vendor server by default; 78 attempts on one 15 s recording | Forced off, with Hugging Face, ChromaDB and OpenTelemetry opt-outs, before any model loads | Outbound data from a defence installation |
| Fix | Search database telemetry | ChromaDB sent usage events | Disabled | Same |
| Fix | Fonts | Loaded from Google Fonts | 34 font files bundled with the app | Blank or slow first paint offline, plus outbound requests |
| Fix | Build dependency | lovable-tagger, an external service plugin, in the frontend build | Removed | External dependency, and it blocked installation |
| Changed | API docs pages | /docs and /redoc on by default, loading scripts from public CDNs | Off unless a developer enables them | Outbound requests and a broken page offline |
| Changed | Desktop app loading | Electron loaded files from disk with web security relaxed | Private app:// protocol, path traversal blocked, web security on | Relative assets broke and security was weakened |
| New | Offline test | None | Real recording processed with all outbound connections blocked; zero attempts | Proves the offline claim rather than assuming it |

## 5. Model choice (no Chinese-origin models)

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Changed | Language model order | Qwen and DeepSeek preferred | Phi-4 (Microsoft), then Mistral Small, Llama 3.1, others; Qwen and DeepSeek removed | Stated sourcing constraint; Phi-4 rated best of the permitted options |
| Changed | Text embedding model | Qwen embedding model | mxbai-embed-large-v1 (Mixedbread, Germany); picker lists only permitted models | Same constraint |
| Fix | Embedding pooling | Pooling method assumed | Read from each model's own configuration | Wrong pooling degrades document search |
| Changed | Interface labels | "Powered by Qwen AI" | "Local AI, runs entirely on this machine" | Label was inaccurate |
| New | Settings migration | Existing accounts kept Qwen settings and the AI switched off | Old values converted once at startup | Your account's settings were wrong after the change |
| Open | OCR component | RapidOCR uses Baidu models | Not yet replaced | Remaining sourcing issue |

## 6. Security and access control

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| New | Administrator role | Dashboard, logs and maintenance open to any logged-in user | Role per user, first account is admin, admin-only routes enforced including live updates | Anyone could read logs and run maintenance |
| Fix | Login token key | Every install shipped the same known secret key | Unique key generated per install and kept out of Git | Anyone with the code could forge logins |
| Fix | Overlap detection endpoint | No login required | Login required | Unauthenticated access to audio processing |
| Changed | Password hashing | passlib, unmaintained and broken with current bcrypt | bcrypt directly; passwords over 72 bytes rejected instead of silently truncated | Registration and login failed |
| Fix | Leaked log file | Diagnostics export containing logs committed to GitHub | Removed from repository and ignored | Log contents in version control |

## 7. Reliability and performance

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Stalled language model | Requests had no timeout; a stalled model froze processing forever | Streaming with a stall timeout; truncated answers rejected | Found when the evaluation hung indefinitely |
| Fix | "Database is locked" | Default SQLite settings | Write-ahead logging, 10 s wait on locks | Long processing blocked the interface |
| Fix | Launch folder | Training data and diagnostics export used the current folder | Fixed application paths; export uses a temporary file deleted after sending | Launching from elsewhere read and wrote the wrong place |
| New | Run metrics | No timing or usage data | Per-run stage timing, model loads and exact token counts stored | Needed to prove any speed-up |
| Fix | Runtime crash defects | Settings page referenced a shadowed variable; several missing imports | Fixed; crash-only lint added | Found by lint, would fail at runtime |

## 8. Installation, build and testing

| Type | Area | Original | Now | Rationale |
|---|---|---|---|---|
| Fix | Python requirements | Could not install: conflicting torch and bcrypt versions | Torch 2.8 pinned, GPU build chosen automatically | A fresh clone could not be installed |
| New | One-command setup | Manual steps | setup.py creates the environment, installs, fetches models, pulls Phi-4, and checks the result | A clone should work upfront |
| New | Automatic checks on GitHub | None | Backend tests, crash lint and frontend build on every push | Catches breakage before a demo |
| New | Golden dataset harness | No way to measure quality | Scores word error, speaker error, information kept, must-keep points, action precision and recall, owner accuracy; 3 synthetic cases; draft-reference helper | Every quality claim needs a number; real recordings still needed |
| Fix | Test isolation | Tests wrote into your real database, changed tracked files and called your live model | Temporary database and files, network blocked in tests | Test runs polluted real data; suite dropped from 26 to about 4 minutes |
| New | Tests | Original suite | 726 passing, about 25 new test files | Each fix above has a test that fails if it regresses |
| Changed | Repository hygiene | Docs ignored; no rules for secrets or audio | Docs tracked; keys, secrets, audio, local datasets and virtual environment ignored | Keep secrets and meeting audio out of Git |

## 9. Documentation

| Type | Document | Purpose |
|---|---|---|
| New | observations/2026-09-12-parameter-audit.md | Every tunable parameter and findings against the three challenges |
| New | REVAMP-PLAN.md | End-to-end plan with rationale for each change |
| New | IMPLEMENTATION-AND-VALIDATION.md | Execution order and how each goal is proven |
| New | OBSERVABILITY-AND-AUDIT.md | Audit trail, traceability and weekly leadership report design |
| New | WORKSTREAMS.md | Plain-language status of all 42 workstreams |
| New | PROGRESS.md | Detailed record of what was built and tested |
| New | CHANGE-SUMMARY.md | This table |
| New | SETUP.md, backend/eval/README.md | Installation and golden dataset instructions |
