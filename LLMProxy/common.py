"""Shared utilities for LLMProxy RCA and auto-healing scripts."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMConfig:
    proxy_url: str
    model: str
    api_key: str


def load_env_file(env_path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env if present.

    Existing environment variables are preserved.
    """
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_llm_config() -> LLMConfig:
    load_env_file()

    proxy_url = os.getenv("LLM_PROXY_URL", "").strip().rstrip("/")
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_PROXY_KEY", "").strip()

    if not proxy_url:
        raise ValueError("Missing LLM_PROXY_URL in environment or .env")
    if not model:
        raise ValueError("Missing LLM_MODEL in environment or .env")
    if not api_key:
        raise ValueError("Missing LLM_PROXY_KEY in environment or .env")

    return LLMConfig(proxy_url=proxy_url, model=model, api_key=api_key)


def post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: int = 60) -> Dict[str, Any]:
    merged_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        merged_headers.update(headers)

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, data=data, method="POST", headers=merged_headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} on {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error on {url}: {exc.reason}") from exc


def call_llm_json(system_prompt: str, user_prompt: str, temperature: float = 0.0) -> Dict[str, Any]:
    config = load_llm_config()
    url = f"{config.proxy_url}/v1/chat/completions"

    payload = {
        "model": config.model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {config.api_key}"}

    response = post_json(url, payload, headers=headers, timeout=90)
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("LLM response did not include choices")

    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("LLM response content is empty")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {content[:300]}") from exc


def load_jira_config() -> Dict[str, str]:
    load_env_file()
    base = os.getenv("JIRA_URL", "").strip().rstrip("/")
    token = os.getenv("JIRA_TOKEN", "").strip()
    project_key = os.getenv("PROJECT_KEY", "").strip()

    return {
        "base": base,
        "token": token,
        "project_key": project_key,
    }


def jira_create_bug(summary: str, description: str, labels: list[str], priority: str = "Medium") -> Dict[str, Any]:
    cfg = load_jira_config()
    if not cfg["base"] or not cfg["token"] or not cfg["project_key"]:
        raise RuntimeError("Missing JIRA_URL/JIRA_TOKEN/PROJECT_KEY for Jira bug creation")

    url = f"{cfg['base']}/rest/api/2/issue"
    payload = {
        "fields": {
            "project": {"key": cfg["project_key"]},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Bug"},
            "labels": labels,
            "priority": {"name": priority},
        }
    }

    response = post_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {cfg['token']}"},
        timeout=60,
    )
    return response


def jira_add_label(issue_key: str, label: str) -> None:
    cfg = load_jira_config()
    if not cfg["base"] or not cfg["token"]:
        raise RuntimeError("Missing JIRA_URL/JIRA_TOKEN for Jira label update")

    url = f"{cfg['base']}/rest/api/2/issue/{issue_key}"
    payload = {
        "update": {
            "labels": [
                {"add": label},
            ]
        }
    }
    post_json(url, payload, headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=60)


def jira_add_comment(issue_key: str, comment: str) -> None:
    cfg = load_jira_config()
    if not cfg["base"] or not cfg["token"]:
        raise RuntimeError("Missing JIRA_URL/JIRA_TOKEN for Jira comments")

    url = f"{cfg['base']}/rest/api/2/issue/{issue_key}/comment"
    payload = {"body": comment}
    post_json(url, payload, headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=60)
