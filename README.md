# TuneForge Knowledge Builder

Internal curation application that converts official motherboard manuals into
structured, human-reviewed BIOS-setting records. Extraction creates candidates,
never trusted facts: only reviewer-approved settings enter the search index.

The current release includes:

- PDF upload, content validation, SHA-256 deduplication, and local storage
- Layout-aware page text and font metadata extraction
- Deterministic BIOS setting and description parsing with parser versioning
- Draft review, editing, approval, rejection, reopening, and audit history
- SQLite FTS5 search over approved settings only
- Versioned REST API under `/api/v1` with OpenAPI documentation at `/docs`
- Manual source provenance, completeness reporting, editing, and audit history
- Extraction-acceptance reports with review completion and quality estimates
- Missed-setting registration, dismissal history, and acceptance certification
- Reviewer-gated metadata and official support-page discovery before extraction
- Deterministic document-type preflight with reviewer-gated extraction eligibility
- Persistent multi-file ingestion batches with failure-isolated progress tracking
- Audited verification/correction of PDF page references for approved settings
- Four-vendor corpus auditing and read-only parser evaluation tools
- Parser 1.3 conservative document-scaffolding filters plus audited ASUS,
  ASRock, Gigabyte, and MSI setting-label recognition
- Controlled expansion with exact-download hash verification and certified
  MSI AMD AM5 plus GIGABYTE Intel 800 BIOS-guide acceptance
- Vendor-normalization 1.0 with stable database IDs, manufacturer/sub-brand
  aliases, raw-input audit history, canonical API/UI output, and search filters
- Certified ASUS AMD 800 expansion with pinned source identity, rendered-page
  review, and a fully audited 1,032-candidate acceptance manifest
- Hardware-applicability normalization 1.0 for stable platform, chipset-family,
  chipset, chipset-set, and motherboard-model identities
- Persisted and audited hardware applicability with deterministic migration
  backfill, API exposure, reviewer visibility, and compatibility-aware search
- Certified GIGABYTE legacy-platform expansion for AMD B550/A520 and Intel
  600 Series, with official provenance, rendered-page review, and pinned
  acceptance manifests
- Certified ASUS legacy-platform expansion for Intel 600 and 700 Series, with
  exact official-download verification, modern ASUS support-page validation,
  and all 182 source pages visually audited
- Reviewer-controlled canonical setting identities with exact-alias
  suggestions, audited mapping/remapping/unmapping, API and reviewer catalog
  exposure, and canonical filtering in approved-setting search
- Persistent canonical suggestion review queue, stable categories, audited risk
  classifications, curated synonym expansion, and category/risk search filters
- Focused PBO canonical foundation for the Ryzen 7 9800X3D pilot, with stable
  CPU identity, typed tuning vocabulary, and no recommendation generation
- Trusted Evidence Intake with official-domain validation, immutable PDF
  artifacts, reviewer-gated extraction, page-scoped passages, SHA-256 identity,
  and append-only source decisions; guidance claims remain out of scope
- Guidance Acceptance with evidence-grounded factual claims, canonical-setting
  links, reviewer editing and decisions, source-scope enforcement, and explicit
  exclusion of recommendation and CPU-model-specific claims in the pilot
- Citation-first guidance lookup by canonical ID, canonical name, or exact
  reviewed alias, with approved facts grouped by type, exact source/page
  citations, risk context, coverage reporting, and fail-closed unknown queries
- Vendor-equivalence discovery in Search and Guidance, derived only from
  approved canonical mappings and accompanied by manual/page citations
- Request-scoped AM5 system context for reviewed motherboard and CPU identities,
  with explicit applicability explanations and no inferred BIOS-version support
- Read-only AM5 coverage validation with per-vendor, per-model, per-manual, and
  observed-canonical metrics plus explicit expansion gates and reviewer actions
- Append-only refusal-demand logging, deterministic nearest reviewed identities,
  and reviewer-registered official referral pointers that remain outside evidence
- Provider-neutral LLM interface contract with exact-claim presentation packets,
  deterministic output validation, and no connected model
- Citation-safe presentation envelopes for supported and refused lookups, excluding raw
  passages and reviewer audit details from the future model boundary
