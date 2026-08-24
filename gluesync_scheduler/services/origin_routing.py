#!/usr/bin/env python3
"""Origin routing for Chronos platform-event trigger flows.

``routing`` on a trigger flow is ``origin`` (loop-back to ``event.source``)
or ``broadcast`` (fan-out every action on the flow). Omitted / null on old
rows is broadcast.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROUTING_ORIGIN = "origin"
ROUTING_BROADCAST = "broadcast"
VALID_ROUTINGS = frozenset({ROUTING_ORIGIN, ROUTING_BROADCAST})

# Hub will attach event.source {kind, id}. Until that lands, tolerate today's
# object/entity id fields on the webhook body.
_ID_KEYS = (
    "id",
    "entityId",
    "entity_id",
    "entityID",
    "objectId",
    "object_id",
    "tableId",
    "table_id",
    "groupId",
    "group_id",
    "pipelineId",
    "pipeline_id",
    "sourceId",
    "source_id",
)

_KIND_BY_ID_KEY = {
    "entityId": "entity",
    "entity_id": "entity",
    "entityID": "entity",
    "objectId": "object",
    "object_id": "object",
    "tableId": "table",
    "table_id": "table",
    "groupId": "group",
    "group_id": "group",
    "pipelineId": "pipeline",
    "pipeline_id": "pipeline",
}

# Entity-scoped platform events (table reorg / truncate / table DDL).
# Exact strings are not listed elsewhere in this repo; these match Hub-style
# SCREAMING_SNAKE names used by existing webhooks (ENTITY_CDC_STARTED, …).
ENTITY_SCOPED_PLATFORM_EVENTS = frozenset(
    {
        "TABLE_REORGANIZATION",
        "TABLE_REORGANIZED",
        "TABLE_REORG",
        "TABLE_TRUNCATE",
        "TABLE_TRUNCATED",
        "TABLE_DDL",
    }
)

_ENTITY_SCOPED_SUBSTRINGS = (
    "TABLE_REORG",
    "TABLE_REORGANIZ",
    "TABLE_TRUNCATE",
    "TABLE_DDL",
)

_ENTITY_LIKE_KINDS = frozenset({"entity", "table", "object"})


@dataclass(frozen=True)
class EventSource:
    kind: Optional[str]
    id: str


def normalize_routing(value: Optional[str]) -> str:
    if value is None or value == "":
        return ROUTING_BROADCAST
    lowered = str(value).strip().lower()
    if lowered not in VALID_ROUTINGS:
        raise ValueError(f"routing must be 'origin' or 'broadcast', got {value!r}")
    return lowered


def is_entity_scoped_platform_event(platform_event: Optional[str]) -> bool:
    if not platform_event:
        return False
    name = str(platform_event).strip().upper()
    if name in ENTITY_SCOPED_PLATFORM_EVENTS:
        return True
    return any(token in name for token in _ENTITY_SCOPED_SUBSTRINGS)


def default_routing_for_platform_event(platform_event: Optional[str]) -> str:
    """Default for new bindings: origin on entity-scoped events, else broadcast."""
    if is_entity_scoped_platform_event(platform_event):
        return ROUTING_ORIGIN
    return ROUTING_BROADCAST


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _stringify_id(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _kind_from_mapping(mapping: Dict[str, Any], id_key: Optional[str] = None) -> Optional[str]:
    for key in ("kind", "type", "objectType", "object_type"):
        raw = mapping.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    if id_key and id_key in _KIND_BY_ID_KEY:
        return _KIND_BY_ID_KEY[id_key]
    return None


def _id_from_mapping(mapping: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    for key in _ID_KEYS:
        if key not in mapping:
            continue
        sid = _stringify_id(mapping.get(key))
        if sid:
            return sid, key
    return None, None


def extract_event_source(payload: Optional[Dict[str, Any]]) -> Optional[EventSource]:
    """Read Hub ``event.source {kind, id}`` or fall back to today's id fields."""
    root = _as_dict(payload)
    if root is None:
        return None

    containers: List[Dict[str, Any]] = [root]
    for key in ("event", "data", "payload", "body"):
        nested = _as_dict(root.get(key))
        if nested is not None:
            containers.append(nested)

    # Prefer an explicit source object (stable Hub contract).
    for container in containers:
        source_obj = container.get("source")
        if isinstance(source_obj, dict):
            sid, id_key = _id_from_mapping(source_obj)
            if sid:
                kind = _kind_from_mapping(source_obj, id_key)
                return EventSource(kind=kind, id=sid)
        elif source_obj is not None:
            sid = _stringify_id(source_obj)
            if sid:
                return EventSource(kind=None, id=sid)

    for container in containers:
        sid, id_key = _id_from_mapping(container)
        if sid:
            return EventSource(kind=_kind_from_mapping(container, id_key), id=sid)

    return None


def _parse_id_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None and str(x).strip() != ""]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x is not None and str(x).strip() != ""]
        return [str(parsed)]
    return [str(raw)]


def job_target_matches(event: Any, source: Optional[EventSource]) -> bool:
    """True when this trigger event's target matches ``event.source`` (kind + id)."""
    if source is None or not source.id:
        return False

    entity_ids = _parse_id_list(getattr(event, "entity_ids", None))
    group_ids = _parse_id_list(getattr(event, "group_ids", None))
    pipeline_id = getattr(event, "pipeline_id", None)
    pipeline_ids = [str(pipeline_id)] if pipeline_id not in (None, "") else []

    kind = (source.kind or "").lower() or None
    sid = source.id

    if kind in _ENTITY_LIKE_KINDS:
        return sid in entity_ids
    if kind == "group":
        return sid in group_ids
    if kind == "pipeline":
        return sid in pipeline_ids

    # Unknown / missing kind: match any target id on the action.
    return sid in entity_ids or sid in group_ids or sid in pipeline_ids


def filter_events_for_routing(
    events: Iterable[Any],
    routing: Optional[str],
    event_payload: Optional[Dict[str, Any]],
    *,
    flow_id: Optional[int] = None,
) -> List[Any]:
    """Apply origin routing. Broadcast / omitted returns the full list."""
    events = list(events)
    try:
        mode = normalize_routing(routing)
    except ValueError:
        logger.warning(
            "origin_routing: unknown routing=%r on flow %s — treating as broadcast",
            routing,
            flow_id,
        )
        return events

    if mode != ROUTING_ORIGIN:
        return events

    source = extract_event_source(event_payload)
    matched = [ev for ev in events if job_target_matches(ev, source)]
    if not matched:
        logger.info(
            "origin_routing: no-op flow=%s source=%s — no trigger event target matched",
            flow_id,
            source,
        )
    else:
        logger.info(
            "origin_routing: flow=%s source=%s matched %d/%d action(s)",
            flow_id,
            source,
            len(matched),
            len(events),
        )
    return matched
