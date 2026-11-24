from functools import lru_cache
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import ReferenceFile


EXAM_ROOT = PurePosixPath("ExamManagement-4.7rc3.iepd")
EXCHANGE_ROOT = EXAM_ROOT / "XMLsamples/4.7"
EXCHANGE_SCHEMA = EXAM_ROOT / "XMLschemas/exchange/ExamManagement-4.7.xsd"
FORM_ROOT = PurePosixPath("dbq-cmt-iepd-11.0-Beta4")
FORM_INDEX = FORM_ROOT / "IEPD_Metadata.json"


@lru_cache(maxsize=1)
def reference_paths() -> tuple[PurePosixPath, ...]:
    try:
        with SessionLocal() as db:
            paths = db.scalars(select(ReferenceFile.path)).all()
        return tuple(sorted(PurePosixPath(path) for path in paths))
    except Exception:
        return ()


def reference_bytes(path: PurePosixPath) -> bytes:
    with SessionLocal() as db:
        content = db.scalar(select(ReferenceFile.content).where(ReferenceFile.path == str(path)))
    if content is None:
        raise HTTPException(503, f"The workspace reference library is missing {path.name}.")
    return content
