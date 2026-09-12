# End-to-End Revamp Plan

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Goal | Production-ready, best-in-class quality; optimisation and efficiency as the primary objective |
| Evidence base | `docs/observations/2026-09-12-parameter-audit.md` |
| Reference specs | `rom-pipeline.pdf`, `transcription-pipeline.pdf` |
| Constraint | Air-gapped single-node desktop deployment (Electron + PyInstaller). No internet access at runtime. Every capability must be resident in the installed application. |

## Runtime network policy

This plan assumes the application has **no network route at runtime**. That is a
hard constraint, not a preference, and it governs every recommendation below.

The distinction that matters throughout: **build time may use the internet,
runtime may not.** Continuous integration, dependency resolution, model
acquisition and artefact signing all happen on a connected build machine. The
shipped artefact must then function with every outbound route closed. Where a
workstream below touches this boundary, it says so explicitly.

Assessment of the current state is in section 14 of the observation record. In
short, the backend is already correctly hardened: the three Hugging Face offline
variables are set in `backend/main.py` before any library that reads them is
imported, SpeechBrain fetches with `allow_network=False`, pyannote loads from a
local directory, Whisper uses `local_files_only`, and the language model is
served from localhost. Four residual outbound dependencies remain and are
addressed in W4.6.

## How to read this document

Every proposed change carries a **Problem** (what was observed, with a file and
line), a **Change** (what to do), a **Rationale** (why this specific approach),
and an **Evidence of success** (the measurement that proves it worked).

Honesty note on numbers. No runtime profiling, accuracy measurement, or load test
has been run against this system. This plan therefore contains **no predicted
speedup figures**. Where efficiency is claimed, the claim is structural (an
operation is performed N times that need only be performed once) and the plan
specifies the measurement that must be taken before and after. Phase 0 exists
precisely so that later phases can be reported in measured numbers rather than
estimates.

---

# Part 1 — Assessment

## 1.1 What is already good

This is not a rewrite. The system has real engineering behind it and the
foundations should be kept.

- The transcription and diarization pipeline follows current best practice:
  faster-whisper `large-v3` with CTranslate2, WhisperX wav2vec2 forced alignment,
  pyannote community-1 with dual exclusive/overlap tracks, ECAPA-TDNN 192-d
  embeddings with VAD gating and sliding-window averaging, greedy global
  bipartite identity resolution. That is a defensible architecture.
- Stage 2 hybrid retrieval (semantic + BM25 + metadata, fused with reciprocal
  rank fusion, then diversity-deduplicated and score-filtered) is more
  sophisticated than most production RAG.
- Factuality validation against the verbatim transcript, followed by a
  correction pass, is the right instinct and is rare in this class of product.
- 44 backend test modules exist and cover real behaviour, not just smoke tests.
- Settings are already user-overridable per install with validated ranges, which
  is most of the work of making the system tunable.

The problems below are about **operational maturity and efficiency**, not about
a flawed core design.

## 1.2 What blocks production readiness

Ordered by severity.

**S1 — Output correctness defects.** Short and medium ROM versions reattach
metadata by list position after an LLM has merged and reordered the points, and
unconditionally discard technical terms, dates and numbers
(`backend/services/rom_service.py:4700-4720`). The speaker refinement pass is
unreachable for its most important case because `original_label_sim` initialises
to 1.0 (`backend/services/identification.py:610`). Action owners returned as null
by the LLM are overwritten with speaker-based guesses
(`backend/services/rom_service.py:3748-3800`). These produce wrong output, not
just slow output.

**S2 — Model lifecycle thrash.** `unload_model()` is called 30 times across
services, routers and tasks; 74 unload or `empty_cache` calls exist in total. A
single ROM run loads and unloads the language model once per Stage 1 window
batch, once per Stage 2 group, once per Stage 3 batch and once per final
generation. Model load is not a cheap operation and this is the largest
identified source of avoidable latency.

**S3 — No durable job execution.** Long pipelines are launched with
`asyncio.create_task` inside the web process (`backend/routers/audio.py:99,165,596`).
A process restart, a crash, or a user closing the app loses in-flight work with
no recovery, and there is no backpressure, retry, priority or concurrency limit.

**S4 — No schema migration path.** There is no Alembic or equivalent.
`backend/database.py` contains raw `CREATE TABLE IF NOT EXISTS` DDL plus 18
ad-hoc `ALTER TABLE` statements executed at startup. Every schema change to a
shipped installation is a hand-written, untested, irreversible migration.

**S5 — SQLite is not configured for concurrent use.** `create_async_engine` is
called with only `check_same_thread: False` (`backend/database.py:29-33`). No
WAL journal mode, no `busy_timeout`, no `synchronous` setting, no
`foreign_keys=ON`. A long pipeline write will block readers and surface as
`database is locked` under exactly the conditions this app creates.

**S6 — No CI, no lint, no type checking, no packaging metadata.** There is no
`pyproject.toml`, `pytest.ini`, `setup.cfg`, `Dockerfile`, or `.github`
directory. The 44 test modules are not run automatically by anything.

**S7 — Three files concentrate all risk.** `services/ai_provider.py` is 6,196
lines, `services/rom_service.py` 4,824, `routers/rom_router.py` 4,018. Prompts,
provider logic, pipeline orchestration and HTTP handling are interleaved. Every
feature and every fix lands in these files.

**S8 — Committed development defaults.** `JWT_SECRET` ships as
`change-me-in-production-use-long-random-string` and `ENVIRONMENT` defaults to
`development` (`backend/config.py:89,93`). Nothing enforces an override.

**S9 — No quality measurement.** There is no diarization error rate, no word
error rate, no speaker confusion metric, no ROM entity-retention check, no
action-item precision or recall. Quality is currently assessed by customer
complaint. "Best in class" cannot be claimed, defended, or even detected without
these.

**S10 — Duplicated tuning constants.** The 0.92 diversity threshold is redefined
as a local default in four functions and RRF `k=60` in three. Tuning requires
finding every copy.

---

# Part 2 — Target architecture

```
                     Electron shell
                           |
                    Vite/React UI
                           |  typed client generated from OpenAPI
                           v
+---------------------------------------------------------------+
|  FastAPI process (thin)                                        |
|   routers/  -> validate, authorise, enqueue, read              |
|   No ML work. No long-running work. No business logic.         |
+---------------------------------------------------------------+
                           |  jobs table (leases, heartbeats)
                           v
+---------------------------------------------------------------+
|  Worker process (single, supervised)                           |
|   JobRunner -> Stage handlers, each idempotent + checkpointed   |
|   ModelManager -> one owner of GPU memory, ref-counted, LRU     |
+---------------------------------------------------------------+
        |                |                |               |
   transcription    diarization      identification    rom stages
        |                |                |               |
        +----------------+----------------+---------------+
                           |
                  repositories/ (typed, no raw SQL in callers)
                           |
                 SQLite (WAL) + ChromaDB
```