- AM5 Guided Answers over twelve fixed reviewed questions, with claim-type-focused
  citations and visible recommendation, exact-system, and BIOS-version refusals
- Provider-neutral optional-model protocol, explicit adapter registry, strict
  untrusted-JSON parsing, exact-output validation, and deterministic fallback
- Focused public BIOS platform with the Settings Library as its homepage,
  citation-safe answers, verified cross-vendor comparison, official manual
  submission, responsive layout, and clearly separated administrator tools
- Public BIOS Settings Library with canonical and source-setting views, browse-all
  pagination, full-text search, hardware/category/risk filters, vendor aliases,
  applicability, official citations, accepted evidence, and public-safe APIs
- Read-only admin operations dashboard with knowledge metrics, prioritized
  reviewer queues, AM5 readiness, pipeline health, and recent audit activity
- Persistent administrator/reviewer accounts, first-run owner setup, opaque
  server-side sessions, strict route protection, and named audit attribution
- Production configuration validation, trusted-host enforcement, readiness
  diagnostics, container packaging, and verified database-plus-source backups
- Deterministic question routing for the twelve reviewed AM5 questions and
  transparent setting-name wrappers, with no fuzzy matching or generated text
- A read-only v1 launch gate that separates product completion from deployment
  safety, appears in the admin dashboard, and has a protected JSON endpoint
- Evidence-reviewed cross-vendor expansion with 91 canonical identities and
  421 approved mappings, including 136 decisions pinned to official manual
  pages in the v1.7 Wave 2 acceptance batch

This tree is the **TuneForge 1.7 focused BIOS settings platform release**. The
public product is limited to BIOS Settings, Compare Vendors, Ask TuneForge, and
Upload Manual. Guided question acceptance, exact-system validation, ingestion,
provenance, and review operations remain protected administrator tools. See
`docs/focused-bios-platform-v1.2.md` for the product boundary,
`docs/public-bios-settings-library.md` for the Library contract, and
`docs/v1-release.md` for the original focused-v1 scope evidence.
The scope reconciliation with the original desktop PRD is documented in
`docs/v1-completion-audit.md`.

## Requirements

- Python 3.12 or newer
- No external services for local operation

## Setup

Run all commands from `knowledge-builder/`:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m alembic upgrade head
```

On macOS or Linux, activate with `source .venv/bin/activate`.

## Run

```text
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The public BIOS Settings Library opens immediately. The
first visit to `/admin` redirects to a one-time administrator setup; subsequent
curator access requires sign-in. Internet deployment still requires HTTPS,
secure-cookie mode, reverse-proxy rate limiting, and an operational backup plan.

## Data and backups

The default persistent data directory is `knowledge-builder/data/`:

```text
data/
  knowledge_builder.sqlite3
  uploads/
  evidence_sources/
```

Set `KNOWLEDGE_BUILDER_DATA_DIR` to keep curation data elsewhere. Back up the
entire directory together because database rows reference files in `uploads/`
and `evidence_sources/`.
Runtime data is ignored by Git.

The TuneForge process must have read/write access to the database file and
create/delete access to its parent directory. SQLite creates rollback-journal
or WAL sidecar files beside the database for transactional writes. Alembic
checks this capability before starting a migration and fails with the resolved
storage path if the host, container volume, or security sandbox blocks it.

Set `TUNEFORGE_BACKUPS_DIR` to the directory where operational archives are
kept. Production deployments should place it outside the live application data
volume so a data-volume failure does not remove both the corpus and its backups.

Create a complete operational archive without stopping the application:

```text
python -m tools.backup_data --output D:\backups\tuneforge-YYYY-MM-DD.zip
```

Verify and restore an archive only into a new directory:

```text
python -m tools.restore_data D:\backups\tuneforge-YYYY-MM-DD.zip --restore-to D:\restored-tuneforge
```

The archive contains a consistent SQLite snapshot and every manual/evidence PDF
referenced by that snapshot. It includes checksums and excludes active login
sessions. Treat it as sensitive because it contains account password hashes and
the complete reviewed corpus; encrypt it when stored off-machine. Backup
progress is printed per stage and per member. TuneForge verifies the staged
archive's structure, decompression, and every manifest checksum before publishing
the final path atomically.

