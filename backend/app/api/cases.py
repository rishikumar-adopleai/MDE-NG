from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.workspace import workspace_reader
from app.core.database import get_db
from app.core.security import CurrentUser
from app.services.workspace_store import get_state

router = APIRouter(tags=["Cases"])


@router.get("/cases")
def list_cases(query: str = "", stage: str | None = None, status: str | None = None,
               db: Session = Depends(get_db), _user: CurrentUser = Depends(workspace_reader)) -> dict[str, object]:
    state = get_state(db)
    cases = list(state.get("esrsById", {}).values())
    if query:
        lowered = query.casefold()
        cases = [item for item in cases if lowered in str(item).casefold()]
    if stage:
        case_ids = {item["esrId"] for item in state["workItemsById"].values()
                    if item.get("stage") == stage and item.get("status") not in {"completed", "cancelled"}}
        cases = [item for item in cases if item.get("id") in case_ids]
    if status:
        cases = [item for item in cases if item.get("intakeStatus") == status]
    return {"items": cases, "count": len(cases), "filters": {"stage": stage}}


@router.get("/cases/{esr_id}")
def get_case(esr_id: str, db: Session = Depends(get_db), _user: CurrentUser = Depends(workspace_reader)) -> dict[str, object]:
    state = get_state(db)
    esr = state.get("esrsById", {}).get(esr_id)
    if not esr:
        raise HTTPException(status_code=404, detail="Case not found.")
    return {"esr": esr, "veteran": state.get("veteransById", {}).get(esr.get("veteranId")),
            "contentions": [state.get("contentionsById", {}).get(item_id) for item_id in esr.get("contentionIds", [])],
            "workItems": [item for item in state.get("workItemsById", {}).values() if item.get("esrId") == esr_id]}
