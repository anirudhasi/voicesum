# Observation Record OBS-2026-09-12-01 — Parameter Audit

| Field | Value |
|---|---|
| Type | Observation / findings record |
| Date recorded | 2026-09-12 |
| Status | Open — findings not yet remediated |
| Triggered by | `Current issue and challenges.docx` (speaker ID edge cases, short/medium point loss, action point quality) |
| Reference specs | `rom-pipeline.pdf`, `transcription-pipeline.pdf` |
| Remediation plan | `docs/REVAMP-PLAN.md` |
| Method | Static source inspection. No runtime profiling or accuracy measurement was performed. Every value cited was read from the file and line given. |

This document records the state of the system as observed. It is a point-in-time
record and is not updated as the code changes. Corrective work is tracked in the
revamp plan linked above.

---

Scope: full backend inspection (48,732 lines across services, routers, tasks),
cross-checked against `transcription-pipeline.pdf`, `rom-pipeline.pdf`, and
`Current issue and challenges.docx`.

Stack: FastAPI + SQLAlchemy/aiosqlite, faster-whisper (CTranslate2), WhisperX,
pyannote community-1, SpeechBrain ECAPA-TDNN, Qwen3-Embedding-0.6B, ChromaDB +
FAISS (legacy), local Qwen3-4B / Ollama, RapidOCR. 44 backend test modules,
190 frontend TS/TSX files, Electron shell.

Verification note: every value below was read from source. Where the PDF spec and
the code disagree, both are shown. No numbers are estimated.

---

## 1. Audio ingestion and preprocessing

| Parameter | Value | Source |
|---|---|---|
| Target sample rate | 16000 Hz mono | `services/embedding.py:53`, ffmpeg `-ar 16000 -ac 1 -c:a pcm_s16le` |
| `min_audio_duration_seconds` | 2.0 (range 0.1–30.0) | `models/settings.py:120` |
| `min_audio_rms_threshold` | 0.003 (range 0.0001–0.1) | `models/settings.py:121` |
| `AUDIO_PREPROCESS_BEFORE_ALIGNMENT` | True | `config.py:145` |
| `ENABLE_AUDIO_NORMALIZATION` | True | `config.py:188` |
| `NORM_TARGET_DBFS` | -3.0 (range -30.0–0.0) | `config.py:189` |
| `NORM_COMPRESSION_RATIO` | 2.0 (range 1.0–10.0) | `config.py:190` |
| `ENABLE_VAD` / transcription / alignment | True / True / True | `config.py:184-186` |
| `ENABLE_ADAPTIVE_VAD` | True | `config.py:192` |
| `VAD_SPEECH_THRESHOLD` | 0.15 | `config.py:193` |
| `VAD_SILENCE_THRESHOLD` | 0.10 | `config.py:194` |
| `VAD_MIN_SPEECH_MS` | 250 | `config.py:195` |
| `VAD_MIN_SILENCE_MS` | 400 | `config.py:196` |
| `SPEECH_PAD_MS` | 400 | `config.py:199` |
| `MAX_MERGE_SILENCE_MS` | 500 | `config.py:202` |
| `ENABLE_LOW_VOLUME_RECOVERY` | True | `config.py:204` |
| `RECOVERY_ENERGY_THRESHOLD` | -45.0 dB | `config.py:205` |
| `RECOVERY_MIN_DURATION_MS` | 300 | `config.py:206` |
| `MISSING_SEGMENT_MIN_DURATION_SEC` | 2.0 | `config.py:207` |

Spec note: the PDF documents loudness normalisation to -23 LUFS (EBU R128). The
code normalises to `NORM_TARGET_DBFS = -3.0` dBFS with a 2.0 compression ratio.
These are different quantities and different targets. The doc is out of date here.

## 2. Transcription

