# Observability, Audit and Leadership Reporting

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Companion documents | `docs/REVAMP-PLAN.md`, `docs/IMPLEMENTATION-AND-VALIDATION.md` |
| Scope | Administrator observability, complete audit trail of logins and changes, end-to-end content traceability, automatic weekly health report for leadership |
| Runtime constraint | Air-gapped. No email delivery, no remote log shipping, no external time source, no remote audit witness. |

---

# Part 1 — What already exists

This is less of a greenfield build than it appears. Several foundations are in
place and should be extended rather than replaced.

| Capability | State | Location |
|---|---|---|
| Session records | Present: session id, user, device name, IP address, created, expires, last used, revoked flag | `backend/database.py` `sessions` table |
| Login attempt records | Present: email, IP, success flag, timestamp | `backend/database.py` `login_attempts` table |
| Login rate limiting and lockout | Present: 5 attempts, 300 s window, 900 s lockout | `backend/config.py:96-98` |
| Stage 2 edit history | Present, but for Stage 2 points only | `backend/database.py` `stage2_edit_history` table |
| Pipeline analytics | Present: record, fetch, summarise | `backend/services/analytics.py` |
| Admin dashboard | Present: HTML dashboard with status, logs, endpoint listing, maintenance actions, diagnostics export, log download | `backend/routers/dashboard_router.py` |

The `sessions` table already captures device and IP per session, which is the
hard part of attributing an action to a person and a machine. The
`stage2_edit_history` table already establishes the shape of a change record.
The dashboard already exists as a surface. The work below is largely about
generalising these three things and closing what is missing around them.

## What is missing

| Gap | Consequence |
|---|---|
| No administrator role on users | There is no identity that "admin observability" can be granted to |
| No general audit log | Only authentication and Stage 2 edits are recorded; configuration changes, exports, deletions and administrative actions are not |
| No tamper evidence | Any audit record can be altered in the database with no trace |
| No retention or archival policy | `login_attempts` and logs grow without bound on a fixed disk |
| No scheduler | Nothing can generate anything on a recurring basis |
| No health model | "Health" is undefined, so it cannot be reported |
| No report generation or archive | No leadership summary exists |

---

# Part 2 — Finding: the administrative surface is unauthenticated

This was found while surveying the existing dashboard and it changes the
sequencing of everything below, so it is stated before the design.

`backend/routers/dashboard_router.py:26` declares the router as:

```python
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
```

There is no `dependencies=[Depends(get_current_user)]` on the router, and the
handlers do not take a current user. `post_maintenance(body: dict)` at line 1746
takes only a request body. The following are therefore reachable without
authentication by anything that can reach the local API port:

| Endpoint | Exposure |
|---|---|
| `GET /dashboard` | Administrative dashboard HTML |
| `GET /dashboard/api/logs` | Application logs |
| `GET /dashboard/api/status` | Runtime and system status |
| `GET /dashboard/api/endpoints` | Full route inventory |
| `POST /dashboard/api/maintenance` | Unloads models, clears GPU cache, acts on jobs |
| `GET /dashboard/api/diagnostics/export` | Diagnostics bundle |
| `GET /dashboard/api/diagnostics/logs/download` | Full log download |

Two aspects make this worse than a generic missing-auth finding.

**The logs contain participant identities.** The identification pass logs lines
of the form `[Identify] Override Applied: Time: 12.40 - 18.20 | Speaker 1 ->
Alice`. Meeting participant names, with timings, are therefore downloadable from
an unauthenticated endpoint. For a defence customer this is disclosure of who
attended which meeting and when they spoke.

**The maintenance endpoint is a state-changing action.** Unloading models or
acting on jobs without authentication is an availability issue reachable by any
local process.

This must be fixed before any observability work, because the first requirement
of an audit trail is that the surface it protects is itself protected. It is
also the natural place to introduce the administrator role that the rest of this
document depends on. It is scheduled into M0 in Part 5.

## Status: remediated 2026-09-12 (W6.1)