Four structural principles drive every change below.

1. **The web process never does heavy work.** It validates, authorises, enqueues
   and reads. This is what makes the API responsive and the app restartable.
2. **One component owns the GPU.** Model loading and eviction is a single
   policy, not 74 scattered calls.
3. **Every pipeline stage is idempotent and checkpointed.** Re-running a stage
   with the same input produces the same output and costs nothing if already
   done. This is what makes work resumable and makes reprocessing cheap.
4. **Nothing is optimised before it is measured.** Phase 0 is not optional.

---

# Part 3 — Workstreams

## Phase 0 — Measurement foundation

Nothing else in this plan can be reported honestly until this exists. It is
small and it is first.

### W0.1 — Golden dataset and evaluation harness

**Problem.** There is no way to tell whether a change improved or degraded
output quality. The only current signal is customer feedback, which is how the
Aeronautical Development Agency issue was discovered.

**Change.** Assemble a fixed evaluation set of 10 to 20 recordings spanning the
real distribution: two speakers and many speakers, clean and noisy audio, with
and without video slides, short and long duration, and at least one recording
representative of the customer's domain vocabulary. Hand-label each with
reference speaker turns, a reference transcript, and a reference list of the
facts, dates, numbers and action items that must survive to the final ROM. Store
the set outside the repository with a manifest and checksums committed.

Build `backend/eval/` producing a single report with:

| Metric | Measures | Stage |
|---|---|---|
| Word Error Rate | Transcription accuracy | Transcription |
| Diarization Error Rate | Who spoke when | Diarization |
| Jaccard Error Rate | Diarization, cluster-purity view | Diarization |
| Speaker confusion rate | Wrong name assigned to correct turn | Identification |
| Unmatched turn rate | Turns left as generic `Speaker N` | Identification |
| Entity retention rate | Reference dates, numbers, technical terms surviving into final ROM per version | ROM short / medium / long |
| Action item precision and recall | Against labelled reference actions | Final ROM |
| Owner attribution accuracy | Correct owner, correctly-null owner counted as correct | Final ROM |
| Wall-clock per stage | Latency | All |
| Peak VRAM and RAM | Resource ceiling | All |

**Rationale.** Diarization Error Rate and Jaccard Error Rate are the standard
academic metrics for this task, so results are comparable against published
systems and the phrase "best in class" becomes a defensible statement rather
than an aspiration. Entity retention rate is the metric that directly encodes
the customer's complaint: it turns "some information is lost" into a number that
can be driven to a target. Counting a correctly-null owner as correct is
deliberate, because the current system's failure mode is confident wrongness,
and a metric that rewarded any non-null answer would reward that failure.

**Evidence of success.** A single command produces the table above. Baseline
figures for the current build are recorded and committed as the reference point
for every later phase.

### W0.2 — Stage-level instrumentation

**Problem.** `logging.basicConfig` at `backend/main.py:95` is the entire
observability story. `voicesum_diagnostics.json` exists but is ad hoc. There is
no per-stage timing, so the claim in S2 that model reloading dominates latency is
structurally sound but unquantified.

**Change.** Add a `run_metrics` table keyed by recording id and stage, recording
start, end, duration, peak VRAM, model load count, LLM call count, prompt and
completion tokens, and retry count. Emit structured JSON logs carrying
`run_id`, `stage`, `recording_id` on every line. Expose the per-run report in
the existing diagnostics endpoint.

**Rationale.** Model load count per run is the single number that proves or
disproves the S2 hypothesis, and it costs almost nothing to record. Token counts
per stage identify which prompts are actually expensive, which is the input to
any prompt reduction work. Without a stage dimension, a slow run cannot be
attributed and optimisation becomes guesswork.

**Evidence of success.** A completed run yields a per-stage breakdown summing to
the observed wall clock, with model load counts visible per stage.

---

## Phase 1 — Output correctness

These fix wrong output. They come before performance work, because optimising a
pipeline that produces wrong answers only produces wrong answers faster.

### W1.1 — Provenance-based metadata for short and medium ROM versions

**Problem.** `backend/services/rom_service.py:4700-4720` assigns metadata by
list index: `src = pts[min(j, len(pts) - 1)]`. The prompt instructs the model to
merge and restructure, so output index `j` bears no relation to input index `j`.
Output points beyond the input count all collapse onto the final source point.
The same block sets `technical_terms`, `dates` and `numbers` to empty lists
unconditionally.

**Change.** Change the short and medium prompts to return objects rather than
bare strings:

```json
[{"text": "...", "source_point_ids": ["pt-3", "pt-7"]}]
```

Derive every metadata field from the union of the referenced source points:
speakers, action items, references, technical terms, dates and numbers unioned;
`timeline_start` as the minimum and `timeline_end` as the maximum. Reject and
retry once any response whose `source_point_ids` are not all valid; on a second
failure keep the original points and record the failure in `run_metrics`.

**Rationale.** This exact pattern already exists and works elsewhere in the
codebase. `ROM_POLISH_PROMPT` returns `original_point_ids`, and the Stage 2
semantic deduplication path already merges metadata from a cluster by union with
min and max timelines. The fix is to apply an established internal convention to
the one path that skipped it, not to invent a mechanism. Union semantics are
correct rather than merely safe: a condensed point derived from three sources is
genuinely about all three sources' entities, and dropping any of them is the
information loss being reported.

**Evidence of success.** Entity retention rate for the short and medium versions
against the golden set. The target is that no date, number or technical term
present in the long version is absent from the medium version, and that the
short version's losses are confined to points deliberately dropped and are
listed in the coverage report from W1.3.

### W1.2 — Importance scoring in Stage 2

**Problem.** Searching all services and routers for importance, priority,
salience, rank or weight fields returns only RAG retrieval scores. No point
carries any measure of significance. The short prompt's instruction to keep the
important points therefore has nothing behind it, and the model's only available
signals are ordinal position and text length.

**Change.** Add an `importance` object to every Stage 2 point, computed when the
full transcript and retrieved context are still in scope:

| Signal | Basis | Why it is available |
|---|---|---|
| Has action items | Point carries a non-empty `action_items` | Stage 1 output |
| Decision language | Decision or commitment verb present in polished text | Text analysis |
| Speaker breadth | Count of distinct speakers on the point | Stage 1 `speakers` |
| Entity density | Technical terms, dates and numbers per hundred words | Stage 1 metadata |
| Discussion duration | `timeline_end` minus `timeline_start` | Stage 1 metadata |
| Merge weight | Number of source points merged during dedup | Stage 2 dedup |
| Agenda confidence | `high`, `medium` or `probable` | Stage 3 |
| Context corroboration | Retrieval score of the best supporting chunk | Stage 2 RAG |

Store both the component signals and a combined score. Make the weights
configurable in settings, seeded with defaults calibrated against the golden set
rather than chosen by intuition.

