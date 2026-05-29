"""Platform for Smartschool Results sensor."""
import logging
import re
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import CONF_BASE_URL, CONF_BIRTHDAY, DEFAULT_SCAN_INTERVAL_MINUTES, DOMAIN
from .smartschool_api import SmartschoolAPI, SmartschoolAuthError

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

_SAFE_RE = re.compile(r"[^a-z0-9]+")


def _course_slug(course_name: str) -> str:
    """Convert a course name to a safe slug for use in unique IDs."""
    return _SAFE_RE.sub("_", course_name.lower()).strip("_")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smartschool sensors from a config entry."""
    base_url = config_entry.data[CONF_BASE_URL]
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    birthday = config_entry.data.get(CONF_BIRTHDAY, "")

    api = SmartschoolAPI(base_url, username, password, birthday)

    async def async_update_data():
        try:
            await hass.async_add_executor_job(api.login)
            data = await hass.async_add_executor_job(api.get_all_evaluations)
            if data is None:
                _LOGGER.warning("Smartschool returned no evaluation data.")
                return None
            return data
        except SmartschoolAuthError as err:
            raise UpdateFailed(f"Authentication/data error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Smartschool: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="smartschool_results",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    # Track which course sensors already exist so we don't add them twice.
    known_courses: set[str] = set()

    @callback
    def _add_new_course_sensors() -> None:
        """Add a sensor for every course that doesn't have one yet."""
        data = coordinator.data
        if not data:
            return
        by_course: dict = data.get("by_course", {})
        new_courses = set(by_course.keys()) - known_courses
        if not new_courses:
            return
        known_courses.update(new_courses)
        _LOGGER.debug("Adding sensors for new courses: %s", new_courses)
        async_add_entities(
            [
                SmartschoolCourseSensor(coordinator, course, username, config_entry.entry_id)
                for course in sorted(new_courses)
            ]
        )

    # Listen for coordinator updates to catch newly appearing courses.
    coordinator.async_add_listener(_add_new_course_sensors)

    await coordinator.async_refresh()

    # Add the "latest result" overview sensor once.
    async_add_entities(
        [SmartschoolLatestResultSensor(coordinator, username, config_entry.entry_id)]
    )

    # Add initial course sensors (if data already available).
    _add_new_course_sensors()


class SmartschoolLatestResultSensor(CoordinatorEntity, SensorEntity):
    """Shows the most recently available result across all courses."""

    _attr_icon = "mdi:school"

    def __init__(self, coordinator: DataUpdateCoordinator, username: str, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_name = f"Smartschool Laatste Resultaat ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_latest_result"

    @property
    def native_value(self):
        if not self.coordinator.last_update_success:
            return "Niet beschikbaar"
        data = self.coordinator.data
        if not data:
            return "Geen resultaten"
        latest = data.get("latest", {})
        return latest.get("name") or latest.get("score_description") or "Resultaat gevonden"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        latest = data.get("latest", {})
        recent = data.get("recent", [])
        attrs = {
            "vak": latest.get("course"),
            "score": latest.get("score_description"),
            "score_percentage": latest.get("score_value"),
            "datum": latest.get("date"),
            "beschikbaar_sinds": latest.get("availability_date"),
            "feedback": latest.get("feedback"),
            "recente_resultaten": recent,
            "last_update_success": self.coordinator.last_update_success,
        }
        if self.coordinator.last_exception:
            attrs["last_error"] = str(self.coordinator.last_exception)
        return attrs


class SmartschoolCourseSensor(CoordinatorEntity, SensorEntity):
    """Shows the latest score for a specific course."""

    _attr_icon = "mdi:book-open-variant"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        course_name: str,
        username: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._course_name = course_name
        self._attr_name = f"Smartschool {course_name} ({username})"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_course_{_course_slug(course_name)}"

    def _get_course_data(self) -> dict:
        data = self.coordinator.data or {}
        return (data.get("by_course") or {}).get(self._course_name, {})

    @property
    def native_value(self):
        """Return the latest score (numeric if possible, else description)."""
        if not self.coordinator.last_update_success:
            return None
        ev = self._get_course_data().get("latest", {})
        if not ev:
            return None
        score = ev.get("score_value")
        if score is not None:
            try:
                return float(score)
            except (ValueError, TypeError):
                pass
        return ev.get("score_description")

    @property
    def native_unit_of_measurement(self):
        ev = self._get_course_data().get("latest", {})
        if ev.get("score_value") is not None:
            try:
                float(ev["score_value"])
                return "%"
            except (ValueError, TypeError):
                pass
        return None

    @property
    def extra_state_attributes(self):
        course_data = self._get_course_data()
        ev = course_data.get("latest", {})
        history = course_data.get("history", [])

        score_history = [
            {
                "datum": h.get("availability_date") or h.get("date"),
                "toets": h.get("name"),
                "score": h.get("score_value"),
                "score_omschrijving": h.get("score_description"),
                "feedback": h.get("feedback"),
            }
            for h in history
        ]

        numeric_scores = []
        for h in history:
            try:
                numeric_scores.append(float(h["score_value"]))
            except (TypeError, ValueError, KeyError):
                pass
        gemiddelde = round(sum(numeric_scores) / len(numeric_scores), 1) if numeric_scores else None

        return {
            "toets": ev.get("name"),
            "score": ev.get("score_description"),
            "score_percentage": ev.get("score_value"),
            "datum": ev.get("date"),
            "beschikbaar_sinds": ev.get("availability_date"),
            "feedback": ev.get("feedback"),
            "gemiddelde_score": gemiddelde,
            "aantal_toetsen": len(numeric_scores),
            "score_history": score_history,
        }
