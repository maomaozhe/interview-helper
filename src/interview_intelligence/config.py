"""Runtime configuration and local path boundaries."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "sqlite:///./data/interview_intelligence.db"
    elasticsearch_url: str | None = None
    model_api_key: str | None = None
    model_base_url: str | None = None
    extraction_model: str | None = "ark-code-latest"
    embedding_model: str | None = "doubao-embedding-vision"
    judge_model: str | None = "ark-code-latest"
    reranker_model: str | None = "ark-code-latest"
    corpus_root: Path = Path("./md")
    local_user_id: str = "local"
    app_signing_key: str | None = None
    embedding_dimension: int | None = 1024
    max_model_calls: int | None = None
    max_model_tokens: int | None = None
    model_min_interval_seconds: float = 2.0
    model_request_timeout_seconds: float = Field(default=180, gt=0)
    extraction_stream: bool = True
    model_lock_path: Path = Path("data/model-call.lock")
    api_root_path: str = ""


def resolve_corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    candidate_path = Path(relative_path)
    if candidate_path.is_absolute():
        raise ValueError("absolute corpus path is not allowed")
    candidate = (root / candidate_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("path escapes corpus root")
    return candidate


def validate_model_preflight(
    *, api_key: str | None, embedding_dimension: int | None,
    max_calls: int | None, max_tokens: int | None,
) -> None:
    if not api_key:
        raise ValueError("model API key is missing")
    if embedding_dimension is None or embedding_dimension <= 0:
        raise ValueError("embedding dimension must be positive")
    if max_calls is None or max_calls <= 0:
        raise ValueError("maximum model calls must be positive")
    if max_tokens is None or max_tokens <= 0:
        raise ValueError("maximum model tokens must be positive")


def validate_backend_model_endpoint(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("MODEL_BASE_URL_INVALID")


def load_settings() -> Settings:
    return Settings(_env_file=".env")