| Change | File |
|---|---|
| `role` column on `users`, defaulting to `user` | `backend/database.py` |
| Additive migration plus promotion of the oldest account when no admin exists | `backend/database.py` |
| First account created at first-run setup is made administrator | `backend/routers/auth.py` |
| `role` returned in the auth payload | `backend/routers/auth.py` |
| `require_admin`, accepting a bearer token or the HttpOnly refresh cookie, refusing the cookie path cross-site | `backend/routers/auth.py` |
| Router-level `require_admin` on every dashboard HTTP route | `backend/routers/dashboard_router.py` |
| WebSocket moved to an unguarded router and authorised inline, closing with code 1008 | `backend/routers/dashboard_router.py`, `backend/main.py` |

Tests added: `tests/test_admin_authorization.py` (41),
`tests/test_user_role_migration.py` (5),
`tests/test_route_access_classification.py` (2, skipped without the ML stack).
Suite moved from 207 to 253 passing with failures and errors unchanged.

Two findings came out of testing rather than review.

**The bearer scheme cannot guard a WebSocket.** Applying the router-level
dependency made `/dashboard/ws` raise `TypeError: OAuth2PasswordBearer.__call__()
missing 1 required positional argument: 'request'` at connect time. FastAPI
applies router-level dependencies to WebSocket routes, but the bearer scheme
needs a `Request`. The socket now lives on a separate router and authorises
inline via the refresh cookie, which a browser sends on a same-origin handshake
without any client-side change.

**The console authenticates by cookie, not bearer.** The server-rendered console
issues plain `fetch()` calls with no Authorization header, so a bearer-only gate
would have left it unusable. `require_admin` therefore accepts either
credential. Cookie authentication is refused when `Sec-Fetch-Site` is
`cross-site`, so it cannot be driven from another origin; the cookie is
independently `SameSite=lax`, which blocks cross-site POST.

### Outstanding

`tests/test_route_access_classification.py` asserts that every route in the
application declares an access class. It could not be executed here because it
needs the full ML stack to import the application. **Expect it to fail on its
first real run**, listing routes outside the dashboard that carry no
authentication dependency. That list is the next piece of W6.1 and is the
reason the test was written this way rather than scoped to the dashboard alone.

Not addressed in this change: `webSecurity: false` at
`frontend-electron/main.js:87`.

---

# Part 3 — Design

## W6.1 — Administrator identity and authorisation

**Problem.** The `users` table has no role column. There is no way to express
"administrator", so there is no principal to grant administrative observability
to, and no basis on which to deny it to anyone else.

**Change.** Add a `role` column to `users` with values `admin` and `user`. The
account created during first-run setup becomes the administrator. Enforce an
invariant that at least one administrator always exists, so the last one cannot
be demoted or deleted. Introduce a `require_admin` dependency and apply it to
the dashboard router, the diagnostics endpoints, the maintenance endpoint, user
and session management, retention controls and the report archive. Add a test
that enumerates every route and asserts each one is either public by explicit
declaration, user-authenticated, or admin-authenticated, failing on anything
unclassified.

**Rationale.** The role is a prerequisite rather than a feature: every item
below grants something to an administrator, and none of them can be expressed
until that principal exists. The route enumeration test is the part with lasting
value, because the current gap arose from a router being declared without a
dependency, which is invisible in review and will recur. A test that forces
every new route to declare its access class makes the omission impossible rather
than unlikely.

The at-least-one-admin invariant matters specifically for an air-gapped
deployment. On a connected system, an installation locked out of its own
administration can be recovered by a vendor. Here it cannot.

## W6.2 — Append-only audit log with tamper evidence

**Problem.** Only authentication events and Stage 2 edits are recorded.
Configuration changes, exports, deletions, user management and administrative
actions leave no trace. Nothing that is recorded is protected from alteration.

**Change.** Add an `audit_log` table, written append-only:

| Column | Purpose |
|---|---|
| `seq` | Monotonic sequence number, gap-detectable |
| `occurred_at` | Event time, with the clock source recorded |
| `actor_user_id`, `actor_session_id` | Who, and in which session |
| `actor_ip`, `actor_device` | From where, joined from `sessions` |
| `category` | Authentication, authorisation, data, configuration, administration, export, pipeline |
| `action` | Verb, for example `login.success`, `settings.update`, `rom.export`, `user.role_change` |
| `object_type`, `object_id` | What was acted on |
| `outcome` | Success, failure, denied, degraded |
| `changed_fields` | Field names only, never values |
| `before_hash`, `after_hash` | Hashes of prior and new values, for change detection without content |
| `reason` | Operator-supplied justification where required |
| `run_id` | Links to the pipeline run from W0.2 where applicable |
| `prev_hash`, `row_hash` | Hash chain |