| Parameter | Value | Source |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `large-v3` | `config.py:148` |
| `WHISPER_DEVICE` | `auto` (cuda/cpu/auto) | `config.py:149` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `config.py:150` |
| `WHISPER_BATCH_SIZE` | 8 (range 1–32) | `config.py:151` |
| `WHISPER_PARALLEL_PROCESSING` | 1 (range 1–8) | `config.py:160` |
| `WHISPER_PARALLEL_CHUNK_MINUTES` | 10 (range 1–60) | `config.py:164` |
| `local_files_only` | True (offline) | spec section 3.2 |
| `WORD_CONF_LOW` | 0.7 | `config.py:171` |
| `WORD_CONF_MID` | 0.85 | `config.py:172` |
| `MIN_AVG_SEGMENT_CONFIDENCE` | 0.40 | `config.py:177` |
| Alignment | WhisperX wav2vec2 forced phoneme | spec section 4 |

VRAM scales linearly with `WHISPER_PARALLEL_PROCESSING`; each worker loads its
own model copy and calls `torch.cuda.empty_cache()` on teardown.

## 3. Diarization

| Parameter | Value | Source |
|---|---|---|
| Model | `pyannote/speaker-diarization-community-1` | spec section 5.1 |
| Overlap detection threshold | > 0.1 s (100 ms) | `services/diarization.py:208,273` |
| `MIN_SEGMENT_DURATION` | 1.5 s (range 0.1–10.0) | `config.py:137` |
| Energy fallback frame / hop | 2048 / 512 | `services/diarization.py:373-374` |
| Energy fallback speech percentile | 30th | spec section 5.4 |
| Energy fallback `SILENCE_GAP` | 0.8 s | `services/diarization.py:386` |
| Dual tracks | `exclusive_speaker_diarization` (primary), `speaker_diarization` (overlap only) | spec section 5.2 |

## 4. Speaker embedding (ECAPA-TDNN)

| Parameter | Value | Source |
|---|---|---|
| Model | `speechbrain/spkrec-ecapa-voxceleb` | spec section 6.1 |
| `EMBEDDING_DIM` | 192 float32, L2-normalised | `services/embedding.py:52` |
| webrtcvad aggressiveness | 2 | `services/embedding.py:331` |
| `_ENERGY_VAD_FRAME_MS` | 30 ms | `services/embedding.py:322` |
| `_ENERGY_SPEECH_QUANTILE` | 0.25 | `services/embedding.py:323` |
| `_MIN_SPEECH_REGION_SEC` | 0.25 s | `services/embedding.py:324` |
| `_MIN_SILENCE_MERGE_SEC` | 0.40 s | `services/embedding.py:325` |
| `min_speech_sec` | 2.5 s (below this returns None) | `services/embedding.py:491` |
| `max_speech_sec` | 8.0 s (single forward pass) | `services/embedding.py:492` |
| `win_sec` / `hop_sec` | 4.0 / 3.0 (1.0 s overlap) | `services/embedding.py:493-494` |
| Minimum segment audio for ID | 0.5 s | `services/identification.py:275` |

## 5. Speaker identification and matching

| Parameter | Value | Source |
|---|---|---|
| `SPEAKER_SIMILARITY_THRESHOLD` | 0.75 (range 0.5–0.99) | `config.py:134` |
| `SPEAKER_SIMILARITY_THRESHOLD_ECAPA_TDNN` | 0.72 | `config.py:135` |
| `_LEGACY_THRESHOLD` | 0.75, auto-substituted to 0.72 | `services/identification.py:61,192` |
| Grouping minimum segment | >= 0.5 s, non-overlap only | spec section 8.1 |
| Matching | greedy global bipartite, descending similarity, mutual lock | spec section 8.3 |
| Unmatched label | `Speaker N`, `scoring_method = "no_match"` | `services/identification.py:418` |
| `speaker_refinement_margin` | 0.30 | `config.py:139` |
| In-function fallback margin | 0.10 (used only if config import fails) | `services/identification.py:516` |
| Refinement hard-accept | `best_sim >= 0.82` | `services/identification.py:681` |
| Refinement margin gate | `best_sim > 0.20 AND (best_sim - orig_sim) > margin` | `services/identification.py:683-685` |
| Scoring methods | `centroid`, `single_sample`, `sample_max_fallback`, `no_match` | spec section 7.3 |

