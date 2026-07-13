"""Versioned prompt artifacts for the resume judge.

Layout (under judge/prompts/):
    ACTIVE_VERSION                    single line naming the active version dir
    <version>/manifest.json           provenance metadata
    <version>/grounding_judge_prompt.md
    <version>/severity_calibration.md
    <version>/rubric_judge_prompt.md

Versions are immutable once exported; switching ACTIVE_VERSION is a
deliberate, reviewed git change. The GEPA harness (evals/) writes new
versions via evals/gepa/export.py.
"""

import json
from pathlib import Path
from typing import Any, Optional

from tp_gateway.judge.schemas import JudgePrompts

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_COMPONENT_FILES = (
    "grounding_judge_prompt",
    "severity_calibration",
    "rubric_judge_prompt",
)


def active_version(prompts_dir: Optional[Path] = None) -> str:
    base = prompts_dir or PROMPTS_DIR
    return (base / "ACTIVE_VERSION").read_text(encoding="utf-8").strip()


def list_versions(prompts_dir: Optional[Path] = None) -> list[str]:
    base = prompts_dir or PROMPTS_DIR
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def load_manifest(version: str, prompts_dir: Optional[Path] = None) -> dict[str, Any]:
    base = prompts_dir or PROMPTS_DIR
    manifest: dict[str, Any] = json.loads((base / version / "manifest.json").read_text(encoding="utf-8"))
    return manifest


def load_judge_prompts(version: Optional[str] = None, prompts_dir: Optional[Path] = None) -> JudgePrompts:
    """Load a prompt version (the ACTIVE_VERSION when version is None)."""
    base = prompts_dir or PROMPTS_DIR
    resolved = version or active_version(base)
    version_dir = base / resolved
    if not version_dir.is_dir():
        raise FileNotFoundError(f"judge prompt version not found: {version_dir}")
    components = {name: (version_dir / f"{name}.md").read_text(encoding="utf-8") for name in _COMPONENT_FILES}
    return JudgePrompts(version=resolved, **components)
