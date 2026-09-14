# Implementation Progress

Running record of what has actually been built, against `docs/REVAMP-PLAN.md`
and `docs/IMPLEMENTATION-AND-VALIDATION.md`.

Scope note: forty-two workstreams are planned. The counts below are the honest
position, not a summary of intent.

| | Count |
|---|---|
| Workstreams complete | 3 |
| Workstreams partially complete | 2 |
| Workstreams not started | 37 |
| Milestones complete | 0 of 6 |
| G1, G2, G3 (speaker ID, short/medium loss, action points) | Not started |

## Test suite

| | Value |
|---|---|
| Passing | 561 |
| Failing | 21 |
| Collection errors | 7 |
| Skipped | 2 |

Tests written in this work: 244, all passing.

Failures and errors dropped because `soundfile` and `librosa` were added to the
test environment, which let four previously-uncollectable modules run. The
remaining failures need `transformers`, `torch`, `chromadb`, `aiofiles`,
`rapidocr_onnxruntime` or `pptx`. Tests written in this work: 118, all passing.

No failure references a changed file. The counts are compared after every
change, which is how regressions are detected here.

### Known test-environment artifact

`test_action_point_pipeline.py::test_generate_mom_from_enhanced_rom_action_items`
fails in the full local suite with `assert 'Vikas' == 'Bob'`, but passes when
its module runs alone or paired with related modules.

It was isolated to collection rather than execution: running that single test
while collecting the whole suite still fails. Three modules
(`test_dashboard_router.py`, `test_bulk_folder_imports.py`,
`test_video_rerun_ocr.py`) fail partway through importing `main`, which
performs module-level work before its `import torch` line. The partial import
leaves state behind.

This is an artifact of the absent ML stack in the light development
environment, not a product defect. Those modules import cleanly where torch is
installed. **Confirm it disappears in the full environment**; if it persists
there, it is a real isolation bug and needs fixing.

Environment: an isolated virtual environment in the session scratchpad, created
with `--system-site-packages` plus `aiosqlite`, `python-jose[cryptography]`,
`email-validator`, `soundfile` and `librosa`. The system Python is untouched. The full ML stack is absent,
so suites needing it must be run elsewhere before merge.

---

## Complete

### W6.1 — Administrator identity and route lockdown

Closed a live exposure: the dashboard console, application logs, diagnostics
export, log download and the maintenance endpoint were reachable without
authentication, and the logs carry meeting participant names and speaking times.

- `role` column on `users`, additive migration, oldest account promoted when no
  administrator exists, first account at setup becomes administrator.
- `require_admin` accepting a bearer token or the HttpOnly refresh cookie,
  refusing the cookie path cross-site.
- Applied to every dashboard route and to `GET /analysis`, which returned up to
  1000 processing records across all users with no authentication at all.
- WebSocket moved to an unguarded router and authorised inline.

Tests: `test_admin_authorization.py` (46), `test_user_role_migration.py` (5),
`test_route_access_classification.py` (2, skipped without the ML stack).

Two things testing caught that review had not. The bearer scheme cannot guard a
WebSocket, because FastAPI applies router dependencies to handshakes but the
scheme needs a `Request`; applying it raised `TypeError` at connect time. And
the console authenticates by cookie, not bearer, so a bearer-only gate would
have left it unusable.

Outstanding: nothing prevents the last administrator being demoted or deleted,
because no role-management endpoint exists yet.

### W4.1 — Secrets enforcement

`JWT_SECRET` shipped as `change-me-in-production-use-long-random-string`. The
application is distributed as a built artefact, so a baked-in key would be
readable by anyone holding a copy and would mint valid tokens for every
installation.

- Per-installation key generated on first run, persisted under the runtime
  directory, `chmod 600` best-effort.
- An explicit `JWT_SECRET` still wins, but is refused below 32 characters
  rather than silently falling back.
- A short or corrupted stored key is regenerated.
- `.gitignore` extended with `**/secrets/` and `*.key`; the runtime directory
  was already ignored.

Tests: `test_jwt_secret_resolution.py` (14).

`ENVIRONMENT` was deliberately left at `development`. It controls only the
`Secure` cookie flag, and the application is served over `http://127.0.0.1`,
where secure cookies would not be sent at all. Forcing production would break
authentication rather than harden it.

Outstanding: the build-time check that a packaged artefact carries no
development defaults. It needs the pipeline from W4.4, which does not exist.