Every official manual should record its vendor, document title, official source
URL, retrieval timestamp, and language. Board model/revision, chipset, document
version, and publication date are recorded when available. Existing manuals can
be backfilled from their detail page. Incomplete provenance remains visible and
does not block ordinary draft review, but it does block acceptance certification.

Each manual has an acceptance report at `/manuals/{id}/acceptance`. The report
tracks review completion, approval/rejection precision, estimated recall using
reviewer-recorded missed settings, page-reference accuracy, description gaps,
and provenance readiness.
Certification is permitted only after extraction succeeds, every candidate has
left draft state, every approved page reference is checked, and required
provenance is complete. Later changes to review
decisions, setting content, provenance, or missed-setting records automatically
invalidate certification.

## Verification

```text
pytest
ruff check .
ruff format --check .
```

To rebuild the approved-setting search index:

```text
python -m app.search.rebuild
```

## Staged manual corpus

Official PDFs awaiting evaluation live outside runtime storage under
`../assets/manuals/staging/<vendor>/`. They are not uploaded automatically and
are ignored by Git. Audit a corpus without changing the database:

```text
python -m tools.audit_manual_corpus ../assets/manuals/staging \
  --json ../assets/manuals/staging-audit.json \
  --csv ../assets/manuals/staging-manifest.csv
```

Dry-run parser behavior on selected files without creating drafts:

```text
python -m tools.evaluate_parser path/to/manual.pdf \
  --json ../assets/manuals/parser-evaluation.json
```

Preview canonical vendor identities without changing the database or corpus:

```text
python -m tools.audit_vendor_normalization \
  --json ../assets/manuals/vendor-normalization-phase1.json \
  --strict
```

Preview normalized hardware applicability without changing the database or
staging manifest:

```text
python -m tools.audit_hardware_applicability \
  --json ../assets/manuals/hardware-applicability-phase1.json \
  --strict
```

Verify the persisted migration projection against normalization contract 1.0:

```text
python -m tools.verify_hardware_migration data/knowledge_builder.sqlite3 \
  --expect-revision 0012 --strict
```

Always invoke these utilities with `python -m` from `knowledge-builder/` so
imports resolve to the current working tree. A staged manual is eligible for
upload only after it is a valid unique PDF, has an official source URL and
required provenance, and the current pilot acceptance work is complete.

The reviewer upload form now holds every new upload at a pending extraction run.
TuneForge classifies the document type and, when provenance is incomplete,
suggests cover-page metadata and searches for an official vendor support page.
Suggestions are audited but never become reviewer decisions automatically.
Extraction starts only after required provenance is complete and a reviewer
confirms that the document is eligible. See `docs/provenance-discovery.md` and
`docs/eligibility-gate.md`.

For larger intake rounds, `/batches/upload` accepts up to 50 PDFs. Each file is
validated and committed independently, so invalid files and duplicates remain
visible without cancelling valid manuals. Batch progress links every accepted
manual to its provenance, eligibility, extraction, or draft-review next action.
The same workflow is available under `/api/v1/batches`. See
`docs/batch-ingestion.md`.

## Layout

- `app/api/` - versioned JSON API
- `app/db/` - SQLAlchemy models and database sessions
- `app/domain/` - business rules and state transitions
- `app/extraction/` - PDF reading and deterministic parsing
- `app/search/` - FTS5 indexing and queries
- `app/storage/` - local PDF storage
- `app/web/` - server-rendered curator interface
- `migrations/` - Alembic schema migrations
- `tests/` - unit and integration coverage
- `tools/` - corpus audit, parser evaluation, metadata evaluation, and pinned
  acceptance-curation commands

All twelve ingested manuals have complete provenance and certified acceptance
reviews, with no drafts remaining. The approved search index contains 4,018
settings. The MSI MPG X670E CARBON WIFI full user guide is retained as a
certified negative case. Five pre-0.6 records remain marked `legacy` by the
eligibility migration; their certified extraction and review histories remain
unchanged. The two controlled-expansion manuals passed the current provenance
and eligibility gates before parser 1.3 extraction, as did the ASUS AMD 800
and GIGABYTE legacy-platform expansions.