Each row's `row_hash` is computed over its canonical serialisation together with
the previous row's hash. Verification walks the chain and reports the first
break. Add a `GET /admin/audit/verify` endpoint and run verification on startup
and before each weekly report.

Record events across all seven categories, including successful and failed
logins, logouts, session revocations, lockouts, role changes, every settings and
prompt and threshold change, every recording, ROM, MoM, voice profile and
collection mutation, every export and download, every maintenance action, every
retention purge, and every pipeline run outcome.

**Rationale.**

*Why hash chaining rather than a plain table.* For a defence customer, an audit
log that the local administrator can silently edit provides little assurance,
and the administrator is precisely the principal the log exists to hold
accountable. The usual answer is to ship logs to a separate system the
administrator does not control, and the air gap forecloses that. A hash chain is
the available alternative: it cannot prevent alteration, but it makes alteration
detectable, and detectability is most of the value. It costs one hash per write.

*Why field names and hashes rather than values.* Meeting content is the most
sensitive data in this system. An audit log storing before-and-after values
would become a second, less protected copy of it, and would grow at the rate of
the content itself. Storing the field names plus value hashes answers the
questions an audit actually asks, which are who changed what and when, and
whether a given record still holds the value it had, without duplicating the
corpus. Where a value genuinely must be recoverable, the entity's own version
history under W6.3 holds it, protected by the same access controls as the
content.

*Why the sequence number as well as the hash chain.* The chain detects
modification of a row. A gap-detectable sequence detects deletion of one. Both
failure modes are relevant and they need different defences.

*Why the reason field.* Some actions, such as purging audit data or changing a
role, should require a stated justification. A free-text reason is weak
evidence in isolation but is strong in combination with the actor and timestamp,
and it changes behaviour at the moment of the action.

**Implementation placement, and an honest trade-off.** Audit writes belong in
the repository layer of W3.3, in the same transaction as the change, so that a
mutation without an audit record is structurally impossible. W3.3 lands in
milestone M5, which is late. Waiting would leave the system unaudited through
most of the programme. The plan is therefore to implement audit writes at the
service and route level now, enforce completeness with the test described in
Part 4, and relocate the writes into the repository layer when W3.3 lands. Until
then, completeness rests on a test rather than on structure. This is a real
weakness for that interval and is recorded here rather than glossed.

## W6.3 — End-to-end content traceability

**Problem.** `stage2_edit_history` records edits to Stage 2 points only. For any
sentence in a final Record of Meeting, there is no way to answer where it came
from, whether a person or a model wrote it, or what evidence supports it.

**Change.** Two mechanisms.

*Version history for every editable entity.* Generalise the
`stage2_edit_history` pattern to recordings, transcripts, all ROM stages, the
final ROM, Minutes of Meeting, agendas, action items and voice profiles.
Each version records the actor, the timestamp, whether the change was
model-generated or human, and the prior content.

*A queryable provenance chain.* Make the existing lineage traversable end to
end: transcript segment with timestamps and speaker, to Stage 1 point with its
window index, to Stage 2 point with the retrieved context that enriched it and
the `original_point_ids` merged into it, to the Stage 3 agenda assignment with
its confidence, to the final ROM point, to the short or medium version via the
`source_point_ids` introduced in W1.1, to the Minutes of Meeting action item.
Expose it as a provenance view for any selected point.

**Rationale.** This is the traceability that matters for a meeting record, and
it is distinct from the audit log. The audit log answers who touched the system.
Provenance answers where a statement came from, which is the question a reader
disputing a record actually asks. The two need each other: provenance without
audit cannot tell you who made an edit, and audit without provenance cannot tell
you what the edit was about.

Most of the chain already exists in the data. Stage 2 already records
`original_point_ids`, Stage 3 records agenda confidence, W1.1 adds
`source_point_ids` to the condensation step, and W0.2 adds the run identifier.
The missing piece is that these links are not traversable as a unit. This
workstream is therefore mostly connection rather than construction, which is why
it is scheduled alongside W1.1 rather than after it.

Distinguishing model-generated from human-edited content is the single most
valuable field here. A reviewer's confidence in a line depends heavily on which
it is, and today the distinction is unrecoverable once a document is produced.

## W6.4 — Retention, archival and purge

**Problem.** `login_attempts` accumulates one row per attempt with no purge.
Logs are written without rotation limits. Audit records and version history will
grow faster than either. The disk is fixed and the machine cannot offload.

**Change.** A per-category retention policy, administrator-configurable within
bounds, with defaults set conservatively. Before purging, export the affected
records to a compressed, checksummed archive written to a configured directory.
Record the purge itself in the audit log, including the archive checksum and the
range purged. Surface retention state, current sizes and projected exhaustion
dates in the admin console. Enforce a floor below which audit records cannot be
purged regardless of configuration.

**Rationale.** Unbounded growth on a machine that cannot be remotely managed
eventually fills the disk, and disk exhaustion on this system stops transcription
and corrupts nothing gracefully. Archiving before purge is what makes retention
acceptable to an auditor, since a retention policy that destroys evidence is
different in kind from one that relocates it. Auditing the purge closes the
obvious circumvention, which is to delete the record of having deleted records.
The floor exists because a configurable retention period with no lower bound is
equivalent to no audit log, at the discretion of the principal being audited.

Projected exhaustion dates rather than raw sizes are deliberate: an
administrator reading "18 GB used" has to do arithmetic to know whether to act,
and will not.

## W6.5 — Log discipline and content policy

**Problem.** Logging is configured by `logging.basicConfig` at
`backend/main.py:95`. Log lines carry participant names, as in `[Identify]
Override Applied: Time: 12.40 - 18.20 | Speaker 1 -> Alice`. These files are
downloadable through the currently unauthenticated diagnostics endpoint.

**Change.** Structured JSON logging carrying `run_id`, `stage`, `recording_id`,
`user_id` and `request_id`, building on W0.2. Size-capped rotation with a total
budget. A content policy: at default levels, identities are referenced by
identifier rather than by name, with names emitted only at debug level, which is
off by default and whose activation is itself audited. Redact names from the
diagnostics bundle unless an administrator explicitly opts in and states a
reason.

**Rationale.** Logs on this system are a data export surface, not an internal
convenience, because the product ships to the customer's machine and the
diagnostics bundle is designed to be sent to the vendor for support. A support
bundle that carries meeting participant names and speaking times out of a secure
facility is a disclosure, and the person assembling it will not notice. Making
the default safe and the unsafe mode explicit and audited puts the decision
where it belongs.

Keeping names available at debug level is deliberate rather than a compromise.
The speaker identification work in M1 depends on those exact log lines for
diagnosis, and removing them entirely would make the system harder to fix.

## W6.6 — Health model

**Problem.** "Health of the application" has no definition here, and an
undefined health report becomes a wall of numbers that leadership stops reading.

**Change.** Define health as six dimensions, each with signals already available
or introduced elsewhere in this programme.

| Dimension | Signals | Source |
|---|---|---|
| Reliability | Pipeline success rate, failures by stage, retries, degraded outcomes | W0.2, W4.3 |
| Performance | Median and 95th percentile stage duration, end-to-end duration, queue wait, model load counts | W0.2, W2.1, W2.2 |
| Quality | Metrics from the evaluation harness when it has run in the period | W0.1 |
| Capacity | Disk headroom, database size, projected exhaustion, peak video memory against budget | W6.4, W0.2 |
| Security | Failed logins, lockouts, denied authorisations, sessions revoked, admin actions, debug-logging activations | W6.2 |
| Integrity | Audit chain verification, artefact manifest verification, outbound connection attempts | W6.2, W4.7, W4.6 |

Each dimension yields a status of normal, attention or action required, derived
from thresholds that are configuration rather than code.

**Rationale.** Defining health before reporting it is what separates a report
that changes behaviour from one that is filed unread. The six dimensions are
chosen so that each maps to a different response: reliability and performance go
to engineering, capacity goes to the administrator, security and integrity go to
the security owner, and quality goes to the product decision about whether the
output is good enough. A single composite score would be easier to read and
would destroy exactly that routing.

Including integrity as a first-class dimension is specific to this deployment.
On an air-gapped defence installation, "no outbound connection was attempted and
the audit chain is intact" is a statement leadership needs weekly, and it is
cheap to produce once W6.2 and W4.6 exist.