Then restructure short and medium generation into two steps. First, **select
deterministically in code** by importance rank to the target point count.
Second, ask the LLM only to compress and merge the already-selected points.

**Rationale.** This is the central change of the whole plan and it deserves an
explicit argument. The customer's complaint is that information is lost. The
current design delegates both *what to drop* and *how to compress* to a single
opaque LLM call with a percentage target. Those are different kinds of decision.
Compression is a language task and the model is good at it. Selection is a
ranking task over structured attributes the pipeline has already computed, and
handing it to the model discards that computed evidence and makes the outcome
unrepeatable across runs. Splitting them makes selection deterministic,
inspectable, tunable and testable, and confines the model to what it is actually
good at. Storing the component signals alongside the score means that when the
customer disagrees with a drop, the reason is visible and the weights can be
adjusted, rather than the prompt being rewritten and re-hoped.

Every signal listed is already computed by the existing pipeline. This adds no
LLM call and no model inference.

**Evidence of success.** Entity retention rate and action item recall for short
and medium versions on the golden set, measured before and after. Selection must
also be deterministic: the same input produces the same dropped set on repeated
runs, verified by a test.

### W1.3 — Coverage reporting on condensation

**Problem.** When a short or medium version drops a point, nothing records it.
The loss is discovered by a reader noticing an absence, which is how this issue
reached the team.

**Change.** Every condensation pass emits a coverage record: which Stage 2 point
IDs were retained, which were merged and into what, which were dropped, and the
importance score of each dropped point. Persist it with the ROM version and
surface it in the UI as an expandable panel on the version view.

**Rationale.** A reviewer approving a short ROM currently cannot tell what is
missing without diffing against the long version by hand. Making the loss
explicit at the moment of review converts a silent failure into a visible,
correctable one, and it gives the importance weights of W1.2 a feedback channel
grounded in real reviewer decisions.

**Evidence of success.** Every generated short or medium version has a coverage
record accounting for one hundred percent of input point IDs.

### W1.4 — Speaker refinement reachability

**Problem.** In `refine_transcript_speakers_with_ecapa`, `original_label_sim`
initialises to 1.0 (`backend/services/identification.py:610`) and is only
replaced when the original label matches an enrolled profile. A segment labelled
`Speaker N` has no profile, so the value stays 1.0 and the margin gate evaluates
`(best_sim - 1.0) > 0.30`, which no cosine similarity can satisfy. Only the
hard-accept at `best_sim >= 0.82` can correct such a segment. Additionally, the
thresholds 0.82 and 0.20 are hard-coded at lines 681 and 683 while the margin
beside them is configurable, and 0.82 sits above the 0.72 matching threshold, so
a segment can match a profile during identification yet be un-correctable during
refinement.

**Change.** Initialise `original_label_sim` to the measured similarity against
the segment's current assignment, or to 0.0 where no comparable representation
exists. Promote the accept threshold and the floor to settings. Define the
accept threshold relative to the active matching threshold rather than as an
absolute literal, so that tuning one does not silently invalidate the other.

**Rationale.** The refinement pass exists specifically to correct
misattributions. Unmatched generic-label segments are the population most likely
to be wrong and are currently the population the pass cannot touch. This is a
one-line initialisation defect gating an entire correction stage. Tying the
accept threshold to the matching threshold rather than fixing it 0.10 above
prevents the two from drifting apart the next time either is tuned.

**Evidence of success.** Speaker confusion rate and unmatched turn rate on the
golden set. The count of override-applied versus override-rejected log lines per
run is already emitted and should shift measurably.

### W1.5 — Diarization cluster reconciliation

**Problem.** Matching is performed at speaker level and locks one profile to one
diarization cluster for the whole meeting. Correction is performed at segment
level. Nothing reconciles the two. When pyannote splits one person across two
clusters, only one cluster can win the profile and the other is permanently
generic; by W1.4's defect the refinement pass then cannot recover it either.

**Change.** Insert a cluster reconciliation step between diarization and
bipartite matching. Compute pairwise cosine similarity between cluster mean
embeddings and merge clusters above a configurable threshold before profiles are
assigned. Set the initial threshold above the identification threshold, since
merging two clusters is a stronger claim than matching a cluster to a profile,
and calibrate it against the golden set.

**Rationale.** Cluster over-segmentation is the standard and well-documented
failure mode of neural diarization, and it is the most likely mechanical cause
of the reported edge cases. Fixing it before the bipartite matching step is
strictly better than fixing it after, because the greedy matcher's one-profile-
per-cluster lock is what converts an over-segmentation into an unrecoverable
wrong label. All required inputs, the per-cluster mean embeddings, are already
computed during identification.

**Evidence of success.** Diarization Error Rate and Jaccard Error Rate before
and after, plus the count of clusters merged per run.

### W1.6 — Short-turn speaker assignment

**Problem.** A turn needs at least 0.5 s of audio to be embedded at all
(`backend/services/identification.py:275`) and at least 2.5 s of detected speech
to yield an embedding (`backend/services/embedding.py:491`). Below that,
`vad_extract_speaker_embedding` returns None and the label comes solely from
WhisperX `fill_nearest=True`, which propagates the neighbouring speaker. Short
interjections are assigned by temporal proximity, not by acoustics.

**Change.** For turns that produce no embedding, replace bare nearest-neighbour
fill with a conversation-local decision that weighs the surrounding turn
speakers, the diarization track's own opinion for that interval, and any partial
acoustic evidence below the embedding threshold. Mark such assignments with a
low confidence value and surface them in the transcript UI as reviewable.

**Rationale.** The thresholds themselves are correct and should not be lowered;
ECAPA-TDNN embeddings from under 2.5 s of speech are genuinely unreliable and
lowering the gate would trade a visible failure for an invisible one. The defect
is not the threshold but the fallback, which currently makes a confident guess
where it has no information. Short interjections such as agreements and
objections are disproportionately what a meeting record needs to attribute
correctly, so the right response is a better-informed guess plus honest
confidence marking, not a weaker acoustic gate.

**Evidence of success.** Speaker confusion rate restricted to turns under 2.5 s,
reported as a separate line in the evaluation report.

### W1.7 — Action owner attribution integrity

**Problem.** `MOM_EXTRACT_ACTIONS_FROM_POINTS_PROMPT` instructs the model never
to assign ownership merely because someone was speaking and to return
`owner: null` when unassigned. `_resolve_point_owner`
(`backend/services/rom_service.py:3748-3800`) then discards that null: step 3
assigns the source point's entire `speakers` list, step 4 assigns its `speaker`,
and step 5 assigns the sole participant when only one exists. Every unassigned
action is presented as assigned, frequently to several people at once.

**Change.** Cap the fallback at step 2, the source point's own extracted
`action_owner`. Preserve null beyond that. Add an `owner_source` field with
values such as `llm_explicit`, `stage1_extracted` or `unassigned`. Surface
unassigned actions in the UI as an explicit review queue.

