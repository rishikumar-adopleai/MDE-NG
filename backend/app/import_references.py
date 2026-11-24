"""One-time import of the local reference package into the development workspace DB."""

import argparse
import json
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from sqlalchemy import Engine, select

from app.models import ReferenceFile
from app.services.reference_store import EXCHANGE_ROOT, EXCHANGE_SCHEMA, FORM_INDEX, FORM_ROOT


def collect_files(directory: Path) -> dict[str, bytes]:
    root = directory.resolve(strict=True)

    def read(path: Path) -> bytes:
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"Reference outside the package: {path.name}")
        return path.read_bytes()

    schema = read(root / EXCHANGE_SCHEMA)
    if not ElementTree.fromstring(schema).attrib.get("targetNamespace"):
        raise ValueError("Missing Exam Management namespace")
    index = read(root / FORM_INDEX)
    documents = json.loads(index.decode("utf-8-sig"))["documents"]
    requests = list((root / EXCHANGE_ROOT).rglob("*RequestCreatedEvent*.xml"))
    if not documents or not requests:
        raise ValueError("The package must include DBQ definitions and examination requests")
    files = {str(EXCHANGE_SCHEMA): schema, str(FORM_INDEX): index}
    for row in documents:
        for field in ("documentStructureJson", "conditionalLogic"):
            relative = PurePosixPath(row[field])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid catalog reference: {relative}")
            key = str(FORM_ROOT / relative)
            if key not in files:
                content = read(root / key)
                json.loads(content.decode("utf-8-sig"))
                files[key] = content
    for folder in {path.parent for path in requests}:
        for path in folder.rglob("*.xml"):
            files[path.relative_to(root).as_posix()] = read(path)
    return files


def import_library(database: Engine, directory: Path) -> int:
    if database.dialect.name != "sqlite":
        raise ValueError("This import targets a local SQLite workspace only")
    files = collect_files(directory)
    table = ReferenceFile.__table__
    with database.begin() as connection:
        table.create(connection, checkfirst=True)
        existing = dict(connection.execute(select(table.c.path, table.c.content)).all())
        if existing and existing != files:
            raise ValueError("A different reference library is already stored; no files were replaced")
        if not existing:
            connection.execute(table.insert(), [{"path": path, "content": content} for path, content in files.items()])
    return len(files)


if __name__ == "__main__":
    from app.core.config import settings
    from app.core.database import engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if settings.app_env != "dev":
        parser.error("Use a development workspace")
    print(f"Verified/imported {import_library(engine, args.directory)} reference files in SQLite.")
