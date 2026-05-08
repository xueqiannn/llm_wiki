from __future__ import annotations

import json
import os
from typing import Any

from openai import AzureOpenAI, OpenAI

from .config import openai_model


def _client_and_model() -> tuple[AzureOpenAI | OpenAI, str]:
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        return client, os.environ["AZURE_OPENAI_DEPLOYMENT"]

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )
    return client, openai_model()


def chat(messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
    client, model = _client_and_model()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
