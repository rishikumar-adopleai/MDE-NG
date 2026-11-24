# MDE-NG Backend

## Local development

Requires Python 3.12 and Node.js 24. Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ./backend
APP_ENV=dev .venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 9090
```

`backend/requirements.txt` is the dependency list for both editable installs and Docker. To install dependencies only in an active virtual environment, run `python -m pip install -r backend/requirements.txt` from the repository root and run backend modules from `backend/`.

Workspace records, uploads and reference files are stored in `backend/data/mde-ng-dev.sqlite3`; accounts and sessions are stored in `backend/data/mde-ng-accounts.sqlite3`. These files are private and intentionally not tracked by Git. The backend discovers Node.js 24 or newer automatically. Set `NODE_BINARY` only to select a specific executable. Browser signup creates an Intake account. To create an administrator, run `.venv/bin/python -m app.provision_account` and enter the account details at the hidden prompts.

Back up both databases using SQLite's backup facility, or stop the API before copying them. On a new checkout, restore the populated files into `backend/data/` **before starting the API**. For a new empty workspace with access to the original source package, run `.venv/bin/python -m app.import_references /absolute/path/to/package` from the repository root once. It imports only the required XML, schema, and catalog files, verifies an identical repeat import, and never overwrites a different library. Normal operation and restarts do not need the source package. An empty workspace database without imported references reports a missing library rather than silently generating an empty queue.

The optional local Docker Compose API uses the same `backend/data/` bind mount and SQLite job queue. Stop the host API before running Compose against the same data. Copy `backend/.env.example` to `backend/.env` if no local environment file exists.

## Production mode

Set `APP_ENV=prod`, an explicit PostgreSQL `DATABASE_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`, and a dedicated `JWT_SECRET`. Apply the existing Alembic migration. Use a trusted same-origin gateway to pass short-lived issuer- and audience-bound administrator credentials to the read API. Development login, synthetic seeds, and simulated workflow commands are unavailable in this mode. Production record-scoped access, mutation handlers, and VA integrations are tracked in the [implementation plan](../IMPLEMENTATION_PLAN.md).

## Implemented entry points

- `GET /api/health` checks database connectivity and reports the backend mode.
- `GET /api/runtime` reports mode, authentication requirements, and simulation availability.
- `POST /api/auth/signup`, `POST /api/auth/login`, and `POST /api/auth/logout` manage local accounts and HttpOnly sessions.
- `GET /api/workspace`, `GET /api/cases`, and `GET /api/cases/{esr_id}` read persisted data.
- `GET /api/workspace/work-items/{work_id}` returns the server-derived detail for the selected Scheduling, Clinical Evaluation, Internal QA, or VA Delivery & Review item in development mode.
- `POST /api/workspace/commands` supports development workflow actions with a revision and a UUID `Idempotency-Key`.
- The individual stage URLs in `app/api/stages.py` remain placeholders. The frontend uses the workspace command endpoint.

## Intake document processing

Open Intake and select **New ESR**. **Receive example ESR** loads a synthetic source file and its request metadata from the backend. You can also supply a synthetic PDF or UTF-8 text file, up to 5 MB and 40 pages. Use **Add another source file** for an existing active ESR.

FastAPI saves the ESR, local AEP, source metadata, raw file, and processing job together. The development worker extracts text, classifies the document, and saves page excerpts and exceptions. Its queue survives a restart. Image-only or locked PDFs need manual review or a readable replacement. Intake staff review the result and explicitly validate the handoff to Triage. The workspace API returns the queue, action availability, metrics, and blockers.

The default `INTAKE_PROVIDER=local` needs no credentials or external calls. To test Azure OpenAI, set these in the server environment or `backend/.env` and restart FastAPI:

```dotenv
APP_ENV=dev
INTAKE_PROVIDER=azure-openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
AZURE_OPENAI_API_KEY=your-server-only-key
```

For a Foundry project inference URL, use `https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/`. `AZURE_OPENAI_CHAT_DEPLOYMENT` is also accepted as the deployment setting. Use the exact deployment name in Foundry. The v1 route uses implicit API versioning. Use a deployment that supports strict structured output. The Azure request contains bounded text excerpts with their source page numbers. The backend validates the response shape and page references. Review the suggestion against the source. Failed Azure requests can be retried from the manifest. Dev uploads and the Azure adapter support synthetic files only. Production VA ingestion, OCR for image-only files, and approved clinical data handling remain separate work.

Run the document extraction and Azure contract tests with `python -m unittest discover -s backend/tests -v` from the repository root.

### Stage AI reviews

The configured Intake provider also powers the on-demand AI review on each selected work item. The backend builds a bounded view of the selected synthetic record, applies the stage-specific prompt, and validates each citation. The Intake Document Review Assistant saves the latest review for each case and records staff acceptance or rejection in Activity. Intake validation remains a separate staff action.

The six reviews cover Intake document and exception summaries, Triage evidence and plan checks, Scheduling outreach and eligibility summaries, Clinical documentation and source-reported diagnostic flags, QA of a signed report revision, and Delivery reconciliation and recorded travel checks. Live OCR and VA feeds, voice or SMS delivery, drive-time routing, and production authorization require the appropriate provisioned services. Keep this Azure configuration limited to synthetic data.

### Development workflow verification

Receive the synthetic Intake example, validate it, and approve its examination plan in Medical Triage. In Scheduling, hold an eligible slot and record answered claimant contact and logistics before confirming. Clinical Evaluation records the simulated encounter and requires an attestation to the saved DBQ revision. Internal QA checks and approves that revision or returns it for correction. VA Delivery & Review validates the approved reports, records a simulated submission and response, and closes once contention dispositions and reconciliation are complete.

The generated fixture includes historical local approvals and the associated diagnostic review records. Persisted older demo data may expose package blockers when a supporting record is missing. Use a fresh synthetic Intake example to exercise the complete lifecycle on an existing development database.