Voice profile store: SQLite `voice_profiles` (id, user_id, label, embeddings JSON
192-d list-of-lists, sample_count, is_self, timestamps). Enrolment paths:
`/voice/sample` + `/voice/finalize-setup`, `/voice/add-profile`,
`/voice/bulk-folder-import`, `/voice/extract-samples` + `/voice/train-from-transcript`.
Transcript-mined samples require >= 2.0 s, no overlap, ranked by Whisper `avg_logprob`,
3–5 slices per profile.

## 6. ROM Stage 1 — windowed extraction

| Parameter | Value | Source |
|---|---|---|
| `window_minutes` / `rom_transcript_window` | 2.0 min = 120 s (range 0.5–10.0) | `rom_service.py:391`, `models/settings.py:68` |
| `ROM_PARALLEL_WINDOW_PROCESSING` | 2 (range 1–5) | `config.py:168` |
| `separate_action_extraction` | False (embedded default) | `rom_service.py:397` |
| Split-call action window | 2x window = 240 s | spec section 2.4 |
| `max_tokens_rom_discussion` | 4096 | `models/settings.py:80` |
| `max_tokens_rom_discussion_no_actions` | 4096 | `models/settings.py:81` |
| `max_tokens_rom_action_extraction` | 2048 | `models/settings.py:82` |
| `max_tokens_stage1_json_repair` | 4548 | `models/settings.py:84` |

Stage 1 output fields: `id, window_index, timeline_start, timeline_end,
discussion_point, speakers, technical_terms, dates, numbers, references,
action_items[task/owner/deadline], action_owner, raw_transcript_text,
video_transcript_context`. There is no importance or priority field.

## 7. ROM Stage 2 — RAG enhancement, validation, dedup

| Parameter | Value | Source |
|---|---|---|
| Embedding model | Qwen3-Embedding-0.6B, 1024-d | `config.py:55` |
| `discussion_window_size` | 5 windows (~10 min) | `rom_service.py:1047` |
| `process_all_together` | False | `rom_service.py:1049` |
| `rom_windows_per_batch` | 5 (range 1–20) | `models/settings.py:71` |
| `rom_meeting_top_k` | 5 (range 1–50) | `models/settings.py:69` |
| `rom_global_top_k` | 3 (range 1–50) | `models/settings.py:70` |
| RRF constant k | 60 | `rom_service.py:1089,1430,2808` |
| Project name match boost | +0.10 | `rom_service.py:1169` |
| Technical entity overlap boost | +0.05 each, capped +0.15 | `rom_service.py:1175` |
| Section / heading boost | +0.08 | `rom_service.py:1185` |
| Document title boost | +0.10 | `rom_service.py:1189` |
| Date match boost | +0.05 | `rom_service.py:1195` |
| Diversity dedup cutoff | cosine >= 0.92 | `rom_service.py:1102,1456,2824,3209` |
| `rom_min_similarity_threshold` | 0.80 | `models/settings.py:76`, `rom_service.py:1048,1375` |
| Semantic dedup cluster threshold | cosine >= 0.95 | `rom_service.py:2458` |
| LLM dedup classes | duplicate / complementary / different | spec section 3.5 |
| `max_tokens_rom_enhance_window` | 4096 | `models/settings.py:87` |
| `max_tokens_rom_deduplicate` | 2048 | `models/settings.py:88` |
| Cross-meeting index | ChromaDB `stage2_points_<user_id>` | spec section 3.6 |

Note: `ROM_DEDUPLICATION_PROMPT` text states ">= 0.90" while the clustering code
uses 0.95. Cosmetic mismatch in the prompt wording only.

## 8. ROM Stage 3 — agenda mapping

| Parameter | Value | Source |
|---|---|---|
| Agenda assignment `batch_size` | 20 | spec section 4.2 |
| MoM continuity expander char limit | 20,000 | spec section 6 table |
| Confidence labels | high / medium / probable | spec section 4.2 |
| Invalid agenda ID fallback | default agenda A1, `is_probable=True` | spec section 4.2 |
| `max_tokens_rom_agenda` | 2048 | `models/settings.py:89` |
| `max_tokens_rom_agenda_assign_batch` | 4096 | `models/settings.py:91` |
| `max_tokens_rom_mom_expansion` | 3000 | `models/settings.py:90` |
| `max_tokens_rom_agenda_doc_points` | 1024 | `models/settings.py:92` |

