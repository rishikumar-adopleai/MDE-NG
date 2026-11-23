import json
import logging
import os
import re
import subprocess
from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import BACKEND_ROOT, settings
from app.models import WorkspaceSnapshot
from app.services.node_runtime import resolve_node_binary

WORKSPACE_ID = "mde-ng-demo-workspace" if settings.app_env == "dev" else "mde-ng-production-workspace"
WORKFLOW_SCRIPT = BACKEND_ROOT.parent / "frontend" / "server" / "workspace.mjs"
LEGACY_SNAPSHOT_ID = "mde-ng-legacy-generated-records"
SAMPLE_SOURCE_MODE = "exam-management-bundle"
logger = logging.getLogger(__name__)
WORKSPACE_MAPS = """
veteransById esrsById contentionsById examinationRequirementsById examinationsById
appointmentsById examinersById facilitiesById dbqsById dbqVersionsById diagnosticOrdersById
diagnosticResultsById evidenceDocumentsById intakeExceptionsById communicationsById
outreachAttemptsById qaReviewsById qaFindingsById correctionRequestsById resultsPackagesById
deliveryEventsById aiInsightsById workItemsById activitiesById
""".split()


def empty_workspace() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        **{key: {} for key in WORKSPACE_MAPS},
        "workspaceId": WORKSPACE_ID, "schemaVersion": 2, "revision": 0,
        "initializedAt": now, "updatedAt": now,
    }


def run_engine(payload: dict | None = None, *, bootstrap: bool = False) -> dict:
    binary = resolve_node_binary(settings.node_binary, development=settings.app_env == "dev")
    command = [binary, "--disable-warning=ExperimentalWarning", str(WORKFLOW_SCRIPT)]
    if payload is None:
        command.append("--seed")
    elif bootstrap:
        command.append("--bootstrap")
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("NODE_CHANNEL")}
    try:
        result = subprocess.run(
            command, input=json.dumps(payload), capture_output=True, text=True,
            timeout=30, check=True, env=clean_env,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if ("bad option: --disable-warning" in stderr or "ERR_UNKNOWN_FILE_EXTENSION" in stderr
                or "ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING" in stderr):
            logger.error("Workspace engine requires Node.js 24. Check NODE_BINARY or the server PATH.")
            raise HTTPException(503, "The workspace engine requires Node.js 24. Set NODE_BINARY to its executable.") from exc
        logger.error("Workspace engine exited with code %s. Stderr: %s", exc.returncode, exc.stderr)
        raise HTTPException(503, "The local workflow engine is unavailable.") from exc
    except FileNotFoundError as exc:
        resolve_node_binary.cache_clear()
        logger.error("The workspace engine could not find the resolved Node.js executable.")
        raise HTTPException(503, "Node.js became unavailable. Retry the request or check the server PATH.") from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.error("Workspace engine failed (%s).", type(exc).__name__)
        raise HTTPException(503, "The local workflow engine is unavailable.") from exc


def get_snapshot(db: Session) -> WorkspaceSnapshot | None:
    return db.get(WorkspaceSnapshot, WORKSPACE_ID)


def source_workspace(state: dict) -> dict:
    from app.services.intake_examples import is_legacy_example

    esrs = {
        key: esr for key, esr in state["esrsById"].items()
        if esr.get("examManagement") or (
            not re.fullmatch(r"esr-2026-\d{5}", key)
            and esr.get("intakeOrigin") != "example" and not is_legacy_example(state, esr)
        )
    }
    kept = {key: value for key, value in state.items() if key not in WORKSPACE_MAPS}
    kept.update({key: {row_id: row for row_id, row in state[key].items() if row.get("esrId") in esrs}
                 for key in WORKSPACE_MAPS})
    veteran_ids = {row["veteranId"] for row in esrs.values()}
    kept.update(
        esrsById=esrs, workspaceDataSource=SAMPLE_SOURCE_MODE, loadedExamRequestScenarios=[],
        veteransById={key: row for key, row in state["veteransById"].items() if key in veteran_ids},
        dbqVersionsById={key: row for key, row in state["dbqVersionsById"].items() if row.get("dbqId") in kept["dbqsById"]},
        examinersById={key: row for key, row in state["examinersById"].items() if not re.fullmatch(r"exam-00[1-8]", key)},
        facilitiesById={key: row for key, row in state["facilitiesById"].items()
                        if key not in {"fac-cle", "fac-col", "fac-pit", "fac-det", "fac-ind", "fac-chi"}},
    )
    return kept


def get_state(db: Session, *, backfill_requests: bool = False, load_practice: bool = False) -> dict:
    snapshot = get_snapshot(db)
    if snapshot:
        state = snapshot.state or empty_workspace()
        if backfill_requests and settings.app_env == "dev":
            from app.services.va_samples import add_bundled_requests, pending_scenarios
            from app.services.va_practice import PRACTICE_COHORT, add_practice_workflow

            needs_migration = state.get("workspaceDataSource") != SAMPLE_SOURCE_MODE
            next_state = source_workspace(state) if needs_migration else state
            scenarios = pending_scenarios(next_state)
            needs_practice = load_practice and next_state.get("practiceCohort") != PRACTICE_COHORT
            if scenarios or needs_migration or needs_practice:
                revision = snapshot.revision
                next_state = deepcopy(next_state)
                add_bundled_requests(db, next_state, scenarios)
                if needs_practice:
                    next_state = add_practice_workflow(next_state)
                if next_state == state:
                    return state
                next_state.update(revision=revision + 1, updatedAt=datetime.now(UTC).isoformat())
                try:
                    save_next_state(db, revision, next_state)
                    if needs_migration and db.get(WorkspaceSnapshot, LEGACY_SNAPSHOT_ID) is None:
                        db.add(WorkspaceSnapshot(id=LEGACY_SNAPSHOT_ID, revision=revision, state=state))
                    db.commit()
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    return get_state(db)
                return next_state
        return state
    if settings.app_env == "prod":
        return empty_workspace()

    from app.services.va_samples import add_bundled_requests, pending_scenarios

    fixture = empty_workspace()
    fixture["workspaceDataSource"] = SAMPLE_SOURCE_MODE
    add_bundled_requests(db, fixture, pending_scenarios(fixture))
    if load_practice:
        from app.services.va_practice import add_practice_workflow

        fixture = add_practice_workflow(fixture)
        fixture["revision"] = 0
    db.add(WorkspaceSnapshot(id=WORKSPACE_ID, revision=0, state=fixture))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = get_snapshot(db)
        if winner is None:
            raise
        return winner.state
    return fixture


def save_next_state(db: Session, revision: int, next_state: dict) -> None:
    saved = db.execute(
        update(WorkspaceSnapshot)
        .where(WorkspaceSnapshot.id == WORKSPACE_ID, WorkspaceSnapshot.revision == revision)
        .values(revision=revision + 1, state=next_state)
    )
    if saved.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "The workspace changed. Review the latest records and retry.")
