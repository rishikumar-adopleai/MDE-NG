# MDE-NG Frontend

Full-screen, responsive React, TypeScript, and Vite frontend for the six MDE-NG examination lifecycle workflows. The sidebar stays in place while the workspace scrolls. Stage content follows `z-project-details/VA_MDE_Original_Workflow_Clearer_Text.docx`. It uses sample data, has no VA integration, and does not make clinical or benefits decisions.

## Run locally

Start FastAPI first using the [backend setup](../backend/README.md). Open the [Intake workspace](http://localhost:5173/?stage=Intake). Vite forwards `/api/*` unchanged to `http://127.0.0.1:9090`. Set `MDE_API_TARGET` to use another backend address. The backend supplies the mode and workspace data.

```sh
npm ci
npm run dev
```

## Build

```sh
npm run build
npm exec tsc -- --noEmit
```

The build output is prepared for the Product Design Sites starter contract. Serve the frontend at `/` and forward `/api/*` unchanged to FastAPI.
