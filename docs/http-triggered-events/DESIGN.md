# HTTP-Triggered Events — Design Document

**Branch:** `feature/http-triggered-events`  
**Base:** `feature/chained-events`  
**Status:** Draft — pre-implementation

---

## 1. Problem Statement

Chained events let users define a sequential chain of Gluesync actions that fires automatically after a scheduled cron job completes. They are powerful but always bound to a time-based trigger.

This feature introduces **TriggerFlows**: the same sequential action-chain concept, but fired on-demand by an external HTTP call instead of a cron schedule. A remote system, a CI/CD pipeline, an n8n workflow, or a human operator can POST to a stable endpoint and immediately kick off an ordered chain of pipeline operations.

**Key insight:** a TriggerFlow is exactly like a scheduled job without the schedule. It is a standalone named chain of actions, reachable via a secret-protected HTTP endpoint.

---

## 2. Concepts

### TriggerFlow

A **TriggerFlow** is a named, reusable definition of an ordered list of pipeline actions. It:

- Has a human-readable `name` and optional `description`
- Has an `enabled` flag
- Carries a **secret token** used to authenticate incoming fire requests
- Owns an ordered list of **TriggerFlowEvent** items (position 0-based)
- Records `last_triggered`, `last_successful_trigger`, and `last_error_message` for observability
- Exposes a stable trigger URL: `POST /api/triggers/{id}/fire`

### TriggerFlowEvent

Identical in shape to a `ChainedJobEvent`, with the same fields:

| Field | Description |
|---|---|
| `task_type` | One of the existing `TaskType` enum values |
| `pipeline_id` | Target pipeline |
| `entity_ids` | Entity filter (optional) |
| `group_ids` | Group filter (optional) |
| `with_snapshot` | Snapshot flag |
| `snapshot_write_method` | `UPSERT` or `INSERT` |
| `execution_mode` | `async` or `sync` |
| `position` | 0-based ordering within the flow |

### Execution

When fired, the TriggerFlow runs its events in position order using the exact same engine as chained events (`ChainExecutionService`):

- **async** events: fire-and-forget; next event starts immediately
- **sync** events: registers a corehub one-shot webhook, waits for the callback before proceeding

The fire endpoint returns `202 Accepted` immediately and executes the chain in the background (FastAPI `BackgroundTask`). An optional `wait=true` query parameter blocks until the chain finishes (or a timeout is reached), returning the final status — useful for CI/CD integrations.

---

## 3. Data Model

### 3.1 New DB Tables

#### `trigger_flows`

```sql
CREATE TABLE trigger_flows (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    VARCHAR NOT NULL,
    description             VARCHAR,
    enabled                 BOOLEAN NOT NULL DEFAULT 1,
    secret_token            VARCHAR NOT NULL,          -- plaintext; returned once on create
    last_triggered          TIMESTAMP WITH TIME ZONE,
    last_successful_trigger TIMESTAMP WITH TIME ZONE,
    last_error_message      TEXT,
    last_trigger_error_time TIMESTAMP WITH TIME ZONE,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### `trigger_flow_events`

```sql
CREATE TABLE trigger_flow_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_flow_id      INTEGER NOT NULL REFERENCES trigger_flows(id) ON DELETE CASCADE,
    position             INTEGER NOT NULL,
    task_type            VARCHAR NOT NULL,
    pipeline_id          VARCHAR NOT NULL,
    entity_ids           TEXT,       -- JSON array
    group_ids            TEXT,       -- JSON array
    with_snapshot        BOOLEAN NOT NULL DEFAULT 0,
    snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT',
    execution_mode       VARCHAR NOT NULL DEFAULT 'async',
    created_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_trigger_flow_events_flow_id ON trigger_flow_events (trigger_flow_id);
