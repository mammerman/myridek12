"""
MyRide K-12 API client: Cognito auth, student lookup, and the live GPS stream.

This is a straight port of the standalone spike script that was run against
the real hub before this integration was written (see README.md). The
negotiate-then-watch approach, header set, and payload shapes below are
confirmed working, not guessed at.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from .const import (
    API_BASE,
    APP_ORIGIN,
    CLIENT_VERSION,
    COGNITO_CLIENT_ID,
    COGNITO_URL,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
    HUB_URL,
    PING_INTERVAL,
    RS,
    SERVER_TIMEOUT,
    SIGNALR_TYPE_CLOSE,
    SIGNALR_TYPE_INVOCATION,
    TOKEN_REFRESH_MARGIN,
    USER_AGENT,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class MyRideError(Exception):
    """Base error for all MyRide API problems."""


class MyRideAuthError(MyRideError):
    """The refresh token is invalid/expired/revoked - need a fresh one from the user."""


class MyRideConnectionError(MyRideError):
    """A transient network/API problem. Worth retrying."""


def _decode_jwt_segment(segment: str) -> dict:
    segment += "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment))


def _browser_headers(tenant_id: str) -> dict[str, str]:
    """Headers the official app sends; MyRide's WAF rejects requests without them."""
    return {
        "User-Agent": USER_AGENT,
        "Origin": APP_ORIGIN,
        "Referer": f"{APP_ORIGIN}/",
        "x-client-language": "en",
        "x-client-version": CLIENT_VERSION,
        "x-device-type": "browser",
        "x-tenant-id": tenant_id,
    }


