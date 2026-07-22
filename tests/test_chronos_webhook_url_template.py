#!/usr/bin/env python3
"""Regression: CoreHub webhooks must use {{chronos_address}}, not localhost.

Commit cb4c40c ("url builder") replaced template webhook URLs with a concrete
``https://localhost:1717/...`` base. CoreHub then POSTed callbacks to itself
and got HTTP 302 from the SPA StatusPages handler.

Human-facing TriggerFlow fire URLs still use the concrete callback base
(frontend overrides with window.location.origin + '/chronos').
"""

import os
import unittest
from unittest import mock


class ChronosWebhookUrlTemplateTests(unittest.TestCase):
    def test_corehub_webhook_urls_use_chronos_address_template(self):
        from gluesync_scheduler.services.chain_execution_service import (
            _corehub_chronos_webhook_url,
        )

        notify = _corehub_chronos_webhook_url("/api/webhooks/notify")
        platform = _corehub_chronos_webhook_url("/api/webhooks/platform-event")

        self.assertEqual(notify, "{{chronos_address}}/api/webhooks/notify")
        self.assertEqual(platform, "{{chronos_address}}/api/webhooks/platform-event")
        self.assertNotIn("localhost", notify)
        self.assertNotIn("https://", notify)

    def test_register_corehub_webhook_payload_uses_template(self):
        import asyncio
        from gluesync_scheduler.models.models import (
            ChainedJobEvent,
            ExecutionMode,
            TaskType,
        )
        from gluesync_scheduler.services.chain_execution_service import (
            ChainExecutionService,
        )

        service = ChainExecutionService()
        event = ChainedJobEvent(
            id=3,
            parent_job_id=2,
            position=0,
            task_type=TaskType.ENTITY_START,
            pipeline_id="pipe-1",
            entity_ids='["ent-1"]',
            group_ids=None,
            with_snapshot=False,
            snapshot_write_method="UPSERT",
            execution_mode=ExecutionMode.SYNC,
            webhook_timeout_seconds=3600,
        )

        captured = {}

        def fake_put(url, json=None, headers=None, timeout=None, verify=None):
            captured["url"] = url
            captured["json"] = json
            resp = mock.Mock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        def fake_get(url, headers=None, timeout=None, verify=None):
            resp = mock.Mock()
            resp.status_code = 200
            resp.json.return_value = []
            resp.text = "[]"
            return resp

        with mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_base",
            return_value="https://gluesync-core-hub:1717",
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_auth_headers",
            return_value={"Authorization": "Bearer x"},
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_ssl_verify",
            return_value=False,
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service.requests.get",
            side_effect=fake_get,
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service.requests.put",
            side_effect=fake_put,
        ):
            webhook_id = asyncio.run(
                service._register_corehub_webhook(event, TaskType.ENTITY_SNAPSHOT)
            )

        self.assertEqual(webhook_id, "chronos-sync-3")
        self.assertTrue(captured.get("json"))
        registered = captured["json"][-1]
        self.assertEqual(
            registered["webhookUrl"],
            "{{chronos_address}}/api/webhooks/notify",
        )
        self.assertNotIn("localhost", registered["webhookUrl"])

    def test_register_platform_event_webhook_payload_uses_template(self):
        import asyncio
        from gluesync_scheduler.services.chain_execution_service import (
            ChainExecutionService,
        )

        service = ChainExecutionService()
        captured = {}

        def fake_put(url, json=None, headers=None, timeout=None, verify=None):
            captured["json"] = json
            resp = mock.Mock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        def fake_get(url, headers=None, timeout=None, verify=None):
            resp = mock.Mock()
            resp.status_code = 200
            resp.json.return_value = []
            resp.text = "[]"
            return resp

        with mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_base",
            return_value="https://gluesync-core-hub:1717",
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_auth_headers",
            return_value={"Authorization": "Bearer x"},
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service._get_corehub_ssl_verify",
            return_value=False,
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service.requests.get",
            side_effect=fake_get,
        ), mock.patch(
            "gluesync_scheduler.services.chain_execution_service.requests.put",
            side_effect=fake_put,
        ):
            webhook_id = asyncio.run(
                service._register_platform_event_webhook(1, "ENTITY_SNAPSHOT_COMPLETED")
            )

        self.assertEqual(webhook_id, "chronos-platform-1")
        registered = captured["json"][-1]
        self.assertEqual(
            registered["webhookUrl"],
            "{{chronos_address}}/api/webhooks/platform-event",
        )

    def test_display_callback_base_still_concrete_for_trigger_urls(self):
        from gluesync_scheduler.services.chain_execution_service import (
            _get_chronos_callback_base,
        )
        from gluesync_scheduler.services.trigger_flow_service import _build_trigger_url

        with mock.patch.dict(
            os.environ,
            {
                "CHRONOS_CALLBACK_URL": "",
                "SSL_ENABLED": "true",
                "SCHEDULER_INTERNAL_HOST": "localhost",
                "PORT": "1717",
            },
            clear=False,
        ):
            # Ensure empty CHRONOS_CALLBACK_URL falls through to host/port
            os.environ.pop("CHRONOS_CALLBACK_URL", None)
            base = _get_chronos_callback_base()
            fire = _build_trigger_url(42)

        self.assertEqual(base, "https://localhost:1717")
        self.assertEqual(fire, "https://localhost:1717/api/triggers/42/fire")
        self.assertNotIn("{{chronos_address}}", fire)


if __name__ == "__main__":
    unittest.main()
