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
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Prefix used for all one-shot webhooks created by Chronos.
_CHRONOS_WEBHOOK_PREFIX = "chronos-sync-"

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
    url = client._get_current_corehub_url()   # uses existing discovery logic
    return url.rstrip("/") if url else ""


def _get_corehub_auth_headers() -> dict:
    """Return headers with the Bearer token for CoreHub API authentication."""
    from gluesync_scheduler.core.play_pause import CoreHubClient
    client = CoreHubClient()
    token, _ = client._get_current_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_corehub_ssl_verify() -> bool:
    """Return whether SSL certificate verification should be enabled."""
    ssl_enabled = os.getenv("SSL_ENABLED", "False").lower() in ("true", "1", "t")
    ssl_skip_verify = os.getenv("SSL_SKIP_VERIFY", "False").lower() in ("true", "1", "t")
    return not ssl_skip_verify if ssl_enabled else True


def _task_type_to_webhook_events(task_type: TaskType) -> list:
    """Map a chained event's TaskType to the WebhookEventType values
    that signal completion of the operation.

    The values must match the enum names in WebhookEventType.kt
    (not the cloudEventType string).
    """
    mapping = {
        TaskType.ENTITY_START: ["ENTITY_CDC_STARTED"],
        TaskType.ENTITY_STOP: ["ENTITY_CDC_STOPPED"],
        TaskType.ENTITY_SNAPSHOT: ["ENTITY_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.ENTITY_REDO: ["ENTITY_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.PIPELINE_START: ["ENTITY_CDC_STARTED"],
        TaskType.PIPELINE_STOP: ["ENTITY_CDC_STOPPED"],
        TaskType.PIPELINE_SNAPSHOT: ["ENTITY_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.PIPELINE_REDO: ["ENTITY_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.GROUP_START: ["GROUP_CDC_STARTED"],
        TaskType.GROUP_STOP: ["GROUP_CDC_STOPPED"],
        TaskType.GROUP_SNAPSHOT: ["GROUP_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.GROUP_REDO: ["GROUP_SNAPSHOT_COMPLETED", "ENTITY_SNAPSHOT_FAILED"],
        TaskType.PIPELINE_ENTER_MAINTENANCE: ["PIPELINE_ENTER_MAINTENANCE"],
        TaskType.PIPELINE_EXIT_MAINTENANCE: ["PIPELINE_EXIT_MAINTENANCE"],
    }
    return mapping.get(task_type, ["ENTITY_SNAPSHOT_COMPLETED", "ENTITY_CDC_STARTED", "ENTITY_CDC_STOPPED"])


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

    # Serializes all CoreHub webhook-list mutations (GET-then-PUT) to prevent
    # concurrent register/delete operations from overwriting each other.
    _webhook_list_lock: asyncio.Lock = asyncio.Lock()

    # ---------------------------------------------------------------------------
    # Public entry points
    # ---------------------------------------------------------------------------

    async def execute_chain(self, job: ScheduledJob, db: Session) -> dict:
        """Called after the parent job executes successfully.

        Returns a dict with keys:
          - started: bool — whether any chained events exist
          - success: bool — whether all events completed without error
          - errors: list[str] — human-readable error messages
        """
        events: List[ChainedJobEvent] = (
            db.query(ChainedJobEvent)
            .filter(ChainedJobEvent.parent_job_id == job.id)
            .order_by(ChainedJobEvent.position)
            .all()
        )
        if not events:
            return {"started": False, "success": True, "errors": []}

        logger.info(
            "Executing chained events for job %d (%s): %d event(s)",
            job.id, job.name, len(events)
        )

        errors: list[str] = []

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
                    webhook_detail = getattr(self, '_last_webhook_error', None)
                    if webhook_detail:
                        err_msg = (
                            f"Chained event pos={event.position} "
                            f"({event.task_type}) failed: {webhook_detail}"
                        )
                    else:
                        err_msg = (
                            f"Chained event pos={event.position} "
                            f"({event.task_type}) failed or timed out"
                        )
                    logger.error(err_msg)
                    errors.append(err_msg)
                    break

        chain_success = len(errors) == 0

        # Persist chain errors to the parent job so the UI can see them
        if errors:
            try:
                parent = db.query(ScheduledJob).filter(
                    ScheduledJob.id == job.id
                ).first()
                if parent:
                    parent.last_error_message = "; ".join(errors)
                    parent.last_run_error_time = datetime.now(timezone.utc)
                    db.commit()
            except Exception as exc:
                logger.error("Failed to persist chain error to DB: %s", exc)

        return {"started": True, "success": chain_success, "errors": errors}

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
                body_preview = response.text[:200] if response.text else ""
                logger.error(
                    "Chained event %d failed: HTTP %d — response: %s",
                    event.id, response.status_code, body_preview,
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
            webhook_id = await self._register_corehub_webhook(task_guid, event)
            if not webhook_id:
                detail = getattr(self, '_last_webhook_error', 'unknown error')
                logger.error(
                    "Could not register corehub webhook for sync event %d: %s",
                    event.id, detail,
                )
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

    async def _register_corehub_webhook(self, task_guid: str, event: ChainedJobEvent) -> Optional[str]:
        """Register a one-shot webhook configuration in the corehub.

        The corehub exposes a PUT /global-config/webhooks endpoint that
        replaces the full list, so we first GET the current list and append.
        Returns the id of the newly registered webhook, or None on failure.

        The GET-then-PUT cycle is protected by ``_webhook_list_lock`` and
        retried up to 3 times to handle transient failures.
        """
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                self._last_webhook_error = "CoreHub URL unknown"
                logger.error("Cannot register webhook — corehub URL unknown")
                return None

            callback_url = "{{chronos_address}}/api/webhooks/notify"

            # Map the chained event's task type to the specific webhook event(s)
            # that signal completion of the operation.
            enabled_events = _task_type_to_webhook_events(event.task_type)

            # Build filters so the webhook only fires for the specific
            # pipeline / entity / group being targeted.
            entity_ids = _parse_json_list(event.entity_ids)
            group_ids = _parse_json_list(event.group_ids)

            new_webhook = {
                "id": f"{_CHRONOS_WEBHOOK_PREFIX}{task_guid}",
                "name": f"chronos-sync-{task_guid[:8]}",
                "webhookUrl": callback_url,
                "enabled": True,
                "enabledEvents": enabled_events,
                "pipelineFilter": [event.pipeline_id] if event.pipeline_id else [],
                "entityFilter": entity_ids if entity_ids else [],
                "groupFilter": group_ids if group_ids else [],
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
            auth_headers = _get_corehub_auth_headers()
            ssl_verify = _get_corehub_ssl_verify()
            loop = asyncio.get_event_loop()

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                async with self._webhook_list_lock:
                    # GET existing list
                    get_resp = await loop.run_in_executor(
                        None,
                        lambda: requests.get(config_endpoint, headers=auth_headers, timeout=10, verify=ssl_verify),
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
                            headers=auth_headers,
                            timeout=10,
                            verify=ssl_verify,
                        ),
                    )
                    if put_resp.status_code in (200, 201, 202, 204):
                        logger.info("Registered corehub webhook %s for task_guid %s", new_webhook["id"], task_guid)
                        return new_webhook["id"]

                    self._last_webhook_error = (
                        f"HTTP {put_resp.status_code}: {put_resp.text[:200]}"
                    )
                    logger.error(
                        "Failed to register corehub webhook (attempt %d/%d): HTTP %d — response: %s",
                        attempt, max_attempts, put_resp.status_code, put_resp.text[:300]
                    )

                if attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)

            return None
        except Exception as exc:
            self._last_webhook_error = str(exc)
            logger.error("Exception registering corehub webhook: %s", exc)
            return None

    async def _delete_corehub_webhook(self, webhook_config_id: str) -> None:
        """Remove the one-shot webhook from the corehub by rebuilding the list without it.

        Retried up to 3 times with exponential backoff to handle transient
        failures.  The GET-then-PUT cycle is protected by
        ``_webhook_list_lock`` to prevent concurrent mutations.
        """
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                corehub_url = _get_corehub_base()
                if not corehub_url:
                    return

                config_endpoint = f"{corehub_url}/global-config/webhooks"
                auth_headers = _get_corehub_auth_headers()
                ssl_verify = _get_corehub_ssl_verify()
                loop = asyncio.get_event_loop()

                async with self._webhook_list_lock:
                    get_resp = await loop.run_in_executor(
                        None,
                        lambda: requests.get(config_endpoint, headers=auth_headers, timeout=10, verify=ssl_verify),
                    )
                    if get_resp.status_code != 200:
                        logger.warning(
                            "Cannot fetch webhook list for deletion (attempt %d/%d): HTTP %d",
                            attempt, max_attempts, get_resp.status_code,
                        )
                        if attempt < max_attempts:
                            await asyncio.sleep(0.5 * attempt)
                        continue

                    try:
                        existing: list = get_resp.json()
                        if not isinstance(existing, list):
                            existing = []
                    except Exception:
                        existing = []

                    updated = [w for w in existing if w.get("id") != webhook_config_id]
                    put_resp = await loop.run_in_executor(
                        None,
                        lambda: requests.put(
                            config_endpoint,
                            json=updated,
                            headers=auth_headers,
                            timeout=10,
                            verify=ssl_verify,
                        ),
                    )
                    if put_resp.status_code in (200, 201, 202, 204):
                        logger.info("Deleted corehub webhook %s", webhook_config_id)
                        return

                    logger.warning(
                        "Failed to delete corehub webhook %s (attempt %d/%d): HTTP %d",
                        webhook_config_id, attempt, max_attempts, put_resp.status_code,
                    )

            except Exception as exc:
                logger.warning(
                    "Exception deleting corehub webhook %s (attempt %d/%d): %s",
                    webhook_config_id, attempt, max_attempts, exc,
                )

            if attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)

        logger.error(
            "Could not delete corehub webhook %s after %d attempts — it may need manual cleanup",
            webhook_config_id, max_attempts,
        )

    @classmethod
    async def cleanup_stale_webhooks(cls) -> int:
        """Remove all leftover ``chronos-sync-*`` webhooks from CoreHub.

        Called at startup to clean up any one-shot webhooks that were not
        deleted because of a crash or timeout in a previous run.
        Returns the number of webhooks removed.
        """
        removed = 0
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                logger.warning("Cannot cleanup stale webhooks — corehub URL unknown")
                return 0

            config_endpoint = f"{corehub_url}/global-config/webhooks"
            auth_headers = _get_corehub_auth_headers()
            ssl_verify = _get_corehub_ssl_verify()
            loop = asyncio.get_event_loop()

            async with cls._webhook_list_lock:
                get_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(config_endpoint, headers=auth_headers, timeout=10, verify=ssl_verify),
                )
                if get_resp.status_code != 200:
                    logger.warning("Cannot fetch webhook list for cleanup: HTTP %d", get_resp.status_code)
                    return 0

                try:
                    existing: list = get_resp.json()
                    if not isinstance(existing, list):
                        return 0
                except Exception:
                    return 0

                stale = [w for w in existing if str(w.get("id", "")).startswith(_CHRONOS_WEBHOOK_PREFIX)]
                if not stale:
                    logger.info("No stale chronos webhooks found in corehub")
                    return 0

                updated = [w for w in existing if not str(w.get("id", "")).startswith(_CHRONOS_WEBHOOK_PREFIX)]
                put_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.put(
                        config_endpoint,
                        json=updated,
                        headers=auth_headers,
                        timeout=10,
                        verify=ssl_verify,
                    ),
                )
                if put_resp.status_code in (200, 201, 202, 204):
                    removed = len(stale)
                    logger.info("Cleaned up %d stale chronos webhook(s) from corehub", removed)
                else:
                    logger.error(
                        "Failed to cleanup stale webhooks: HTTP %d — response: %s",
                        put_resp.status_code, put_resp.text[:300],
                    )
        except Exception as exc:
            logger.warning("Exception during stale webhook cleanup: %s", exc)

        return removed

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
