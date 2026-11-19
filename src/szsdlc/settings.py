"""User and project settings, read from where Claude Code keeps its own.

szsdlc has settings that are not part of a project's model — where a project's
files are kept, for one — and those belong with the rest of a user's tooling
preferences rather than in `.szsdlc/config.yml`, which is the project's shape
and is read by CI. So they live in the `env` block of Claude Code's settings
files, under keys prefixed `SZSDLC_`:

    ~/.claude/settings.json                 user-wide
    <project>/.claude/settings.json         per project, committed
    <project>/.claude/settings.local.json   per project, personal

Later layers override earlier ones **key by key**, which is the precedence
Claude Code applies itself. Inside a Claude Code session the merged result is
already in the process environment; reading the files directly as well is what
makes the plain CLI, run from a terminal, see the same values. A variable set
in the process environment wins over every file, so a one-off override is
`SZSDLC_BACKEND=workdir szsdlc ...`.

`CLAUDE_CONFIG_DIR` relocates the user layer, as it does for Claude Code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

PREFIX = "SZSDLC_"

#: The process-environment layer, named as a source.
ENVIRONMENT = "environment"

TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Setting:
    """One resolved value, with where it came from.

    `base` is the directory a relative path in this value is resolved against:
    the project for a project file, the home directory for the user file, and
    the project or working directory for the environment.
    """

    key: str
    value: str
    source: str
    base: Path

    def as_bool(self) -> bool:
        lowered = self.value.strip().lower()
        if lowered in TRUTHY:
            return True
        if lowered in FALSEY:
            return False
        raise ConfigError(f"{self.key}={self.value!r} (from {self.source}) is not a boolean.",
                          fix=f"set {self.key} to true or false")

    def as_path(self) -> Path:
        path = Path(os.path.expanduser(self.value))
        return path if path.is_absolute() else (self.base / path)


def claude_config_dir() -> Path:
    """Claude Code's user directory: `$CLAUDE_CONFIG_DIR`, else `~/.claude`."""
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def user_settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def project_settings_paths(project: Path) -> list[Path]:
    return [project / ".claude" / "settings.json",
            project / ".claude" / "settings.local.json"]


def project_dir(start: Path | str | None = None) -> Path | None:
    """The directory whose `.claude/` holds project settings for `start`.

    `$CLAUDE_PROJECT_DIR` when Claude Code provides it — the hooks and every
    command Claude runs see it — and otherwise the nearest ancestor of `start`
    with a `.claude/settings*.json`, never counting the user directory itself.
    """
    explicit = os.environ.get("CLAUDE_PROJECT_DIR")
    if explicit:
        return Path(explicit).resolve()

    here = Path(start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    user_dir = _resolved(claude_config_dir())
    for candidate in (here, *here.parents):
        if _resolved(candidate / ".claude") == user_dir:
            continue
        if any(p.is_file() for p in project_settings_paths(candidate)):
            return candidate
    return None


def load(start: Path | str | None = None) -> dict[str, Setting]:
    """Every `SZSDLC_*` setting in effect for `start`, last layer winning."""
    resolved: dict[str, Setting] = {}

    layers: list[tuple[Path, Path]] = [(user_settings_path(), Path.home())]
    project = project_dir(start)
    if project is not None:
        layers += [(path, project) for path in project_settings_paths(project)]

    for path, base in layers:
        for key, value in _env_block(path).items():
            resolved[key] = Setting(key, value, str(path), base)

    env_base = project or Path(start or Path.cwd()).resolve()
    for key, value in os.environ.items():
        if key.startswith(PREFIX) and value != "":
            resolved[key] = Setting(key, value, ENVIRONMENT, env_base)
    return resolved


def _env_block(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path}: cannot be read as JSON ({exc}).",
                          fix=f"correct the JSON in {path}") from None
    env = document.get("env") if isinstance(document, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(k): _stringify(v) for k, v in env.items()
            if str(k).startswith(PREFIX) and v is not None}


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover
        return path
