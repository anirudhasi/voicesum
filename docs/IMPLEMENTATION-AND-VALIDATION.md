# Implementation and Validation Plan

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Companion documents | `docs/REVAMP-PLAN.md` (what to change and why), `docs/observations/2026-09-12-parameter-audit.md` (evidence) |
| Purpose | How the work is sequenced, and how each stated goal is proven met |
| Runtime constraint | Air-gapped. All validation runs with outbound routes blocked. |

## Note on sizing

Effort sizes below are S, M or L and are judgement calls based on the observed
structure of the code, not estimates derived from measurement. They are for
sequencing and for identifying what can run in parallel. They are not a schedule
commitment, and no calendar dates appear in this document because none can be
justified from what has been measured so far.

---

# Part 1 — Goal traceability

Your stated goals, each mapped to the work that addresses it and the measurement
that proves it. Nothing is considered done on assertion.

## G1 — Speaker identification

> "Our method has significantly performed well. But there are some edge cases of
> wrong speaker assignment."

| Workstream | Addresses | Primary metric | Gate |
|---|---|---|---|
| W1.4 Refinement reachability | Correction stage cannot fire on generic labels | Speaker confusion rate | Must improve |
| W1.5 Cluster reconciliation | One person split across two diarization clusters | Diarization Error Rate, Jaccard Error Rate | Must improve |
| W1.6 Short-turn assignment | Turns under 2.5 s assigned by proximity, not acoustics | Confusion rate on turns under 2.5 s | Must improve |
| W3.4 Threshold consolidation | 0.82 and 0.20 hard-coded beside a configurable margin | All thresholds reachable in settings | Structural |

These three fixes interact, which shapes how they are validated. W1.4 makes the
refinement pass reachable, W1.5 reduces how often it is needed, and W1.6 changes
what happens where it cannot help. Measuring them only in combination would tell
you the total moved without telling you which change moved it, and would leave a
regression in one masked by a gain in another. Each is therefore measured
independently against the same baseline before the combined result is taken.

## G2 — Short and medium point generation

> "We are passing the points and saying llm to keep the important point and
> summarize. But some information is lost... How we can improve and assign
> importance to the points."

Your question has two halves and they are treated as two separate problems,
because they have different causes and different fixes.

**The information loss is a code defect, not a prompt weakness.** Metadata is
reattached by list position after the model has merged and reordered the points,
and technical terms, dates and numbers are discarded unconditionally
(`backend/services/rom_service.py:4700-4720`). No prompt change can fix this.

**The importance question is a missing capability.** No point in the system
carries any measure of significance, so the instruction to keep the important
points has nothing behind it.

| Workstream | Addresses | Primary metric | Gate |
|---|---|---|---|
| W1.1 Provenance metadata | Positional reattachment; discarded entity fields | Entity retention rate | Medium version: no loss permitted |
| W1.2 Importance scoring | No significance signal exists | Must-keep point recall | Must improve at fixed output length |
| W1.3 Coverage reporting | Losses are invisible to the reviewer | Coverage record completeness | 100 percent of input IDs accounted for |

## G3 — Action point generation

> "N also improvement in action point generation"

| Workstream | Addresses | Primary metric | Gate |
|---|---|---|---|
| W1.7 Owner integrity | Code overwrites the model's correct null owner with a speaker guess | Non-null owner precision | Must improve |
| W1.8 Cross-chunk reconciliation | Ten-point window cannot see a commitment reassigned later | Action precision and recall | Must improve |
| W1.9 Empty prompt constant | A registered prompt is an empty string | Startup assertion | Binary |

## G4 — Efficiency and optimisation