Soft guidance inputs: `discussion_order` and `agenda_timeline`. Document-derived
points are tagged `speaker: "From Document"`, `is_doc_point: True`.

## 9. Final ROM and MoM

| Parameter | Value | Source |
|---|---|---|
| Introduction / Conclusion constraint | exactly one paragraph, 5–7 sentences | spec section 5.4 |
| Versions | long / medium / short | `routers/rom_router.py:2014` |
| Short reduction target | 30–50% of input, 2–5 points per agenda | `ai_provider.py:2445` |
| Medium reduction target | 50–70% of input, 4–8 points per agenda | `ai_provider.py:2476` |
| Version generation max_tokens | 4096, temperature 0.2 | `rom_service.py:4688` |
| `rom_action_generation_chunk_size` | 10 (range 1–100) | `models/settings.py:74` |
| `max_tokens_mom_extract_actions` | 4096 | `models/settings.py:83` |
| `max_tokens_mom_action_regen` | 4048 | `models/settings.py:85` |
| `MOM_CONTEXT_TOKEN_THRESHOLD` | 3000 | `config.py:111` |
| Export formats | .docx, PDF, JSON | spec section 5.5 |

## 10. LLM inference

| Parameter | Value | Source |
|---|---|---|
| `QWEN_MODEL_ID` | `Qwen/Qwen3-4B` | `config.py:101` |
| `QWEN_MAX_NEW_TOKENS` | 1024 | `config.py:102` |
| `OLLAMA_SERVER_URL` / port | `http://localhost:11434` / 11434 | `config.py:106-107` |
| `OLLAMA_MODEL_PRIORITY` | gemma, qwen, llama, deepseek, mistral | `config.py:108` |
| `ollama_num_ctx` | 32768 (range 512–131072) | `models/settings.py:27` |
| `ollama_dynamic_ctx` | True | `models/settings.py:28` |
| `ollama_think` | False | `models/settings.py:29` |
| `ollama_temperature` | 0.0 | `models/settings.py:30` |
| `ollama_top_p` | 0.9 | `models/settings.py:31` |
| `ollama_top_k` | 40 | `models/settings.py:32` |
| `ollama_repeat_penalty` | 1.15 | `models/settings.py:33` |
| `ollama_seed` | -1 | `models/settings.py:34` |
| `ollama_num_thread` / `num_gpu` | 0 (auto) / -1 (auto) | `models/settings.py:37-38` |

Per-task max_tokens: mom 1500, mom_merge 3072, agenda_compress 2000,
reference_compress 2000, agenda_from_summary 1024, executive_summary 700,
short_summary 120, detailed_summary 3000, chunk_summary 256, key_points 1028,
action_items 1028, key_decisions 1028, speaker_summary 200,
speaker_key_points 350, speaker_action_items 250, collection_chat 1500,
collection_compare 1500, collection_topic_growth 1500, vocab_extractor 512,
rom_polish 4096.

## 11. General RAG and chunking

| Parameter | Value | Source |
|---|---|---|
| `RAG_CHUNK_SIZE` | 400 words (range 10–5000) | `config.py:79` |
| `RAG_CHUNK_OVERLAP` | 50 words (range 0–1000) | `config.py:80` |
| `RAG_RETRIEVAL_K_GLOBAL` | 2 | `config.py:83` |
| `RAG_RETRIEVAL_K_MEETING` | 3 | `config.py:84` |
| `RAG_RETRIEVAL_K_TRANSCRIPT` | 10 | `config.py:85` |
| `RAG_RELATIVE_SCORE_CUTOFF` | 0.01 | `config.py:86` |
| `rag_max_collection_context` | 10 | `models/settings.py:21` |
| Stores | ChromaDB (current), FAISS (legacy migration) | `config.py:73,76` |

## 12. Video OCR

