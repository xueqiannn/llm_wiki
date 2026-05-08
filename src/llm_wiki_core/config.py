from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


@dataclass(frozen=True)
class Paths:
    root: Path
    raw_sources: Path
    wiki: Path


def get_paths() -> Paths:
    root = Path(os.getenv("LLM_WIKI_HOME", "workspace")).expanduser().resolve()
    return Paths(
        root=root,
        raw_sources=root / "raw" / "sources",
        wiki=root / "wiki",
    )


def openai_model() -> str:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def has_llm_config() -> bool:
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_azure = bool(
        os.getenv("AZURE_OPENAI_API_KEY")
        and os.getenv("AZURE_OPENAI_ENDPOINT")
        and os.getenv("AZURE_OPENAI_DEPLOYMENT")
    )
    return has_openai or has_azure