## W6.7 — Automatic weekly leadership report

**Problem.** No scheduler exists, no report exists, and the two obvious
approaches both fail on this deployment. A cron-style schedule assumes a host
that is always running; this is a desktop application that may be switched off
for days. Email delivery assumes a network; there is none.

**Change.**

*Generation with catch-up semantics.* On startup and on a timer, compute which
weekly periods have no report and generate them. A week in which the application
ran for only part of the time is generated and marked partial, carrying the
uptime fraction. No week is ever silently skipped.

*Content, one page.* Headline status across the six dimensions of W6.6. Usage:
meetings processed, hours of audio, active users, documents produced. Reliability
and performance trend against the previous week and a rolling baseline. Quality
trend where the evaluation harness ran. Security and integrity summary. Capacity
with projected exhaustion. The three items most needing attention, each with the
evidence behind it. What changed: version, settings changed by whom, and
administrative actions taken.

*Comparison discipline.* Report direction and magnitude against the previous
week and a rolling four-week baseline. Where volume in a period is too low for a
rate to be meaningful, print "insufficient data" rather than a percentage.

*Delivery without a network.* Write HTML and PDF into a reports directory.
Surface an administrator inbox in the console with unread state. Provide export
to removable media as a signed bundle. If the customer operates a mail relay on
their internal network, offer it as an optional configured sink, disabled by
default.

*Reproducibility.* The report is deterministic: the same period over the same
data produces an identical document. Each report records the application
version, the settings hash and the audit chain verification result at generation
time. Report generation is itself audited.

**Rationale.**

*Catch-up rather than cron* is the central design decision and it follows
directly from the deployment. A scheduled job that fires only when the machine
happens to be running produces a report series with unexplained gaps, and a gap
in a leadership series is read as either a failure or a concealment. Generating
retrospectively and labelling partial weeks with their uptime fraction makes the
gap informative instead: a week at twenty percent uptime is itself a fact
leadership should see.

*One page, with trend and exception rather than raw metrics.* Leadership reports
that grow are reports that stop being read. The three-items-needing-attention
section is the part that drives action, and it is placed to be readable without
the rest.

*The insufficient-data rule* prevents the most common failure of automated
reporting, which is a quiet week producing an alarming percentage swing from two
events to one. A report that cries wolf on low volume trains its readers to
ignore it, and that training is not reversible.

*Determinism and the recorded settings hash* matter because these reports become
the record. Someone will ask in six months why a figure was what it was, and a
report that cannot be regenerated cannot answer.

*Signed export bundles* exist because the realistic path for a report leaving an
air-gapped facility is a person carrying it, and a document that can be edited
in transit without detection is not a record.

## W6.8 — Administrator console

**Problem.** The existing dashboard covers runtime status, logs, endpoints,
maintenance and diagnostics. It has no audit view, no user or session
management, no retention controls, no report archive and no integrity view.

**Change.** Extend the existing dashboard rather than build a second surface.
Add audit log search filtered by actor, category, object, outcome and period,
with export. User and session management with role assignment and session
revocation. Retention configuration with current sizes and projected exhaustion.
The report archive with an unread inbox. An integrity panel showing audit chain
verification, artefact manifest verification and outbound connection attempts.
Live job queue state once W2.2 exists. Apply `require_admin` throughout, and fix
the Google Fonts link at `backend/routers/dashboard_router.py:35` as part of
W4.6.

**Rationale.** The dashboard already solves the hard parts, which are being
served by the application, rendering server-side and being reachable in the
installed product. Building a parallel administrative interface would duplicate
that and leave two surfaces to secure, which is how the current unauthenticated
one came to be overlooked. Session revocation is called out specifically because
`sessions` already carries an `is_revoked` flag with, as far as the survey
found, no administrative path to set it.

---

# Part 4 — Validation

Each of these is a gate, not a review item.

**Audit completeness.** An automated test enumerates every mutating route,
exercises each one, and asserts a corresponding audit row. Any mutating route
without an audit record fails the build. This test is the entire guarantee
during the interval before W3.3 moves audit writes into the repository layer, and
it remains useful afterwards as a check that the repository path is actually
taken.

**Tamper evidence.** Modify an audit row directly in the database. Verification
must detect it and identify the first broken link. Delete a row. Verification
must detect the sequence gap. Both cases are tested.