| Parameter | Value | Source |
|---|---|---|
| `FRAME_STRIDE_SEC` | 15.0 s | `video_processing_service.py:37` |
| `SCENE_THRESHOLD` | 0.40 | `video_processing_service.py:38` |
| `MIN_OCR_CHARS` | 5 | `video_processing_service.py:39` |
| `OCR_MERGE_RATIO` | 0.90 fuzzy | `video_processing_service.py:40` |
| `MIN_WORD_CONFIDENCE` | 30.0 | `video_processing_service.py:41` |
| `MIN_FRAME_WIDTH` | 1280 px | `video_processing_service.py:42`, `ocr_engine.py:57` |
| `JPEG_QUALITY` | 90 | `video_processing_service.py:43` |
| `MIN_OCR_SCORE` | 0.40 per line | `ocr_engine.py:56` |
| CLAHE | clipLimit 2.5, tile 8x8 | `ocr_engine.py:194` |
| Frame dedup spacing | >= 2.0 s | `video_processing_service.py:336` |
| Min image dims for OCR | 100 px, 10000 px area | `ocr_engine.py:378-379` |

## 13. Security and auth

| Parameter | Value | Source |
|---|---|---|
| `JWT_SECRET` | `change-me-in-production-use-long-random-string` | `config.py:89` |
| `JWT_ALGORITHM` | HS256 | `config.py:90` |
| `ENVIRONMENT` | development | `config.py:93` |
| `RATE_LIMIT_LOGIN_MAX` | 5 attempts | `config.py:96` |
| `RATE_LIMIT_LOGIN_WINDOW_SECONDS` | 300 | `config.py:97` |
| `ACCOUNT_LOCKOUT_SECONDS` | 900 | `config.py:98` |
| `OFFLINE_MODE` | True | `config.py:123` |
| `HF_TOKEN` | empty | `config.py:114` |

The JWT secret and the development environment flag are committed defaults. They
must be overridden through `.env` before any shipped build.

## 14. Offline posture

The application is required to run with no internet access. The backend is
genuinely hardened for this and the work was done deliberately.

| Control | State | Source |
|---|---|---|
| `OFFLINE_MODE` | True | `config.py:123` |
| `TRANSFORMERS_OFFLINE` | Set to 1 before any import | `main.py:7` |
| `HF_DATASETS_OFFLINE` | Set to 1 before any import | `main.py:8` |
| `HF_HUB_OFFLINE` | Set to 1 before any import | `main.py:9` |
| Re-asserted in model loader | Yes | `services/model_loader.py:301-303` |
| Whisper `local_files_only` | True | spec section 3.2 |
| SpeechBrain fetch | `FetchConfig(allow_network=False)` | `services/embedding.py:176` |
| pyannote load | Local directory, no hub call | `services/diarization.py:118` |
| Ollama endpoint | `http://localhost:11434` | `config.py:106` |
| Training model listing | Ollama localhost only | `services/training/training_model_service.py:12` |
| License validation | No network calls present | `license.py` |

Setting the three Hugging Face variables at the top of `main.py`, before the
libraries that read them are imported, is the correct placement and is easy to
get wrong. This is well done.

### Residual network dependencies

Four places still reach outward. None is required for function; all are live
defects under the offline requirement.

**O1 — Frontend web fonts.** `frontend/src/index.css:1` opens with
`@import url('https://fonts.googleapis.com/css2?...')` requesting seven font
families. A CSS `@import` is render-blocking, so with no route to the internet
the first paint waits on the connection attempt until it times out. The page
then renders in fallback fonts. This is both a startup latency cost and a
typography regression on every launch.

**O2 — Server-generated HTML fonts.** `routers/dashboard_router.py:35` emits a
`<link>` to Google Fonts in generated dashboard HTML, with the same effect.

**O3 — ChromaDB telemetry.** `services/vector_store.py:63` constructs
`chromadb.PersistentClient(path=target_path)` with no settings object. ChromaDB
enables anonymised telemetry by default, which posts usage events to a
third-party analytics endpoint. Offline these attempts fail, costing time and
log noise. In a secured environment an outbound telemetry attempt is a
compliance finding whether or not it succeeds.

**O4 — External image references.** `frontend/index.html:15,19` carry Open Graph
and Twitter image meta tags pointing at `lovable.dev`, left over from project
scaffolding. Meta tags are inert in an Electron shell, so the practical impact
is nil, but they should not ship in a customer-facing build.

