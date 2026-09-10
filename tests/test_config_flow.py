"""Tests for config_flow.py, driven through Home Assistant's real flow manager."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.myride_k12.const import (
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    DOMAIN,
)


async def test_user_flow_success(hass, mock_myride_http):
    """A valid token creates an entry with the tenant ID derived from it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "fake-refresh-token"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REFRESH_TOKEN] == "fake-refresh-token"
    assert result["data"][CONF_TENANT_ID] == "tenant-abc123"


async def test_user_flow_invalid_token(hass, mock_myride_auth_rejected):
    """A rejected token shows the form again with an invalid_auth error, not a crash."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "bad-token"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_duplicate_tenant_aborts(hass, mock_myride_http):
    """Setting up the same MyRide account twice aborts as already_configured."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "fake-refresh-token"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], {CONF_REFRESH_TOKEN: "fake-refresh-token"}
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_reauth_flow_updates_existing_entry(hass, mock_myride_http):
    """Reauth with a valid new token for the same tenant updates the entry in place."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="tenant-abc123",
        data={CONF_REFRESH_TOKEN: "old-stale-token", CONF_TENANT_ID: "tenant-abc123"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "fresh-new-token"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "fresh-new-token"


async def test_reauth_flow_wrong_account_mismatch(hass, aioclient_mock):
    """Reauth with a token for a *different* MyRide account is rejected, not silently swapped in."""
    import base64
    import json
    import time

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myride_k12.const import API_BASE, COGNITO_URL

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="tenant-abc123",
        data={CONF_REFRESH_TOKEN: "old-stale-token", CONF_TENANT_ID: "tenant-abc123"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    # A token that validates fine, but belongs to a *different* tenant/account.
    other_tenant_token = (
        f"{b64({'alg': 'RS256'})}."
        f"{b64({'exp': int(time.time()) + 3600, 'cognito:groups': ['tenant-DIFFERENT']})}.sig"
    )
    aioclient_mock.post(
        COGNITO_URL, json={"AuthenticationResult": {"AccessToken": other_tenant_token}}
    )
    aioclient_mock.get(f"{API_BASE}/api/student", json=[])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REFRESH_TOKEN: "token-for-a-different-account"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_account_mismatch"
    assert entry.data[CONF_REFRESH_TOKEN] == "old-stale-token"  # unchanged