**Rationale.** The prompt's rule is the correct one and considerable care went
into writing it; the code then overrides it. Of the two, the prompt encodes the
intended product behaviour. An action item wrongly attributed to a named
individual is worse than one marked unassigned, because a wrong owner is acted
upon while an unassigned item is triaged. The `owner_source` field is the
compromise that lets the weaker inferences be retained for display without
laundering them into apparent fact.

**Evidence of success.** Owner attribution accuracy on the golden set, counting
a correctly-null owner as correct. Precision on non-null owners is reported
separately and is the number that must rise.

### W1.8 — Action reconciliation across chunks

**Problem.** `rom_action_generation_chunk_size` is 10, so the model never sees
more than ten points at once and cannot observe a commitment made in one chunk
and reassigned or withdrawn in another. A global deduplication pass runs
afterwards (`backend/routers/mom_router.py:1219`) but deduplicates only; it does
not reconcile ownership or supersession.

**Change.** Add a reconciliation pass after global deduplication that groups
action items by semantic similarity across the whole meeting and resolves
conflicting owners and deadlines in favour of the latest timeline position,
flagging genuine conflicts for review rather than silently picking.

**Rationale.** Meetings revisit commitments; that is normal discourse, not an
edge case. Chunked extraction is a necessary concession to context limits, but
it makes cross-chunk reconciliation a structural requirement rather than a
refinement. Preferring the latest timeline position encodes the ordinary meaning
of a meeting, where a later statement supersedes an earlier one.

**Evidence of success.** Action item precision and recall on the golden set,
with a specific count of superseded-commitment cases resolved correctly.

### W1.9 — Empty prompt constant

**Problem.** `ROM_ENHANCE_ACTION_POINTS_PROMPT` is an empty string
(`backend/services/ai_provider.py:2196`). Any path reaching it sends an empty
prompt rather than failing.

**Change.** Delete it if unreferenced. If referenced, implement it, or raise
explicitly. Add a startup assertion that every registered prompt key resolves to
a non-empty template.

**Rationale.** A silently empty prompt produces a plausible-looking but
ungrounded model response, which is the hardest class of bug to detect
downstream. The startup assertion costs nothing and makes the entire class
impossible.

**Evidence of success.** Startup fails loudly on any empty registered prompt.

---

## Phase 2 — Efficiency

This is where the optimisation goal is realised. It follows Phase 1 deliberately:
output shape stabilises first, so performance work does not have to be redone.

### W2.1 — Central model manager

**Problem.** `unload_model()` appears 30 times across services, routers and
tasks; 74 unload or `empty_cache` calls exist in total. `generate_rom_version`
alone unloads the provider in a `finally` block after processing all agendas
(`backend/services/rom_service.py:4742`). A ROM run therefore loads and unloads
the language model repeatedly within a single logical operation. The provider
itself is a module-level singleton (`backend/services/ai_provider.py:6170-6178`),
so the singleton is being defeated by explicit unloads.

**Change.** Introduce `backend/services/model_manager.py` as the sole owner of
model residency for Whisper, the alignment model, pyannote, ECAPA-TDNN, the
embedding model and the language model. It provides reference-counted leases,
least-recently-used eviction under a configured VRAM budget, an idle timeout,
and a single GPU semaphore serialising exclusive access. Remove every scattered
`unload_model` and `empty_cache` call from services, routers and tasks. Where
the language model is served by Ollama, use its keep-alive rather than
process-local loading.

**Rationale.** The scattered calls are each locally reasonable and collectively
harmful: every caller defensively frees memory because no caller can know
whether another needs the model, so the model is evicted between consecutive
uses that would have shared it. Reference counting replaces that defensive
guessing with knowledge. An explicit VRAM budget with LRU eviction is what makes
it safe to stop unloading defensively, and it also addresses the documented
linear VRAM scaling with `WHISPER_PARALLEL_PROCESSING` by making the ceiling a
declared policy rather than an emergent property. The GPU semaphore matters
independently: Whisper, pyannote, ECAPA and the language model can currently
contend for the same device with no coordination, which on a constrained desktop
GPU produces thrash or out-of-memory failures rather than parallelism.

**Evidence of success.** Model load count per run from W0.2, which should fall
to approximately one per model per run. Wall-clock per stage and peak VRAM from
the evaluation harness. This is the change most likely to produce the largest
measured latency improvement, and after Phase 0 that improvement can be stated
as a measured figure rather than a claim.

### W2.2 — Durable job queue and worker process

**Problem.** Pipelines are launched with `asyncio.create_task` in the web
process (`backend/routers/audio.py:99,165,596`). Work is lost on restart, there
is no retry, no backpressure, no priority, and no concurrency limit. Heavy ML
work is dispatched to the default executor via `run_in_executor(None, ...)`
throughout the routers, competing with request handling.

**Change.** Add a `jobs` table with type, payload, state, lease holder, lease
expiry, attempt count, priority and last error. Move execution into a separate
supervised worker process that claims jobs by lease and heartbeats while
running. Routers enqueue and return immediately. Expired leases are reclaimed,
making crash recovery automatic. Concurrency is a declared limit, not an
accident of how many requests arrived.

**Rationale.** A durable queue in SQLite is the right choice here specifically
because the deployment is offline single-node. Redis or Celery would add an
operational dependency that an air-gapped desktop install cannot carry, and the
job volume does not require them. Leases with heartbeats rather than simple
status flags are what make crash recovery correct: a flag cannot distinguish a
job still running from a job whose process died. Separating the worker process
also gives the model manager of W2.1 a single address space to own, which a
multi-worker web server could not provide.

**Evidence of success.** Killing the worker mid-pipeline and restarting it
resumes the run without user intervention and without redoing completed stages.
API latency under concurrent load is measured with a pipeline running.

### W2.3 — Stage checkpointing and idempotency

**Problem.** Pipeline state is tracked as coarse statuses on the recordings row
such as `transcript_ready` and `done` (`backend/tasks/pipeline.py`). A failure
late in the ROM stages means re-running transcription, the most expensive stage
in the system.

**Change.** Give every stage a content-addressed input key and persist its
output as a checkpoint. Re-running a stage with an unchanged key returns the
stored result without recomputation. Make each stage a pure function of its
declared inputs. Represent a run as an explicit stage graph rather than a single
status column.

**Rationale.** This is what makes the entire system cheap to iterate on, and it
compounds with every other workstream. Tuning a Stage 2 threshold currently
costs a full transcription; with checkpointing it costs only the stages
downstream of the change. The same mechanism gives W2.2's crash recovery its
granularity and makes the golden-set evaluation loop of W0.1 fast enough to run
on every change rather than occasionally. Content-addressed keys rather than
timestamps are what make invalidation correct when a setting changes.