### Related observation

`frontend-electron/main.js:87` sets `webSecurity: false`, which disables the
same-origin policy in the renderer. This is not an offline issue but was found
alongside and is a security defect in its own right.

### Specification drift

`transcription-pipeline.pdf` describes an optional cloud-based Groq fallback for
transcription. No `GroqProvider` class or Groq endpoint exists in the code; the
only occurrence is a docstring line at `services/ai_provider.py:14`. The
capability was removed or never implemented. The document should be corrected,
since a reader would reasonably conclude the product can transmit audio to a
third party.

---

# Findings against the three stated challenges

## Challenge 1 — speaker identification edge cases

The pipeline is sound and matches the spec. Four concrete defects explain the
residual wrong assignments.

**1. The refinement pass cannot fire when the original similarity is unknown.**
In `refine_transcript_speakers_with_ecapa`, `original_label_sim` is initialised to
1.0 (`identification.py:610`) and only replaced if the original label is found
among enrolled profiles. When a segment currently carries a generic `Speaker N`
label there is no profile to compare against, so the value stays 1.0. The margin
branch then evaluates `(best_sim - 1.0) > 0.30`, which is unsatisfiable for any
cosine score. Only the hard-accept `best_sim >= 0.82` can rescue that segment.
This is exactly the population most in need of correction.

**2. The refinement thresholds 0.82 and 0.20 are hard-coded.**
`identification.py:681-683` embeds both literals while the margin beside them is
configurable. The accept threshold 0.82 also sits well above the matching
threshold 0.72, so a segment can match a profile at Stage 8 yet be un-correctable
at Stage 10.

**3. Matching is speaker-level, correction is segment-level, and nothing
reconciles them.** Greedy bipartite matching locks one profile to one diarization
cluster for the whole meeting. If pyannote splits one person across two clusters,
only one cluster can win the profile; the other becomes `Speaker N` permanently,
and by defect 1 the refinement pass cannot recover it.

**4. Short turns are structurally excluded.** A turn needs >= 0.5 s of audio to be
embedded at all and >= 2.5 s of detected speech to produce an embedding. Below
that, `vad_extract_speaker_embedding` returns None and the label comes purely from
WhisperX `fill_nearest=True`, which propagates the neighbouring speaker. Short
interjections are therefore assigned by proximity, not acoustics.

Suggested direction, in order of expected return:
- Initialise `original_label_sim` to the actual measured similarity, or to 0.0 for
  unenrolled labels, so the margin gate becomes reachable.
- Promote 0.82 and 0.20 to settings and tie the accept threshold to the ECAPA
  threshold rather than fixing it 0.10 above.
- Add a cluster-merge step before bipartite matching: if two diarization clusters
  have centroid cosine above a threshold, merge them into one before assigning
  profiles.
- For sub-2.5 s turns, use a conversation-local centroid vote across the
  surrounding turns instead of `fill_nearest`.

## Challenge 2 — information loss in short and medium point generation

This is the most serious defect found, and it is not primarily a prompt problem.

**Metadata is reattached positionally after the LLM merges points.**
In `generate_rom_version` (`rom_service.py:4700-4720`), for each returned text at
index `j` the code takes `src = pts[min(j, len(pts) - 1)]` and copies that source
point's `speaker`, `speakers`, `action_owner`, `action_items`, `timeline_start`,
`timeline_end` and `references` onto the new point. The prompt explicitly
instructs the model to merge and restructure, so output index `j` has no
relationship to input index `j`. With 10 input points reduced to 3, output point 3
inherits input point 3's owner and action items regardless of what it actually
says. Every output beyond the input count collapses onto the last source point.

**Three metadata fields are discarded outright.** The same block sets
`technical_terms: []`, `dates: []` and `numbers: []` unconditionally. All
technical terminology, calendar dates and figures carried through Stages 1 and 2
are erased in the short and medium versions. For a defence engineering customer
this is the single loudest form of the reported loss.

