# Documentation Index

| Document | Purpose |
|---|---|
| [CHANGE-SUMMARY.md](CHANGE-SUMMARY.md) | Every change since the original codebase in one table: what it was, what it is now, and why. |
| [PROGRESS.md](PROGRESS.md) | What has actually been built so far, with test counts. Start here. |
| [WORKSTREAMS.md](WORKSTREAMS.md) | Plain-language guide: what every workstream is for, what you would notice, and its status. |
| [REVAMP-PLAN.md](REVAMP-PLAN.md) | End-to-end plan to bring the application to production readiness. Every change carries a problem, the change, its rationale, and the measurement that proves it worked. |
| [IMPLEMENTATION-AND-VALIDATION.md](IMPLEMENTATION-AND-VALIDATION.md) | Execution sequence and validation method. Maps each stated goal to the work that addresses it and the gate that proves it met. Read after the revamp plan. |
| [OBSERVABILITY-AND-AUDIT.md](OBSERVABILITY-AND-AUDIT.md) | Administrator observability, tamper-evident audit trail of logins and changes, end-to-end content provenance, and the automatic weekly leadership health report. Contains a live security finding on the dashboard router. |
| [observations/2026-09-12-parameter-audit.md](observations/2026-09-12-parameter-audit.md) | OBS-2026-09-12-01. Full inventory of every tunable parameter across 13 subsystems with source lines, plus findings against the three challenges raised in `Current issue and challenges.docx`. |
| [update-guide.html](update-guide.html) | End-user update instructions. |

Project-level documents at the repository root: `PROJECT_ARCHITECTURE.md`,
`BUILD.md`, `README.md`.

Reference specifications: `rom-pipeline.pdf`, `transcription-pipeline.pdf`.

## Observation records

Observation records are point-in-time findings. They are not updated as the code
changes. Corrective work is tracked in the revamp plan, not in the observation
record itself. Each record carries an identifier of the form `OBS-YYYY-MM-DD-NN`.
