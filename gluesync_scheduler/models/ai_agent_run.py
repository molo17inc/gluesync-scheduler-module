#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import json
import re
from typing import Any, Dict, Iterable, List, Optional

TOKEN_RE = re.compile(
    r"\{\{\s*([a-zA-Z]\w*(?:\.[a-zA-Z]\w*)*)\s*\}\}",
    re.ASCII,
)
MAX_FIRE_BODY_BYTES = 64 * 1024
AI_RUN_EVENT_PREFIX = "AI_RUN_"
AI_RUN_CLOUD_PREFIX = "gluesync.ai.run."
CHRONOS_CORRELATION_PREFIX = "chronos:"
AI_AGENT_RUN_SOURCE = "chronos"

_UNSAFE_PATH_MARKERS = ("..", "__")


def is_safe_payload_path(path: str) -> bool:
    if not path or not isinstance(path, str):
        return False
    stripped = path.strip()
    if not stripped or any(marker in stripped for marker in _UNSAFE_PATH_MARKERS):
        return False
    parts = stripped.split(".")
    return all(part.isidentifier() and not part.startswith("_") for part in parts)


def template_tokens(template: Optional[str]) -> List[str]:
    if not template:
        return []
    return TOKEN_RE.findall(str(template))


def validate_allow_list(paths: Optional[Iterable[str]]) -> List[str]:
    allow_list = []
    for path in paths or []:
        if not is_safe_payload_path(path):
            raise ValueError(f"payload_allow_list contains an unsafe path: {path}")
        allow_list.append(path.strip())
    return allow_list


def require_ai_agent_run_fields(
    task_type,
    agent_alias,
    prompt_template=None,
    payload_allow_list=None,
    agent_input=None,
    idempotency_key=None,
):
    """Validate AI_AGENT_RUN fields. No-op for other task types."""
    from gluesync_scheduler.models.models import TaskType, stripped_or_none

    if task_type != TaskType.AI_AGENT_RUN:
        return
    if not stripped_or_none(agent_alias):
        raise ValueError("ai_agent_run requires a non-empty agent_alias")

    allow_list = _coerce_allow_list(payload_allow_list)
    validate_allow_list(allow_list)
    allow_set = set(allow_list)

    templates = [prompt_template or ""]
    if isinstance(idempotency_key, str):
        templates.append(idempotency_key)
    templates.extend(_string_leaves(agent_input))

    for template in templates:
        for token in template_tokens(template):
            if token not in allow_set:
                raise ValueError(
                    f"template token '{{{{{token}}}}}' is not in payload_allow_list"
                )


def parse_capped_json_body(raw: bytes) -> Dict[str, Any]:
    if raw is None:
        return {}
    if len(raw) > MAX_FIRE_BODY_BYTES:
        raise ValueError("request body exceeds the AI trigger payload size limit")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be valid JSON") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a JSON object")
    return parsed


def extract_scalar(payload: Any, path: str) -> Optional[Any]:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, bool):
        return current
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return current
    if isinstance(current, str):
        return current
    return None