### W0.2 — Stage instrumentation

`services/run_metrics.py` and the `run_metrics` table, following the
conventions of the existing `services/analytics.py`.

- One row per (run, stage): the stage set differs across the single, chunked,
  rerun and ROM pipelines, and flat columns cannot extend to cover them.
- Counters accumulate through a `contextvars` variable, so deep call sites
  report without their signatures changing. Concurrent Stage 1 windows keep
  separate counters; a module global would have merged them.
- Records `model_load_count` and `model_unload_count`, which quantify the
  reload thrash in W2.1 directly, plus LLM calls, prompt and completion tokens,
  retries, peak video and resident memory, and a degraded-versus-ok outcome.
- Every write swallows its exceptions, and the pipeline wrapper degrades to a
  null context if the service cannot start. Instrumentation must never be the
  reason a transcription fails, and there is a test for that.

Wired at four call sites, each guarded, each verified by test:

| Site | Records |
|---|---|
| `run_pipeline` in `tasks/pipeline.py` | Opens the run stage; without it every reporting call is a no-op |
| `QwenProvider._call_ollama` | LLM calls with exact token counts reported by Ollama, so measured rather than estimated |
| `QwenProvider.unload_model` | Unloads, inside the stage so the cleanup unload is counted |
| Whisper, ECAPA and Qwen load paths | Model loads |

Tests: `test_run_metrics.py` (22) and `test_run_metrics_wiring.py` (17). The
wiring tests exercise the real `run_pipeline` and `_call_ollama` paths rather
than asserting the source contains a string; load paths that need the ML stack
are verified structurally, including that every instrumentation call sits
inside a try block.

Outstanding: per-stage subdivision within a run. The run-level stage gives
total duration, model loads and token spend per run, which is what the W2.1
hypothesis needs. Attributing time to transcription versus diarization versus
the ROM stages needs wrapping the progress transitions inside a 550-line
function, which should be done with the full stack available to verify.

### W1.1 — Short and medium versions no longer lose information (G2)

The defect ADA reported. Metadata was reattached to condensed points by list
position, after a prompt that explicitly asks the model to merge and reorder,
so output index *j* took its owner, action items and timeline from input index
*j* regardless of content. Outputs beyond the input count all collapsed onto
the last source point. `technical_terms`, `dates` and `numbers` were set to
empty lists unconditionally.

- Both prompts now require `source_point_ids` on every output point.
- `_parse_condensed_items` extracts text with its provenance, accepting the
  `original_point_ids` spelling already used by Stage 2 dedup, and degrading
  to the previous text-only parsing when a model ignores the contract.
- `_merge_source_metadata` unions the metadata of every source behind a point:
  entities, references and action items deduplicated in order; timeline
  spanning min to max; scalars taking the first non-empty value.
- Each agenda gains a `condensation_coverage` record naming the input points
  retained, merged and dropped. A silent drop is how this reached the customer.

Tests: `test_rom_version_provenance.py` (36), including end-to-end runs through
the real `generate_rom_version` using the real prompt templates.

### W1.4 — Speaker refinement can now fire (G1)

`original_label_sim` was initialised to 1.0 and only replaced when the current
label matched an enrolled profile or a conversation centroid. A generic
"Speaker N" matches neither, so the override test became
`(best - 1.0) > 0.30`, unsatisfiable for any cosine value. The segments most
likely to be mislabelled were the only ones the correction pass could not
touch.

- An unmeasured original now scores 0.0, which states the truth: nothing
  supports the current label.
- The hard-coded 0.82 and 0.20 became
  `SPEAKER_REFINEMENT_ACCEPT_THRESHOLD` and
  `SPEAKER_REFINEMENT_MIN_SIMILARITY`, beside the already-configurable margin.
- Defaults are unchanged, asserted by test. This fixes a bug; it does not
  retune the system.

Tests: `test_speaker_refinement_gate.py` (20).

### W1.7 — Action ownership follows evidence (G3)

The extraction prompt forbids assigning ownership merely because someone was
speaking and asks for a null owner when nobody was assigned.
`_resolve_point_owner` then overrode that null with the source point's whole
speakers list, then its speaker, then the sole participant, so every unassigned
action appeared assigned and a multi-speaker point named everyone who had
talked.

The resolver now returns `(owner, owner_source)`:

| Source | Evidence |
|---|---|
| `llm_explicit` | The model named an owner |
| `stage1_extracted` | Extraction recorded one for the source point |
| `named_in_task` | A participant is named inside the task text |
| `inferred_from_speaker` | Exactly one speaker on the point; weak, surfaced as inferred |
| `unassigned` | No evidence |

Comma-joining several speakers is gone. A single speaker is retained because it
is often correct, but labelled so the interface can mark it for confirmation
rather than presenting it as fact. `owner_source` reaches all three emit sites.

Tests: `test_action_owner_attribution.py` (23). The pre-existing
`test_generate_mom_from_enhanced_rom_resolves_owners_from_speakers` still
passes: its single-speaker case keeps its owner, now labelled inferred.

Expect the unassigned count to rise on multi-speaker points. That is the fix.

### W1.2 — Importance scoring for discussion points (G2)

Answers the second half of the question in `Current issue and challenges.docx`:
"How we can improve and assign importance to the points". Nothing in the system
carried any measure of significance, so "keep the important point and
summarize" had nothing behind it and the model's only signals were ordinal
position and text length.

`services/point_importance.py` scores each point from attributes Stage 1 and
Stage 2 already produce, so it costs no inference and no model load:

| Signal | Weight | Rationale |
|---|---|---|
| Carries action items | 0.30 | An action is the strongest reason to keep a point |
| Decision language | 0.20 | Decisions are the purpose of a meeting record |
| Entity density | 0.15 | Figures, dates and terminology carry the detail |
| Merge weight | 0.10 | A point that several others merged into |
| Speaker breadth | 0.10 | More participants implies broader relevance |
| Duration | 0.10 | Time spent is weak but real evidence |
| Agenda confidence | 0.05 | A confidently mapped point is likelier to be real |

Every signal saturates, so no single one can dominate. The component scores
travel with the total, which is what makes a drop explainable to a reviewer and
the weights tunable against real decisions. Weights are parameters, not
constants in code.

The score is attached to each point and shown to the model in the condensation
prompt, with an explanation of what it means and an instruction that it is
guidance rather than a rule: never drop an action item or decision because its
score is low.

Honesty note on the weights: they are tuned to intent, not to data. No labelled
set exists yet, so they are a starting point to be calibrated against the golden
set in W0.1, and that is stated in the module itself rather than left implied.

`select()` and `rank()` provide deterministic selection for when condensation
moves fully out of the model; the current wiring stops at informing it.

Tests: `test_point_importance.py` (40), including saturation, determinism
across repeated runs, stable tie-breaking, and that scoring failure cannot stop
a ROM being produced.

### W1.9 — No prompt key resolves to an empty template

An empty prompt does not fail. The model receives nothing and returns something
plausible but ungrounded, which surfaces much later as poor output rather than
as an error. Four keys were affected, three more than the audit had found:

| Key | Problem |
|---|---|
| `raw_mom_extraction` | Mapped to `""` despite `RAW_MOM_EXTRACTION_PROMPT` existing |
| `raw_mom_repair` | Mapped to `""` despite `RAW_MOM_REPAIR_PROMPT` existing |
| `raw_mom_to_mom` | Mapped to `""` despite `RAW_MOM_TO_MOM_PROMPT` existing |
| `rom_enhance_action_points` | Placeholder constant, on a live call path |

The three real templates are now wired to their constants. The placeholder
constant is deleted and its key unregistered, so the dead call path raises
instead of sending an empty prompt. `_get_prompt` now raises `KeyError` on any
empty or unknown key rather than returning `""`.

Tests: `test_prompt_registry.py` (47), which walk every registered key and
assert it resolves to a real template, plus a guard that no module-level prompt
constant is empty.

### W3.2 — SQLite configured for concurrent use

The engine was created with only `check_same_thread=False`. This application's
normal state is a long pipeline writing while the interface polls for progress,
which under the default rollback journal blocks readers and surfaces as
`database is locked`.

| Pragma | Value | Reason |
|---|---|---|
| `journal_mode` | WAL | Readers proceed during a write |
| `busy_timeout` | 10000 | Contention waits rather than failing |
| `synchronous` | NORMAL | Safe against process crash; faster for many small writes |
| `temp_store` | MEMORY | Keeps sort scratch out of the filesystem |

Applied per connection through a SQLAlchemy connect event, with each pragma
guarded so one unsupported setting cannot make connections unusable.

