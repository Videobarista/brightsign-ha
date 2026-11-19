"""Fixtures for BrightSign tests."""

from unittest.mock import AsyncMock, patch

import pytest

SCAN = "custom_components.brightsign.config_flow.BrightSignConfigFlow._async_scan"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return


@pytest.fixture(autouse=True)
def no_network_scan():
    """No real network scan in tests; tests that need players found patch it again."""
    with patch(SCAN, AsyncMock(return_value={})) as mock:
        yield mock
