# What the workstreams are for

Plain-language guide to every workstream in `docs/REVAMP-PLAN.md`: what problem
each one solves, what you would notice once it is done, and where it stands.
Technical detail and rationale live in the plan; this is the overview.

Status as of 2026-09-14.

| | Count |
|---|---|
| Done | 12 |
| Partly done | 5 |
| Not started | 25 |

---

## 1. Measuring quality — "is it actually better?"

Without measurement, every improvement is an opinion. These make quality a
number that can be tracked.

| ID | What it's for | Status |
|---|---|---|
| W0.1 | **Golden dataset and scoring harness.** Scores speaker accuracy, information kept in short/medium ROMs, and action-point accuracy, against human-labelled answers. | **Done** for the harness and three synthetic cases. Real recordings must be added by your team (see `backend/eval/README.md`). |
| W0.2 | **Stage timing and counters.** Records how long each stage takes, how often models load, how many tokens the language model spends. Needed to prove any speed-up. | **Done** at run level. Per-stage breakdown still to add. |

## 2. Your three reported problems

| ID | What it's for | Status |
|---|---|---|
| W1.1 | **Short/medium ROM losing information.** Dates, figures and technical terms were being dropped, and owners attached to the wrong points. | **Done** |
| W1.2 | **Deciding which points matter.** Gives every point an importance score, so condensing keeps what matters rather than guessing. | **Done** |
| W1.3 | **Showing what was dropped.** Lists the points removed from a short version so a reviewer can see and restore them. | **Partly done**: recorded in the data, not yet shown in the interface. |
| W1.4 | **Speaker correction that never ran.** A bug stopped the correction step from ever fixing a mislabelled speaker. | **Done** |
| W1.5 | **One person split into two speakers.** When the diarizer splits one voice into two clusters, only one gets the right name; the other stays "Speaker 2". Merges such clusters first. | Not started. Needs real recordings to validate. |
| W1.6 | **Short replies given to the wrong person.** A quick "yes, agreed" under 2.5 seconds is labelled with whoever spoke just before. | Not started. Needs real recordings to validate. |
| W1.7 | **Actions assigned to whoever was talking.** Unassigned tasks were given to every speaker. Now only evidence assigns an owner, and guesses are labelled. | **Done** |
| W1.8 | **Reassigned commitments.** "Kiran will book it" then later "Meera will book it instead" should yield one action for Meera, not two. | Not started |
| W1.9 | **Empty AI instructions.** Four prompts silently sent nothing to the model. | **Done** |

## 3. Speed and reliability — biggest impact on the demo machines

These decide whether a long meeting takes minutes or hours, and whether a crash
loses the work.

| ID | What it's for | Status |
|---|---|---|
| W2.1 | **Stop reloading models.** Models are loaded and unloaded many times within one meeting. Loading once per run is likely the single largest speed-up available. | Not started |
| W2.2 | **Survive crashes.** Processing runs inside the web server; closing the app or a crash loses the job. A proper job queue resumes it. | Not started |
| W2.3 | **Never redo finished work.** A failure late in processing re-runs transcription from scratch. Saves each stage's result so only failed stages rerun. | Not started |
| W2.4 | **Faster search over documents.** Embeds text one item at a time; batching and caching make context retrieval much faster. | Not started |
| W2.5 | **Faster duplicate removal.** The same slow duplicate-detection code exists in four places. One fast version. | Not started |
| W2.6 | **Reliable AI output format.** The model sometimes returns broken JSON, costing a retry call. Constraining its output removes the retry. | Not started |
| W2.7 | **Fewer wasted tokens.** Trims oversized AI instructions and budgets, based on measurement. | Not started |

## 4. Data safety and maintainability

| ID | What it's for | Status |
|---|---|---|
| W3.1 | **Safe upgrades.** Database changes are hand-applied at startup with no rollback. A migration tool makes upgrades safe for installed customer data. | Not started |
| W3.2 | **"Database is locked" errors.** Long processing blocked the interface from reading. | **Done** |
| W3.3 | **Cleaner database code.** Raw SQL is scattered across the app, which is where slow queries and connection leaks hide. | Not started |
| W3.4 | **One place for each setting.** The same defaults were copied many times and drifted apart (this caused your account's settings to be wrong). | **Partly done**: model priority centralised; other thresholds remain. |
| W3.5 | **Break up oversized files.** Three files of 4,000 to 6,000 lines are where most bugs live and every change lands. | Not started |
| W3.6 | **One vector store.** Two storage systems for search data exist side by side. | Not started |

## 5. Hardening for delivery

| ID | What it's for | Status |
|---|---|---|
| W4.1 | **Unique security key per install.** Every copy shipped with the same known key. | **Done** |
| W4.2 | **Safe file uploads.** Uploaded files are served without per-user access checks and decoded without time limits. | Not started |
| W4.3 | **Honest error messages.** Failures blame the user (e.g. "please re-record") when the fault is internal. Distinguishes user, temporary and internal errors. | Not started |
| W4.4 | **Automatic testing on every change.** Tests and a crash-defect scan run on every push to GitHub. | **Done** |
| W4.5 | **Safe offline updates.** Install and update without internet, verifying files, never leaving a half-updated install. | **Partly done**: one-command setup exists; offline update bundles do not. |
| W4.6 | **Guaranteed no internet use.** Removes external calls and fails the build if one reappears. | **Partly done**: fixes and tests in place; startup enforcement not yet. |
| W4.7 | **Verify model files.** Checks every model file is present and intact at startup, with an exact list of anything missing. | **Partly done**: presence checked; integrity checksums not yet. |

## 6. Interface

| ID | What it's for | Status |
|---|---|---|
| W5.1 | **Frontend and backend never drift apart.** Generates the interface's API code from the backend, so a mismatch fails the build instead of the demo. | Not started |
| W5.2 | **Smooth long transcripts.** Long meetings render slowly because every word is drawn at once. | Not started |
| W5.3 | **Live progress.** Replaces repeated polling with pushed progress updates. | Not started |

## 7. Audit trail and leadership reporting

The capability you asked for: who logged in, who changed what, and a weekly
health summary.

| ID | What it's for | Status |
|---|---|---|
| W6.1 | **Administrator role.** The admin dashboard, logs and maintenance actions were open to anyone. | **Done** |
| W6.2 | **Complete audit log.** Every login and every change, tamper-evident. | Not started |
| W6.3 | **Trace any sentence to its source.** From a line in the final ROM back to the exact moment in the recording, and whether a person or the AI wrote it. | Not started |
| W6.4 | **Retention and archiving.** Logs and audit records grow without limit on a fixed disk. | Not started |
| W6.5 | **Keep names out of logs.** Logs currently record participant names and speaking times. | Not started |
| W6.6 | **Define "health".** The measurable signals the weekly report is built from. | Not started |
| W6.7 | **Weekly leadership report.** Generated automatically, including catch-up for weeks the machine was off. | Not started |
| W6.8 | **Admin console.** Audit search, user and session management, retention, report archive. | Not started |

---

## Suggested order from here

1. **Add real recordings to the golden dataset.** Everything about accuracy
   depends on it, and only your team can supply them.
2. **W2.1, W2.2, W2.3** — speed and crash recovery. Largest effect on how the
   product behaves on real meetings.
3. **W6.2, W6.5** — audit log and keeping names out of logs.
4. **W1.5, W1.6, W1.8** — remaining accuracy fixes, validated against the real
   recordings from step 1.
5. **W3.1, W4.3** — safe upgrades and honest errors, before wider deployment.
