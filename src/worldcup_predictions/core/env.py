"""Project environment loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_LOADED_ENV_FILES: set[Path] = set()


def load_project_env(project_root: Path) -> None:
    """Load project `.env` values into `os.environ` without overriding existing env."""

    env_path = (Path(project_root) / ".env").resolve()
    if env_path in _LOADED_ENV_FILES:
        return
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    _LOADED_ENV_FILES.add(env_path)


def env_value(project_root: Path, name: str) -> str | None:
    """Return an environment value after loading the project `.env` file."""

    load_project_env(project_root)
    return os.environ.get(name) or None
