# MDE-NG Frontend Instructions

## MDE-NG Product And Visual Direction

- Use `../MDE-NG_Examination_Workspace.html` as the visual source of truth: Figtree typography, a sea-glass canvas with teal actions, white rounded panels, subtle borders and soft shadows, a narrow left workflow rail, a search/profile header, a stage-specific workspace, and a live selected-case panel.
- Keep the workspace responsive and preserve the reference's six-stage navigation, contextual case details, and workflow actions in the React implementation.
- Keep `src/App.tsx` focused on the shared shell and stage routing. Implement each of the six stage workspaces as an individual page component under `src/stages/`, with page-specific interactions and state kept in that stage component and shared models/controls kept in shared stage modules.
- Make each non-Intake stage show its own workflow progress and stage-specific handoff. Distinguish deterministic rules from AI suggestions, keep approval decisions with authorized staff/clinicians, and run workflow interactions against the development backend and bundled source documents.
- Keep exactly these six workflow items, in order: Intake, Medical Triage, Scheduling, Clinical Evaluation, Internal QA, VA Delivery & Review.
- Make stage content and actions specific to the examination lifecycle. Use sample data only; do not connect to VA systems, adjudicate claims, or let AI make final clinical decisions.
- Open each stage in its staff work queue. Keep exchange files and DBQ definitions in the reference library, and label practice cases built from those sources in their work queues.

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable app-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local app can be prepared for Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

- Use the backend runtime mode and API as the frontend data source. The default development workspace uses bundled Exam Management and DBQ source documents and persists staff work. Keep source events separate from actionable case work, and preserve evidence, scheduling and approval gates. Production must use explicitly configured data and authentication. Keep VA interfaces disabled until the client supplies approved access and specifications.

- Intake sample files, generated records, processing providers, and workflow decisions belong to the backend. React renders the Intake workspace returned by the API and sends staff intent and user-selected files.

- Keep Intake visually calm and easy to scan. Use a compact queue, one selected-case workspace, and progressive disclosure for documents, exceptions, activity, and uploads. Treat each stage's layout as a separate design review.
- Keep the Intake case list independently scrollable beneath its search and filters. Receive requests and add sources inline, without upload modals or directory-input confirmations. Support multi-file selection, ZIPs and dropped folders; retain the inline synthetic-record attestation. Lead case review with the request summary, missing items and next step; distinguish source text from generated summaries.

- Give Intake readiness states a single-line queue badge and a clear case-level label. Keep native selects and file inputs keyboard-accessible within the styled controls. Present Medical Triage as a contention-level queue with evidence and the examination plan in one selected workspace.

- Use the same navigation, header hierarchy, queue layout, and drawer behavior across all six workflow routes. Keep route links addressable and support browser Back and Forward.
- Serve local stage links at the origin root on port 5173. The Intake entry is `http://localhost:5173/?stage=Intake`; do not prefix stage or API links with `/dev`.
- Display the main heading in uppercase on each of the six workflow pages.
- Put Sign out in the top-bar user account menu and omit the environment badge.
- Display workflow timestamps in `America/New_York` with the applicable EST or EDT abbreviation.
- Keep source originals server-side and display document details and cited excerpts in the workspace.
- In Intake and Medical Triage, lead each evidence review with a short paragraph, key points and known gaps. Keep cited source text available on demand in a keyboard-scrollable reader; long document lists must not push the workflow actions down the page.
- Use workflow language in the interface and omit environment labels. State unavailable external actions clearly and retain the upload confirmation for claimant records.

- Keep the desktop workflow rail compact and icon-only with stage labels on hover and keyboard focus. Keep the labels visible in the mobile drawer. Show AI features with generic labels and keep provider names, deployment details, and credentials on the backend.