| Workstream | Addresses | Primary metric | Gate |
|---|---|---|---|
| W2.1 Model manager | Model unloaded and reloaded repeatedly within one run | Model load count per run | At most one per model per run |
| W2.3 Stage checkpointing | A late failure re-runs transcription | Re-run cost with no input change | Near zero |
| W2.4 Embedding batch and cache | Per-item GPU inference on a batchable operation | Embedding call count, cache hit rate | Must improve |
| W2.5 Vectorised dedup | Quadratic Python scan over resident NumPy arrays | Stage 2 wall clock | Must improve, decisions identical |
| W2.7 Prompt efficiency | Token budgets set without measurement | Tokens per run | Must improve, no quality regression |
| W4.6 Font fix | Render-blocking import that cannot resolve offline | Time to first paint | Must improve |
| W5.2 Virtualisation | Word-level data rendered as ordinary lists | Interaction latency, longest recording | Must improve |

## G5 — Administrator observability, audit and leadership reporting

Designed in full in `docs/OBSERVABILITY-AND-AUDIT.md`. Summarised here for
traceability.

| Workstream | Addresses | Primary metric | Gate |
|---|---|---|---|
| W6.1 Admin role and route lockdown | Dashboard, logs, diagnostics and maintenance are reachable unauthenticated | Route classification coverage | No unclassified route; admin routes deny non-admins |
| W6.2 Tamper-evident audit log | Only logins and Stage 2 edits are recorded; nothing is tamper-evident | Audit completeness over mutating routes | 100 percent; modification and deletion both detected |
| W6.3 Provenance chain | No way to trace a final ROM sentence to its origin | Chain resolution on arbitrary sentences | Full chain with human-versus-model marked at each hop |
| W6.4 Retention and archival | `login_attempts` and logs grow unbounded on fixed disk | Purge correctness | Archive verifies; purge self-audits; audit floor respected |
| W6.5 Log content policy | Logs carry participant names and are downloadable | Participant names in default-level logs | Zero |
| W6.6 Health model | Health is undefined | Six dimensions populated | All signals collected |
| W6.7 Weekly leadership report | No scheduler, no report | Determinism and catch-up | Identical on regeneration; no week silently skipped |
| W6.8 Admin console | No audit, user, retention, report or integrity views | Console coverage | All six areas present, admin-gated |

Two placement notes carry into the milestones below. W6.1 is urgent because the
exposure is live. Health signal collection must begin at M0 even though the
report ships at M6, because a report has nothing to trend against if collection
starts when the report does.

## G6 — Production readiness and offline operation

Validated against the twelve-point definition of done in the revamp plan. The
two offline items are gated by a single check: the full golden-set pipeline
completes with every outbound route blocked and zero connection attempts
recorded.

---

# Part 2 — The critical path

One item gates almost everything, and it is not a coding task.

**The labelled golden dataset is the critical path.** Every gate in Part 1
except the structural and binary ones is measured against it. It requires
someone with domain knowledge to listen to recordings and mark reference speaker
turns, then read transcripts and mark which facts, dates, numbers and action
items must survive to the final record. That work cannot be done by the
engineering team alone and cannot be parallelised by adding engineers.

Two consequences follow.

Start dataset labelling before any code work, and run it in parallel with
everything else. It should be the first thing commissioned.

Sequence the labelling so it unblocks work in order of need. Speaker turn
references unblock G1. Must-keep entity and action labels unblock G2 and G3.
Speaker labelling is the slower of the two and should go first.

A fallback exists if labelling lags. The structural gates, the binary gates, and
the efficiency metrics in G4 need no labels at all, because model load count,
wall clock, token count and cache hit rate are properties of execution rather
than of correctness. G4 work can therefore proceed on an unlabelled corpus while
labelling continues. What cannot proceed is any claim about quality.

---

# Part 3 — Milestones

## M0 — Measurement foundation

**Entry.** None. Starts immediately.

**Tasks.**

| Task | Workstream | Size | Parallel |
|---|---|---|---|
| Commission dataset labelling | W0.1 | L | Yes, runs throughout |
| Build evaluation harness and metric implementations | W0.1 | M | Yes |
| Stage instrumentation and `run_metrics` table | W0.2 | M | Yes |
| Blocked-network evaluation job | W4.6 partial | S | Yes |
| Self-host fonts, disable ChromaDB telemetry | W4.6 partial | S | Yes |
| Enforce secrets, fail closed | W4.1 | S | Yes |
| Admin role; lock down dashboard, logs, diagnostics, maintenance | W6.1 | M | Yes, urgent |
| Audit log schema, hash chain, write path | W6.2 | M | Yes |
| Structured logging and name-redaction policy | W6.5 | M | Yes |
| Begin collecting all health signals | W6.6 | S | Yes |