Parser 1.3 retains every previously approved setting and recovers all 122
curator-recorded omissions across the four BIOS guides. It adds no candidates
to the certified MSI full-manual negative case. See
`docs/parser-1.3-validation.md`.

The controlled-expansion pilot adds MSI AMD AM5 and GIGABYTE Intel 800
coverage: 465 candidates were approved, 164 were rejected, and one GIGABYTE
omission was recorded with its source page. The eligibility classifier now
recognizes compact and reordered official vendor filenames such as
`AMDAM5BIOS.pdf` and `mb_manual_intel800-bios_e_v2.pdf`. See
`docs/controlled-expansion.md`.

Vendor Normalization Phase 2 assigns stable vendor IDs to every production
manual while retaining compatible canonical display names. Uploads and
provenance edits accept known manufacturer and sub-brand aliases, preserve the
submitted value in immutable audit detail, and reject unknown nonblank vendors.
The API, reviewer detail page, and approved-setting search expose stable IDs;
the regenerated 30-manual corpus manifest now reports canonical `GIGABYTE`.
See `docs/vendor-normalization.md`.

The ASUS AMD 800 wave adds 835 approved settings from a 116-page guide. All
1,032 parser candidates and every rendered source page were reviewed; 197
document headings, option fragments, subgroup labels, and read-only fields were
rejected. No source-page omissions were found. See
`docs/asus-amd800-expansion.md`.

The GIGABYTE legacy-platform wave adds 232 approved settings from the 19-page
AMD B550/A520 and 28-page Intel 600 Series BIOS guides. All 328 parser
candidates and 47 rendered source pages were reviewed; 96 non-settings were
rejected, long voltage labels were reconstructed, and one Intel power-limit
omission was recorded. Both manuals have official support provenance and
hardware applicability with no warnings. See
`docs/gigabyte-legacy-expansion.md`.

The ASUS Intel legacy-platform wave adds 1,032 approved settings from dedicated
Intel 600 and 700 Series BIOS manuals. All 1,303 candidates and 182 rendered
pages were reviewed; 271 non-settings were rejected, eight split option
descriptions were reconstructed, and five visible omissions were recorded.
The staged MSI manuals were retained for future full-manual extraction work
because none contains a dedicated BIOS setting catalog. See
`docs/asus-intel-legacy-expansion.md`.

Canonical Setting Mapping Phase 1 adds 20 vendor-neutral identities, 23 exact
aliases, and 137 reviewed mappings across ASUS, ASRock, GIGABYTE, and MSI.
Only approved settings can be mapped. Alias matches remain suggestions until a
reviewer confirms them, and every mapping change is append-only audited.
Canonical IDs are exposed in setting details, catalog endpoints, and approved
search results. See `docs/canonical-setting-mapping.md`.

Canonical Mapping Phase 2 classifies all 20 identities into a stable
10-category taxonomy with explicit risk levels and reviewer rationale. It adds
9 source-faithful aliases and accepts 16 exact matches through a persistent,
audited suggestion queue, bringing the catalog to 32 aliases and 153 mappings.
Suggestions never map automatically, dismissed decisions are not regenerated,
and approved-setting search can filter by category and risk. See
`docs/canonical-mapping-phase2.md`.

The PBO Canonical Foundation adds 17 tuning-critical identities and 51 reviewed
source mappings for Precision Boost Overdrive, power/current limits, scalar,
boost override, thermal throttle controls, and Curve Optimizer. The production
catalog now contains 37 identities, 73 aliases, and 204 mappings. CPU identity
normalization is deliberately limited to the AMD Ryzen 7 9800X3D pilot, and a
small typed tuning vocabulary is exposed at `/api/v1/tuning-contract`. This
release contains no tuning recommendations. See
`docs/pbo-canonical-foundation.md`.

Trusted Evidence Intake adds a separate, reviewer-gated corpus for tuning
sources. Official PDF provenance is validated against supported publisher
domains; approval creates deterministic, immutable page-scoped passages and an
append-only audit trail. The first accepted source is AMD publication 68886,
Ryzen Master User Guide revision 3.1.0: 42 pages and 55 evidence passages.
No guidance claim, recommendation, or generated answer is created in this
release. See `docs/trusted-evidence-intake.md`.