**Evidence of success.** Re-running a completed pipeline with no input change
completes in near-zero time. Changing only a Stage 2 setting re-runs Stage 2
onward and no earlier stage.

### W2.4 — Embedding batching and cache

**Problem.** Embeddings are computed per point and per chunk throughout Stage 2,
the deduplication passes and the vector store paths. Embedding is a batchable
GPU operation being performed one item at a time, and identical text is
re-embedded across runs.

**Change.** Route all embedding through a batching service that accumulates
requests and dispatches them as tensor batches. Add a persistent cache keyed by
the SHA-256 of the normalised text plus the model identifier. Invalidate on
model change via the key.

**Rationale.** Per-item GPU inference wastes most of the device's throughput;
this is the clearest available efficiency win after model loading, and it
requires no change to any calling site's semantics. Including the model
identifier in the cache key is what makes the cache safe across the embedding
model changes that `EMBEDDING_MODEL` explicitly permits.

**Evidence of success.** Embedding call count and Stage 2 wall clock from the
harness, plus cache hit rate on a re-run.

### W2.5 — Vectorised deduplication

**Problem.** Diversity deduplication is implemented as a local function in four
separate places (`backend/services/rom_service.py:1102,1456,2824,3209`) and the
Stage 2 semantic dedup builds a full pairwise similarity matrix and scans it
with a nested loop (`backend/services/rom_service.py:2458`).

**Change.** Extract one shared, vectorised implementation. Use upper-triangular
matrix operations and connected-component clustering rather than nested Python
iteration. Short-circuit when the candidate set is below a size where the
overhead dominates.

**Rationale.** Four copies of one algorithm means four places to fix a bug and
four thresholds to tune, and the nested scan is quadratic Python over data
already resident in NumPy arrays where the same operation is a single call.
This consolidation is also a prerequisite for W3.4's threshold centralisation.

**Evidence of success.** Stage 2 wall clock on the largest golden-set recording;
identical dedup decisions before and after, asserted by test.

### W2.6 — Structured LLM output contracts

**Problem.** LLM responses are parsed by bespoke helpers such as
`_parse_condensed_points`, which handles thinking tags, markdown fences, JSON
arrays and bullet lists. A dedicated repair budget exists,
`max_tokens_stage1_json_repair` at 4548, implying parse failure is routine, and
each repair is an extra model call.

**Change.** Define a Pydantic schema per LLM task. Use the provider's structured
output or JSON mode where available and grammar-constrained decoding where not.
Validate against the schema, with a single shared repair path used only when
constrained decoding is unavailable. Record parse failure rate per task in
`run_metrics`.

**Rationale.** The repair budget is evidence of a systemic problem being handled
symptomatically. Constraining generation removes the failure rather than
correcting it, eliminating the extra call entirely on the affected paths.
Per-task failure rates then show which prompts remain problematic, which is the
input to prompt work that is otherwise guesswork. This also gives W1.1's
`source_point_ids` contract a place to be enforced rather than hoped for.

**Evidence of success.** Parse failure rate per task, and repair call count per
run, both approaching zero.

Offline amendment. Constrained decoding must come from the local runtime, not a
hosted structured-output feature. Ollama supports JSON-mode and grammar-
constrained generation locally, and the in-process transformers path can be
constrained with a local grammar library. Choose the mechanism on the basis of
what the bundled runtime provides, and verify it works with the network severed
before adopting it, since some libraries fetch grammar or tokeniser assets on
first use.

### W2.7 — Prompt efficiency

**Problem.** Per-task token budgets are generous across the board: several ROM
tasks at 4096, agenda batch assignment at 4096, MoM action extraction at 4096,
Stage 1 JSON repair at 4548. Whether these are necessary is unknown because
actual token usage is not recorded.

**Change.** Using the W0.2 token instrumentation, measure real prompt and
completion lengths per task against the golden set. Reduce budgets to measured
requirement plus a defined headroom. Deduplicate the substantial shared
boilerplate across the ROM prompt family into composed fragments.

**Rationale.** This work is placed after instrumentation deliberately, because
reducing a token budget without measurement risks truncating legitimate output,
which would be a correctness regression introduced in the name of efficiency.
Prompt tokens are paid on every call, so boilerplate deduplication compounds
across the dozens of calls in a single ROM run.

**Evidence of success.** Tokens per run against baseline, with no regression in
any Phase 0 quality metric.

---

## Phase 3 — Structure and data integrity

### W3.1 — Schema migrations

**Problem.** No Alembic. `backend/database.py` executes raw `CREATE TABLE IF NOT
EXISTS` DDL plus 18 ad-hoc `ALTER TABLE` statements at startup. Every schema
change to an installed copy is hand-written, unversioned and irreversible.

**Change.** Introduce Alembic. Freeze the current schema as the baseline
revision. Convert the 18 ad-hoc alters into explicit revisions. Run migrations on
startup with a version check that refuses to start against a newer schema than
the binary understands.

**Rationale.** This application ships to customer machines and holds their
meeting data. Data migration correctness on upgrade is not negotiable, and the
current approach has no rollback, no version record, and no way to detect a
partially-applied change. The refusal to start against a newer schema prevents
the specific failure where a downgrade silently corrupts data.

**Evidence of success.** A test upgrading a database from each historical shape
to current, with data intact.

### W3.2 — SQLite configuration

**Problem.** `create_async_engine` is called with only
`check_same_thread: False` (`backend/database.py:29-33`). No WAL, no
`busy_timeout`, no `synchronous` setting, no `foreign_keys=ON`.

**Change.** Set `journal_mode=WAL`, `busy_timeout` of several seconds,
`synchronous=NORMAL`, `foreign_keys=ON`, and a considered `cache_size`, applied
per connection. Add indexes on the foreign keys and status columns that the
pipeline and dashboard queries filter on.

**Rationale.** WAL allows readers to proceed during writes, which is precisely
this application's access pattern: a long pipeline writing while the UI polls.
Without it, `database is locked` is not a possibility but an eventual certainty
under normal use. `busy_timeout` converts the remaining contention from an
immediate error into a brief wait. `synchronous=NORMAL` under WAL is the
standard durability and throughput trade-off for a single-node application and
is safe against process crash, losing only against operating system crash.
`foreign_keys=ON` matters because SQLite silently ignores foreign key
constraints by default, so referential integrity is currently declared and not
enforced.

**Evidence of success.** No lock errors under a concurrent read-while-writing
load test. Dashboard query times before and after indexing.

### W3.3 — Repository layer

**Problem.** Raw `text("UPDATE recordings SET ...")` SQL appears throughout the
routers and tasks. `backend/tests/test_connection_leak.py` exists, indicating
session lifetime has already caused problems.

**Change.** Introduce `backend/repositories/` with one typed module per
aggregate. Callers use repository methods. Raw SQL is confined to repository
internals. Session lifetime is owned by one dependency and one worker-side
context manager.

