"""Local pytest config for the chronos security suite."""

import pytest

# Individual tests use @pytest.mark.asyncio; setting the mode explicitly
# here avoids a warning from pytest-asyncio >= 0.23 when strict mode is
# implicit.
def pytest_collection_modifyitems(config, items):
    # No-op today; hook reserved for later ordering / marker sync.
    return
