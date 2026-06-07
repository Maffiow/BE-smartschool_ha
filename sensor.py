"""Platform for Smartschool Results sensor."""

import logging
import re
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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


def _safe_slug(value: str) -> str:
    """Convert a string to a safe slug for unique IDs."""
    return _SAFE_RE.sub("_", (value or "").lower()).strip("_")


def _course_slug(course_name: str) -> str:
    """Convert a course name to a safe slug."""
    return _safe_slug(course_name)


def _student_name(student: dict) -> str:
    """Return best display name for a student."""
    return (
        student.get("fullNameBIN")
        or student.get("fullName")
        or f"{student.get('name', '')} {student.get('surname', '')}".strip()
        or "Student"
    )


def _teacher_name(teacher: dict) -> str:
    """Return best display name for a teacher."""
    return (
        teacher.get("fullNameBIN")
        or teacher.get("fullName")
        or f"{teacher.get('name', '')} {teacher.get('surname', '')}".strip()
        or None
    )


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

            evaluations = await hass.async_add_executor_job(api.get_all_evaluations)
            students = await hass.async_add_executor_job(api.get_students)

            if evaluations is None:
                _LOGGER.warning("Smartschool returned no evaluation data.")

            return {
                "evaluations": evaluations or {},
                "students": students or [],
            }

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

    known_courses: set[str] = set()
    known_students: set[int] = set()

    @callback
    def _add_new_course_sensors() -> None:
        """Add a sensor for every course that does not have one yet."""
        data = coordinator.data or {}
        evaluations = data.get("evaluations") or {}
        by_course: dict = evaluations.get("by_course", {})

        new_courses = set(by_course.keys()) - known_courses

        if not new_courses:
            return

        known_courses.update(new_courses)
        _LOGGER.debug("Adding sensors for new courses: %s", new_courses)

        async_add_entities(
            [
                SmartschoolCourseSensor(
                    coordinator,
                    course,
                    username,
                    config_entry.entry_id,
                )
                for course in sorted(new_courses)
            ]
        )

    @callback
    def _add_new_student_sensors() -> None:
        """Add one info sensor/device for every student."""
        data = coordinator.data or {}
        students = data.get("students") or []

        new_students = [
            student
            for student in students
            if student.get("userID") is not None
            and int(student.get("userID")) not in known_students
        ]

        if not new_students:
            return

        for student in new_students:
            known_students.add(int(student["userID"]))

        _LOGGER.debug("Adding Smartschool student devices: %s", known_students)

        async_add_entities(
            [
                SmartschoolStudentInfoSensor(
                    coordinator,
                    student,
                    base_url,
                    config_entry.entry_id,
                )
                for student in new_students
            ]
        )

    coordinator.async_add_listener(_add_new_course_sensors)
    coordinator.async_add_listener(_add_new_student_sensors)

    await coordinator.async_refresh()

    async_add_entities(
        [
            SmartschoolLatestResultSensor(
                coordinator,
                username,
                config_entry.entry_id,
            )
        ]
    )

    _add_new_course_sensors()
    _add_new_student_sensors()


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

        data = self.coordinator.data or {}
        evaluations = data.get("evaluations") or {}

        if not evaluations:
            return "Geen resultaten"

        latest = evaluations.get("latest", {})

        return latest.get("name") or latest.get("score_description") or "Resultaat gevonden"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        evaluations = data.get("evaluations") or {}

        latest = evaluations.get("latest", {})
        recent = evaluations.get("recent", [])

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
        evaluations = data.get("evaluations") or {}

        return (evaluations.get("by_course") or {}).get(self._course_name, {})

    @property
    def native_value(self):
        """Return the latest score, numeric if possible, else description."""
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


class SmartschoolStudentInfoSensor(CoordinatorEntity, SensorEntity):
    """Student info sensor, grouped as a Home Assistant device."""

    _attr_icon = "mdi:account-school"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        student: dict,
        base_url: str,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)

        self._student_id = int(student["userID"])
        self._base_url = base_url.rstrip("/")

        name = _student_name(student)

        self._attr_name = f"{name} Info"
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_student_{self._student_id}_info"

    def _get_student(self) -> dict:
        data = self.coordinator.data or {}
        students = data.get("students") or []

        for student in students:
            if int(student.get("userID", 0)) == self._student_id:
                return student

        return {}

    @property
    def native_value(self):
        """Return the student name as state."""
        student = self._get_student()

        return _student_name(student) if student else None

    @property
    def device_info(self) -> DeviceInfo:
        """Group all future student entities under one device."""
        student = self._get_student()
        name = _student_name(student) if student else f"Student {self._student_id}"

        return DeviceInfo(
            identifiers={(DOMAIN, f"student_{self._student_id}")},
            name=name,
            manufacturer="Smartschool",
            model="Student",
            configuration_url=self._base_url,
        )

    @property
    def extra_state_attributes(self):
        """Return student metadata."""
        student = self._get_student()

        titularissen = []

        for teacher in student.get("titu", []) or []:
            name = _teacher_name(teacher)
            if name:
                titularissen.append(name)

        return {
            "user_id": student.get("userID"),
            "account_id": student.get("accountID"),
            "name": student.get("name"),
            "surname": student.get("surname"),
            "full_name": student.get("fullName"),
            "display_name": student.get("fullNameBIN"),
            "class": (student.get("class") or "").strip(),
            "admin_name": student.get("adminName"),
            "titularissen": titularissen,
            "photo_url": student.get("photoUrl"),
            "current_account": student.get("currentAccount"),
            "status": student.get("status"),
            "is_current_user": student.get("isCurrentUser"),
        }
