"""Shared test fixtures for the MyRide K-12 integration tests."""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlencode

import pytest


def pytest_configure(config):  # noqa: ANN001, ARG001 - pytest hook signature
    """
    Prime CPython's lazily-created concurrent.futures shutdown thread.

    Without this, whichever test happens to be the first to touch an executor
    (often indirectly, via aiohttp's DNS resolver) gets blamed for a "lingering
    thread" by the harness's teardown check, since that thread doesn't exist
    yet when that test's "before" snapshot is taken. Creating it here, before
    any test runs, means every test sees it as pre-existing.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(lambda: None).result()


@pytest.fixture(autouse=True)
def verify_cleanup(
    event_loop, expected_lingering_tasks: bool, expected_lingering_timers: bool
):
    """
    Override pytest_homeassistant_custom_component's own `verify_cleanup`.

    Identical to the upstream fixture, except the final thread-leak check also
    tolerates threads named "_run_safe_shutdown_loop" - a CPython stdlib thread
    that `asyncio.AbstractEventLoop.shutdown_default_executor()` itself creates
    on newer Python 3.12 patch releases, *after* this same fixture's own
    "before" snapshot. It's stdlib housekeeping, not anything our tests or code
    leak; the upstream fixture (pinned before this Python behavior existed)
    doesn't know about it yet. Safe to delete once the harness catches up, or
    if CI runs a Python version where this doesn't trigger.
    """
    import asyncio
    import reprlib
    import threading
    from contextlib import contextmanager

    from homeassistant.core import HassJob
    from homeassistant.util.async_ import get_scheduled_timer_handles
    from pytest_homeassistant_custom_component.common import INSTANCES

    @contextmanager
    def long_repr_strings():
        arepr = reprlib.aRepr
        original_maxstring, original_maxother = arepr.maxstring, arepr.maxother
        arepr.maxstring = arepr.maxother = 300
        try:
            yield
        finally:
            arepr.maxstring, arepr.maxother = original_maxstring, original_maxother

    threads_before = frozenset(threading.enumerate())
    tasks_before = asyncio.all_tasks(event_loop)
    yield

    event_loop.run_until_complete(event_loop.shutdown_default_executor())

    if len(INSTANCES) >= 2:
        count = len(INSTANCES)
        for inst in INSTANCES:
            inst.stop()
        pytest.exit(f"Detected non stopped instances ({count}), aborting test run")

    tasks = asyncio.all_tasks(event_loop) - tasks_before
    for task in tasks:
        if expected_lingering_tasks:
            pass
        else:
            pytest.fail(f"Lingering task after test {task!r}")
        task.cancel()
    if tasks:
        event_loop.run_until_complete(asyncio.wait(tasks))

    for handle in get_scheduled_timer_handles(event_loop):
        if not handle.cancelled():
            with long_repr_strings():
                if expected_lingering_timers:
                    pass
                elif handle._args and isinstance(job := handle._args[-1], HassJob):
                    if job.cancel_on_shutdown:
                        continue
                    pytest.fail(f"Lingering timer after job {job!r}")
                else:
                    pytest.fail(f"Lingering timer after test {handle!r}")
                handle.cancel()

    threads = frozenset(threading.enumerate()) - threads_before
    for thread in threads:
        if isinstance(thread, threading._DummyThread):
            continue
        if thread.name.startswith("waitpid-"):
            continue
        if "_run_safe_shutdown_loop" in thread.name:  # see docstring above
            continue
        pytest.fail(f"Lingering thread after test {thread!r}")


from custom_components.myride_k12.const import API_BASE, COGNITO_URL, HUB_URL


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Required by pytest-homeassistant-custom-component for every test that loads our integration."""
    return


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


# A JWT-shaped (not JWE-shaped) access token, matching what Cognito actually
# returns from InitiateAuth - only the refresh token is the opaque JWE.
FAKE_ACCESS_TOKEN = (
    f"{_b64({'alg': 'RS256', 'kid': 'test'})}."
    f"{_b64({'exp': int(time.time()) + 3600, 'cognito:groups': ['tenant-abc123']})}."
    "fakesig"
)

# Shaped like a real /api/student response (same fixture shape used in the
# original Node bridge's tests, and confirmed against real captures).
STUDENT_PAYLOAD = [
    {
        "uniqueId": 2008416,
        "firstName": "Lucas",
        "lastName": "Gregory",
        "runInfo": [
            {
                "runId": 1,
                "busNumber": "BUS 012",
                "activeVehicle": "BUS 012",
                "stopsInfo": [
                    {"stopTime": "1900-01-01T08:45:00"},
                    {"stopTime": "1900-01-01T09:10:00"},
                ],
            },
            {
                "runId": 2,
                "busNumber": "BUS 012",
                "activeVehicle": "BUS 012",
                "stopsInfo": [
                    {"stopTime": "1900-01-01T15:15:00"},
                    {"stopTime": "1900-01-01T15:45:00"},
                ],
            },
        ],
    }
]

NEGOTIATE_URL = f"{HUB_URL}/negotiate?" + urlencode(
    {"x-tenant-id": "tenant-abc123", "negotiateVersion": "1"}
)


@pytest.fixture
def mock_myride_http(aioclient_mock):
    """
    Mock Cognito refresh + /api/student + negotiate with realistic payloads.

    Uses HA's own aioclient_mock (not aioresponses / a real ClientSession) so it
    intercepts calls made via async_get_clientsession(hass) the same way our
    code makes them, without the resolver-thread teardown issues a real
    ClientSession triggers in this harness. Does NOT mock the WebSocket itself -
    tests that need the live stream mock MyRideApiClient.async_watch directly
    instead (see test_coordinator.py).
    """
    aioclient_mock.post(
        COGNITO_URL,
        json={
            "AuthenticationResult": {
                "AccessToken": FAKE_ACCESS_TOKEN,
                "IdToken": "fake-id-token",
            }
        },
    )
    aioclient_mock.get(f"{API_BASE}/api/student", json=STUDENT_PAYLOAD)
    aioclient_mock.post(
        NEGOTIATE_URL,
        json={
            "negotiateVersion": 1,
            "connectionId": "cid",
            "connectionToken": "ctok",
            "availableTransports": [
                {"transport": "WebSockets", "transferFormats": ["Text"]}
            ],
        },
    )
    return aioclient_mock


@pytest.fixture
def mock_myride_auth_rejected(aioclient_mock):
    """Mock Cognito rejecting the refresh token outright."""
    aioclient_mock.post(
        COGNITO_URL,
        json={"__type": "NotAuthorizedException", "message": "Invalid Refresh Token"},
        status=400,
    )
    return aioclient_mock