**Traceability.** Select an arbitrary sentence from a final Record of Meeting in
the golden set and resolve its full chain back to transcript segments with
timestamps and speakers, reporting for each hop whether the content was
model-generated or human-edited.

**Report determinism.** Generate the same week twice and compare. The documents
must be identical.

**Catch-up.** Simulate the application being off for three weeks. On launch,
three reports are generated, each marked with its correct uptime fraction, none
skipped.

**Retention.** A purge run produces an archive whose checksum verifies, writes
its own audit record, and respects the floor below which audit records cannot be
purged. The archive restores and its contents verify.

**Authorisation.** Every administrative route denies an unauthenticated caller
and denies an authenticated non-administrator. The route enumeration test
reports no unclassified routes.

**Content policy.** Scan generated logs and a diagnostics bundle at default
levels for the participant names present in the golden set. Expect zero
occurrences. This test would currently fail, which is the point of writing it.

**Performance.** Audit writes occur on the path of every mutation, including
inside pipeline stages. Measure end-to-end pipeline duration with auditing on
and off against the M0 baseline. A material regression means the write path
needs batching, and it is better to find that with a measurement than with a
customer report.

---

# Part 5 — Sequencing

This work does not form a single block at the end. It splits across the existing
milestones, for two reasons that are worth stating plainly.

**The unauthenticated administrative surface is live now.** W6.1 cannot wait.

**Telemetry must be collected long before the report is built.** A weekly report
first generated in the final milestone, using data first collected in the final
milestone, has no history to trend against and is worthless for a quarter. The
collection side must start at M0 even though the report itself ships much later.
This is the single most important sequencing point in this document.

| Milestone | Items | Rationale for placement |
|---|---|---|
| M0 | W6.1 admin role and route lockdown; W6.2 audit schema, hash chain and write path; W6.5 structured logging and content policy; begin collecting all W6.6 health signals | Closes the live exposure. Starts the historical record on day one so later reports have something to trend. |
| M1 to M3 | W6.3 provenance chain, alongside W1.1 which introduces `source_point_ids`; extend version history as each entity is touched | The lineage fields are being added anyway; connecting them now is far cheaper than retrofitting. |
| M4 | Extend health signals with the job queue and model manager metrics from W2.1 and W2.2 | Those signals do not exist until those workstreams land. |
| M5 | Relocate audit writes into the repository layer of W3.3 | Converts audit completeness from a test guarantee into a structural one. |
| M6 | W6.4 retention and archival; W6.7 weekly report generation and delivery; W6.8 console extensions | The report needs several weeks of accumulated data to be meaningful, and the console needs the job queue and integrity signals to be complete. |

An implication worth accepting deliberately: the first genuinely useful weekly
report arrives roughly a month after M6 begins, because that is when a rolling
four-week baseline first exists. Reports generated before then are correct but
carry "insufficient data" against most comparisons. Setting that expectation now
avoids the report being judged a failure on its first issue.

---

# Part 6 — Decisions needed

**Who is the administrator in the customer's deployment.** This is a per-machine
installation with local accounts. Whether the administrator is an IT function, a
meeting secretary or a project lead determines what the console should surface
by default and what the weekly report should lead with.

**Who receives the weekly report, and by what route.** With no network, the
realistic options are an in-application inbox, a file on a shared drive if the
facility has one, or a printed or exported copy carried out. The answer
determines whether the PDF path, the signed export bundle, or both are required.

**Retention periods, and whether the customer has a mandated standard.** Defence
environments frequently specify audit retention. If a standard applies, it sets
the floor rather than the default, and it may forbid purging entirely, which
changes the capacity planning in W6.4.

**Whether audit records may leave the machine in a diagnostics bundle.** The
bundle is the vendor support path. If audit records may not leave, support
diagnosis becomes harder and the trade-off should be made explicitly rather than
discovered during an incident.

**Clock trust.** An air-gapped machine has no time synchronisation source, so
timestamps depend on the local clock and a user with sufficient privilege can
change it. The hash chain preserves ordering regardless, since sequence is
independent of clock, but absolute times may drift or be manipulated. If
defensible absolute timestamps are required, that needs a trusted local time
source and is a procurement question rather than a software one.