The font fix and the telemetry fix are pulled into M0 rather than left in Phase
4 for a specific reason: both affect the baseline. A startup latency baseline
measured with a render-blocking font import that times out is not a baseline of
the product, and every later latency comparison would inherit the distortion.

**Exit.** Baseline figures for every metric in Part 1 committed to the
repository, measured with the network severed. Per-stage timing and model load
counts visible for a complete run. No administrative route reachable without an
administrator. Audit records written for every mutating route, with chain
verification passing. Participant names absent from default-level logs.

The observability items are in M0 rather than later for two distinct reasons.
The route lockdown closes a live exposure. The audit log and health signal
collection start here because both are historical records, and a record that
starts late cannot be backfilled.

**Validation.** Run the harness twice on the same input and confirm the metrics
are stable. An evaluation harness that is itself noisy will produce false
regressions later and erode trust in every gate that follows.

## M1 — Speaker identification (G1)

**Entry.** M0 complete. Speaker turn references labelled.

**Tasks.**

| Task | Workstream | Size | Depends on |
|---|---|---|---|
| Fix `original_label_sim` initialisation | W1.4 | S | M0 |
| Promote refinement thresholds to settings | W1.4, W3.4 | S | M0 |
| Cluster reconciliation before bipartite matching | W1.5 | M | M0 |
| Short-turn confidence-marked assignment | W1.6 | M | M0 |
| Threshold calibration against golden set | W1.4, W1.5 | M | The three above |

**Validation.** Ablation, in this order. Measure the baseline. Apply W1.4 alone
and measure. Revert, apply W1.5 alone and measure. Revert, apply W1.6 alone and
measure. Then apply all three and measure. Report five rows.

The reason for the ablation is that these changes overlap in effect. Without it,
a combined improvement could conceal that one of the three made things worse,
and you would carry that regression forward without knowing.

Calibration comes last and only after the ablation, because tuning thresholds
before the structural fixes are in place would tune them against behaviour that
is about to change.

**Exit.** Speaker confusion rate, Diarization Error Rate and Jaccard Error Rate
all improved against baseline, with the sub-2.5-second confusion rate reported
as a separate line. No metric regressed.

**A caution on interpreting the result.** With ten to twenty recordings, a small
movement in a mean is not evidence. Report per-recording deltas alongside the
aggregate and look at whether the change helped consistently or helped a lot on
one recording and hurt on others. The second pattern is overfitting to that
recording and should not pass the gate.

## M2 — Short and medium generation (G2)

**Entry.** M0 complete. Must-keep entity labels available. Independent of M1 and
may run in parallel with it.

**Tasks.**

| Task | Workstream | Size | Depends on |
|---|---|---|---|
| Change prompts to return `source_point_ids` | W1.1 | S | M0 |
| Union metadata from referenced sources | W1.1 | M | Above |
| Validate and retry on invalid provenance | W1.1 | S | Above |
| Compute importance signals in Stage 2 | W1.2 | M | M0 |
| Deterministic selection by importance rank | W1.2 | M | Above |
| Restrict the model to compression of selected points | W1.2 | M | Above |
| Coverage record, persisted and surfaced in UI | W1.3 | M | W1.1 |
| Calibrate importance weights against labels | W1.2 | M | All above |

**Sequencing within M2.** W1.1 goes first and ships on its own. It is the
smaller change, it is a pure defect fix, and it alone should eliminate the
unconditional loss of dates, numbers and technical terms. Shipping it separately
means the customer sees improvement early and you get a clean measurement of how
much of the reported loss was the positional defect rather than the selection
behaviour. That answer informs how much weight-tuning W1.2 needs.

