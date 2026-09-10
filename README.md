# MyRide K-12 for Home Assistant

Real-time school bus tracking from Tyler Technologies' MyRide K-12 platform, as
a native Home Assistant integration: configured entirely through the UI,
installable via HACS, with one `device_tracker` per student that follows
whichever bus is active today (regular or substitute).

This is a Python/native-integration rewrite of
[legrego/myride-ha-bridge](https://github.com/legrego/myride-ha-bridge) (MIT),
which did the hard work of reverse engineering MyRide's Cognito auth flow and
SignalR hub. That project ships as a standalone Node.js service that publishes
to MQTT; this one runs inside Home Assistant directly, with a config flow, a
proper reauth flow, and entities native to HA.

## Status

**Scaffold with real test coverage, not yet run against a live Home Assistant
UI or a real bus route.** The API/auth/streaming logic (`api.py`,
`student_tracker.py`) was validated against the real MyRide hub with a
standalone spike script before being ported here. From there:

- `custom_components/myride_k12/` has 25 tests in `tests/`, run against a real
  `pytest-homeassistant-custom-component` harness (an actual `HomeAssistant`
  instance, not just imports) - covering Cognito auth, `/api/student`,
  negotiate, the full UI config flow (success/invalid-token/duplicate-account),
  the native reauth flow (including rejecting a token for the *wrong* MyRide
  account), and the staleness/live detection logic, including the exact
  repeated-stale-heartbeat scenario observed during the real spike run.
  `student_tracker.py`'s run-selection logic passes the same cases as the
  original bridge's own test suite.
- **Not yet covered**: a real WebSocket connection from within HA (tests mock
  the HTTP calls but not the socket itself), and anything only visible in an
  actual running instance - the frontend rendering, a real config flow click
  through the UI, `strings.json`/`translations` actually loading, hassfest/HACS
  validation.
- Test environment note: these tests ran against `homeassistant==2025.1.4`ish
  (whatever the sandbox's package index had cached), not the `2026.6.4`
  pinned in `requirements_dev.txt`/`hacs.json`. Re-run `pytest` inside the
  devcontainer (which has real internet access) before trusting this further -
  a newer HA version could surface API differences these tests didn't catch.

Next step: stand up the devcontainer (`scripts/setup` then `scripts/develop`),
re-run `pytest` there, then do a real config flow + a real bus route.

## What we learned from the spike (and how this integration handles it)

- **Negotiating registers you as a watcher independently.** You don't need the
  official app open first - a fresh `negotiate` call each connection attempt
  activates the GPS relay for your own students' buses.
- **The hub re-broadcasts a stale cached position on some heartbeat interval**,
  even when a bus isn't actively reporting fresh GPS - same lat/lng, same
  `logTime`, arriving repeatedly. There's no explicit "trip started" or "trip
  ended" event. So instead of a speed-based "moving" sensor (the original
  bridge's approach), each student has a `live` binary sensor driven by
  whether `logTime` has actually *advanced* recently - see
  `const.DEFAULT_STALE_AFTER` and `coordinator.is_live()`.
- **District stop times are local wall-clock times.** The original bridge
  needed a manual `TZ` environment variable to interpret them correctly. This
  integration uses Home Assistant's own configured timezone instead
  (`homeassistant.util.dt.now()`), so there's one less setting to get wrong -
  as long as HA's timezone matches the district's, which is the common case.

## Installation

Not yet published. For now:

1. Add this repository to HACS as a custom repository (category: Integration).
2. Install "MyRide K-12", restart Home Assistant.
3. Settings → Devices & services → Add integration → search "MyRide K-12".

## Setup

You'll need a Cognito **refresh token** captured from your browser session -
same one-time step as the original bridge:

1. Log in to [myridek12.tylerapp.com](https://myridek12.tylerapp.com) in Chrome.
2. Open DevTools (F12) → Console.
3. Paste the contents of `capture-tokens.js` (from the original bridge's repo;
   we haven't duplicated it here to avoid drifting out of sync - grab the
   latest from
   [legrego/myride-ha-bridge/src/capture-tokens.js](https://github.com/legrego/myride-ha-bridge/blob/main/src/capture-tokens.js))
   and press Enter.
4. Copy the value printed after `✅ REFRESH TOKEN FOUND` / `MYRIDE_REFRESH_TOKEN=`.
   **Not** the access token or ID token - both live in the same browser storage
   the capture script searches, and it's an easy mix-up. A real refresh token
   is a 5-part JWE (`header.key.iv.ciphertext.tag`) whose header decodes to
   something like `{"alg":"RSA-OAEP","enc":"A256GCM","cty":"JWT"}`. An access
   or ID token is a 3-part JWT whose header starts `{"kid":...}` - if
   Home Assistant rejects your token as "invalid_auth", check this first.
5. Paste it into the "Refresh token" field during setup. The district tenant
   ID is derived automatically from the token - no need to look it up
   separately.

Refresh tokens last about 30 days. When one expires, Home Assistant will
prompt you to reauthenticate right in Settings → Devices & services - just
repeat steps 1-4 with a fresh token.

## Entities

Per student (device named after the student's first name):

| Entity | Description |
| --- | --- |
| `device_tracker.<student>_bus` | Map position, following today's active bus |
| `sensor.<student>_speed` | Speed (mph) |
| `sensor.<student>_heading` | Compass heading (degrees) |
| `sensor.<student>_bus` | Which bus is active today |
| `binary_sensor.<student>_live` | On when the last position fix is fresh, off when it looks like a stale heartbeat |
| `binary_sensor.<student>_substitute` | On when today's bus differs from the student's regular bus |

## Development

Scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint)
(MIT), which provides the devcontainer and HACS-compatible layout.

Still to do:
- [ ] Re-run `pytest` inside the devcontainer against the pinned HA version (this
      scaffold's tests ran against an older HA release - see Status above)
- [ ] Test the full config flow + a real bus route inside a dev HA instance
- [ ] Mock the WebSocket itself in tests, not just the HTTP calls (negotiate,
      auth, roster) - `test_config_flow.py`'s reauth tests currently let the
      background stream task attempt (and fail) a real connection in the
      background, which works but isn't fully isolated
- [ ] Tune `DEFAULT_STALE_AFTER` against a full-route capture (currently a guess)
- [ ] Options flow for an explicit timezone override, for districts outside
      the HA instance's own timezone
- [ ] hassfest/HACS validation CI

## License

MIT - see `LICENSE`. Derivative work; endpoints/auth-flow credit to
[legrego/myride-ha-bridge](https://github.com/legrego/myride-ha-bridge).
