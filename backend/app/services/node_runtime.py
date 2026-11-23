import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.core.config import BACKEND_ROOT

MINIMUM_NODE_MAJOR = 24


def development_candidates():
    project = BACKEND_ROOT.parent / "frontend"
    yield project / "node_modules" / ".bin" / "node"
    nvm_root = Path(os.getenv("NVM_DIR") or Path.home() / ".nvm").expanduser()
    yield from sorted((nvm_root / "versions" / "node").glob("*/bin/node"), reverse=True)
    npm_cache = Path(os.getenv("npm_config_cache") or os.getenv("NPM_CONFIG_CACHE") or Path.home() / ".npm").expanduser()
    yield from sorted((npm_cache / "_npx").glob("*/node_modules/node/bin/node"))


def supports_workflow(binary: str) -> bool:
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    number = re.fullmatch(r"v(\d+)\.\d+\.\d+[^\s]*", version.stdout.strip())
    return bool(number and int(number[1]) >= MINIMUM_NODE_MAJOR)


@lru_cache(maxsize=4)
def resolve_node_binary(override: str, *, development: bool) -> str:
    override = override.strip()
    if override and override != "node":
        binary = shutil.which(os.path.expanduser(override))
        if binary and supports_workflow(binary):
            return os.path.abspath(binary)
        raise HTTPException(503, "NODE_BINARY must point to an executable Node.js 24 or newer.")

    for directory in os.get_exec_path():
        binary = shutil.which("node", path=directory)
        if binary and supports_workflow(binary):
            return os.path.abspath(binary)
    if development:
        for candidate in development_candidates():
            binary = shutil.which(str(candidate))
            if binary and supports_workflow(binary):
                return os.path.abspath(binary)
    raise HTTPException(503, "Install Node.js 24 or newer and add it to the server PATH, or set NODE_BINARY.")