def allow_listed_fields(payload: Optional[Dict[str, Any]], allow_list: Iterable[str]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    source = payload or {}
    for path in allow_list:
        value = extract_scalar(source, path)
        if value is None:
            continue
        fields[path.split(".")[-1]] = value
    return fields


def substitute_template(template: Optional[str], fields_by_path: Dict[str, Any]) -> str:
    if not template:
        return ""

    def _replace(match: re.Match) -> str:
        path = match.group(1)
        value = fields_by_path.get(path)
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return TOKEN_RE.sub(_replace, str(template))


def interpolate_value(value: Any, fields_by_path: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return substitute_template(value, fields_by_path)
    if isinstance(value, list):
        return [interpolate_value(item, fields_by_path) for item in value]
    if isinstance(value, dict):
        return {key: interpolate_value(item, fields_by_path) for key, item in value.items()}
    return value


def path_values(payload: Optional[Dict[str, Any]], allow_list: Iterable[str]) -> Dict[str, Any]:
    source = payload or {}
    return {
        path: extract_scalar(source, path)
        for path in allow_list
        if extract_scalar(source, path) is not None
    }


def build_run_input(
    prompt_template: Optional[str],
    payload_allow_list: Optional[Iterable[str]],
    agent_input: Optional[Dict[str, Any]],
    event_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    allow_list = _coerce_allow_list(payload_allow_list)
    by_path = path_values(event_payload, allow_list)
    fields = allow_listed_fields(event_payload, allow_list)
    interpolated_input = interpolate_value(agent_input or {}, by_path)
    if not isinstance(interpolated_input, dict):
        interpolated_input = {}
    prompt = substitute_template(prompt_template, by_path)
    return {
        **interpolated_input,
        "prompt": prompt,
        "fields": fields,
        "source": AI_AGENT_RUN_SOURCE,
    }


def interpolate_idempotency_key(
    template: Optional[str],
    payload_allow_list: Optional[Iterable[str]],
    event_payload: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not template:
        return None
    allow_list = _coerce_allow_list(payload_allow_list)
    by_path = path_values(event_payload, allow_list)
    rendered = substitute_template(template, by_path).strip()
    return rendered or None


def platform_event_name(payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return ""
    event_type = payload.get("eventType") or payload.get("type") or ""
    text = str(event_type).strip()
    if text.startswith(AI_RUN_CLOUD_PREFIX):
        suffix = text[len(AI_RUN_CLOUD_PREFIX):].replace("-", "_").replace(".", "_")
        return f"{AI_RUN_EVENT_PREFIX}{suffix.upper()}"
    return text.upper().replace("-", "_")


def is_ai_run_platform_event(payload: Optional[Dict[str, Any]]) -> bool:
    name = platform_event_name(payload)
    raw_type = str((payload or {}).get("type") or "")
    return name.startswith(AI_RUN_EVENT_PREFIX) or raw_type.startswith(AI_RUN_CLOUD_PREFIX)


def source_run_or_fire_id(payload: Optional[Dict[str, Any]], fallback: str) -> str:
    data = (payload or {}).get("data") if isinstance((payload or {}).get("data"), dict) else {}
    event_source = (payload or {}).get("eventSource") or (payload or {}).get("source")
    source_id = None
    if isinstance(event_source, dict):
        source_id = event_source.get("id")
    run_id = data.get("runId") if isinstance(data, dict) else None
    payload_id = (payload or {}).get("id")
    return str(run_id or source_id or payload_id or fallback)


def incoming_correlation_id(payload: Optional[Dict[str, Any]]) -> str:
    data = (payload or {}).get("data") if isinstance((payload or {}).get("data"), dict) else {}
    value = data.get("correlationId") or (payload or {}).get("correlationId") or ""
    return str(value)


def has_chronos_hop(payload: Optional[Dict[str, Any]]) -> bool:
    return incoming_correlation_id(payload).startswith(CHRONOS_CORRELATION_PREFIX)


def loop_guard_error(
    task_type,
    source: str,
    event_payload: Optional[Dict[str, Any]],
    allow_ai_run_loop: bool,
) -> Optional[str]:
    from gluesync_scheduler.models.models import TaskType

    if task_type != TaskType.AI_AGENT_RUN:
        return None
    if source != "platform_event":
        return None
    if not is_ai_run_platform_event(event_payload):
        return None
    if not allow_ai_run_loop:
        return "refusing AI_RUN_* loop into ai_agent_run (allow_ai_run_loop is false)"
    if has_chronos_hop(event_payload):
        return "refusing AI_RUN_* loop into ai_agent_run (one hop already consumed)"
    return None


def chronos_correlation_id(flow_id: Any, event_id: Any, source_id: str) -> str:
    return f"{CHRONOS_CORRELATION_PREFIX}{flow_id}:{event_id}:{source_id}"


def json_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def parse_json_object(value: Any) -> Optional[Dict[str, Any]]:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_json_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _coerce_allow_list(value: Any) -> List[str]:
    parsed = parse_json_list(value)
    return [str(item) for item in parsed]


def _string_leaves(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        leaves: List[str] = []
        for item in value:
            leaves.extend(_string_leaves(item))
        return leaves
    if isinstance(value, dict):
        leaves: List[str] = []
        for item in value.values():
            leaves.extend(_string_leaves(item))
        return leaves
    return []


def pipeline_path_id(pipeline_id: Optional[str]) -> str:
    text = (pipeline_id or "").strip()
    return text or "-"


def ai_agent_run_orm_kwargs(obj) -> Dict[str, Any]:
    return {
        "agent_alias": getattr(obj, "agent_alias", None),
        "agent_version": getattr(obj, "agent_version", None),
        "agent_input": json_text(getattr(obj, "agent_input", None)),
        "prompt_template": getattr(obj, "prompt_template", None),
        "payload_allow_list": json_text(getattr(obj, "payload_allow_list", None)),
        "idempotency_key": getattr(obj, "idempotency_key", None),
        "allow_ai_run_loop": bool(getattr(obj, "allow_ai_run_loop", False)),
    }


def ai_agent_run_http_payload(obj, wait: bool = True) -> Dict[str, Any]:
    payload = {
        "agent_alias": getattr(obj, "agent_alias", None),
        "wait": wait,
        "input": getattr(obj, "resolved_input", None),
        "prompt_template": getattr(obj, "prompt_template", None),
        "payload_allow_list": parse_json_list(getattr(obj, "payload_allow_list", None)),
        "agent_input": parse_json_object(getattr(obj, "agent_input", None)),
        "idempotency_key": getattr(obj, "resolved_idempotency_key", None)
        or getattr(obj, "idempotency_key", None),
        "correlation_id": getattr(obj, "correlation_id", None),
    }
    version = getattr(obj, "agent_version", None)
    if version is not None:
        payload["agent_version"] = version
    event_payload = getattr(obj, "event_payload", None)
    if payload["input"] is None:
        payload["input"] = build_run_input(
            payload["prompt_template"],
            payload["payload_allow_list"],
            payload["agent_input"],
            event_payload,
        )
    if payload["idempotency_key"] and "{{" in str(payload["idempotency_key"]):
        payload["idempotency_key"] = interpolate_idempotency_key(
            payload["idempotency_key"],
            payload["payload_allow_list"],
            event_payload,
        )
    return {key: value for key, value in payload.items() if value is not None or key == "wait"}
