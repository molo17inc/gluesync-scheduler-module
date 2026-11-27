#!/usr/bin/env python3
"""Startup must not wait for Core Hub: the HTTP server (and /health) comes up while the SDK client is still connecting."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.core import app as app_module


def _patch_startup_steps(init_sdk, calls):
    """Replace every startup step so the test touches no database, file or network."""
    return [
        patch.object(app_module, "_print_version_banner", MagicMock()),
        patch.object(app_module, "_ensure_runtime_dirs", MagicMock()),
        patch.object(app_module, "_resolve_db_url", MagicMock(return_value="sqlite://")),
        patch.object(app_module, "_init_database", MagicMock()),
        patch.object(app_module, "_init_default_settings", MagicMock()),
        patch.object(app_module, "_log_configuration", MagicMock()),
        patch.object(app_module, "_init_sdk_client", init_sdk),
        patch.object(app_module, "_start_health_check", MagicMock(side_effect=lambda: calls.append("health_check"))),
        patch.object(app_module, "_load_existing_jobs", MagicMock(side_effect=lambda: calls.append("load_jobs") or ["missed"])),
        patch.object(app_module, "_start_catchup", MagicMock(side_effect=lambda runs: calls.append(("catchup", runs)))),
        patch.object(app_module, "_cleanup_stale_webhooks", AsyncMock(side_effect=lambda: calls.append("cleanup"))),
    ]


async def _run_startup(init_sdk, calls):
    patches = _patch_startup_steps(init_sdk, calls)
    for p in patches:
        p.start()
    try:
        await asyncio.wait_for(app_module.startup_event(), timeout=2)
        return app_module._corehub_link_task, patches
    except Exception:
        for p in patches:
            p.stop()
        raise


async def test_startup_returns_while_corehub_never_answers():
    never = asyncio.Event()
    calls = []

    async def init_sdk():
        calls.append("init_sdk")
        await never.wait()  # Core Hub unreachable: the SDK client retries forever

    task, patches = await _run_startup(init_sdk, calls)
    try:
        await asyncio.sleep(0)
        assert not task.done()
        # Nothing that needs Core Hub ran yet
        assert calls == ["init_sdk"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for p in patches:
            p.stop()


async def test_corehub_steps_run_in_order_once_connected():
    connected = asyncio.Event()
    calls = []

    async def init_sdk():
        calls.append("init_sdk")
        await connected.wait()

    task, patches = await _run_startup(init_sdk, calls)
    try:
        await asyncio.sleep(0)  # let the background task start
        assert calls == ["init_sdk"]
        connected.set()
        await asyncio.wait_for(task, timeout=2)
        assert calls == ["init_sdk", "health_check", "load_jobs", ("catchup", ["missed"]), "cleanup"]
    finally:
        for p in patches:
            p.stop()


async def test_link_failure_does_not_escape():
    calls = []

    async def init_sdk():
        raise RuntimeError("boom")

    task, patches = await _run_startup(init_sdk, calls)
    try:
        await asyncio.wait_for(task, timeout=2)  # logged, not raised
        assert calls == []
    finally:
        for p in patches:
            p.stop()
