#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
 *
 * 1. GNU General Public License (GPL) Version 3
 *    You may use, modify, and distribute this software under the terms of the GPL v3.
 *    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
 *    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
 *
 * 2. MOLO17 Commercial License
 *    Alternatively, you may use this software under the MOLO17 Commercial License,
 *    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
 *    for licensing terms and conditions.
 *
 * You must choose one of these licenses to use this software. Using this software implies
 * acceptance of one of these licenses. See the accompanying LICENSE files or contact
 * MOLO17 for more information.
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, List, Optional

import requests
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ChainedJobEvent, ExecutionMode, ScheduledJob, TaskType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers to resolve runtime URLs
# ---------------------------------------------------------------------------

def _get_chronos_callback_base() -> str:
    """Return the base URL at which Chronos is reachable from the corehub."""
    url = os.getenv("CHRONOS_CALLBACK_URL", "").rstrip("/")
    if url:
        return url
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("SCHEDULER_INTERNAL_HOST", "localhost")
    ssl_enabled = os.getenv("SSL_ENABLED", "False").lower() in ("true", "1", "t")
    proto = "https" if ssl_enabled else "http"
    return f"{proto}://{host}:{port}"


def _get_corehub_base() -> str:
    """Return the corehub base URL (without trailing slash)."""
    from gluesync_scheduler.core.play_pause import CoreHubClient
    client = CoreHubClient()
    url = client._get_corehub_url()          # uses existing discovery logic
    return url.rstrip("/") if url else ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ChainExecutionService:
    """Executes chained events sequentially after a parent job completes.

    Async mode  → fire-and-forget; the next event starts immediately after
                  the previous one has been *triggered* (not waited on).
    Sync mode   → before firing, register a one-shot webhook inside the
                  corehub.  Execution blocks until the corehub POSTs the
                  callback to ``/api/webhooks/notify``, then the webhook is
                  deleted and the next event fires.
    """

    # Class-level registry so the webhook router can signal a waiting coroutine.
    # key: task_guid  → value: asyncio.Event
    _pending: Dict[str, asyncio.Event] = {}

    # ---------------------------------------------------------------------------
    # Public entry points
    # ---------------------------------------------------------------------------

    async def execute_chain(self, job: ScheduledJob, db: Session) -> None:
        """Called after the parent job executes successfully."""
        events: List[ChainedJobEvent] = (
            db.query(ChainedJobEvent)
            .filter(ChainedJobEvent.parent_job_id == job.id)
            .order_by(ChainedJobEvent.position)
            .all()
        )
        if not events:
            return

        logger.info(
            "Executing chained events for job %d (%s): %d event(s)",
            job.id, job.name, len(events)
        )

        for event in events:
            logger.info(
                "Chained event pos=%d type=%s mode=%s pipeline=%s",
                event.position, event.task_type, event.execution_mode, event.pipeline_id,
            )
            if event.execution_mode == ExecutionMode.ASYNC:
                # Fire-and-forget: trigger the event but don't await its HTTP result
                asyncio.ensure_future(self._execute_chained_event(event, db))
            else:
                # Sync: register webhook, fire the event, wait for callback
                success = await self._execute_sync_event(event, db)
                if not success:
                    logger.error(
                        "Sync chained event pos=%d failed or timed out — stopping chain",
                        event.position,
                    )
                    break

    def notify_webhook_received(self, task_guid: str) -> bool:
        """Called by the webhook receiver endpoint.

        Returns True when the guid was pending (i.e. a waiting coroutine was
        unblocked), False when the guid is unknown.
        """
        event = self._pending.get(task_guid)
        if event is None:
            logger.warning("notify_webhook_received: unknown guid %s", task_guid)
            return False
        event.set()
        logger.info("notify_webhook_received: signalled guid %s", task_guid)
        return True

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    async def _execute_chained_event(self, event: ChainedJobEvent, db: Session) -> bool:
        """Execute a single chained event by calling the Chronos internal pipeline API.

        Returns True on HTTP 2xx, False otherwise.
        """
        try:
            ssl_enabled = os.getenv("SSL_ENABLED", "False").lower() in ("true", "1", "t")
            proto = "https" if ssl_enabled else "http"
            host = os.getenv("SCHEDULER_INTERNAL_HOST", "localhost")
            port = int(os.getenv("PORT", "8000"))
            base_url = f"{proto}://{host}:{port}/api"

            action = _task_type_to_action(event.task_type)
            if action is None:
                logger.error("Unknown task_type %s for chained event %d", event.task_type, event.id)
                return False

            endpoint = f"{base_url}/pipelines/{event.pipeline_id}/{action}"
            payload: dict = {}

            entity_ids = _parse_json_list(event.entity_ids)
            group_ids = _parse_json_list(event.group_ids)

            if entity_ids:
                payload["entity_ids"] = entity_ids
            if group_ids and event.task_type in (
                TaskType.GROUP_REDO, TaskType.GROUP_START,
                TaskType.GROUP_STOP, TaskType.GROUP_SNAPSHOT,
            ):
                payload["group_ids"] = group_ids
            if event.with_snapshot:
                payload["with_snapshot"] = True
            if event.snapshot_write_method:
                payload["snapshot_write_method"] = event.snapshot_write_method

            timeout = int(os.getenv("SCHEDULER_INTERNAL_HTTP_TIMEOUT", "120"))
            ssl_skip = os.getenv("SSL_SKIP_VERIFY", "False").lower() in ("true", "1", "t")
            verify = not ssl_skip if ssl_enabled else True

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                    verify=verify,
                ),
            )
            if response.status_code in (200, 201, 202):
                logger.info(
                    "Chained event %d executed OK (HTTP %d)", event.id, response.status_code
                )
                return True
            else:
                logger.error(
                    "Chained event %d failed: HTTP %d", event.id, response.status_code
                )
                return False
        except Exception as exc:
            logger.error("Chained event %d raised exception: %s", event.id, exc)
            return False

    async def _execute_sync_event(self, event: ChainedJobEvent, db: Session) -> bool:
        """Register a corehub webhook, fire the event, and wait for the callback."""
        task_guid = str(uuid.uuid4())
        webhook_id: Optional[str] = None
        try:
            webhook_id = await self._register_corehub_webhook(task_guid)
            if not webhook_id:
                logger.error("Could not register corehub webhook for sync event %d", event.id)
                return False

            # Fire the actual task
            ok = await self._execute_chained_event(event, db)
            if not ok:
                return False

            # Wait for callback (default 1h timeout)
            timeout = int(os.getenv("CHRONOS_SYNC_WEBHOOK_TIMEOUT_SECONDS", "3600"))
            completed = await self._wait_for_webhook(task_guid, timeout_seconds=timeout)
            if not completed:
                logger.warning("Timed out waiting for webhook callback for sync event %d", event.id)
            return completed
        finally:
            # Always clean up the one-shot webhook
            if webhook_id:
                await self._delete_corehub_webhook(webhook_id)
            self._pending.pop(task_guid, None)

    async def _register_corehub_webhook(self, task_guid: str) -> Optional[str]:
        """Register a one-shot webhook configuration in the corehub.

        The corehub exposes a PUT /global-config/webhooks endpoint that
        replaces the full list, so we first GET the current list and append.
        Returns the id of the newly registered webhook, or None on failure.
        """
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                logger.error("Cannot register webhook — corehub URL unknown")
                return None

            callback_url = (
                f"{_get_chronos_callback_base()}/api/webhooks/notify"
            )
            new_webhook = {
                "id": f"chronos-sync-{task_guid}",
                "name": f"chronos-sync-{task_guid[:8]}",
                "webhookUrl": callback_url,
                "enabled": True,
                "customHeaders": {
                    "X-Task-GUID": task_guid,
                    "EXT_MODULE": "chronos",
                },
                "retryConfig": {
                    "maxRetries": 0,
                    "initialDelayMs": 0,
                    "backoffMultiplier": 1.0,
                    "maxDelayMs": 0,
                },
            }

            config_endpoint = f"{corehub_url}/global-config/webhooks"
            loop = asyncio.get_event_loop()

            # GET existing list
            get_resp = await loop.run_in_executor(
                None,
                lambda: requests.get(config_endpoint, timeout=10),
            )
            existing: list = []
            if get_resp.status_code == 200:
                try:
                    existing = get_resp.json()
                    if not isinstance(existing, list):
                        existing = []
                except Exception:
                    existing = []

            # PUT updated list
            updated = existing + [new_webhook]
            put_resp = await loop.run_in_executor(
                None,
                lambda: requests.put(
                    config_endpoint,
                    json=updated,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                ),
            )
            if put_resp.status_code in (200, 201, 202, 204):
                logger.info("Registered corehub webhook %s for task_guid %s", new_webhook["id"], task_guid)
                return new_webhook["id"]
            else:
                logger.error(
                    "Failed to register corehub webhook: HTTP %d", put_resp.status_code
                )
                return None
        except Exception as exc:
            logger.error("Exception registering corehub webhook: %s", exc)
            return None

    async def _delete_corehub_webhook(self, webhook_config_id: str) -> None:
        """Remove the one-shot webhook from the corehub by rebuilding the list without it."""
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                return

            config_endpoint = f"{corehub_url}/global-config/webhooks"
            loop = asyncio.get_event_loop()

            get_resp = await loop.run_in_executor(
                None,
                lambda: requests.get(config_endpoint, timeout=10),
            )
            if get_resp.status_code != 200:
                return

            try:
                existing: list = get_resp.json()
                if not isinstance(existing, list):
                    return
            except Exception:
                return

            updated = [w for w in existing if w.get("id") != webhook_config_id]
            await loop.run_in_executor(
                None,
                lambda: requests.put(
                    config_endpoint,
                    json=updated,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                ),
            )
            logger.info("Deleted corehub webhook %s", webhook_config_id)
        except Exception as exc:
            logger.warning("Exception deleting corehub webhook %s: %s", webhook_config_id, exc)

    async def _wait_for_webhook(self, task_guid: str, timeout_seconds: int = 3600) -> bool:
        """Block until ``notify_webhook_received`` signals this guid, or timeout."""
        ev = asyncio.Event()
        self._pending[task_guid] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=float(timeout_seconds))
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(task_guid, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

chain_execution_service = ChainExecutionService()


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _task_type_to_action(task_type: TaskType) -> Optional[str]:
    mapping = {
        TaskType.ENTITY_START: "play",
        TaskType.PIPELINE_START: "play",
        TaskType.GROUP_START: "play",
        TaskType.ENTITY_STOP: "pause",
        TaskType.PIPELINE_STOP: "pause",
        TaskType.GROUP_STOP: "pause",
        TaskType.ENTITY_SNAPSHOT: "one-time-snapshot",
        TaskType.PIPELINE_SNAPSHOT: "one-time-snapshot",
        TaskType.GROUP_SNAPSHOT: "one-time-snapshot-group",
        TaskType.ENTITY_REDO: "redo",
        TaskType.PIPELINE_REDO: "redo",
        TaskType.GROUP_REDO: "redo-group",
        TaskType.PIPELINE_ENTER_MAINTENANCE: "enter-maintenance",
        TaskType.PIPELINE_EXIT_MAINTENANCE: "exit-maintenance",
    }
    return mapping.get(task_type)


def _parse_json_list(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