**Validation.**

*Entity retention.* For each recording, take the reference set of must-keep
dates, numbers and technical terms. Measure the fraction present in the long,
medium and short versions. The medium version must lose nothing relative to the
long version. This is a hard gate, not a target, because W1.1 makes it
structurally achievable through union semantics rather than through model
cooperation.

*Must-keep recall.* For each recording, the domain reviewer marks which
discussion points must appear in a short version. Measure recall at the fixed
short output length, before and after W1.2. This is the metric that answers your
question about assigning importance, and it must be measured at a fixed output
length or a system that simply keeps more points would appear better.

*Determinism.* Run condensation three times on identical input and confirm the
dropped set is identical. This is a hard gate. If selection is not repeatable,
no calibration of the weights is meaningful, because the next run may drop
differently regardless of the weights.

*Coverage completeness.* Every generated version accounts for one hundred
percent of input point IDs as retained, merged or dropped.

*Customer confirmation.* The Aeronautical Development Agency reported the
original problem and is the only party that can confirm it is resolved. Put a
before-and-after sample of the same meeting in front of the same reviewers.
Passing the internal gates means the change is measurably correct; it does not
by itself mean the reviewer is satisfied, and those are different claims.

**Exit.** All four internal gates passed. Customer sample sent.

## M3 — Action points (G3)

**Entry.** M0 complete. Action item and owner labels available. May run in
parallel with M1 and M2.

**Tasks.**

| Task | Workstream | Size | Depends on |
|---|---|---|---|
| Cap owner fallback at step 2, preserve null | W1.7 | S | M0 |
| Add `owner_source` provenance field | W1.7 | S | Above |
| Unassigned-action review queue in UI | W1.7 | M | Above |
| Cross-meeting action reconciliation pass | W1.8 | M | M0 |
| Resolve or remove the empty prompt constant | W1.9 | S | None |
| Startup assertion on non-empty prompts | W1.9 | S | Above |

**Validation.**

*Owner attribution accuracy*, counting a correctly-null owner as correct. This
framing is deliberate and it matters. A metric that rewarded any non-null answer
would reward exactly the failure being fixed, and the system would appear to
regress while genuinely improving.

*Non-null owner precision*, reported separately. This is the number that must
rise, and it is the number the customer experiences, because a wrong owner is
acted upon while an unassigned item is triaged.

*Action precision and recall* against the labelled reference set.

*Supersession cases.* Count the labelled instances where a commitment was
reassigned or withdrawn later in the meeting, and how many were resolved
correctly. Ensure the golden set contains at least a few, or W1.8 cannot be
validated at all. If the current corpus has none, that is a labelling
instruction, not a reason to skip the metric.

**Expect the unassigned count to rise, and do not treat that as a regression.**
Capping the fallback converts confident wrong attributions into honest nulls.
The action list will show more unowned items than before. That is the fix
working. This needs to be explained to reviewers before they see it, or it will
be reported as a defect.

**Exit.** Non-null owner precision improved. Action precision and recall
improved. Startup fails on any empty registered prompt.

## M4 — Efficiency (G4)

**Entry.** M1, M2 and M3 complete. This ordering is firm: W2.1 and W2.3 touch
the same execution paths that M1 to M3 restructure, and doing the performance
work first would mean doing it twice.

**Tasks.**

| Task | Workstream | Size | Depends on |
|---|---|---|---|
| Model manager with leases, LRU, GPU semaphore | W2.1 | L | M1-M3 |
| Remove scattered unload and cache-clear calls | W2.1 | M | Above |
| Durable job table and worker process | W2.2 | L | M1-M3 |
| Stage checkpointing and idempotency | W2.3 | L | W2.2 |
| Embedding batching and content-hash cache | W2.4 | M | Parallel |
| Shared vectorised deduplication | W2.5 | M | Parallel |
| Local constrained decoding and schemas | W2.6 | M | Parallel |
| Token budget reduction from measurement | W2.7 | S | W0.2 data |

**Validation.**