class MyRideApiClient:
    """Handles auth, the student roster, and the live location WebSocket."""

    def __init__(self, session: aiohttp.ClientSession, refresh_token: str) -> None:
        """Set up the client. Does not make any network calls yet."""
        self._session = session
        self._refresh_token = refresh_token
        self.access_token: str | None = None
        self.tenant_id: str | None = None
        self._expires_at: float = 0

    @property
    def refresh_token(self) -> str:
        """Return the current refresh token (may have been rotated by Cognito)."""
        return self._refresh_token

    async def async_ensure_token(self) -> str:
        """Return a valid access token, refreshing first if missing or near expiry."""
        if (
            not self.access_token
            or (self._expires_at - time.time()) < TOKEN_REFRESH_MARGIN.total_seconds()
        ):
            await self.async_refresh_token()
        assert self.access_token is not None  # noqa: S101 - refresh either succeeds or raises
        return self.access_token

    async def async_refresh_token(self) -> None:
        """Exchange the refresh token for a fresh access token via Cognito."""
        body = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": self._refresh_token},
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }
        try:
            async with self._session.post(
                COGNITO_URL,
                data=json.dumps(body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                if resp.status != HTTP_OK:
                    try:
                        err = json.loads(text)
                    except json.JSONDecodeError:
                        err = {}
                    err_type = err.get("__type", "")
                    message = err.get("message", text[:200])
                    if "NotAuthorizedException" in err_type:
                        raise MyRideAuthError(message)
                    raise MyRideConnectionError(
                        f"Cognito refresh failed ({resp.status}): {err_type} {message}"
                    )
        except TimeoutError as err:
            raise MyRideConnectionError("Timed out contacting Cognito") from err
        except aiohttp.ClientError as err:
            raise MyRideConnectionError(
                f"Network error contacting Cognito: {err}"
            ) from err

        result = json.loads(text)["AuthenticationResult"]
        self.access_token = result["AccessToken"]
        claims = _decode_jwt_segment(self.access_token.split(".")[1])
        self._expires_at = claims["exp"]
        groups = claims.get("cognito:groups") or []
        if groups:
            self.tenant_id = groups[0]
        if result.get("RefreshToken"):
            # Cognito can rotate the refresh token on use; the caller must persist this
            # back into the config entry, or the next refresh uses a stale token.
            self._refresh_token = result["RefreshToken"]

    async def async_get_students(self) -> list[dict[str, Any]]:
        """Fetch today's roster: each student's runs and active vehicle."""
        tenant = self.tenant_id
        if not tenant:
            raise MyRideAuthError("No tenant ID yet; call async_ensure_token() first")
        headers = {
            **_browser_headers(tenant),
            "Authorization": f"Bearer {await self.async_ensure_token()}",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        try:
            async with self._session.get(
                f"{API_BASE}/api/student",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                if resp.status == HTTP_UNAUTHORIZED:
                    raise MyRideAuthError("/api/student rejected the access token")
                if resp.status != HTTP_OK:
                    raise MyRideConnectionError(
                        f"/api/student failed ({resp.status}): {text[:200]}"
                    )
        except TimeoutError as err:
            raise MyRideConnectionError("Timed out contacting /api/student") from err
        except aiohttp.ClientError as err:
            raise MyRideConnectionError(
                f"Network error contacting /api/student: {err}"
            ) from err
        return json.loads(text)

    async def _async_negotiate(self) -> tuple[str, str, str]:
        """POST negotiate - registers us as a watcher and activates the GPS relay."""
        tenant = self.tenant_id
        assert tenant is not None  # noqa: S101 - caller always holds a token by this point
        hub_url = HUB_URL
        access_token = await self.async_ensure_token()
        for _ in range(3):  # redirects are rare on this hub; cap them defensively
            url = URL(f"{hub_url}/negotiate").update_query(
                {"x-tenant-id": tenant, "negotiateVersion": "1"}
            )
            headers = {
                **_browser_headers(tenant),
                "Authorization": f"Bearer {access_token}",
                "X-Requested-With": "XMLHttpRequest",
            }
            try:
                async with self._session.post(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    text = await resp.text()
                    if resp.status == HTTP_UNAUTHORIZED:
                        raise MyRideAuthError("Negotiate rejected the access token")
                    if resp.status != HTTP_OK:
                        raise MyRideConnectionError(
                            f"Negotiate failed ({resp.status}): {text[:200]}"
                        )
            except TimeoutError as err:
                raise MyRideConnectionError(
                    "Timed out negotiating with the hub"
                ) from err
            except aiohttp.ClientError as err:
                raise MyRideConnectionError(
                    f"Network error negotiating with the hub: {err}"
                ) from err

            data = json.loads(text)
            if data.get("error"):
                raise MyRideConnectionError(f"Negotiate error: {data['error']}")
            if data.get("url"):
                hub_url = data["url"].split("?")[0]
                access_token = data.get("accessToken", access_token)
                continue
            transports = [
                t.get("transport") for t in data.get("availableTransports", [])
            ]
            if "WebSockets" not in transports:
                raise MyRideConnectionError(
                    f"Hub did not offer WebSockets (offered {transports})"
                )
            token = data.get("connectionToken") or data["connectionId"]
            return hub_url, token, access_token
        raise MyRideConnectionError("Too many negotiate redirects")

    async def async_watch(
        self,
        on_location: Callable[[dict[str, Any]], None],
        on_event: Callable[[str, list[Any]], None] | None = None,
    ) -> None:
        """
        Run one connection lifecycle against the LiveVehicleHub.

        Negotiates, opens the WebSocket, completes the SignalR handshake, then
        calls `on_location` for each NewLocation event and `on_event` for
        anything else the hub sends, until the socket closes or goes silent.

        Raises MyRideAuthError (not worth retrying without a new token) or
        MyRideConnectionError/TimeoutError (worth retrying with backoff) when
        the connection ends. The caller owns the retry loop, matching the
        spike's proven reconnect-with-fresh-negotiate approach - HA's
        integration for automatic reconnection is a DataUpdateCoordinator
        background task, not this method.
        """
        hub_url, conn_token, access_token = await self._async_negotiate()

        hub = URL(hub_url)
        ws_url = hub.with_scheme("wss" if hub.scheme == "https" else "ws").update_query(
            {
                "x-tenant-id": self.tenant_id,
                "id": conn_token,
                "access_token": access_token,
            }
        )
        ws_headers = {"User-Agent": USER_AGENT, "Origin": APP_ORIGIN}

        try:
            async with self._session.ws_connect(
                ws_url, headers=ws_headers, autoping=True
            ) as ws:
                await ws.send_str(json.dumps({"protocol": "json", "version": 1}) + RS)
                handshake_done = False

                async def pinger() -> None:
                    try:
                        while True:
                            await asyncio.sleep(PING_INTERVAL)
                            await ws.send_str('{"type":6}' + RS)
                    except (ConnectionResetError, RuntimeError):
                        pass  # socket went away; the receive loop below will report it

                ping_task = asyncio.ensure_future(pinger())
                try:
                    while True:
                        try:
                            msg = await ws.receive(timeout=SERVER_TIMEOUT)
                        except TimeoutError as err:
                            raise MyRideConnectionError(
                                f"No traffic (not even pings) for {SERVER_TIMEOUT}s"
                            ) from err
                        if msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSED,
                        ):
                            raise MyRideConnectionError(
                                f"Socket closed by server (code={ws.close_code})"
                            )
                        if msg.type == aiohttp.WSMsgType.ERROR:
                            raise MyRideConnectionError(
                                f"Socket error: {ws.exception()!r}"
                            )
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue

                        for record in msg.data.split(RS):
                            if not record:
                                continue
                            frame = json.loads(record)
                            if not handshake_done:
                                handshake_done = True
                                if frame.get("error"):
                                    raise MyRideConnectionError(
                                        f"Handshake rejected: {frame['error']}"
                                    )
                                continue
                            _handle_frame(frame, on_location, on_event)
                finally:
                    ping_task.cancel()
        except TimeoutError as err:
            raise MyRideConnectionError("Timed out opening the WebSocket") from err
        except aiohttp.ClientError as err:
            raise MyRideConnectionError(f"WebSocket connection failed: {err}") from err


def _handle_frame(
    frame: dict[str, Any],
    on_location: Callable[[dict[str, Any]], None],
    on_event: Callable[[str, list[Any]], None] | None,
) -> None:
    kind = frame.get("type")
    if kind == SIGNALR_TYPE_INVOCATION:
        target = frame.get("target", "")
        arguments = frame.get("arguments", [])
        if target == "NewLocation":
            for location in arguments:
                on_location(location)
        elif on_event is not None:
            on_event(target, arguments)
    elif kind == SIGNALR_TYPE_CLOSE:
        raise MyRideConnectionError(f"Server sent Close: {frame.get('error')!r}")
    # type 6 (ping) and anything else: nothing to do
