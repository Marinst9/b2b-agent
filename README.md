# B2B Agent

A B2B outreach agent: imports/qualifies leads, researches companies,
generates drafts via an LLM, and sends outreach emails after human approval.
Background processing (research -> qualify -> draft) runs via Celery +
Redis, with durable dispatch, lease-based crash recovery, and atomic
version-safe draft writes.

## Prerequisites

- Python 3.11+ and a virtualenv at `venv/` (repo root)
- Node.js 18+ (for the dashboard)
- PostgreSQL (dev database)
- Docker Desktop (with WSL2 on Windows), for background-processing
  (Celery/Redis) and for the real-broker integration tests — see
  [Docker setup](#docker-desktop-setup-windows) below if not yet installed

Copy `.env.example` to `.env` and fill in real values before first run.
`.env` is never overwritten by any command or test in this repo.

## Architecture

**Pipeline**: import CSV leads → deduplicate → research each company (crawl →
extract facts via LLM → validate/flag conflicts) → qualify against campaign
criteria (deterministic scoring, not ML) → generate an evidence-based draft
(cites specific facts, flags unsupported claims) → human review/approval →
send. Steps after import run as Celery background jobs; every job/step is
tracked in PostgreSQL (the authoritative status store) independent of
Celery/Redis, which are only the delivery mechanism (durable "outbox" style
dispatch — see `job_service.py`).

**Backend module map** (`app/`):

| Layer | Modules | Responsibility |
|---|---|---|
| API | `api.py`, `schemas.py` | FastAPI routes; request/response validation |
| Orchestration | `services.py`, `research_service.py`, `qualification_service.py`, `draft_service.py`, `job_service.py`, `tasks.py` | Business logic per pipeline stage; job/step tracking; Celery task wrappers |
| Providers (I/O) | `modules/web_fetcher.py`, `modules/research_extractor.py`, `modules/ai_generator.py`, `modules/draft_generator.py`, `modules/email_sender.py`, `modules/enrichment.py` | The only code that talks to the outside world (websites, the OpenAI API, Hunter.io for email discovery in `enrichment.py`, Mailtrap/SMTP for delivery in `email_sender.py`) — always called through an injectable seam so tests/demo can fake it |
| Safety/quality | `modules/research_validation.py`, `modules/draft_safety.py`, `modules/provider_budget.py` | Fact conflict detection, unsupported-claim scanning, fleet-wide concurrency limits |
| Persistence | `database.py`, `app/alembic/` | SQLAlchemy models + migrations |
| Optional ML | `ml_experiment.py` | Predicts the human suitability label (not conversion, not a replacement for `qualification_service.py`'s deterministic scoring) — see [Known limitations](#known-limitations) |
| Evaluation | `evaluation/` | Offline drafting-comparison harness — see [Offline evaluation](#offline-evaluation-drafting-comparison) |
| Demo | `demo/` | Isolated, fixture-backed demo mode — see [Demo (isolated, no real sending)](#demo-isolated-no-real-sending) |

`main.py` is the original single-script CLI prototype from before the
FastAPI/Celery architecture existed — kept for history, superseded by
`api.py` + `tasks.py` for everything the dashboard actually drives.
`modules/research.py` is a second, earlier prototype (reachable via
`POST /research` and the dashboard's Prompts tab) that directly compares
three prompt-engineering variants per lead — see
[Known limitations](#known-limitations) for its real-cost caveat.

**Key design decisions** (each documented in more depth in its module's own
docstring):
- **Deterministic qualification, not ML**: scoring against campaign criteria
  is a fixed, explainable rubric (`qualification_service.py`) so every score
  can be traced to specific evidence. `ml_experiment.py` is a separate,
  optional layer that only trains once enough diverse human labels exist.
- **Evidence binding**: drafts cite specific `ResearchFact` rows by id;
  `draft_safety.py` verifies every citation resolves to a real fact for that
  lead's current research snapshot and flags language that isn't backed by
  any fact.
- **Immutable draft history**: every (re)generation or edit creates a new
  `DraftVersion` row; approval is bound to the exact version, subject, body,
  and recipient email, so an edit after approval can never silently send
  something nobody approved.
- **Durable job dispatch**: a `Job` row is committed before publishing to
  Celery; if the broker is down, a periodic sweep retries dispatch later
  instead of losing the job.

## Backend (API)

```bash
cd app
../venv/Scripts/python.exe -m uvicorn api:app --reload --port 8000
```

Verified: serves on `http://127.0.0.1:8000`, e.g. `GET /campaigns`.

## Frontend (dashboard)

```bash
cd dashboard
npm install
npm run dev
```

Verified: serves on `http://localhost:5173`, talks to the backend above.

## Backend tests

```bash
cd app
../venv/Scripts/python.exe -m pytest -q
```

Verified result (this environment, 2026-09-15, with the disposable
integration infra from [Real-broker integration tests](#real-broker-integration-tests)
running): **279 passed, 0 failed, 0 skipped**. Without that infra running,
the 4 real-broker tests in `app/tests/integration/test_real_broker.py` skip
automatically instead of failing (275 passed, 4 skipped). (Count grew from
this project's earlier 239/235 after Milestone 6 added the `evaluation/` and
`demo/` test files — no test was removed.)

## Frontend build & lint

```bash
cd dashboard
npm run build   # verified: builds cleanly to dashboard/dist
npm run lint    # verified: no errors
```

## Background processing (Celery + Redis) via Docker

`docker-compose.yml` runs only Redis + a Celery worker + Celery beat; the API
and its Postgres database keep running on the host exactly as above —
nothing here touches your existing database.

```bash
docker compose up --build
```

The worker/beat containers reach your host Postgres via
`host.docker.internal` — adjust the port in `docker-compose.yml` if your
local Postgres isn't on the default port from `.env`.

## Real-broker integration tests

`app/tests/integration/` verifies behavior that only shows up against a real
broker and a real, separate Linux worker process: genuine scheduled retries,
duplicate task delivery, and cross-process task execution — never a worker
running natively on the host. These run against fully **disposable**
infrastructure defined in `docker-compose.integration.yml` — separate
container project, separate ports (Postgres `55432`, Redis `6380`), no
persistent volumes, and a hard runtime safety check in `conftest.py` that
refuses to start if the integration database ever resolves to the same
target as your dev `.env`. External providers (website fetch, LLM, email)
are mocked *inside* the worker container itself (`container_fakes.py`), since
monkeypatching from the test process can't reach a separate container.

```bash
# 1. build and start the disposable test infra, INCLUDING the real worker
docker compose -f docker-compose.integration.yml up -d --build redis-integration postgres-integration worker-integration

# 2. run the suite
cd app
../venv/Scripts/python.exe -m pytest tests/integration -v

# 3. tear down (discards all test data)
cd ..
docker compose -f docker-compose.integration.yml down
```

Full details, including how to also manually run the containerized
`worker-integration`/`beat-integration` services, are in
[app/tests/integration/README.md](app/tests/integration/README.md).

**Status in this environment: verified.** 4 passed, 0 failed, 0 skipped
(2026-09-15), against a real Redis, a real disposable Postgres, and a real
Celery worker running inside a genuine Linux container (Docker Desktop +
WSL2, set up per the steps below). Confirmed via the worker's own logs, not
just assertions: real duplicate-delivery deduplication and a real
Celery-scheduled retry both actually happening inside that container.

## Offline evaluation (drafting comparison)

`app/evaluation/` compares the basic drafting prompt (`modules/ai_generator.py`)
against the evidence-based drafter (`modules/draft_generator.py`) over 24
synthetic, fictional company cases (`app/evaluation/fixtures/companies_v1.json`
— all `.example`-TLD domains, none resolve to anything real). Cases are
tagged `normal`, `missing_info`, `conflicting_info`, `irrelevant_info`,
`unsupported_claim_bait`, and `prompt_injection`, specifically to probe
whether each approach fabricates content it wasn't given.

Every result gets **deterministic** metrics (`evaluation/metrics.py`, no LLM
involved): schema validity, a citation check (does every claim's `fact_id`
resolve to a real evidence fact — only applicable to the evidence-based
approach, since the basic prompt has no citation concept), a regex-based
unsupported-claims scan (`modules/draft_safety.py`, the same one production
uses), plus cheap language/length heuristics. These are useful, cheap
signals, not a semantic-correctness judge — see the module docstring for
exactly what they can and can't catch. Results (plus optional human ratings,
1–5 relevance/personalization — left `null`/unrated until someone actually
reviews them in the dashboard's Evaluation tab) are stored in the
`EvalRun`/`EvalResult` tables, entirely separate from real `leads` and
`qualification_labels` data.

Two run modes:

```bash
cd app
# offline, free, deterministic (default) -- a MOCK model call, verifies the
# harness end-to-end but does NOT measure real model quality
../venv/Scripts/python.exe -m evaluation.run_eval

# a real, budgeted comparison (requires OPENAI_API_KEY, and is a paid call) --
# opt-in only, never runs automatically, and stops once max-calls is spent
../venv/Scripts/python.exe -m evaluation.run_eval --mode live --max-calls 10
```

Results are viewable in the dashboard's 🧪 Evaluation tab (list of runs → a
run's per-case results, with a form to record human ratings).

**Measured results (mock mode, 2026-09-15)** — all 24 cases, 48 generations.
Reported as-is; this mode exists to validate the harness and metrics wiring
(e.g., the mock evidence-based generator deliberately injects invented
language for `unsupported_claim_bait` cases so the safety scan has something
real to catch), not as a claim about real model quality:

| Metric | basic | evidence_based |
|---|---|---|
| Schema valid | 24/24 | 24/24 |
| Language-compliant | 24/24 | 24/24 |
| Length-compliant | 21/24 | 22/24 |
| Source-ref citations (applicable/verified/invalid) | not applicable (no citation concept) | 22 applicable, 22 verified, 0 invalid |
| Unsupported-claim flags (cases with ≥1) | 0/24 | 3/24 |
| `unsupported_claim_bait` cases caught | 0/3 | 3/3 |

The `unsupported_claim_bait` row is the headline result: the evidence-based
path's own safety scan catches 100% of the deliberately-injected invented
claims in this mock run, while the basic path's mock never emits them in the
first place (it has no safety scan to test — this asymmetry is expected and
documented in `evaluation/harness.py`, not a bug). A real (`--mode live`)
comparison with actual model output has not been run as part of this
session — see the opt-in command above.

## Demo (isolated, no real sending)

A self-contained, reproducible demo: the real FastAPI app + Celery (eager
mode), with every external provider (website fetch, LLM extraction/drafting,
email) replaced by deterministic, fixture-backed fakes drawn from the same
synthetic dataset the evaluation harness uses (`app/demo/fake_providers.py`).
No OpenAI key, Docker, Redis, or real network access required, and it runs
against an **isolated SQLite file** (`app/demo/demo_data/demo.db`) — your
real `.env`/`DATABASE_URL` and its data are never read or written.

```bash
cd app
../venv/Scripts/python.exe -m demo.run_demo   # starts the API in demo mode, port 8000
```

In another terminal, run the dashboard as usual (`cd dashboard && npm run
dev`) and import `app/demo/demo_leads.csv`. Reset demo data anytime with
`../venv/Scripts/python.exe -m demo.reset` (only ever touches `demo.db`).

**Sending is blocked server-side** whenever `DEMO_MODE=1` — `email_sender.py`
raises `DemoModeSendBlockedError` before any SMTP call is attempted, and
`fake_providers.py` adds a second, independent guard on top for the demo
process specifically. See `app/tests/test_demo_mode.py`.

**3–5 minute walkthrough** (screenshots in [`docs/demo/`](docs/demo/)):

1. Create a campaign and set qualification criteria.
   [`01_criteria.png`](docs/demo/01_criteria.png)
2. Import `demo_leads.csv` — leads appear on the dashboard.
   [`02_dashboard_after_import.png`](docs/demo/02_dashboard_after_import.png)
3. Start a processing job (research → qualify → draft) from the Processing
   tab and watch it reach a terminal, completed state.
   [`03_processing_workflow_complete.png`](docs/demo/03_processing_workflow_complete.png)
4. Open the Drafts tab — a generated draft is waiting for review, citing
   facts from the fixture company page.
   [`04_drafts_review.png`](docs/demo/04_drafts_review.png)
5. Approve the draft (binds approval to that exact version/recipient).
   [`05_draft_approved.png`](docs/demo/05_draft_approved.png)
6. Click Send — it's rejected server-side because demo mode blocks real
   sending, even though the draft is approved.
   [`06_send_blocked_by_demo_mode.png`](docs/demo/06_send_blocked_by_demo_mode.png)
7. Open the 🧪 Evaluation tab to see offline drafting-comparison runs and
   record a human rating.
   [`07_evaluation_tab.png`](docs/demo/07_evaluation_tab.png)

## Docker Desktop setup (Windows)

Minimal steps to get Docker Desktop with Linux containers working, so the
real-broker integration tests and the background-processing containers above
can actually run. (Already completed and verified in this environment —
kept here for setting up a fresh machine.)

1. **Enable WSL2** (Docker Desktop on Windows uses it as the Linux
   container backend). In an elevated (Administrator) PowerShell:
   ```powershell
   wsl --install
   ```
   This installs WSL2 plus a default Linux distribution.
2. **Restart Windows.** This step is required — WSL2 does not become
   usable without a reboot after `wsl --install` on a machine where WSL was
   not already present.
3. After rebooting, WSL usually finishes its first-time distro setup
   automatically. Confirm with:
   ```powershell
   wsl --status
   ```
4. **Install Docker Desktop** from
   https://www.docker.com/products/docker-desktop/, choosing the WSL2
   backend when prompted (this is the default on current installers).
5. Start Docker Desktop and confirm it's using Linux containers (Docker
   Desktop defaults to Linux containers on Windows; if it's ever switched to
   Windows containers, switch back via the tray icon's context menu).
6. Verify from a regular terminal:
   ```bash
   docker --version
   docker run --rm hello-world
   ```

None of this affects the existing `.env` or PostgreSQL data — both are used
as plain network clients by the containers, never modified by installing or
running Docker.

## UI verification (background processing end-to-end)

Manually verified in a real browser (Playwright, with external providers —
web fetch, LLM, email — mocked and Celery in eager mode, since no real
broker is available yet in this environment):

- Creating a campaign, setting qualification criteria, and importing a CSV
  lead.
- Starting a workflow job from the Processing tab and observing it reach a
  terminal "Завршено" (succeeded) state.
- Refreshing the browser mid-flow and after completion — job state persists
  correctly (it's read from the backend, not client-only state).
- A generated draft appearing in the Drafts tab awaiting approval, with no
  email actually sent (the mocked email sender raises if ever called for
  real — it was never triggered).
- Cancelling a queued job via the Cancel button, confirming the cancellation
  persists across a browser reload, then using Retry to re-run it to a
  successful terminal state.
- Zero browser console errors across all of the above.

This used eager-mode simulation for timing reasons (a real broker makes
"stuck" intermediate states trivial to observe; eager mode completes a whole
workflow within a single request). The underlying Cancel/Retry/dispatch
logic itself is also covered by the automated backend test suite above,
independent of the UI.

## Continuous Integration

`.github/workflows/ci.yml` runs on every push/PR to `main`, as three
independent jobs:

- **backend-unit-tests** — `pytest --ignore=tests/integration` (SQLite, no
  external services, matches [Backend tests](#backend-tests) above).
- **backend-integration-tests** — brings up the disposable
  `docker-compose.integration.yml` infra (real Redis, real disposable
  Postgres, a real containerized Celery worker), waits for the worker to
  answer a control-plane ping, then runs `pytest tests/integration` per
  [Real-broker integration tests](#real-broker-integration-tests), and always
  tears the infra back down afterward.
- **frontend** — `npm ci`, `npm run lint`, `npm run build` in `dashboard/`.

## Known limitations

- The ML suitability classifier (`ml_experiment.py`) predicts a human
  reviewer's suitable/unsuitable/unsure label — it never replaces
  `qualification_service.py`'s deterministic scoring, which remains the only
  qualification signal used elsewhere in the app. `assess_label_readiness()`
  requires at least 30 total labels, 8 per class, and labels spread across
  enough distinct companies (see the module docstring for exact thresholds)
  before `train_suitability_model()` will train at all; with the current
  human-labeled data volume, it has not yet trained and is not wired into any
  production code path.
- The offline evaluation harness (`app/evaluation/`) has only been run in
  **mock mode** so far (see [Offline evaluation](#offline-evaluation-drafting-comparison)) —
  it validates that the harness/metrics wiring itself is correct, not real
  model output quality. A `--mode live` run is opt-in, budgeted, and costs
  real API calls; it hasn't been run as part of this project yet.
- Worker-crash lease recovery and overlapping-worker duplicate delivery are
  verified at the unit level (`app/tests/test_job_service.py`'s zombie-worker
  tests) and via a real broker's at-least-once redelivery
  (`test_duplicate_delivery_over_a_real_broker_still_does_not_duplicate_work`),
  but not yet via two genuinely concurrent `worker-integration` container
  *replicas* racing each other — the current suite runs one real worker
  container. The lease-owner compare-and-swap this depends on is the same
  code path either way, so this is a coverage gap in breadth, not a known gap
  in the mechanism itself.
- `provider_budget.py`'s Redis-backed budget enforces **concurrency only**
  (at most N calls in flight at once) — it does not implement request-rate
  limiting or a total-usage/cost cap. See its module docstring.
- `POST /research` and the dashboard's Prompts tab (`modules/research.py`) —
  an earlier prototype that compares three prompt-engineering variants
  (V1 basic, V2 detailed, V3 few-shot) — make **three real, unbudgeted
  OpenAI calls per lead** on every invocation, with no mock/opt-in gate like
  `evaluation.run_eval`'s live mode has. It predates and is unrelated to the
  evidence-based `draft_generator.py` pipeline; be aware of the real cost
  before calling it.

