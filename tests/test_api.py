"""Tests for api.py against mocked HTTP responses."""

from __future__ import annotations

import pytest

from custom_components.myride_k12.api import (
    MyRideApiClient,
    MyRideAuthError,
    MyRideConnectionError,
)


async def test_refresh_token_success(hass, mock_myride_http):
    """A successful Cognito refresh sets the access token and derives the tenant ID."""
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "fake-refresh-token"
    )
    await client.async_refresh_token()
    assert client.access_token is not None
    assert client.tenant_id == "tenant-abc123"


async def test_refresh_token_rejected(hass, mock_myride_auth_rejected):
    """An invalid/expired refresh token raises MyRideAuthError, not a generic error."""
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "bad-token"
    )
    with pytest.raises(MyRideAuthError):
        await client.async_refresh_token()


async def test_get_students_requires_tenant(hass):
    """Fetching students before we have a tenant ID (i.e. before refreshing) is a clear error."""
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "fake-refresh-token"
    )
    with pytest.raises(MyRideAuthError):
        await client.async_get_students()


async def test_get_students_success(hass, mock_myride_http):
    """After refreshing, /api/student returns the roster."""
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "fake-refresh-token"
    )
    await client.async_refresh_token()
    students = await client.async_get_students()
    assert len(students) == 1
    assert students[0]["firstName"] == "Lucas"


async def test_negotiate_success(hass, mock_myride_http):
    """Negotiate returns a hub URL, connection token, and access token for the WebSocket."""
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "fake-refresh-token"
    )
    await client.async_refresh_token()
    hub_url, conn_token, access_token = await client._async_negotiate()
    assert conn_token == "ctok"
    assert access_token == client.access_token
    assert "livevehiclehub" in hub_url


async def test_negotiate_no_websockets_offered(hass, aioclient_mock):
    """If the hub doesn't offer WebSockets, that's a connection error, not a silent hang."""
    from custom_components.myride_k12.const import API_BASE, COGNITO_URL

    from .conftest import FAKE_ACCESS_TOKEN, NEGOTIATE_URL

    aioclient_mock.post(
        COGNITO_URL,
        json={"AuthenticationResult": {"AccessToken": FAKE_ACCESS_TOKEN}},
    )
    aioclient_mock.get(f"{API_BASE}/api/student", json=[])
    aioclient_mock.post(
        NEGOTIATE_URL,
        json={
            "negotiateVersion": 1,
            "connectionId": "cid",
            "availableTransports": [{"transport": "LongPolling"}],
        },
    )
    client = MyRideApiClient(
        hass.helpers.aiohttp_client.async_get_clientsession(), "fake-refresh-token"
    )
    await client.async_refresh_token()
    with pytest.raises(MyRideConnectionError, match="WebSockets"):
        await client._async_negotiate()