**Rationale.** Scattered raw SQL is where N+1 patterns, missing indexes and
inconsistent session handling hide, and a query changed in one of several
duplicated places is a silent inconsistency. Centralising also gives W3.1's
migrations a single surface to keep in step with, and gives the connection leak
that already warranted a test one owner rather than many.

**Evidence of success.** No raw SQL outside `repositories/`, enforced by a lint
rule. Existing connection leak tests continue to pass.

### W3.4 — Configuration consolidation

**Problem.** The 0.92 diversity threshold is redefined as a local default in
four functions and RRF `k=60` in three. Refinement thresholds 0.82 and 0.20 are
hard-coded beside a configurable margin. Tuning requires finding every copy.

**Change.** Move every algorithmic constant into the settings model with a
documented range, as `rom_min_similarity_threshold` already is. Remove local
defaults. Add a test asserting no numeric literal appears as a threshold default
in service function signatures.

**Rationale.** The settings model already demonstrates the right pattern with
validated ranges; the problem is only that it was applied inconsistently. Every
duplicated constant is a latent inconsistency that will eventually be tuned in
one place and not the others, producing behaviour that cannot be explained from
any single file. The test is what prevents regression, since this is a pattern
that re-emerges naturally as new functions are written.

**Evidence of success.** One definition per constant. Every threshold in the
Phase 0 parameter audit is reachable through settings.

### W3.5 — Decomposition of the three large modules

**Problem.** `services/ai_provider.py` is 6,196 lines, `services/rom_service.py`
4,824 and `routers/rom_router.py` 4,018. Prompts, provider logic, orchestration
and HTTP handling are interleaved. All feature work and all fixes land here.

**Change.** Split along existing seams:

| Current | Target |
|---|---|
| `ai_provider.py` prompt constants | `prompts/` package, one module per family, versioned, integrated with the existing DB-backed `prompt_service` |
| `ai_provider.py` provider classes | `providers/` package, one module per backend, behind one protocol |
| `rom_service.py` | `rom/stage1.py`, `rom/stage2.py`, `rom/stage3.py`, `rom/final.py`, `rom/versions.py` |
| `rom_router.py` business logic | Moved into the `rom/` service modules; the router validates, authorises and delegates |

Do this incrementally, one seam at a time, with the test suite green between
each step.

**Rationale.** These files already have internal structure, marked with region
comments and status annotations such as `STATUS: NOT USED (Legacy)`; the split
follows boundaries the code has already drawn. The specific justification for
prioritising this is that the Phase 1 and Phase 2 changes all land in these three
files, so decomposing them reduces the risk of every subsequent change. Moving
prompts into a versioned package also makes prompt changes reviewable as diffs
and testable in isolation, which the customer feedback loop in W1.3 requires.
The incremental approach is deliberate: a single large refactor of 15,000 lines
would be unreviewable and would stall every other workstream behind it.

**Evidence of success.** No module above roughly 800 lines. Test suite green
throughout. No behaviour change, asserted by golden-set output equality.

### W3.6 — Vector store consolidation

**Problem.** Both ChromaDB and FAISS are present. `VECTOR_STORE_DIR` is marked
legacy and kept for migration detection, and FAISS indexes are still on disk
under `backend/runtime/vector_store/`.

**Change.** Complete the ChromaDB migration. Add a one-time migration for
remaining FAISS indexes. Remove the FAISS path and dependency once no installed
version depends on it.

**Rationale.** Two vector stores means two code paths, two sets of failure
modes, two dependencies in the installer, and a persistent question at every
retrieval site about which one is authoritative. The migration is already
partly done; finishing it is cheaper than maintaining the fork indefinitely.

**Evidence of success.** One vector store in the codebase. Retrieval quality
unchanged on the golden set.

---

## Phase 4 — Production hardening

### W4.1 — Secrets and environment enforcement

**Problem.** `JWT_SECRET` ships as `change-me-in-production-use-long-random-string`
and `ENVIRONMENT` defaults to `development` (`backend/config.py:89,93`). Nothing
prevents a build shipping with these values.

**Change.** Refuse to start when `ENVIRONMENT` is production and the JWT secret
is the default or shorter than a required length. Generate a per-installation
secret at first run and store it in the user profile with appropriate file
permissions. Add a build-time check that the packaged artefact carries no
development defaults.

**Rationale.** A known JWT secret means any party can mint valid tokens for any
installation. Per-installation generation is the correct fix for a desktop
product, since there is no operator to configure a secret and a single shared
build-time secret would be equivalent to the current state. The build-time check
is what makes the guarantee hold, since a startup check alone can be reached
only after the flawed artefact has already shipped.

**Evidence of success.** A production build with default secrets fails to
start. The build pipeline fails on a development default.

### W4.2 — Input and file handling

**Problem.** `/files` is mounted as static from `UPLOAD_DIR`
(`backend/main.py:330`). Uploads accept a broad set of media types, and OCR,
document extraction and audio decoding all parse untrusted input.

**Change.** Validate uploads by content sniffing rather than extension, enforce
size limits, store under generated identifiers rather than user-supplied names,
serve files through an authorising endpoint rather than a static mount, and run
media decoding with resource limits and timeouts.

**Rationale.** A static mount over a user-writable directory serves whatever is
placed there to whoever can reach the path, with no per-user authorisation, in
an application whose entire data model is per-user. Generated identifiers remove
path traversal as a category rather than filtering for it. Timeouts on decoding
matter because ffmpeg and OCR on a malformed file can consume the worker
indefinitely, which after W2.2 means blocking every queued job.

**Evidence of success.** Cross-user file access is denied. Malformed media
fails within the timeout without stalling the worker.

### W4.3 — Error taxonomy and user-facing failures

**Problem.** Failures across the ROM stages are frequently caught and logged as
warnings while the pipeline continues with original content, for example in
`generate_rom_version` where per-agenda failures keep the originals silently.
The user sees a completed run with degraded output and no indication.

**Change.** Define an error taxonomy separating user-correctable problems such
as inaudible audio, transient problems that should be retried, and defects.
Persist per-stage outcome including degraded-but-continued. Surface degradation
in the UI. Retry transient failures through the W2.2 retry mechanism rather than
swallowing them.

**Rationale.** Continuing with original content on failure is a defensible
choice, but doing it silently is not: the user cannot distinguish a short
version that was correctly generated from one where every agenda fell back. This
is the same category of problem as W1.3's coverage reporting, and the same
principle applies, which is that the system should make its degradations
visible rather than plausible.

**Evidence of success.** Every run reports per-stage outcome. Induced transient
failures are retried and succeed without user action.

### W4.4 — CI, lint, and type checking

**Problem.** No `pyproject.toml`, `pytest.ini`, `setup.cfg`, `Dockerfile` or
`.github` directory. The 44 backend test modules and the frontend vitest suite
are not run automatically.

