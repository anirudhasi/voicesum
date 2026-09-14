# Golden dataset and evaluation harness

Turns "the output looks better" into numbers that can be compared build to
build. Every claim about speaker identification, information retention in
condensed ROM versions, or action-point quality should be backed by a report
from here.

```bash
cd backend
.venv/Scripts/python -m eval.runner --dataset eval/golden --out eval/reports/latest
```

The run writes `report.md` (human summary) and `report.json` (full detail).

## What the dataset contains

Two kinds of case, because the two halves of the system need different ground
truth.

| Kind | Scores | Ground truth comes from |
|---|---|---|
| `audio` | Transcription, diarization, speaker identification | A person listening to a real recording |
| `rom` | Information retention in short/medium ROM, action extraction | A person reading the discussion points |

Every case declares `provenance`: `real` or `synthetic`. A report built only
from synthetic cases is stamped as such, so constructed data can never be quoted
as a measurement of real meetings.

### Current contents

| Case | Kind | Provenance | Targets |
|---|---|---|---|
| `propulsion_review` | rom | synthetic | Figures and dates under merge pressure; filler; two-speaker unassigned action; reassigned commitment |
| `avionics_integration` | rom | synthetic | Co-owned action; team owner; three-speaker unassigned action; timing figures |
| `budget_schedule` | rom | synthetic | Same topic revisited with different figures; milestone slip; explicit owners |

**No real recordings are in the dataset yet.** That is the most valuable thing
to add, and only your team can do it: the audio half of the harness is built
and tested, but it has nothing real to measure.

## Adding a real recording

Target 10 to 20 recordings spanning the real distribution: two speakers and
many, clean and noisy, short and long, and at least two in your domain
vocabulary. Include a meeting where a commitment is later reassigned.

1. **Process it in the application** as normal and note the recording id.

2. **Export a draft** from the system's own output:

   ```bash
   .venv/Scripts/python -m eval.prelabel --recording-id <id> --case-id standup_0914 \
       --enrolled "Arjun,Meera"
   ```

   This writes `eval/golden/audio/standup_0914/reference.draft.json` and the
   system output to `eval/golden/system/standup_0914.json`.

3. **Correct the draft by listening.** Fix every turn boundary, every speaker
   name and every word. Correcting a draft takes a fraction of the time of
   labelling from scratch, which is why this step exists.

4. **Rename** it to `reference.json` and delete the `_status` field. The runner
   refuses to load an uncorrected draft: scoring the system against its own
   output always reads as perfect and measures nothing.

5. **Add it to `manifest.local.json`** (create it if absent, same format as
   `manifest.json`) with `"provenance": "real"`. Real cases never go in the
   tracked `manifest.json`; see below.

6. **Run** with `--system-dir eval/golden/system`.

**Real meeting data stays out of git by default.** Audio, corrected references
and exported transcripts all contain meeting content, so `.gitignore` excludes
`eval/golden/audio/`, `eval/golden/system/` and `manifest.local.json`. The
tracked `manifest.json` holds only synthetic cases, which keeps a fresh clone
self-consistent. Share real cases through whatever channel your security
policy permits, not through the repository.

## Metric definitions

Every metric is defined exactly, because the same outputs scored with different
definitions give different numbers. Text comparisons share one normalisation
(`metrics/text.py`): lowercase, accents and punctuation stripped, decimal points
inside figures kept.

### Information retention (short and medium ROM)

| Metric | Definition |
|---|---|
| Entities in text | Fraction of must-keep dates, figures and terms that appear in the prose a reader sees. **This is the customer-facing metric.** |
| Entities in metadata | Fraction present in the structured `technical_terms`, `dates`, `numbers` fields. Provenance merging guarantees this, so it tests plumbing, not the model. |
| Must-keep recall | Fraction of must-keep points that survive into the short version, determined from provenance (`source_point_ids`), so a well-paraphrased point still counts. |

Reported separately on purpose. High metadata retention with low text
retention means the facts were kept in the data but written out of the prose,
which a reader experiences as loss.

### Action points

| Metric | Definition |
|---|---|
| Precision / recall | One-to-one matching of system to reference actions by content-token Jaccard similarity of the task text, threshold 0.35, greedy in descending similarity. Superseded reference actions are excluded. |
| Owner accuracy | Over matched pairs. **A correctly-null owner counts as correct**, so a metric cannot reward guessing an owner for an unassigned action. |
| Named-owner precision | Over matched pairs where the system named someone: how often it was right. The number a reader experiences, and the one that must rise. |
| Superseded emitted | Reassigned or withdrawn commitments the system still emitted as stated. |

### Speech

| Metric | Definition |
|---|---|
| Word error rate | jiwer WER after shared normalisation. |
| Diarization error rate / Jaccard error rate | pyannote.metrics, 0.25 s collar, overlap scored. Measures *who spoke when* under optimal label mapping, independent of names. |
| Speaker confusion rate | Duration-weighted fraction of enrolled speakers' speech where the resolved *name* is wrong. No mapping. The metric for "wrong speaker assignment". |
| Short-turn confusion rate | The same, for reference turns under 2.5 s, where embeddings are unreliable. |
| Unmatched rate | Enrolled speakers' speech left under a generic label. |

## Reading a report

- **Report per case, not only the mean.** With a small dataset, a change that
  helps one recording a lot and hurts the rest raises the mean while getting
  worse. Look for a consistent direction across cases.
- **Compare like with like.** A report records the commit, language model,
  embedding model and a settings hash. Latency depends on hardware and is not
  comparable across machines.
- **A synthetic-only report measures the system against constructed meetings.**
  It is useful for catching regressions in known failure modes. It is not
  evidence about real-world accuracy.