Guidance Acceptance adds a reviewer-controlled layer between accepted evidence
passages and any future answer system. The AMD pilot contains 16 approved
source-scope claims, 16 exact passage links, 29 canonical-setting links, and 32
append-only claim events. It covers PBO modes and limits, PPT/TDC/EDC
definitions, Curve Optimizer behavior and modes, and explicit process/stability
warnings. No claim is scoped specifically to the Ryzen 7 9800X3D, and the
release cannot create recommendation-type claims or user-facing tuning answers.
See `docs/guidance-acceptance.md`.

Citation-First Guidance projects the accepted claim corpus into a read-only
setting explanation API and curator page. It resolves only canonical IDs,
canonical names, and exact normalized reviewer aliases; it performs no fuzzy
matching. Responses group accepted definitions, behaviors, constraints, and
safety facts while preserving source-scope notices and exact source/page
citations. Fourteen canonical settings currently have claim coverage. The
response contract explicitly reports `accepted_claims_only` and
`recommendation_status: not_available`. Release 0.21 makes no schema or
production data changes. See `docs/citation-first-guidance.md`.

The v0.21 baseline hardening keeps the schema and API shapes unchanged while
tightening five invariants: certification requires a recognized official
support page; mapped settings must be explicitly unmapped before reopening;
interrupted evidence extraction is recovered and retryable; acceptance
certification/invalidation is append-only audited; and guidance scope notices
are derived from retrieved sources rather than release-specific text.

Vendor Equivalence Discovery in v0.22 makes the existing canonical mapping
layer visible in Search and Citation-First Guidance. Each canonical result can
include vendor-specific source names with exact manual, page, setting-record,
and provenance URL citations. Guidance also resolves exact normalized names of
approved mapped settings. Conflicting mapped names fail closed instead of being
silently merged. The projection is read-only and adds no tables or migrations;
see `docs/vendor-equivalence-discovery.md`.

The AM5 System Context Pilot in v0.23 lets a user select one reviewed AM5
motherboard and CPU identity and carry that context into Search and Guidance.
Search explains whether each cited manual is exact-model, chipset,
chipset-family, platform, unverified, or incompatible evidence. Guidance keeps
accepted claims source-scoped and explicitly states that they are not validated
for the selected motherboard, CPU, or BIOS version. The context is
request-scoped, is not saved, and adds no database migration. BIOS-version
identity and applicability remain deliberately unmodeled. AM4 and Intel
LGA1700 reuse the same platform-neutral identity graph only after the AM5 exit
gates pass; see `docs/am5-system-context-pilot.md`.

AM5 Coverage Validation in v0.24 makes those exit gates measurable at
`/am5-coverage` and `/api/v1/hardware/am5-coverage`. The production snapshot
contains six acceptance-certified AM5 manuals, 1,547 approved settings, 113
mapped approved settings, 35 canonical identities observed in approved AM5
sources, and 14 of those identities with accepted guidance. All four vendors
have reviewed manuals and selectable AM5 identities, and every selectable model
has compatible evidence. Release 0.24.1 adds the reviewed ASUS TUF GAMING
X870-PLUS WIFI identity and certifies three narrowly supported exact-model BIOS
entries from its official user manual. All required evidence gates now pass and
the report returns `ready`.
Unobserved catalog identities are deliberately not classified as AM5 omissions.
Broad manual compatibility remains vendor-scoped, including in My System
Search, so one vendor's platform guide cannot support another vendor's board.
The coverage projection remains read-only; v0.24.1 adds only motherboard-catalog
migration 0015 and the reviewed exact source. See
`docs/am5-coverage-validation.md`.

The AM5 Product Journey in v0.25 connects the existing My System, Search, and
Citation-First Guidance surfaces into one context-preserving workflow. Every
mapped Search result links directly to Guidance using its canonical identity;
when a reviewed AM5 motherboard and CPU are selected, both identities survive
the handoff. Guidance either returns accepted claims with their existing source
passages or explicitly refuses when no accepted claims exist. Unmapped and
empty Search outcomes now explain why guidance is unavailable and never fall
back to unreviewed sources or model memory. The release changes no schema,
review state, canonical mapping, claim, or production evidence record. See
`docs/am5-product-journey.md`.