**Change.** Add `pyproject.toml` with Ruff, mypy and pytest configuration.
Add a pipeline running lint, type check, backend tests, frontend tests and a
build of both artefacts on every change. Add the golden-set evaluation as a
scheduled job, since it requires the dataset and a GPU.

**Rationale.** A test suite that is not run automatically decays, and this one
represents substantial existing investment worth protecting. Type checking is
disproportionately valuable in this codebase because the ROM stages pass large
untyped dictionaries between functions, which is exactly the shape of the W1.1
positional metadata defect: a typed point object with a required provenance
field would have made that code fail to type-check. Separating the evaluation
job is a practical concession to its dataset and hardware requirements.

**Evidence of success.** Pipeline green on every change. Type coverage
increasing on the ROM modules.

Offline amendment. The build machine is connected and that is fine; the shipped
artefact is not. Pin and vendor dependencies so a build is reproducible from a
local mirror rather than from a live index, because a build that silently
resolves a new transitive dependency can introduce a network call into a product
that must not make one. The blocked-network pipeline job specified in W4.6 is
what converts this from an intention into a check.

### W4.5 — Packaging and update integrity

**Problem.** Delivery is PyInstaller plus Electron with `launcher.spec`,
`BUILD.md` and `docs/update-guide.html`. Model files are large and downloaded
separately. `required-runtime-version.txt` exists, implying version coupling
between components.

**Change.** Checksum and verify every model artefact at first use. Make the
runtime version check explicit and fail closed on mismatch. Sign release
artefacts. Make the update path resumable and atomic so that an interrupted
update leaves a working installation.

**Rationale.** An air-gapped or intermittently-connected install cannot recover
from a corrupt model file by re-downloading on demand, so verification must
happen at a point where the failure is actionable. Atomic updates matter because
the alternative failure mode, a half-updated installation on a customer machine
with their meeting data, has no remote remediation path.

**Evidence of success.** A corrupted model is detected before use with a clear
message. An interrupted update leaves the prior version working.

Offline amendment. There is no download to resume, so "resumable" applies to
reading an update bundle from removable media or a local share, not to network
transfer. The update bundle must be self-contained, carrying every model,
dependency and asset the new version needs, because a partially-updated
installation cannot fetch what it is missing. Verify the whole bundle before
applying any part of it. Version coupling between the Python runtime, the
Electron shell and the model set must be declared in the bundle manifest and
checked before application, since `required-runtime-version.txt` shows these
components are already version-coupled and a mismatch on a disconnected machine
has no remote remediation path.

### W4.6 — Offline integrity enforcement

**Problem.** Four outbound dependencies remain at runtime. Nothing enforces
their absence, so a fifth can be introduced by any dependency upgrade without
anyone noticing until a customer reports a slow launch.

| Reference | Location | Effect offline |
|---|---|---|
| Google Fonts `@import` | `frontend/src/index.css:1` | Render-blocking; first paint waits for connection timeout, then falls back |
| Google Fonts `<link>` | `backend/routers/dashboard_router.py:35` | Same, in generated dashboard HTML |
| ChromaDB telemetry | `backend/services/vector_store.py:63` | Failed outbound posts to a third-party analytics endpoint |
| Scaffolding images | `frontend/index.html:15,19` | Inert in Electron, but ships a third-party URL |

**Change.** Four fixes and one guarantee.

Self-host the fonts. Vendor the seven families into the application bundle as
local font files and serve them from a local stylesheet in both the frontend and
the generated dashboard HTML.

Disable ChromaDB telemetry explicitly by passing a settings object with
`anonymized_telemetry=False` to `PersistentClient`.

Remove the scaffolding meta tags.

Then make the guarantee enforceable rather than aspirational. Add a startup
egress check that fails closed in production, an allowlist permitting only
loopback, and a continuous integration job that runs the full golden-set
pipeline with all outbound routes blocked at the operating system level and
fails on any connection attempt.

**Rationale.** The three functional fixes are small. The enforcement is the
substance of this workstream, and it is the part that has lasting value.

Offline correctness is not a property that holds once and stays held. It is a
property that decays silently, because every one of the four current references
arrived through a reasonable act: a UI scaffold that assumed a browser, a
library whose defaults assume a connected host, a template copied from a working
page. A dependency upgrade can add a fifth at any time, and nothing in the
current build would reveal it. Only a test that actually severs the network can
detect this class of regression, because a passing unit test on a connected
machine proves nothing about a disconnected one.

Failing closed at startup rather than warning matters for the same reason the
JWT check in W4.1 fails closed. A warning in a log on a customer's air-gapped
machine reaches nobody.

The font fix deserves separate note because it is not cosmetic. A render-blocking
`@import` that cannot resolve delays first paint by the full connection timeout
on every single launch. This is likely to be among the most visible latency
improvements available anywhere in this plan, and it is also among the cheapest,
which is an unusual combination worth acting on early.

The ChromaDB fix carries a compliance dimension beyond function. For a defence
customer, an application attempting to post usage data to a third-party
analytics service is a finding at audit regardless of whether the connection
succeeds. Nothing is disclosed, because nothing leaves, but the attempt is
visible in network monitoring and the burden falls on the vendor to explain it.
The correct posture is that the attempt never occurs.

**Evidence of success.** The full pipeline completes on the golden set with all
outbound routes blocked, with zero connection attempts recorded. Time to first
paint measured before and after the font change. The blocked-network job runs on
every change.

### Status: the four references are removed (2026-09-12)

| Change | Location |
|---|---|
| ChromaDB constructed with `anonymized_telemetry=False`, falling back safely if the option is rejected | `backend/services/vector_store.py` |
| Six font families vendored as 34 woff2 files, latin and latin-ext only, 1.34 MB | `frontend/public/fonts/` |
| Generated `@font-face` rules with relative sources | `frontend/public/fonts.css` |
| Remote `@import` removed; fonts linked relatively from the HTML head | `frontend/src/index.css`, `frontend/index.html` |
| Administrator console moved to system font stacks | `backend/routers/dashboard_router.py` |
| Scaffolding meta tags removed | `frontend/index.html` |
| SIL Open Font License text bundled alongside the fonts | `frontend/public/fonts/OFL.txt` |

Tests: `backend/tests/test_offline_egress.py`, 14 passing and 1 skipped where
ChromaDB is absent. They assert the forbidden hosts appear nowhere in backend or
frontend source, that every family the application uses is vendored with its
weights, that every declared font source resolves to a non-empty file, and that
no font URL is absolute.

Two decisions worth recording.

**Fonts are linked from the HTML head, not imported from CSS.** The Electron
build sets `base: "./"` and loads over `file://`, so an absolute `/fonts.css`
would resolve against the filesystem root and fail. A `<link>` also loads in
parallel, where an `@import` blocks, so this is the better construction
independently of the path issue.

