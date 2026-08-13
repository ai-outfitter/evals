"""Pi CLI command construction and JSON event-stream parsing.

Ported from the notes-vault ollama-version-harness. The HarnessLoader keeps
YAML tokens like `off` as strings, which is load-bearing for `thinking: off`.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import yaml
from jsonschema.validators import validator_for

JsonDict = dict[str, Any]


class EvalsLoader(yaml.SafeLoader):
    """YAML loader that keeps tokens like `off` as strings."""


EvalsLoader.yaml_implicit_resolvers = deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for _first_char, _resolvers in list(EvalsLoader.yaml_implicit_resolvers.items()):
    EvalsLoader.yaml_implicit_resolvers[_first_char] = [
        entry for entry in _resolvers if entry[0] != "tag:yaml.org,2002:bool"
    ]
EvalsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=EvalsLoader)


def build_pi_command(
    *,
    provider: str,
    model: str,
    thinking: str,
    prompt: str,
    no_tools: bool = False,
    system_prompt: str | None = None,
    append_system_prompts: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        "pi",
        "--provider",
        provider,
        "--model",
        model,
        "--mode",
        "json",
        "--print",
        "--no-session",
        "--thinking",
        thinking,
    ]
    if no_tools:
        command.append("--no-tools")
    if system_prompt:
        command.extend(["--system-prompt", system_prompt])
    for item in append_system_prompts or []:
        command.extend(["--append-system-prompt", item])
    command.extend(extra_args or [])
    command.append(prompt)
    return command


def parse_pi_output(stdout: str) -> JsonDict:
    events: list[JsonDict] = []
    parse_errors: list[str] = []

    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"line {line_number}: {exc.msg}")
            continue
        if isinstance(payload, dict):
            events.append(payload)
        else:
            parse_errors.append(f"line {line_number}: expected object, got {type(payload).__name__}")

    agent_end = next((event for event in reversed(events) if event.get("type") == "agent_end"), None)
    messages = []
    if isinstance(agent_end, dict):
        maybe_messages = agent_end.get("messages", [])
        if isinstance(maybe_messages, list):
            messages = maybe_messages

    chat_history = [normalize_message(message) for message in messages if isinstance(message, dict)]
    assistant_message = next(
        (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        None,
    )
    usage_raw = assistant_message.get("usage", {}) if isinstance(assistant_message, dict) else {}
    usage = {
        "input": int(usage_raw.get("input", 0) or 0),
        "output": int(usage_raw.get("output", 0) or 0),
        "cache_read": int(usage_raw.get("cacheRead", 0) or 0),
        "cache_write": int(usage_raw.get("cacheWrite", 0) or 0),
        "total_tokens": int(usage_raw.get("totalTokens", 0) or 0),
    }
    response_models = sorted(
        {
            str(m.get("responseModel"))
            for m in messages
            if isinstance(m, dict) and isinstance(m.get("responseModel"), str)
        }
    )
    stop_reason = assistant_message.get("stopReason") if isinstance(assistant_message, dict) else None

    return {
        "usage": usage,
        "chat_history": chat_history,
        "response_models": response_models,
        "stop_reason": stop_reason,
        "parse_errors": parse_errors,
    }


def normalize_message(message: JsonDict) -> JsonDict:
    text_parts: list[str] = []
    thinking_parts: list[str] = []

    content = message.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
            if item.get("type") == "thinking" and isinstance(item.get("thinking"), str):
                thinking_parts.append(item["thinking"])

    normalized: JsonDict = {"role": message.get("role", "unknown"), "text": "".join(text_parts)}
    if thinking_parts:
        normalized["thinking"] = "".join(thinking_parts)
    return normalized


def final_assistant_text(chat_history: list[JsonDict]) -> str:
    return next(
        (str(m.get("text", "")) for m in reversed(chat_history) if m.get("role") == "assistant"),
        "",
    ).strip()


def validate_json_schema_output(chat_history: list[JsonDict], schema: JsonDict) -> JsonDict:
    assistant_text = final_assistant_text(chat_history)
    if not assistant_text:
        return {"valid": False, "error": "assistant response was empty"}

    json_text = unwrap_json_fence(assistant_text)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return {"valid": False, "error": f"response was not valid JSON: {exc.msg}"}

    validator = validator_for(schema)(schema)
    error = next(validator.iter_errors(parsed), None)
    if error is not None:
        return {
            "valid": False,
            "error": error.message,
            "path": [str(part) for part in error.absolute_path],
            "parsed": parsed,
        }
    return {"valid": True, "parsed": parsed}


def unwrap_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    # Some models append prose after the fence; fall back to the first fenced block.
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower() or "x"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