AM5 Product-Question Acceptance in v0.26 turns twelve fixed daily-use questions
into a deterministic, read-only product contract at
`/am5-question-acceptance` and
`/api/v1/hardware/am5-question-acceptance`. Nine factual questions require
specific accepted claim types; three intentionally require unavailable
recommendation, exact-system, or BIOS-version capabilities and pass only when
TuneForge refuses them. The production baseline passes 11 of 12 questions. The
single visible gap is a missing accepted behavior claim for PBO Scalar. The
report generates no answers and changes no review or evidence data. See
`docs/am5-question-acceptance.md`.

The ASUS PBO Scalar Evidence milestone in v0.27 closes the only failed AM5
product question through the normal Trusted Evidence workflow. ASUS document
E25269 First Edition is stored as a primary-official evidence source, and one
source-scoped behavior claim is accepted from page 30. Citation-First Guidance
now explains Scalar with both the existing AMD definition and the ASUS behavior
citation; no value recommendation or exact-hardware applicability is created.
The fixed AM5 question set is `ready` at 12/12. See
`docs/asus-scalar-evidence.md`.

Refusal Gap Worklist and Official Referrals in v0.28 turn fail-closed Search and
Guidance outcomes into a demand-ranked curation queue. Each event stores a
whitespace-normalized query capped at 300 characters, whitelisted TuneForge
context IDs, a timestamp, and deterministic nearest canonical identities; it
never stores IP addresses, headers, or free-form hardware profiles. Reviewed
official pointers may be shown after refusal, but contain metadata and links
only and never count as evidence or accepted claims. Migration 0016 adds the
append-only log and audited pointer registry. See
`docs/refusal-gap-worklist.md`.

The LLM Interface Rulebook in v0.29 defines a provider-neutral boundary before
any model is connected. Contract 1.0 permits only exact accepted claim text,
complete retrieved citation sets, unchanged applicability and safety notices,
and deterministic ordering. Its validator rejects paraphrase, claim merging,
reordering, omission, invention, altered citations, changed notices, and factual
output after empty retrieval. The machine-readable policy is available at
`/api/v1/interface-contract`. No prompt, model response, API key, provider,
natural-language router, schema migration, or production data is added. See
`docs/llm-interface-rulebook.md`.

The Interface Presentation Envelope in v0.30 exposes the provider-independent
input boundary at `/api/v1/interface/presentation`. Supported envelopes contain
exact accepted claim text and complete citation display metadata but deliberately
exclude evidence passage text. Resolved identities without claims and unresolved
inputs return structured refusals with navigation-only official pointers and
feed the refusal-gap worklist. Invalid hardware context remains a validation
error. No model, prompt, provider, natural-language router, migration, or
production row is added. See `docs/interface-presentation-envelope.md`.

AM5 Guided Answers in v0.31 turns the fixed product-question acceptance set into
a usable no-model interface at `/assistant`. Nine supported questions return
only the accepted claim types required by that question, with citation metadata
and unchanged scope notices. Three known unsupported questions return explicit
recommendation, exact-system, or BIOS-version capability refusals without
presenting related facts as answers. The same contract is available at
`/api/v1/interface/questions` and
`/api/v1/interface/questions/{question_id}/answer`. See
`docs/am5-guided-answers.md`.

The Provider-Neutral Model Boundary in v0.32 adds a narrow transport contract
for optional model adapters without connecting a live provider. Adapters receive
only the citation-safe envelope and exact required draft. Refused envelopes
bypass models, while malformed output, provider failures, identity mismatches,
or any change to claims, citations, order, scope, or notices is discarded in
favor of deterministic core output. The included fake adapter is test-only;
there are no credentials, external calls, model-backed routes, migrations, or
production data changes. See `docs/provider-neutral-model-boundary.md`.

The Public AM5 Experience in v0.33 replaces the reviewer redirect at `/` with
a simple TuneForge homepage and adds `/answer` for exact reviewed setting and
vendor-alias lookup. Public answers reuse the v0.30 citation-safe envelope, show
exact accepted claims, official citations, vendor equivalence, scope notices,
and structured refusals without exposing raw passages or reviewer records.
Guided Answers and My System now share the public visual shell, while every
existing curation workflow remains available through TuneForge Admin. No model,
external service, migration, authentication change, or production data is
added. See `docs/public-am5-experience.md`.

