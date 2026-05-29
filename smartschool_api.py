"""Smartschool API client."""
import json
import logging
import re
from html import unescape

import requests
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)


class SmartschoolAuthError(Exception):
    """Exception to indicate an authentication error."""


class SmartschoolBirthdayError(SmartschoolAuthError):
    """Wrong or missing birthday during account verification."""


class SmartschoolAPI:
    """Client for Smartschool."""

    def __init__(self, base_url: str, username: str, password: str, birthday: str = ""):
        self._base_url = base_url.rstrip("/")
        self._login_url = f"{self._base_url}/login"
        self._results_url = f"{self._base_url}/results"
        self._evaluations_api = f"{self._base_url}/results/api/v1/evaluations/"
        self._username = username
        self._password = password
        self._birthday = birthday
        self._session = requests.Session()
        self._is_logged_in = False

        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    # ------------------------------------------------------------------
    # Form helpers
    # ------------------------------------------------------------------

    def _extract_form(self, html: str, form_name: str, current_url: str = "") -> tuple[str, dict]:
        """Extract form action URL and all input fields from a named form.

        Empty action means "post to current page" (browser behaviour).
        """
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", {"name": form_name})
        if not form:
            raise SmartschoolAuthError(f"Form '{form_name}' niet gevonden op pagina.")

        action = form.get("action") or ""
        if not action:
            post_url = current_url or self._base_url
        elif action.startswith("http"):
            post_url = action
        else:
            post_url = f"{self._base_url}{action}"

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        return post_url, payload

    def _extract_login_form(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        form = None
        for candidate in soup.find_all("form"):
            if candidate.find("input", {"name": "login_form[_username]"}) and candidate.find(
                "input", {"name": "login_form[_password]"}
            ):
                form = candidate
                break

        if not form:
            raise SmartschoolAuthError("Login form niet gevonden.")

        action = form.get("action") or "/login"
        post_url = action if action.startswith("http") else f"{self._base_url}{action}"

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        payload["login_form[_username]"] = self._username
        payload["login_form[_password]"] = self._password
        return post_url, payload

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _is_login_page(self, response: requests.Response) -> bool:
        url = response.url or ""
        if "/login" in url:
            return True
        txt = (response.text or "")[:2000]
        return "login_form[_username]" in txt or "login_form[_password]" in txt

    def _is_account_verification_page(self, response: requests.Response) -> bool:
        return "/account-verification" in (response.url or "")

    def _handle_account_verification(self, response: requests.Response) -> requests.Response:
        """Submit birthday to the account-verification form."""
        if not self._birthday:
            raise SmartschoolAuthError(
                "Smartschool vraagt om account-verificatie (geboortedatum), "
                "maar er is geen geboortedatum geconfigureerd."
            )

        _LOGGER.debug("Handling /account-verification with birthday")

        soup_check = BeautifulSoup(response.text, "html.parser")
        inp = soup_check.find("input", {"name": "account_verification_form[_security_question_answer]"})
        if inp:
            _LOGGER.debug(
                "Answer field — placeholder='%s' type='%s'",
                inp.get("placeholder", ""),
                inp.get("type", ""),
            )

        try:
            post_url, payload = self._extract_form(
                response.text, "account_verification_form", current_url=response.url
            )
        except SmartschoolAuthError:
            post_url = response.url
            payload = {}

        payload["account_verification_form[_security_question_answer]"] = self._birthday
        _LOGGER.debug("Posting account-verification to %s", post_url)

        verified = self._session.post(
            post_url,
            data=payload,
            timeout=20,
            allow_redirects=True,
            headers={
                "Origin": self._base_url,
                "Referer": response.url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        verified.raise_for_status()
        _LOGGER.debug(
            "Account-verification response: status=%s url=%s",
            verified.status_code,
            verified.url,
        )
        return verified

    def login(self):
        """Log in, handling MFA/account-verification if needed."""
        if self._is_logged_in:
            return

        _LOGGER.debug("Attempting Smartschool login for user '%s'", self._username)

        try:
            login_page = self._session.get(self._login_url, timeout=20, allow_redirects=True)
            login_page.raise_for_status()
            post_url, payload = self._extract_login_form(login_page.text)
            _LOGGER.debug("Login form found, posting to %s", post_url)

            post_resp = self._session.post(
                post_url,
                data=payload,
                timeout=20,
                allow_redirects=True,
                headers={
                    "Origin": self._base_url,
                    "Referer": self._login_url,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            post_resp.raise_for_status()
            _LOGGER.debug("Login POST response: status=%s url=%s", post_resp.status_code, post_resp.url)

        except requests.exceptions.RequestException as err:
            raise SmartschoolAuthError("Login netwerkfout") from err

        txt = (post_resp.text or "").lower()
        if "ongeldige gebruikersnaam of wachtwoord" in txt:
            raise SmartschoolAuthError("Ongeldige logingegevens")

        if self._is_account_verification_page(post_resp):
            try:
                post_resp = self._handle_account_verification(post_resp)
            except SmartschoolAuthError:
                raise
            except requests.exceptions.RequestException as err:
                raise SmartschoolAuthError("Account-verificatie netwerkfout") from err

            _LOGGER.debug("Post-verification response: status=%s url=%s", post_resp.status_code, post_resp.url)

            if self._is_account_verification_page(post_resp):
                raise SmartschoolBirthdayError(
                    "Account-verificatie mislukt — controleer de geboortedatum (formaat JJJJ-MM-DD)"
                )

        if self._is_login_page(post_resp):
            raise SmartschoolAuthError("Login mislukt: teruggeleid naar inlogpagina")

        try:
            warm = self._session.get(
                self._results_url,
                timeout=20,
                allow_redirects=True,
                headers={"Referer": self._base_url + "/"},
            )
            warm.raise_for_status()
            _LOGGER.debug("Warmup /results: status=%s url=%s", warm.status_code, warm.url)

            if self._is_account_verification_page(warm):
                warm = self._handle_account_verification(warm)

        except SmartschoolAuthError:
            raise
        except requests.exceptions.RequestException as err:
            _LOGGER.debug("Warmup request failed (non-fatal): %s", err)

        self._is_logged_in = True
        _LOGGER.debug("Successfully logged in to Smartschool.")

    def _force_relogin(self):
        """Reset session and re-authenticate."""
        _LOGGER.debug("Forcing re-login (session likely expired)")
        self._is_logged_in = False
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )
        self.login()

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_evaluations_page(self, page: int = 1, items: int = 50) -> list | None:
        """Fetch one page of evaluations from the API."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self._results_url,
            "Origin": self._base_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        url = f"{self._evaluations_api}?pageNumber={page}&itemsOnPage={items}"
        resp = self._session.get(url, timeout=20, allow_redirects=True, headers=headers)
        resp.raise_for_status()

        ctype = (resp.headers.get("Content-Type") or "").lower()
        _LOGGER.debug("Evaluations API p%d: status=%s ctype=%s url=%s", page, resp.status_code, ctype, resp.url)

        if self._is_account_verification_page(resp) or self._is_login_page(resp):
            _LOGGER.debug("Evaluations API redirected to auth page — session lost")
            return None

        if "json" not in ctype:
            _LOGGER.debug("API non-json response preview: %s", (resp.text or "")[:200].replace("\n", " "))
            return None

        try:
            data = resp.json()
        except Exception as err:
            _LOGGER.debug("Failed to parse API JSON: %s", err)
            return None

        if isinstance(data, list):
            return data or None
        if isinstance(data, dict):
            for key in ("evaluations", "items", "data", "results"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    return val
        return None

    def _normalize(self, evaluation: dict) -> dict:
        """Normalize a raw evaluation dict to a consistent structure."""
        feedback_list = evaluation.get("feedbacks") or evaluation.get("feedback") or []
        feedback_text = None
        if isinstance(feedback_list, list) and feedback_list:
            feedback_text = feedback_list[0].get("text")

        courses = evaluation.get("courses") or [{}]
        return {
            "name": evaluation.get("name"),
            "course": courses[0].get("name") if courses else None,
            "score_value": (evaluation.get("graphic") or {}).get("value"),
            "score_description": (evaluation.get("graphic") or {}).get("description"),
            "date": evaluation.get("date"),
            "availability_date": evaluation.get("availabilityDate"),
            "feedback": feedback_text,
        }

    def _fetch_all_evaluations(self) -> list | None:
        """Fetch all pages of evaluations and return them as a single list."""
        all_items: list = []
        page = 1
        items_per_page = 50

        while True:
            try:
                batch = self._fetch_evaluations_page(page=page, items=items_per_page)
            except Exception as err:
                _LOGGER.debug("API page %d fetch failed: %s", page, err)
                break

            if not batch:
                break

            all_items.extend(batch)
            _LOGGER.debug("Page %d: got %d items (total so far: %d)", page, len(batch), len(all_items))

            if len(batch) < items_per_page:
                # Last page — no more data.
                break

            page += 1

        return all_items or None

    def get_all_evaluations(self) -> dict | None:
        """Return all evaluations grouped by course + overall latest.

        Returns:
            {
                "by_course": {course_name: normalized_eval},  # latest per course
                "latest": normalized_eval,                    # most recent overall
            }
        or None on failure.
        """
        if not self._is_logged_in:
            self.login()

        raw = None

        try:
            raw = self._fetch_all_evaluations()
        except Exception as err:
            _LOGGER.debug("API fetch failed: %s", err)

        if raw is None and self._is_logged_in:
            _LOGGER.debug("No data from API — forcing re-login")
            try:
                self._force_relogin()
                raw = self._fetch_all_evaluations()
            except Exception as err:
                _LOGGER.debug("Re-login/retry failed: %s", err)

        if not raw:
            _LOGGER.warning("No evaluations found for '%s'.", self._username)
            return None

        _LOGGER.debug("Fetched %d evaluations in total", len(raw))

        # Sort ascending by date so history lists are chronological.
        raw.sort(
            key=lambda x: x.get("availabilityDate") or x.get("date") or "",
        )

        by_course: dict[str, dict] = {}
        for item in raw:
            normalized = self._normalize(item)
            course = normalized.get("course")
            if not course:
                continue
            if course not in by_course:
                by_course[course] = {"latest": normalized, "history": []}
            by_course[course]["history"].append(normalized)
            by_course[course]["latest"] = normalized  # last in sorted order = most recent

        if not by_course:
            _LOGGER.warning("Evaluations found but no course names could be extracted.")
            return None

        # Overall latest = most recent across all courses.
        latest = self._normalize(raw[-1])
        recent = [self._normalize(item) for item in reversed(raw[-5:])]

        _LOGGER.debug(
            "Courses found: %s",
            {k: len(v["history"]) for k, v in by_course.items()},
        )
        return {"by_course": by_course, "latest": latest, "recent": recent}
