"""
Shared pytest fixtures and mocks.

The SchedulerService singleton requires a running asyncio event loop when
its module is imported, which breaks plain pytest collection.  We stub it
out via sys.modules BEFORE any scheduler-related import can happen.
"""

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Build a minimal fake module so that
#   from gluesync_scheduler.services.scheduler_service import scheduler_service
# returns a MagicMock without triggering the real AsyncIOScheduler init.
# ---------------------------------------------------------------------------

_mock_svc = MagicMock()
_mock_svc.create_job.return_value = "mock-scheduler-job-id"
_mock_svc.update_job.return_value = "mock-scheduler-job-id"
_mock_svc.remove_job.return_value = None

_fake_module = MagicMock()
_fake_module.scheduler_service = _mock_svc

sys.modules.setdefault(
    "gluesync_scheduler.services.scheduler_service", _fake_module
)

# Also stub gluesync_sdk which is a native dependency not available in CI
import types as _types

_sdk_mod = _types.ModuleType("gluesync_sdk")
for _name in [
    "GluesyncSDK", "GluesyncClient",
    "GluesyncError", "GluesyncConnectionError",
    "GluesyncAuthenticationError", "GluesyncLicenseError",
]:
    setattr(_sdk_mod, _name, type(_name, (Exception,), {}) if "Error" in _name else MagicMock)
sys.modules.setdefault("gluesync_sdk", _sdk_mod)