The Admin Operations Dashboard in v0.34 adds a focused `/admin` home for the
curator workspace. It derives live counts and work queues from existing manual,
setting, canonical mapping, evidence, claim, refusal, and AM5-validation data;
it does not copy or mutate workflow state. Pipeline failures are elevated,
pending review queues link directly to their existing tools, and recent events
are projected only from append-only audit tables. The responsive admin shell
keeps the public product one click away and retains a visible local-only
security notice. No migration or production row is added. See
`docs/admin-operations-dashboard.md`.

The Authentication and Authorization Boundary in v0.35 protects every curator
page, source artifact, OpenAPI surface, and non-public API while preserving the
public AM5 experience and deterministic interface endpoints. A one-time setup
creates the first administrator; administrators can create named reviewers,
reset passwords, disable accounts, and inspect append-only security events.
Passwords use salted PBKDF2-HMAC-SHA256, session cookies are opaque, HTTP-only,
strict same-site values backed by revocable database records, and unsafe writes
require same-origin confirmation. Signed-in usernames replace the historical
fixed reviewer label in new knowledge audit events. See
`docs/authentication-boundary.md`.

The Operations and Recovery Baseline in v0.36 makes unsafe production settings
fail at startup, rejects untrusted Host headers, and adds a public `/api/v1/ready`
probe for schema and durable-storage checks. A reproducible archive command uses
SQLite's online backup API, includes only database-referenced source artifacts,
hashes every member, and excludes active sessions; restore validates paths,
sizes, checksums, schema revision, and database integrity before atomically
publishing a new data directory. Docker and local Compose definitions preserve
the same single-service architecture and durable volume boundary. See
`docs/operations-recovery-baseline.md`.

Deterministic Question Routing in v0.37 lets the public Ask TuneForge box accept
the twelve exact reviewed AM5 questions and simple forms such as “What is Curve
Optimizer?” or “How does Precision Boost Overdrive work?”. Exact reviewed
questions receive their claim-type-focused guided answer; three anchored wording
rules remove only the visible question wrapper before the existing exact
canonical/alias lookup. Any recommendation, safety, hardware, firmware, or other
unsupported phrasing is left intact and therefore fails closed instead of being
guessed. `/api/v1/interface/route` exposes every routing decision and rule. No
model, fuzzy match, ranking, paraphrase, migration, or knowledge mutation is
added. See `docs/deterministic-question-routing.md`.

Release Readiness in v0.38 adds `/admin/release-readiness` and the protected
`/api/v1/release-readiness` report. Product checks cover reviewed AM5 expansion,
the fixed question contract, extraction health, and provider-free public
operation. Deployment checks cover schema/storage health, first administrator
setup, production cookie/host configuration, and a structurally inspected
backup newer than the live database. The dashboard now links to the gate,
labels authentication bypass accurately, and marks only the current navigation
item active. All protected curator workflows now inherit the same admin shell
instead of the old crowded top navigation. The report is read-only and adds no
migration or knowledge rows. See `docs/release-readiness.md`.

The TuneForge 1.0 release froze the focused AM5 answer scope after all product
gates passed. Release 1.1 adds corpus-wide public discovery, not generated
recommendations or cross-platform tuning advice. Ask TuneForge and Guided
Answers remain bounded by accepted claims and their existing AM5 acceptance
contract. See `docs/v1-release.md` and `docs/public-bios-settings-library.md`.

The Public BIOS Settings Library in v1.1 adds `/library` plus a separate
`/api/v1/library` read contract. Canonical mode groups reviewed cross-vendor
identities; Source mode exposes all approved manual settings, including settings
that have not yet been canonically mapped. Blank search browses the corpus,
keyword search uses the existing approved-only index, and filters cover vendor,
platform, chipset, category, and risk. Public details compose manual provenance,
hardware applicability, canonical classification, vendor-equivalence citations,
and accepted evidence claims without exposing drafts, rejections, reviewer
notes, audit actors, or raw passages. No schema migration or duplicated
knowledge store is introduced. See `docs/public-bios-settings-library.md`.