**The console uses system fonts rather than a bundled family.** Serving a
bundled face to a server-rendered page would need a new static mount in the
backend. The console is an internal administrative surface, so a system stack
is the proportionate answer and it ships no unused assets.

Still outstanding in W4.6: the startup egress check that fails closed, the
loopback allowlist, and the blocked-network continuous integration job. Those
need the pipeline from W4.4, which does not exist yet.

### W4.7 — Resident capability inventory

**Problem.** The application depends on a set of models and assets that are
acquired outside the source tree: Whisper `large-v3` in CTranslate2 form,
wav2vec2 alignment models per language, pyannote community-1 with its
segmentation, embedding and PLDA files, SpeechBrain ECAPA-TDNN,
Qwen3-Embedding-0.6B, a language model served by Ollama, and RapidOCR models.
`MODELS_DIR` resolution already has multiple fallback paths
(`backend/config.py:37-45`), which indicates this has been a source of
difficulty. There is no single manifest declaring what must be present.

**Change.** Produce a manifest enumerating every runtime artefact with its
version, checksum, size and purpose. Add a startup preflight that verifies the
manifest against what is installed and reports precisely what is missing or
corrupt, before any user-visible operation begins. Make the packaging step
assemble the artefact set from the manifest, so the shipped bundle and the
verification list derive from the same source.

**Rationale.** On a connected system a missing model is an inconvenience,
recovered by a download. On an air-gapped system it is a support incident
requiring physical media, so the diagnosis must be exact and must arrive
immediately rather than three stages into a pipeline run. Deriving both the
bundle and the check from one manifest is what prevents the two from drifting,
which is the failure that produces an installation that passes its own checks
and then fails in use.

This also constrains a language decision. Whisper's alignment models are
per-language and only the packaged languages will work. That set must be a
declared product decision recorded in the manifest, not an accident of which
model files happened to be on the build machine.

**Evidence of success.** A deliberately incomplete installation is diagnosed at
startup with an exact list. The packaged bundle matches the manifest by
checksum.

---

## Phase 5 — Frontend

### W5.1 — Generated API client

**Problem.** The frontend hand-maintains API types across 190 TypeScript files
against a FastAPI backend that already produces an OpenAPI schema.

**Change.** Generate the client and types from the OpenAPI schema as a build
step. Fail the build on drift.

**Rationale.** Hand-maintained clients drift from their servers, and the drift
surfaces at runtime in front of the user rather than at build time in front of a
developer. The schema already exists, so this is adoption rather than
construction.

**Evidence of success.** A backend contract change fails the frontend build.

### W5.2 — Transcript rendering performance

**Problem.** Long meetings produce word-level segments with per-word timestamps,
confidence scores and speaker labels. Rendering these as ordinary React lists
degrades with meeting length.

**Change.** Virtualise the transcript and ROM point lists. Memoise segment
components. Move any word-level derivation off the render path.

**Rationale.** The data model is word-level by design, and it is the right
design, so the volume is inherent and will grow with meeting duration rather
than being bounded by a fix elsewhere. Virtualisation bounds rendering cost by
viewport rather than by document size, which is the only approach that holds as
meeting length grows.

**Evidence of success.** Interaction latency on the longest golden-set recording
measured before and after.

### W5.3 — Query cache and polling

**Problem.** TanStack Query is present. Pipeline progress is polled. Cache
configuration and polling intervals have not been reviewed against the new job
model.

**Change.** Set explicit staleness and cache times per query class. Replace
progress polling with server-sent events from the job system of W2.2, falling
back to polling where unavailable.

**Rationale.** Polling a long pipeline means repeated queries against the same
SQLite database the worker is writing to, which is the exact contention W3.2
mitigates rather than eliminates. Event push removes the queries instead of
making them cheaper, and the job table's state transitions give it a natural
source of events.

**Evidence of success.** Progress requests per run against baseline. Update
latency in the UI unchanged or better.

---

# Part 4 — Sequencing

| Phase | Contents | Gate to proceed |
|---|---|---|
| 0 | W0.1 evaluation harness, W0.2 instrumentation | Baseline metrics committed |
| 1 | W1.1 to W1.9 correctness | Quality metrics improved against baseline; no regression |
| 2 | W2.1 to W2.7 efficiency | Latency and resource metrics improved; quality metrics unchanged |
| 3 | W3.1 to W3.6 structure and data | Golden-set output identical before and after; migrations tested |
| 4 | W4.1 to W4.7 hardening | Security checks pass; CI green; update path tested; full run completes with network severed |
| 5 | W5.1 to W5.3 frontend | Frontend metrics improved; contract drift caught at build |

Three ordering constraints are non-negotiable.

**Phase 0 precedes everything.** Without baselines, no later phase can report a
result, and the optimisation goal becomes unverifiable.

**Phase 1 precedes Phase 2.** Optimising the current short and medium generation
would mean optimising code that W1.1 and W1.2 restructure, and the work would be
discarded.

**Phase 3 output must be provably neutral.** Restructuring is only safe if the
golden set demonstrates identical output across the change. Any structural change
that alters output is a Phase 1 or Phase 2 change in disguise and should be
reclassified rather than bundled.

Phases 4 and 5 may proceed in parallel with Phase 3, since they touch different
surfaces. W4.1, secrets enforcement, should be pulled forward to run alongside
Phase 1 regardless of sequence, because it is small, independent, and the current
state is a shipped known credential.

Two items from W4.6 should also be pulled forward into Phase 0. The
blocked-network evaluation job belongs with the harness it runs, because a
baseline measured on a connected machine is not a baseline for this product. The
font fix belongs there too: it is a few hours of work, it affects every launch,
and leaving it in place would contaminate the startup latency baseline that later
phases are measured against.

# Part 5 — Definition of done

The system is production-ready when all of the following hold.

1. Every metric in the W0.1 table is measured on every release candidate and
   compared against the previous release, with regressions blocking.
2. No date, number, technical term or action item present in the long ROM is
   absent from the medium ROM. Losses in the short ROM are enumerated in a
   coverage record.
3. Every action item owner is either explicitly attributed by evidence or
   explicitly null, with provenance recorded. No owner is inferred from the fact
   of speaking.
4. A worker crash at any point in a run resumes without user intervention and
   without recomputing completed stages.
5. Each model is loaded at most once per run, and peak VRAM stays within the
   declared budget.
6. A database from any shipped prior version upgrades cleanly, with a test
   proving it.
7. No installation runs with a default secret. The build fails if one is
   packaged.
8. Lint, type check, and both test suites run on every change and pass.
9. Every algorithmic threshold in the parameter audit is reachable through
   settings and defined exactly once.
10. Every degraded or fallback outcome is visible to the user rather than
    silently absorbed.
11. The full golden-set pipeline completes with every outbound route blocked at
    the operating system level, with zero connection attempts recorded, verified
    on every change.
12. Every runtime artefact is declared in a manifest, verified at startup, and
    assembled into the shipped bundle from that same manifest.