*Quality must not move.* Every G1, G2 and G3 metric is re-measured and must be
unchanged. This is the defining gate of M4. Performance work that shifts output
quality in either direction has changed behaviour and must be reclassified and
re-validated as a correctness change, not accepted because the number happened
to improve.

*Model load count.* At most one load per model per run, taken from the W0.2
instrumentation. This is the direct test of the largest efficiency hypothesis in
the plan.

*Wall clock per stage and end to end*, against the M0 baseline, on identical
hardware and identical inputs. State the hardware in the report, because these
figures are not portable across machines.

*Peak video memory* within the declared budget, including under parallel Whisper
settings above one.

*Crash recovery.* Terminate the worker at three points, during transcription,
during Stage 2, and during final generation. Each must resume without user
intervention and without recomputing completed stages.

*Re-run cost.* A completed pipeline re-run with no input change completes in near
zero time. Changing one Stage 2 setting re-runs Stage 2 onward and no earlier
stage. This second test is the one that proves checkpoint invalidation is keyed
correctly rather than merely present.

*Dedup equivalence.* W2.5 must produce byte-identical deduplication decisions to
the implementation it replaces, asserted by test, before any threshold is tuned.

**Exit.** All gates above passed with quality metrics unchanged.

## M5 — Structure and data integrity

**Entry.** M4 complete.

**Tasks.** W3.1 migrations, W3.2 SQLite configuration and indexing, W3.3
repository layer, W3.5 module decomposition, W3.6 vector store consolidation.

**Validation.**

*Output equality.* Golden-set output must be identical before and after, not
merely equivalent in aggregate metrics. Any structural change that alters output
is a behaviour change misfiled as a refactor and belongs in an earlier
milestone.

*Migration.* A database from every shipped prior shape upgrades cleanly with
data intact, proven by test. Build the fixture databases before writing the
migrations, because writing them afterwards tends to produce fixtures that match
the migration rather than the field.

*Lock behaviour.* Concurrent read-while-writing load test produces no
`database is locked` errors.

*Query performance.* Dashboard query times before and after indexing.

**Exit.** No module above roughly 800 lines. All tests green. Output identical.

## M6 — Hardening, packaging and frontend

**Entry.** M5 complete for the backend items. Frontend items may start after M0.

**Tasks.** W4.2 file handling, W4.3 error taxonomy, W4.4 continuous integration,
W4.5 update integrity, W4.6 remaining enforcement, W4.7 artefact manifest, W5.1
generated client, W5.2 virtualisation, W5.3 event-driven progress. Add the
Electron `webSecurity` fix here. Plus the remaining observability items: W6.4
retention and archival, W6.7 weekly report generation and delivery, W6.8 console
extensions.

**A note on the first report.** A rolling four-week baseline does not exist until
roughly a month after collection reaches steady state. Reports issued before
then are correct but will print "insufficient data" against most comparisons.
Say so when the first one is circulated, or it will be judged a failure on its
first issue.

**Validation.** The twelve-point definition of done in the revamp plan, in full.
Specifically: an incomplete installation is diagnosed at startup with an exact
list; an interrupted update leaves the prior version working; cross-user file
access is denied; a production build with default secrets fails to start; the
blocked-network job passes on every change.

**Exit.** Definition of done satisfied in full.

---

# Part 4 — Validation methodology

## Dataset construction

Ten to twenty recordings spanning the real distribution: two speakers and many
speakers, clean and noisy, with and without video slides, short and long, and at
least two recordings representative of the customer's domain vocabulary. Include
at least a few recordings containing a commitment that is later reassigned or
withdrawn, or W1.8 cannot be validated.

Store the audio outside the repository. Commit the manifest and checksums.
Labels are committed, since they are text and they are the asset that took the
most effort to produce.

## Reference labels required