The Conservative Canonicalization Backlog in v1.3 evaluates every approved,
unmapped Source Setting against reviewed canonical identities using normalized
names, descriptions, vendor aliases, mapped precedents, official provenance,
hardware applicability, and BIOS menu context. Automatic mappings require every
hard gate and at least 99.50% confidence; missing evidence fails closed into the
protected reviewer queue. Suggestions retain structured evidence and automatic
decisions use the existing append-only mapping history with an explicit reversal
workflow. See `docs/canonicalization-backlog.md`.

Canonical Identity Review and Menu Context in v1.4 makes new-identity proposals
durable and reviewer-controlled. Reviewed BIOS menu paths carry append-only,
multi-page official-manual citations; proposal generation, refresh, rejection,
reopening, and approval each retain audit events. Approval atomically creates
the active Canonical Setting and reviewer mappings only while every evidence
gate still passes. Canonical identities can later be deprecated and reactivated
without deletion, and deprecated identities are excluded from the public
Library, search, guidance, and referral projections. The first visually
verified pilot approved four identities and eight mappings across ASUS,
ASRock, GIGABYTE, and MSI while leaving all uncertain records unmapped.

The v1.4.1 Library experience makes the complete approved Source Setting
corpus the default `/library` view. Users can page through mapped and unmapped
manual records directly, while the separate Canonical Comparisons view keeps
reviewed cross-vendor equivalence easy to find. Coverage metrics distinguish
all approved records, active canonical mappings, records awaiting mapping, and
canonical identities; approval and citation boundaries are unchanged.

The v1.5 Evidence-Backed Canonicalization Batch evaluates all 3,809 approved,
unmapped Source Settings with the conservative reviewer and adds deterministic
description agreement to new-identity proposals. A pinned, dry-run-first batch
records cited official-manual menu paths and atomically approves 15 identities
with 31 mappings across ASRock, ASUS, and GIGABYTE evidence. Four `Clear Secure
Boot Keys` records remain intentionally unmapped because the official ASRock
hierarchy is ambiguous. Every decision is append-only, reversible, and covered
by the report in `docs/v1.5-canonicalization-report.md`; 3,778 records remain in
the human-review backlog rather than being guessed.

The v1.5.1 Trust and Public Visibility Audit verifies the complete approved
corpus against the stored official PDFs and public Library read model. It
corrects three descriptions, rejects two parser headings and one duplicate
manual occurrence, preserves one documented ASUS label conflict without
mapping it, and prevents signed positive/negative Boost Override labels from
cross-resolving in search. All 4,018 approved Source Settings and all 56 active
Canonical Settings are publicly discoverable; 3,775 Source Settings remain
visible but intentionally unmapped. See
`docs/v1.5.1-trust-and-visibility-audit.md`.

The v1.6 AM5 Cross-Vendor Coverage Expansion adds 12 visually verified
canonical identities and 42 reviewer-approved mappings from the official ASUS,
MSI, GIGABYTE, and ASRock AM5 manuals. It also records 43 audited BIOS menu
paths and exposes those reviewed locations in the public Library API, setting
details, and vendor-comparison page. One exact-name ASUS `PSS Support` record
remains intentionally unmapped because its documented ACPI objects conflict
with the existing identity evidence. The remaining 3,733 approved Source
Settings stay publicly visible and conservatively queued for human review. See
`docs/v1.6-am5-coverage.md`.

Hardware Applicability Phases 1 and 2 define and persist stable identities for
five CPU/socket platforms, nine chipset families, 29 chipsets, and 15 known
motherboard models. Migration 0009 backfilled the original eight production
manuals exactly as previewed; normal intake synchronization now covers all
thirteen.
Raw provenance and certified review data remain preserved, with immutable
normalization events. Manual and setting pages show normalized targets;
`/api/v1/hardware` publishes the ID contract; approved-setting search can filter
by platform, chipset family, chipset, or motherboard while including compatible
broad-scope guides. See
`docs/hardware-applicability-normalization.md` and
`docs/hardware-applicability-phase2.md`.
