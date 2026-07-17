"""Gateway settings (CONTRACT.md §8), loaded from env + the repo-root .env.

The .env lives at the REPO ROOT (rendered by `make secrets`), one level above
gateway/ — resolved from this file's location so it works regardless of cwd.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_file() -> Path:
    """Repo-root .env; fall back to cwd/.env if the repo-root one is absent."""
    root_env = _REPO_ROOT / ".env"
    if root_env.exists():
        return root_env
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    return root_env  # pydantic-settings tolerates a missing env_file


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_file(), env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    cma_agent_id: str | None = None
    cma_agent_version: int | None = None
    cma_environment_id: str | None = None
    cma_memory_store_id: str | None = None
    cma_workspace_id: str | None = None

    tp_db_path: str = "./data/gateway.db"
    tp_default_engine: str = "mock"
    tp_judge_stub: str | None = None  # "1" force stub / "0" force real; unset = auto
    # auto: stub for MOCK runs always (mock must never spend money), and for cma
    # when OPENAI_API_KEY is absent
    tp_mock_delay_ms: int = 800

    @field_validator("cma_agent_version", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        # .env lines like `CMA_AGENT_VERSION=` (written blank by setup.sh) must
        # not crash the int parse
        return None if v == "" else v

    def judge_stub_for(self, engine: str) -> bool:
        if self.tp_judge_stub is not None and self.tp_judge_stub.strip() != "":
            return self.tp_judge_stub.strip() == "1"
        # MOCK runs (both scenarios) always stub — mock must never spend money (§5)
        return engine in ("mock", "mock-long") or not self.openai_api_key

    @property
    def db_path(self) -> Path:
        p = Path(self.tp_db_path)
        return p if p.is_absolute() else _REPO_ROOT / p

    @property
    def cma_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.cma_agent_id and self.cma_environment_id)
