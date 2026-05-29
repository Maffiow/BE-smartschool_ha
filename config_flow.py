"""Config flow for Smartschool Results integration."""
import logging
import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import CONF_BASE_URL, CONF_BIRTHDAY, DOMAIN
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError, SmartschoolBirthdayError

_LOGGER = logging.getLogger(__name__)

_BIRTHDAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SmartschoolResultsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smartschool Results."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=(user_input or {}).get(CONF_BASE_URL, "")): str,
                vol.Required(CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_BIRTHDAY,
                    description={"suggested_value": (user_input or {}).get(CONF_BIRTHDAY, "")},
                ): str,
            }
        )

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            birthday = user_input[CONF_BIRTHDAY].strip()

            if not base_url.startswith(("http://", "https://")):
                errors["base"] = "invalid_base_url"
            elif not username:
                errors["base"] = "invalid_auth"
            elif not _BIRTHDAY_RE.match(birthday):
                errors[CONF_BIRTHDAY] = "invalid_birthday"
            else:
                api = SmartschoolAPI(base_url, username, password, birthday)
                try:
                    await self.hass.async_add_executor_job(api.login)
                except SmartschoolBirthdayError as err:
                    _LOGGER.warning("Smartschool birthday verification failed: %s", err)
                    errors[CONF_BIRTHDAY] = "invalid_birthday"
                except SmartschoolAuthError as err:
                    _LOGGER.warning("Smartschool login failed: %s", err)
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Unexpected error during Smartschool login")
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(f"{base_url}|{username}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Smartschool ({username})",
                        data={
                            CONF_BASE_URL: base_url,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_BIRTHDAY: birthday,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