**Action items are never preserved as first-class content.** They ride along only
through the positional `src` copy. The short prompt is told to focus on action
points and decisions, but nothing verifies that every input `action_items` entry
survives into some output point.

**There is no importance signal anywhere in the schema.** A search for
importance, priority, salience, rank or weight fields across all services and
routers returns only RAG retrieval scores. The Stage 1 and Stage 2 point objects
carry no measure of how significant a point is, so "keep the important ones" is
an unconstrained instruction with nothing behind it. The only quantities available
to the model are ordinal position and text length.

Suggested direction:
- Have the LLM return `source_point_ids` per condensed point, as
  `ROM_POLISH_PROMPT` and the Stage 2 dedup path already do, and derive the
  metadata from the union of those sources instead of positionally.
- Union `technical_terms`, `dates`, `numbers`, `references` and `action_items`
  over the merged sources, then assert that no Stage 2 date or number disappears.
  A post-generation diff that reinstates dropped entities gives a measurable
  guarantee against loss.
- Add an `importance` field scored during Stage 2, when the full transcript and
  RAG context are still in hand. Reasonable signals already present: presence of
  action items, presence of a decision verb, number of distinct speakers, density
  of technical terms and numbers, agenda confidence, and whether the point
  survived dedup as a merge target. Then make short and medium versions a
  deterministic selection by importance, with the LLM used only to compress the
  selected text, never to choose what to drop.
- Have the short and medium passes emit an explicit coverage report of which
  Stage 2 point IDs were dropped, so a reviewer can see the loss rather than
  discover it later.

## Challenge 3 — action point generation

**The owner fallback hierarchy contradicts the prompt it backstops.**
`MOM_EXTRACT_ACTIONS_FROM_POINTS_PROMPT` states "NEVER assign ownership merely
because someone was speaking" and instructs the model to emit `owner: null` when
unassigned. `_resolve_point_owner` (`rom_service.py:3748-3800`) then overrides
that null: step 3 assigns the entire `speakers` list of the source point, step 4
assigns the single `speaker`, and step 5 assigns the sole participant when the
meeting has one valid participant. The careful null returned by the model is
discarded and replaced with a speaker-based guess. Every unassigned action will
appear assigned, often to several people at once.

**Chunked extraction sees ten points at a time.** With
`rom_action_generation_chunk_size = 10` the model cannot see a commitment made in
one chunk and retracted or reassigned in another. A global dedup pass runs after
(`mom_router.py:1219`) but it deduplicates, it does not reconcile ownership or
supersession.

**`ROM_ENHANCE_ACTION_POINTS_PROMPT` is an empty string** (`ai_provider.py:2196`).
Any code path reaching it produces an empty prompt rather than an error.

Suggested direction:
- Cap the owner fallback at step 2. Preserve null and surface unassigned actions
  in the UI for human assignment rather than silently guessing.
- If the speaker-based guess must remain, mark it with a provenance field such as
  `owner_source: "inferred_from_speaker"` so reviewers can see which owners were
  guessed.
- After the global dedup pass, run a reconciliation over the full action list to
  detect supersession and conflicting owners for the same task.

---

# Cross-cutting observations

**Documentation drift.** The transcription PDF references an older project path
(`e:/projects/Transcriber-meeting-initial`) and the -23 LUFS normalisation that
the code no longer implements. Worth a refresh pass before these go to a customer.

**Threshold sprawl.** The same 0.92 diversity threshold is redefined as a local
default in four separate functions (`rom_service.py:1102, 1456, 2824, 3209`), and
the RRF k=60 in three (`1089, 1430, 2808`). Tuning any of them requires finding
all copies. These belong in settings alongside `rom_min_similarity_threshold`,
which is already exposed.

**Committed secrets.** `JWT_SECRET` ships with a placeholder value and
`ENVIRONMENT` defaults to development. Both need to be enforced at startup, not
merely overridable.

**Test coverage is real but has a gap where it matters most.** 44 backend test
modules cover ROM versioning, action dedup, speaker mapping and the chunk
pipeline. `test_condensed_parser.py` exists, but there is no test asserting that
short and medium generation preserves dates, numbers or action items. That is the
test that would have caught the positional metadata defect.