| Label | Needed for | Produced by |
|---|---|---|
| Speaker turn boundaries and identities | Diarization and identification metrics | Domain listener |
| Reference transcript | Word Error Rate | Domain listener |
| Must-keep dates, numbers, technical terms | Entity retention | Domain reader |
| Must-keep discussion points | Must-keep recall | Domain reader |
| Reference action items with owners and deadlines | Action precision, recall, owner accuracy | Domain reader |
| Superseded commitments | Reconciliation | Domain reader |

Owner labels must distinguish genuinely unassigned from assigned, because the
owner metric counts a correct null as correct and cannot do so without that
distinction in the reference.

## Metric implementations

Diarization Error Rate and Jaccard Error Rate come from `pyannote.metrics`,
which installs locally and needs no network. These are the standard metrics for
the task, which is what allows the result to be compared against published
systems rather than only against your own previous build.

Word Error Rate uses a standard local implementation with a documented
normalisation, since the normalisation choice affects the number and an
undocumented one makes the figure unreproducible.

The remaining metrics are set operations over labels and system output and are
implemented in `backend/eval/`.

## Running and reporting

Every evaluation run executes with outbound routes blocked at the operating
system level and records any connection attempt as a failure.

Every report states the hardware, the commit, and the settings used. Latency
figures are not comparable across machines, and a report without the settings
cannot be reproduced.

Report per-recording results alongside aggregates. Aggregates over ten to twenty
recordings hide the distinction between a consistent improvement and a single
recording dominating the mean, and only the first is a real improvement.

## Regression gate policy

| Change class | Quality metrics | Efficiency metrics |
|---|---|---|
| Correctness, M1 to M3 | Target metric improves, none regresses | Must not materially worsen |
| Efficiency, M4 | Unchanged | Target metric improves |
| Structural, M5 | Output byte-identical | Unchanged or better |
| Hardening, M6 | Unchanged | Unchanged or better |

A regression blocks the change. The gate is not advisory. The value of this
policy comes entirely from it being applied when it is inconvenient.

---

# Part 5 — Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Labelling is slow or unavailable | Every quality gate is blocked | Commission first; run G4 efficiency work on unlabelled data meanwhile; label speaker turns before entity labels |
| Small dataset produces noisy metrics | False confidence or false regressions | Per-recording reporting; require consistent direction, not just a better mean |
| Importance weights overfit the golden set | Works in test, disappoints the customer | Hold out recordings not used in calibration; confirm with the customer on a fresh meeting |
| More unassigned actions read as a regression | Correct fix rejected by reviewers | Explain the change before reviewers see it; report non-null precision alongside the raw count |
| Model manager introduces out-of-memory failures | Worse than the thrash it replaced | Declared video memory budget with LRU eviction; test at parallel Whisper settings above one |
| Module decomposition alters behaviour | Silent regression inside a refactor | Byte-identical output gate; incremental, one seam at a time |
| Migration fails on a field database | Customer data loss | Fixture databases built from real shipped shapes before migrations are written |
| A dependency upgrade adds a network call | Offline guarantee breaks silently | Blocked-network job on every change; pinned and vendored dependencies |

---

# Part 6 — Decisions needed from you

These shape the work and I cannot settle them from the code.

**Which recordings can form the golden set, and who labels them.** This is the
critical path and the first thing to start. Customer recordings would be the most
representative; if they cannot leave the customer's environment, the evaluation
harness has to run there instead, which changes how M0 is delivered.

**Whether the Aeronautical Development Agency will review a before-and-after
sample.** They reported the problem and are the only party who can confirm it is
resolved. Knowing whether that channel is available determines whether M2 ends
at an internal gate or a customer one.

**Which languages ship.** Whisper's alignment models are per-language and only
the packaged ones will work offline. This is a product decision that must be
recorded in the W4.7 manifest rather than inherited from whatever was on the
build machine.

**The video memory budget and target hardware.** W2.1 needs a declared ceiling,
and latency targets are meaningless without a stated machine.

**Whether short and medium versions may change shape.** W1.2 changes how points
are selected, so a regenerated short version of an existing meeting will differ
from the one a customer has already seen. If prior outputs must remain stable,
versions need pinning to the generation logic that produced them, which is
additional work to schedule.
