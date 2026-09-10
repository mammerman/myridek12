"""
Config flow for MyRide K-12.

Setup asks for one thing: the Cognito refresh token captured from the browser
(see README.md for the capture-tokens.js step, ported unmodified from the
original bridge). The tenant ID doesn't need to be asked for separately - it's
the first `cognito:groups` claim in the access token we get back, so it's
derived automatically once the token validates.

Reauth is the direct replacement for the original bridge's separate status
page + credentials binary sensor: when the refresh token expires (~30 days),
Home Assistant's own reauth flow prompts for a new one right in the UI.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MyRideApiClient, MyRideAuthError, MyRideConnectionError
from .const import CONF_REFRESH_TOKEN, CONF_TENANT_ID, DOMAIN, LOGGER

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REFRESH_TOKEN): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD, multiline=True
            ),
        ),
    }
)


class MyRideConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup and reauth for MyRide K-12."""

    VERSION = 1

    async def _async_validate(
        self, refresh_token: str
    ) -> tuple[str | None, dict[str, str]]:
        """Try the token against Cognito; return (tenant_id, errors)."""
        client = MyRideApiClient(
            async_get_clientsession(self.hass), refresh_token.strip()
        )
        try:
            await client.async_refresh_token()
            await client.async_get_students()
        except MyRideAuthError as err:
            LOGGER.warning("MyRide token rejected: %s", err)
            return None, {"base": "invalid_auth"}
        except MyRideConnectionError as err:
            LOGGER.error("MyRide connection error during setup: %s", err)
            return None, {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - surface as a generic error rather than crashing the flow
            LOGGER.exception("Unexpected error validating MyRide token")
            return None, {"base": "unknown"}
        if not client.tenant_id:
            return None, {"base": "no_tenant"}
        return client.tenant_id, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """First and only setup step: paste the refresh token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            tenant_id, errors = await self._async_validate(
                user_input[CONF_REFRESH_TOKEN]
            )
            if tenant_id:
                await self.async_set_unique_id(tenant_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="MyRide K-12",
                    data={
                        CONF_REFRESH_TOKEN: user_input[CONF_REFRESH_TOKEN].strip(),
                        CONF_TENANT_ID: tenant_id,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],  # noqa: ARG002 - required by HA's reauth flow signature
    ) -> config_entries.ConfigFlowResult:
        """Start reauth when the coordinator hits an auth failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for a fresh refresh token and swap it into the existing entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            tenant_id, errors = await self._async_validate(
                user_input[CONF_REFRESH_TOKEN]
            )
            if tenant_id:
                reauth_entry = self._get_reauth_entry()
                if tenant_id != reauth_entry.data.get(CONF_TENANT_ID):
                    return self.async_abort(reason="reauth_account_mismatch")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_REFRESH_TOKEN: user_input[CONF_REFRESH_TOKEN].strip(),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )
