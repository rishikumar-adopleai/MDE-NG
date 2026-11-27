# MDE-NG Examination Workspace

MDE-NG demonstrates the examination workflow from receipt of an Examination Scheduling Request (ESR) through VA Delivery & Review. React displays work queues and sends staff actions to FastAPI. The backend owns workflow rules, records, document jobs, and AI requests.

Development uses **synthetic data only**. Appointment notices, package submissions, VA responses, and case closure are local simulations. Production mode currently supports authenticated administrative reads from a separately configured PostgreSQL database. Live VA exchange and production workflow actions require additional services and authorization.

## Run locally

Use Python 3.12 or later and put Node.js 24 on your shell's `PATH`. Run these commands from the repository root.

```sh
node --version
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -e ./backend
npm --prefix frontend ci
test -f backend/.env || cp backend/.env.example backend/.env
```

For a new checkout, restore the populated SQLite databases into `backend/data/` first (see [database setup](backend/README.md)). Start the API on port **9090** in one terminal.

```sh
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 9090 --reload
```

Start Vite in another terminal and open the [Dashboard](http://localhost:5173/dashboard) or the [Intake workspace](http://localhost:5173/intake).

```sh
npm --prefix frontend run dev
```

Vite forwards `/api/*` unchanged to `http://127.0.0.1:9090`. Set `MDE_API_TARGET` for Vite if the API runs at another address. See the [frontend setup](frontend/README.md) and [backend setup](backend/README.md).

### Development accounts

Sign in with an existing account or select **Create an account** to register for the Intake role. Signed-in development users can generate read-only AI reviews across all six stages. Workflow actions follow the assigned role. The server stores accounts and sessions in the local, Git-ignored SQLite database. Passwords are stored as salted hashes. Signing out revokes the session.

For full access to the six-stage demo, provision a local administrator before registering that email in the browser. Run the following from the repository root. The prompts hide the email and password as you type.

```sh
.venv/bin/python -m app.provision_account
```

### Development configuration

| Setting | Behavior |
| --- | --- |
| `APP_ENV` | Use `dev` to run the workspace with the bundled source documents. |
| `DATABASE_URL` | Leave empty to use `backend/data/mde-ng-dev.sqlite3` for workspace records, uploaded documents, and the reference library. |
| `NODE_BINARY` | Optional override for Node.js 24 or newer. The backend checks its PATH and, in development, project, nvm and npm cache installations. |
| `AUTH_DATABASE_PATH` | Optional SQLite account database path. Defaults to `backend/data/mde-ng-accounts.sqlite3`. The parent directory must exist. |
| `SESSION_MINUTES` | Local session lifetime. Defaults to 480 minutes. |
| `INTAKE_PROVIDER` | `local` processes readable documents with local rules. `azure-openai` enables model-assisted Intake processing and on-demand reviews across all six stages. |

Use [`backend/.env.example`](backend/.env.example) as the template. Restart FastAPI after changing environment settings. No source folder or external data service is needed for local storage.

## Six-stage workflow

Intake accepts Exam Management 4.7 ESR XML alongside the existing PDF and text intake form. The default workspace reads source requests from SQLite and opens one Intake record per ESR identifier. **New ESR → Bundled request** lists the available scenarios. Reused identifiers keep one operational record, while recorded events retain their scenario names. Earlier generated records are archived in the workspace database. Staff-entered records and imported ESRs carry over.

The default development workspace includes four practice records per stage for each supported scenario derived from the bundled requests and DBQ catalog. The current bundle supports Knee and Lower Leg and Hearing Loss and Tinnitus. Each group includes different checkpoints, such as a missing attachment, a clarification request, a held slot, a QA correction and a package awaiting response. A confirmed examination can also appear in Scheduling while Clinical work is active. Earlier practice cases retain their progress. **Reference library** provides the exchange events and questionnaire definitions. Original requests keep their source identifiers and evidence requirements. Practice availability is reserved for practice cases, and package transmission stays within the workspace.

| Stage | Staff workflow in the demo | AI assistance |
| --- | --- | --- |
| **Intake** | Open or import an ESR, review sources and exceptions, and validate the handoff to Medical Triage. | Classify and summarize readable source documents with page references. Generate a cited overview of the selected case. |
| **Medical Triage** | Review evidence per contention, save a draft plan, record clarification, and approve the exam pathway and required forms. | Summarize linked evidence and highlight gaps or questions about the exam pathway and Disability Benefits Questionnaire (DBQ). |
| **Scheduling** | Match eligible examiners and slots, record outreach and logistics, and confirm the appointment. | Explain the scheduling and outreach records and draft a message for staff review when applicable. |
| **Clinical Evaluation** | Record the simulated encounter, complete and validate the DBQ, review diagnostics, and attest to the saved report version. | Flag documentation gaps and surface diagnostic designations already recorded by the source. |
| **Internal QA** | Compare report revisions, run checks, record findings, return a report for correction, or approve the signed revision. | Review the signed revision for missing explanations, potential inconsistencies, and prohibited comments. |
| **VA Delivery & Review** | Validate the package, simulate submission and response, route rework, and close after reconciliation. | Summarize package blockers, local responses, unresolved tasks, and recorded travel. |

### Review the workflow

1. In **Intake**, select the labeled practice case to start the workflow. For a bundled request, attach a verified, readable supporting document for each contention before selecting **Validate and send to Triage**.
2. In **Medical Triage**, review the linked evidence, choose the pathway and forms, record the rationale, and approve the plan.
3. In **Scheduling**, hold an eligible slot, record answered claimant outreach and logistics, and confirm the appointment. Practice cases have remote slots. Other cases require a configured examiner roster and availability.
4. In **Clinical Evaluation**, record the encounter, complete the DBQ, review any required diagnostics, and send the attested version to QA.
5. In **Internal QA**, run the checklist and record the review decision. A correction returns the selected report to Clinical Evaluation and preserves earlier versions.
6. In **VA Delivery & Review**, validate and prepare the package, simulate submission, record a simulated response, and close after reconciliation. A report fix returns to QA. A new-exam request returns to Scheduling.

## Enable AI reviews

Add these settings to the backend environment or the git-ignored `backend/.env`. Use a deployment that supports structured JSON output and keep the API key server-side.

```dotenv
APP_ENV=dev
INTAKE_PROVIDER=azure-openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/openai/v1/
AZURE_OPENAI_DEPLOYMENT=your-exact-deployment-name
AZURE_OPENAI_API_KEY=your-server-only-key
```

The backend also accepts a Foundry project inference URL and `AZURE_OPENAI_CHAT_DEPLOYMENT` as the deployment setting. After restarting FastAPI, open a work item and expand its AI panel. **Sources** opens the cited excerpts in the workspace. The API keeps uploaded originals server-side.

In Intake, expand **Document review suggestion** under **Documents** and select **Review documents**. The assistant uses the selected ESR's sources and exceptions to produce three cited points covering document evidence, document readiness, and staff verification. Intake staff can accept or reject the saved review and can resolve exceptions and validate the case through the existing Intake controls.

- Each review uses a bounded set of records linked to the selected work item. The backend verifies citation IDs before returning a response.
- If the model is unavailable during Intake, local checks still extract and classify readable files. The document offers **Retry AI summary**. Image-only PDFs need staff review or a readable replacement.
- AI reviews are advisory. Accepting or rejecting an Intake review records a staff action in Activity. Clinicians retain diagnosis, risk assessment, and report sign-off. VA retains benefits decisions.
- Use synthetic input in development. Real Veteran and VA records require an approved production security boundary.

## API and verification

Check the backend and the frontend proxy with the services running.

```sh
curl -fsS http://127.0.0.1:9090/api/health
curl -fsS http://localhost:5173/api/runtime
```

The runtime response reports the mode, authentication requirements, simulation availability, and whether AI reviews are enabled.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/signup` | Create a local Intake account and start a session. |
| `POST /api/auth/login`, `POST /api/auth/logout` | Start or revoke a local session. |
| `GET /api/workspace` | Fetch persisted workspace records, work queues, and Intake and Triage views. |
| `GET /api/workspace/work-items/{work_id}` | Load server-derived detail for Scheduling, Clinical Evaluation, Internal QA, and Delivery. |
| `POST /api/workspace/commands` | Run a development workflow action using the current revision and an `Idempotency-Key` UUID. |
| `POST /api/workspace/ai-review` | Request a cited review for a selected synthetic work item using its stage and entity ID. |
| `POST /api/intake/examples/service-note` | Receive the built-in synthetic ESR and queue its document. |

The live OpenAPI contract is at [`/docs`](http://127.0.0.1:9090/docs). Future stage-specific route placeholders return `501`. The React demo uses the implemented workspace and Intake APIs.

```sh
(cd backend && ../.venv/bin/python -m unittest discover -s tests -v)
(cd frontend && npm exec -- tsc --noEmit)
npm --prefix frontend run test:workspace
npm --prefix frontend run test:sites
npm --prefix frontend run build
```

If workspace reads succeed but work-item details return `503`, confirm that FastAPI uses Node.js 24 through `NODE_BINARY`. If AI requests fail, check the backend log for the upstream response status and verify the exact deployment name, endpoint, and server credential.

## Repository map

| Path | Responsibility |
| --- | --- |
| [`frontend/src/`](frontend/src/) | React shell, stage pages, domain types, and API client. |
| [`frontend/server/`](frontend/server/) | Node.js workflow entry point and server-side work-item views. |
| [`backend/app/api/`](backend/app/api/) | FastAPI routes and access checks. |
| [`backend/app/services/`](backend/app/services/) | Workspace persistence, Intake processing, Triage, and AI reviews. |
| [`backend/migrations/`](backend/migrations/) | Database migrations. |
| [`backend/tests/`](backend/tests/) and [`frontend/tests/`](frontend/tests/) | Backend, workflow, and hosting regression tests. |

## Production integration status

The separately supplied client RFP defines the examination workflow and its controls. The **Medical Disability Examinations—Next Generation (MDE-NG)** Word document outlines the proposed AI use across those stages.

Production work includes record-scoped access, VA event exchange, scanned-document OCR, authorized whole-folder retrieval, outbound scheduling communications, live diagnostic feeds, compliant signatures, and reconciliation against authoritative VA records. Production AI also requires an approved, isolated U.S. environment for Veteran data. See the [backend guide](backend/README.md) for production-mode configuration.