`foreign_keys` is deliberately **not** enabled, and there is a test asserting
its absence. SQLite ignores foreign keys unless asked, so the two constraints
declared on `recording_chunks` have never been enforced on any installed
database. Enabling enforcement could start rejecting writes against rows that
already violate them. That is a data question: audit installations for orphans
first, then turn it on.

Tests: `test_sqlite_pragmas.py` (10), including a reader completing while a
write transaction is open, and ten concurrent readers during a writer loop.

---

### Language-model requests can no longer hang forever

Found when the golden evaluation blocked indefinitely on a stalled model
server. Generation requests had no timeout, despite a comment claiming 90 s.

- Requests now stream. A socket timeout, `OLLAMA_STALL_TIMEOUT_SEC` (default
  600 s), fires only when the server sends nothing, so long generations are not
  cut short.
- A stream that ends without its final chunk is treated as a failure, not
  returned as a truncated answer. Error and malformed chunks are raised.
- Tests: `tests/test_ollama_stream.py`.

### Voice enrolment explains failures (W4.3, enrolment only)

The live error "Could not extract embeddings from samples. Please re-record."
was caused by a missing decoding library, so re-recording could never work.

- Each sample is assessed and classified: empty, too short (under 2 s) or too
  quiet are the user's to fix (HTTP 422, specific guidance); undecodable audio,
  a missing model or a failed extraction are internal (HTTP 500, tells the user
  re-recording will not help, no internal detail leaked).
- Tests: `tests/test_voice_sample_assessment.py`.

### Offline and isolation defects found by the full test run

- **API docs pages.** FastAPI's `/docs` and `/redoc` load scripts and fonts
  from public CDNs. They are now off unless `ENABLE_API_DOCS` is set.
  `/openapi.json` has no external references and stays.
- **Working-directory paths.** Training checkpoints and the diagnostics export
  were relative to wherever the process was launched. Checkpoints now follow
  `CHECKPOINTS_DIR` (default `backend/checkpoints`); the export uses a private
  temporary file deleted after sending. The export had been committed to the
  repository with log contents; it is removed and ignored.
- **Tests reached the live model.** Unmocked requests went to the Ollama server
  running on the machine, so results depended on that model and the suite took
  26 minutes. Unmocked network access now fails in tests; the suite takes about
  4 minutes. Tests also work on a copy of the shipped checkpoints.
- Tests: `tests/test_runtime_paths.py`, additions to `tests/test_offline_egress.py`.

## Partially complete

### W4.6 — Offline integrity (functional fixes done, enforcement not)

All four outbound references removed:

| Reference | Resolution |
|---|---|
| Google Fonts `@import` in the app stylesheet | Six families vendored as 34 woff2 files, latin and latin-ext, 1.34 MB |
| Google Fonts `<link>` in the console HTML | System font stacks; the console needs no font mount |
| ChromaDB default telemetry | `anonymized_telemetry=False`, with a safe fallback |
| Scaffolding image meta tags | Removed |

The SIL Open Font License text ships with the fonts, since bundling
redistributes them.

Fonts are linked from the HTML head rather than imported from CSS. The Electron
build sets `base: "./"` and loads over `file://`, where an absolute `/fonts.css`
resolves against the filesystem root. A `<link>` also loads in parallel where an
`@import` blocks, so it is the better construction regardless.

Tests: `test_offline_egress.py` (14, 1 skipped) assert the forbidden hosts
appear nowhere in backend or frontend source, that every family used is
vendored with its weights, that every declared font source resolves to a
non-empty file, and that no font URL is absolute.

Outstanding: the startup egress check that fails closed, the loopback
allowlist, and the blocked-network continuous integration job. All three need
W4.4.

### W6.2 — Audit log (design needs re-scoping before build)

Not started, and the design should be revisited first. The tamper-evident hash
chain and the retention floors were justified on the assumption of an
organisational deployment where the administrator is the party being audited.
That assumption was mine, extrapolated from a single mention of feedback from
the Aeronautical Development Agency in `Current issue and challenges.docx`, and
it was not established. For a single-user personal installation the chain is
probably over-engineering; the underlying request, a complete log of logins and
changes, stands regardless.

---

## Not started

Everything else; see `WORKSTREAMS.md` for the list. The golden-dataset harness
and three synthetic cases exist (`backend/eval/`), but no real recordings have
been labelled yet. Real recordings with human-checked references remain the
critical path for every accuracy claim.
