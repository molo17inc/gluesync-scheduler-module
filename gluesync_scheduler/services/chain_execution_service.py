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
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

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
    """Map a TaskType to the WebhookEventType enum names that signal
    completion of the operation.

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
    Sync mode   → wait for the preceding step's completion webhook callback
                  from the corehub, then fire the event.  The webhook is
                  persistent (registered at job create/update time) and listens
                  for the **preceding** step's completion event(s).
    """

    # Class-level registry so the webhook router can signal a waiting coroutine.
    # Uses threading.Event because the chain runs in a separate thread with its
    # own event loop, while notify_webhook_received is called from the main
    # FastAPI event loop. asyncio.Event does NOT work across event loops.
    _pending: Dict[str, threading.Event] = {}

    # Pre-arrival buffer: callbacks that arrive before _wait_for_webhook is
    # called are stored here so the waiter can consume them immediately.
    # Only populated when the key is in _active_listeners.
    _pre_arrival: Dict[str, bool] = {}

    # Active listening window: correlation keys for which a chain is currently
    # executing and expecting a callback.  Callbacks for keys NOT in this set
    # are rejected by notify_webhook_received so that stale events (e.g. a
    # manual pipeline action that fires the same webhook outside the
    # scheduled window) are not consumed by the next chain run.
    _active_listeners: Set[str] = set()

    # Serializes all CoreHub webhook-list mutations (GET-then-PUT) to prevent
    # concurrent register/delete operations from overwriting each other.
    # Created lazily per-event-loop because the chain runs in a separate thread.
    _webhook_list_lock: Optional[asyncio.Lock] = None

    def _get_webhook_list_lock(self) -> asyncio.Lock:
        """Get or create the asyncio.Lock for the current event loop."""
        if self._webhook_list_lock is None:
            self._webhook_list_lock = asyncio.Lock()
        return self._webhook_list_lock

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

        # Determine which events are SYNC and set up the active listening window.
        # Only SYNC events wait for webhook callbacks, so only their correlation
        # keys are added to _active_listeners.  This ensures that callbacks
        # arriving outside the scheduled execution window are rejected.
        sync_event_keys: Set[str] = set()
        for event in events:
            if event.execution_mode == ExecutionMode.SYNC:
                key = str(event.id)
                sync_event_keys.add(key)
                # Clean up any stale pre-arrival buffer from a previous run
                self._pre_arrival.pop(key, None)

        self._active_listeners.update(sync_event_keys)
        logger.info(
            "Active listening window opened for job %d: %d sync event(s) %s",
            job.id, len(sync_event_keys), sorted(sync_event_keys) if sync_event_keys else "[]",
        )

        errors: list[str] = []

        try:
            for event in events:
                logger.info(
                    "Chained event pos=%d type=%s mode=%s pipeline=%s",
                    event.position, event.task_type, event.execution_mode, event.pipeline_id,
                )
                if event.execution_mode == ExecutionMode.ASYNC:
                    # Fire-and-forget: trigger the event but don't await its HTTP result
                    asyncio.ensure_future(self._execute_chained_event(event, db))
                else:
                    # Sync: wait for preceding step's callback, then fire the event
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
        finally:
            # Close the active listening window
            self._active_listeners.difference_update(sync_event_keys)
            # Clean up any remaining pre-arrival entries for these keys
            for key in sync_event_keys:
                self._pre_arrival.pop(key, None)
            logger.info(
                "Active listening window closed for job %d", job.id,
            )

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

    def sync_register_webhooks_for_events(
        self, events: List[ChainedJobEvent], main_job_task_type: TaskType
    ) -> None:
        """Sync wrapper to register persistent webhooks for SYNC chained events.

        Called from JobService.create_job / update_job (sync context).
        ASYNC events are skipped (they don't use webhooks).
        ``main_job_task_type`` is the parent job's task type — used to determine
        which completion event the first SYNC event should listen for.
        """
        sync_events = [e for e in events if e.execution_mode == ExecutionMode.SYNC]
        if not sync_events:
            return
        try:
            coro = self._register_webhooks_batch(sync_events, main_job_task_type)
            asyncio.run(coro)
        except RuntimeError:
            # Event loop already running — fall back to thread.
            # The coroutine was already created above; close it to avoid
            # "coroutine was never awaited" RuntimeWarning, then recreate
            # inside the thread.
            coro.close()
            import threading
            def _run():
                try:
                    asyncio.run(self._register_webhooks_batch(sync_events, main_job_task_type))
                except Exception as exc:
                    logger.error("Failed to register webhooks in thread: %s", exc)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=30)
        except Exception as exc:
            logger.error("Failed to register webhooks for events: %s", exc)

    def sync_delete_webhooks_for_events(self, event_ids: List[int]) -> None:
        """Sync wrapper to delete persistent webhooks for the given event IDs.

        Called from JobService.update_job / delete_job (sync context).
        """
        if not event_ids:
            return
        webhook_ids = [f"{_CHRONOS_WEBHOOK_PREFIX}{eid}" for eid in event_ids]
        try:
            coro = self._delete_webhooks_batch(webhook_ids)
            asyncio.run(coro)
        except RuntimeError:
            coro.close()
            import threading
            def _run():
                try:
                    asyncio.run(self._delete_webhooks_batch(webhook_ids))
                except Exception as exc:
                    logger.error("Failed to delete webhooks in thread: %s", exc)
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=30)
        except Exception as exc:
            logger.error("Failed to delete webhooks for events %s: %s", event_ids, exc)

    def sync_delete_webhooks_for_job(self, job_id: int) -> None:
        """Sync wrapper to delete all persistent webhooks for a job's chained events.

        Called from JobService.delete_job (sync context).
        """
        try:
            from gluesync_scheduler.db.database import SessionLocal
            db = SessionLocal()
            try:
                events = db.query(ChainedJobEvent).filter(
                    ChainedJobEvent.parent_job_id == job_id
                ).all()
                event_ids = [e.id for e in events]
            finally:
                db.close()
            self.sync_delete_webhooks_for_events(event_ids)
        except Exception as exc:
            logger.error("Failed to delete webhooks for job %d: %s", job_id, exc)

    async def _register_webhooks_batch(
        self, events: List[ChainedJobEvent], main_job_task_type: TaskType
    ) -> None:
        """Register persistent webhooks for multiple events sequentially.

        For each SYNC event, the webhook listens for the **preceding** step's
        completion event(s) — that's the signal that tells Chronos to proceed.
        """
        # Build a lookup of all events by position to find preceding siblings
        all_events_by_pos = {e.position: e for e in events}
        for event in events:
            if event.position == 0:
                preceding_task_type = main_job_task_type
            else:
                prev = all_events_by_pos.get(event.position - 1)
                preceding_task_type = prev.task_type if prev else main_job_task_type
            webhook_id = await self._register_corehub_webhook(event, preceding_task_type)
            if not webhook_id:
                detail = getattr(self, '_last_webhook_error', 'unknown error')
                logger.error(
                    "Could not register persistent webhook for event %d: %s",
                    event.id, detail,
                )

    async def _delete_webhooks_batch(self, webhook_ids: List[str]) -> None:
        """Delete multiple webhooks from CoreHub in a single GET-then-PUT cycle."""
        if not webhook_ids:
            return
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                logger.warning("Cannot delete webhooks — corehub URL unknown")
                return

            config_endpoint = f"{corehub_url}/global-config/webhooks"
            auth_headers = _get_corehub_auth_headers()
            ssl_verify = _get_corehub_ssl_verify()
            loop = asyncio.get_event_loop()

            ids_to_remove = set(webhook_ids)

            async with self._get_webhook_list_lock():
                get_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(config_endpoint, headers=auth_headers, timeout=10, verify=ssl_verify),
                )
                if get_resp.status_code != 200:
                    logger.warning("Cannot fetch webhook list for batch delete: HTTP %d", get_resp.status_code)
                    return

                try:
                    existing: list = get_resp.json()
                    if not isinstance(existing, list):
                        return
                except Exception:
                    return

                updated = [w for w in existing if w.get("id") not in ids_to_remove]
                if len(updated) == len(existing):
                    # Nothing to delete
                    return

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
                    removed = len(existing) - len(updated)
                    logger.info("Batch deleted %d webhook(s) from corehub", removed)
                else:
                    logger.error(
                        "Failed to batch delete webhooks: HTTP %d — response: %s",
                        put_resp.status_code, put_resp.text[:300],
                    )
        except Exception as exc:
            logger.warning("Exception during batch webhook delete: %s", exc)

    def notify_webhook_received(self, correlation_key: str) -> bool:
        """Called by the webhook receiver endpoint.

        Returns True when the key was pending or buffered, False when unknown.
        Callbacks are only accepted when the correlation key is in the
        ``_active_listeners`` set — i.e. when a chain is actively executing
        and expecting this callback.  Callbacks outside the active window
        are rejected so that stale events (e.g. manual pipeline actions)
        are not consumed by the next scheduled chain run.
        """
        if correlation_key not in self._active_listeners:
            logger.info(
                "notify_webhook_received: rejected key %s — not in active listening window",
                correlation_key,
            )
            return False

        event = self._pending.get(correlation_key)
        if event is not None:
            event.set()
            logger.info("notify_webhook_received: signalled key %s", correlation_key)
            return True
        # Buffer for a waiter that hasn't started yet (within the active window)
        self._pre_arrival[correlation_key] = True
        logger.info("notify_webhook_received: buffered key %s (no waiter yet)", correlation_key)
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
        """Wait for the preceding step's completion callback, then fire the event.

        The webhook is persistent (registered at job create/update time) and
        listens for the **preceding** step's completion event(s).  Here we
        wait for that callback, then fire the actual operation.
        The active listening window is managed by ``execute_chain``.
        """
        correlation_key = str(event.id)
        try:
            # Wait for the preceding step's completion callback first
            timeout = event.webhook_timeout_seconds if event.webhook_timeout_seconds else 3600
            completed = await self._wait_for_webhook(correlation_key, timeout_seconds=timeout)
            if not completed:
                logger.warning(
                    "Timed out waiting for preceding step callback for sync event %d",
                    event.id,
                )
                return False

            # Preceding step completed — now fire the actual task
            ok = await self._execute_chained_event(event, db)
            if not ok:
                return False

            return True
        finally:
            self._pending.pop(correlation_key, None)
            self._pre_arrival.pop(correlation_key, None)

    async def _register_corehub_webhook(
        self, event: ChainedJobEvent, preceding_task_type: TaskType
    ) -> Optional[str]:
        """Register a persistent webhook configuration in the corehub.

        The webhook is keyed by the chained event's database ID and stays
        in CoreHub until the event is deleted or updated.
        ``preceding_task_type`` determines which completion event(s) the webhook
        listens for — this is the **preceding** step's task type, so the
        callback signals that the preceding step is done and this event
        can proceed.
        Returns the webhook id on success, or None on failure.
        """
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                self._last_webhook_error = "CoreHub URL unknown"
                logger.error("Cannot register webhook — corehub URL unknown")
                return None

            callback_url = "{{chronos_address}}/api/webhooks/notify"
            correlation_key = str(event.id)
            webhook_id = f"{_CHRONOS_WEBHOOK_PREFIX}{correlation_key}"

            # Map the **preceding** step's task type to the webhook event(s)
            # that signal its completion.  This is what tells Chronos that
            # the preceding step is done and this event can fire.
            enabled_events = _task_type_to_webhook_events(preceding_task_type)

            # Build filters so the webhook only fires for the specific
            # pipeline / entity / group being targeted.
            entity_ids = _parse_json_list(event.entity_ids)
            group_ids = _parse_json_list(event.group_ids)

            new_webhook = {
                "id": webhook_id,
                "name": f"chronos-event-{event.id}",
                "webhookUrl": callback_url,
                "enabled": True,
                "enabledEvents": enabled_events,
                "pipelineFilter": [event.pipeline_id] if event.pipeline_id else [],
                "entityFilter": entity_ids if entity_ids else [],
                "groupFilter": group_ids if group_ids else [],
                "customHeaders": {
                    "X-Event-ID": correlation_key,
                    "EXT_MODULE": "chronos",
                },
                "skipTlsVerification": True,
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
                async with self._get_webhook_list_lock():
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

                    # Remove any existing webhook with the same id (idempotent upsert)
                    filtered = [w for w in existing if w.get("id") != webhook_id]

                    # PUT updated list
                    updated = filtered + [new_webhook]
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
                        logger.info("Registered corehub webhook %s for event %d", webhook_id, event.id)
                        return webhook_id

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

                async with self._get_webhook_list_lock():
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
        """Remove orphaned ``chronos-sync-*`` webhooks from CoreHub.

        Called at startup.  Compares the webhooks in CoreHub against the
        ChainedJobEvent table:
          - Webhooks whose event ID no longer exists in DB are removed (orphans).
          - Webhooks whose event still exists are left in place (persistent).
        Returns the number of orphaned webhooks removed.
        """
        removed = 0
        try:
            corehub_url = _get_corehub_base()
            if not corehub_url:
                logger.warning("Cannot cleanup stale webhooks — corehub URL unknown")
                return 0

            # Fetch all SYNC event IDs from the DB
            from gluesync_scheduler.db.database import SessionLocal
            db = SessionLocal()
            try:
                valid_events = db.query(ChainedJobEvent).filter(
                    ChainedJobEvent.execution_mode == ExecutionMode.SYNC
                ).all()
                valid_ids = {f"{_CHRONOS_WEBHOOK_PREFIX}{e.id}" for e in valid_events}
            finally:
                db.close()

            config_endpoint = f"{corehub_url}/global-config/webhooks"
            auth_headers = _get_corehub_auth_headers()
            ssl_verify = _get_corehub_ssl_verify()
            loop = asyncio.get_event_loop()

            async with chain_execution_service._get_webhook_list_lock():
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

                # Find chronos webhooks that are orphans (event no longer in DB)
                chronos_webhooks = [w for w in existing if str(w.get("id", "")).startswith(_CHRONOS_WEBHOOK_PREFIX)]
                orphans = [w for w in chronos_webhooks if w.get("id") not in valid_ids]

                if not orphans:
                    logger.info("No orphaned chronos webhooks found in corehub (%d valid)", len(chronos_webhooks))
                    return 0

                orphan_ids = {w.get("id") for w in orphans}
                updated = [w for w in existing if w.get("id") not in orphan_ids]
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
                    removed = len(orphans)
                    logger.info("Cleaned up %d orphaned chronos webhook(s) from corehub", removed)
                else:
                    logger.error(
                        "Failed to cleanup orphaned webhooks: HTTP %d — response: %s",
                        put_resp.status_code, put_resp.text[:300],
                    )
        except Exception as exc:
            logger.warning("Exception during stale webhook cleanup: %s", exc)

        return removed

    async def _wait_for_webhook(self, correlation_key: str, timeout_seconds: int = 3600) -> bool:
        """Block until ``notify_webhook_received`` signals this key, or timeout.

        Checks the pre-arrival buffer first — if the callback already arrived
        before this wait started, returns immediately.
        Uses threading.Event because the chain runs in a separate thread with
        its own event loop, while notify_webhook_received is called from the
        main FastAPI event loop.
        """
        # Check pre-arrival buffer first
        if self._pre_arrival.pop(correlation_key, False):
            logger.info("_wait_for_webhook: consumed pre-arrival callback for key %s", correlation_key)
            return True

        ev = threading.Event()
        self._pending[correlation_key] = ev
        try:
            # Poll the threading.Event in a non-blocking way via run_in_executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: ev.wait(timeout=float(timeout_seconds)),
            )
            if result:
                logger.info("_wait_for_webhook: received signal for key %s", correlation_key)
                return True
            else:
                logger.warning("_wait_for_webhook: timed out waiting for key %s", correlation_key)
                return False
        finally:
            self._pending.pop(correlation_key, None)


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