```

### 3.2 SQLAlchemy Models (in `models.py`)

```python
class TriggerFlow(Base):
    __tablename__ = "trigger_flows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    secret_token = Column(String, nullable=False)
    last_triggered = Column(TIMESTAMP(timezone=True), nullable=True)
    last_successful_trigger = Column(TIMESTAMP(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_trigger_error_time = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TriggerFlowEvent(Base):
    __tablename__ = "trigger_flow_events"

    id = Column(Integer, primary_key=True, index=True)
    trigger_flow_id = Column(Integer, ForeignKey("trigger_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    task_type = Column(Enum(TaskType), nullable=False)
    pipeline_id = Column(String, nullable=False)
    entity_ids = Column(Text, nullable=True)
    group_ids = Column(Text, nullable=True)
    with_snapshot = Column(Boolean, default=False)
    snapshot_write_method = Column(String, nullable=False, default="UPSERT")
    execution_mode = Column(Enum(ExecutionMode), nullable=False, default=ExecutionMode.ASYNC)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
```

---

## 4. API Design

All endpoints are under the `/api/triggers` prefix.

### 4.1 CRUD

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/triggers/` | List all TriggerFlows (paginated) |
| `GET` | `/api/triggers/{id}` | Get one TriggerFlow by ID |
| `POST` | `/api/triggers/` | Create a TriggerFlow |
| `PUT` | `/api/triggers/{id}` | Replace a TriggerFlow (events replaced atomically) |
| `PATCH` | `/api/triggers/{id}/status` | Enable / disable |
| `DELETE` | `/api/triggers/{id}` | Delete |
| `POST` | `/api/triggers/{id}/regenerate-token` | Generate a new secret token |

### 4.2 Fire

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/triggers/{id}/fire` | Trigger the flow |

**Request headers:**

```
X-Trigger-Token: <secret_token>
```

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `wait` | bool | `false` | Block until chain finishes (or `wait_timeout_seconds`) |
| `wait_timeout_seconds` | int | `120` | Max seconds to wait when `wait=true` |

**Responses:**

| Status | Meaning |
|---|---|
| `202 Accepted` | Chain queued (background, `wait=false`) |
| `200 OK` | Chain completed synchronously (`wait=true`) |
| `401 Unauthorized` | Invalid or missing token |
| `403 Forbidden` | TriggerFlow is disabled |
| `404 Not Found` | Unknown ID |
| `409 Conflict` | Flow is already running (optional: single-execution guard) |
| `500 Internal Server Error` | Execution failed |

**Response body (both 200 and 202):**

```json
{
  "trigger_flow_id": 42,
  "triggered_at": "2026-07-14T10:05:00+00:00",
  "status": "queued" | "completed" | "failed",
  "events_count": 3,
  "message": "TriggerFlow 'nightly-resync' queued for execution"
}
```

### 4.3 Create Request Body

```json
{
  "name": "post-deploy-resync",
  "description": "Triggered after each production deploy via CI",
  "enabled": true,
  "events": [
    {
      "task_type": "pipeline_stop",
      "pipeline_id": "prod-pipeline",
      "execution_mode": "sync"
    },
    {
      "task_type": "pipeline_snapshot",
      "pipeline_id": "prod-pipeline",
      "with_snapshot": true,
      "snapshot_write_method": "UPSERT",
      "execution_mode": "sync"
    },
    {
      "task_type": "pipeline_start",
      "pipeline_id": "prod-pipeline",
      "execution_mode": "async"
    }
  ]
}
```

**Create response** includes the `secret_token` in plaintext — **only returned at creation time**. Subsequent GETs return `"secret_token": "<redacted>"` so the value never leaks again. Use `POST /regenerate-token` to get a new one.

```json
{
  "id": 42,
  "name": "post-deploy-resync",
  "enabled": true,
  "secret_token": "sk_Jd8fkQ29Xm5...",   ← only here
  "trigger_url": "http://chronos:8000/api/triggers/42/fire",
  "events": [...],
  "last_triggered": null,
  "created_at": "..."
}
```

---

## 5. Token Authentication

- Token is a `secrets.token_urlsafe(32)` string generated at creation.
- Stored **plaintext** in the DB (consistent with codebase's overall approach; DB access implies trust).
- The fire endpoint checks: `X-Trigger-Token` header must equal the stored token.
- If the caller doesn't have the token, they can ask an admin to call `POST /regenerate-token`.
- The token is **not** returned in any GET response — only in create and regenerate responses.

> **Optional hardening** (not in v1): store a SHA-256 hash of the token and compare hashes on verify.

---

## 6. Concurrency Guard (optional, v1)

A class-level `_running: Set[int]` on `TriggerFlowService` tracks which flows are currently executing. If a fire request arrives for a flow already running, the endpoint returns `409 Conflict`. This is opt-in via env var `CHRONOS_TRIGGER_EXCLUSIVE=true` (default: off — multiple concurrent fires are allowed).

---

## 7. ChainExecutionService Refactoring

The existing `ChainExecutionService` is tightly coupled to `ChainedJobEvent` (the SQLAlchemy model). We need it to also execute `TriggerFlowEvent` objects, which have the same shape.

**Strategy: introduce a protocol/dataclass as the common execution unit.**

```python
@dataclass
class ExecutableEvent:
    id: int
    position: int
    task_type: TaskType
    pipeline_id: str
    entity_ids: Optional[str]   # raw JSON string, same as DB column
    group_ids: Optional[str]
    with_snapshot: bool
    snapshot_write_method: str
    execution_mode: ExecutionMode
```

Both `ChainedJobEvent` and `TriggerFlowEvent` are projected into `ExecutableEvent` before being passed to the internal execution helpers. This avoids duplicating execution logic.

The refactoring touch points:
- `_execute_chained_event(event, db)` → `_execute_event(event: ExecutableEvent)` (no db needed)
- `_execute_sync_event(event, db)` → `_execute_sync_event(event: ExecutableEvent)`
- `execute_chain(job, db)` stays as-is, projecting `ChainedJobEvent` → `ExecutableEvent`
- New public entry: `execute_trigger_flow(flow_id, events: List[TriggerFlowEvent], db)` projecting `TriggerFlowEvent` → `ExecutableEvent`

---

## 8. New Files

```
gluesync_scheduler/
├── api/
│   └── trigger_router.py          ← new: CRUD + /fire endpoint
├── services/
│   └── trigger_flow_service.py    ← new: create/read/update/delete/fire logic
└── models/
    └── trigger_schemas.py         ← new: Pydantic schemas for TriggerFlow

migrations/
└── migrate_add_trigger_flows.py   ← new: creates trigger_flows + trigger_flow_events

tests/
└── test_trigger_flows.py          ← new: CRUD + fire tests
```

**Modified files:**

| File | Change |
|---|---|
| `gluesync_scheduler/models/models.py` | Add `TriggerFlow`, `TriggerFlowEvent` SQLAlchemy models |
| `gluesync_scheduler/services/chain_execution_service.py` | Add `ExecutableEvent` dataclass; refactor internals; add `execute_trigger_flow()` |
| `gluesync_scheduler/core/app.py` | Include `trigger_router` at `/api` |
| `gluesync_scheduler/db/migrate.py` | Register `migrate_add_trigger_flows` |

---

## 9. Implementation Plan

| Step | Task | Notes |
|---|---|---|
| 1 | Add `TriggerFlow` + `TriggerFlowEvent` to `models.py` | Pure model additions, no breakage |
| 2 | Write `migrate_add_trigger_flows.py` | Follows pattern of `migrate_add_chained_events.py` |
| 3 | Register migration in `db/migrate.py` | |
| 4 | Introduce `ExecutableEvent` dataclass in `chain_execution_service.py` | Backward-compat refactor |
| 5 | Add `execute_trigger_flow()` to `ChainExecutionService` | New public method |
| 6 | Write Pydantic schemas in `trigger_schemas.py` | |
| 7 | Write `trigger_flow_service.py` | CRUD + token verify + fire logic |
| 8 | Write `trigger_router.py` | Wire FastAPI routes |
| 9 | Mount router in `app.py` | One-liner |
| 10 | Write `test_trigger_flows.py` | At minimum: create, fire (async), fire (wrong token), fire (disabled) |
| 11 | Update `db/migrate.py` so startup auto-runs the new migration | |

---

## 10. Non-goals (v1)

- **Rate limiting** per TriggerFlow — callers are trusted; network-level controls apply
- **Event log / history** per fire invocation — `last_triggered` is enough for now
- **Conditional events** (if/else branching) — out of scope; chains are linear
- **Named slugs** as alternative to ID in the trigger URL — YAGNI
- **UI integration** — Chronos has no UI of its own; this is consumed via the React Gluesync UI separately

---

## 11. Usage Example (curl)

```bash
# 1 — Create a TriggerFlow
TOKEN=$(curl -s -X POST http://chronos:8000/api/triggers/ \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "post-deploy-resync",
    "enabled": true,
    "events": [
      {"task_type":"pipeline_stop","pipeline_id":"prod","execution_mode":"sync"},
      {"task_type":"pipeline_start","pipeline_id":"prod","execution_mode":"async"}
    ]
  }' | jq -r '.secret_token')

# 2 — Fire it from a CI step
curl -X POST http://chronos:8000/api/triggers/1/fire \
  -H "X-Trigger-Token: $TOKEN"

# 3 — Fire and wait for completion (useful in pipelines)
curl -X POST "http://chronos:8000/api/triggers/1/fire?wait=true&wait_timeout_seconds=300" \
  -H "X-Trigger-Token: $TOKEN"
```

---

## 12. Open Questions

1. **Token rotation policy**: should old tokens have a grace period after `regenerate-token`? (v1: no — immediate replacement)
2. **Payload passthrough**: should the fire request body be forwarded as metadata to the corehub actions? (v1: no)
3. **Multiple concurrent fires**: default allow or default deny? (v1: allow; `CHRONOS_TRIGGER_EXCLUSIVE` opt-in for deny)
4. **Audit log**: should fire events be written to a DB table for history? (v1: no — only `last_triggered` updated on the TriggerFlow row)
