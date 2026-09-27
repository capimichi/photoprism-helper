from __future__ import annotations

import logging
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)


class PhotoprismClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        api_token: str = "",
        verify_ssl: bool = True,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.api_token = api_token
        self.verify_ssl = verify_ssl
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._session_token: str | None = api_token if api_token else None

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._session_token:
            headers["X-Auth-Token"] = self._session_token
            headers["X-Session-ID"] = self._session_token
        return headers

    def authenticate(self) -> bool:
        """Authenticate using credentials if token is not already supplied."""
        if self.api_token:
            self._session_token = self.api_token
            return True

        if not self.username or not self.password:
            logger.warning("No PhotoPrism credentials or API token provided.")
            return False

        login_url = f"{self.base_url}/api/v1/session"
        try:
            resp = self._session.post(
                login_url,
                json={"username": self.username, "password": self.password},
                verify=self.verify_ssl,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            session_id = data.get("id") or resp.headers.get("X-Session-ID") or resp.headers.get("X-Auth-Token")
            if session_id:
                self._session_token = session_id
                logger.info("Successfully authenticated with PhotoPrism.")
                return True
            logger.error("Authentication response did not contain a session ID: %s", data)
            return False
        except Exception as e:
            logger.error("Failed to authenticate with PhotoPrism at %s: %s", login_url, e)
            return False

    def check_connection(self) -> dict[str, Any]:
        """Verify PhotoPrism connectivity and return status/config info."""
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/status"
        resp = self._session.get(
            url,
            headers=self._get_headers(),
            verify=self.verify_ssl,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def get_photos(
        self,
        count: int = 100,
        offset: int = 0,
        query: str = "",
        order: str = "newest",
        merged: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch a page of photos/videos from PhotoPrism."""
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/photos"
        params: dict[str, Any] = {
            "count": count,
            "offset": offset,
            "order": order,
            "merged": str(merged).lower(),
        }
        if query:
            params["q"] = query

        resp = self._session.get(
            url,
            params=params,
            headers=self._get_headers(),
            verify=self.verify_ssl,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []

    def get_photo(self, uid: str) -> dict[str, Any]:
        """Fetch detailed information for a single photo by UID."""
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/photos/{uid}"
        resp = self._session.get(
            url,
            headers=self._get_headers(),
            verify=self.verify_ssl,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def update_photo_details(self, uid: str, details: dict[str, Any]) -> dict[str, Any]:
        """Update photo details such as keywords/tags, title, description."""
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/photos/{uid}"
        payload = {"Details": details}
        resp = self._session.put(
            url,
            json=payload,
            headers=self._get_headers(),
            verify=self.verify_ssl,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def add_label_to_photo(self, uid: str, label_name: str) -> bool:
        """Add a label/tag to a photo in PhotoPrism."""
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/photos/{uid}/label"
        payload = {"Name": label_name}
        try:
            resp = self._session.post(
                url,
                json=payload,
                headers=self._get_headers(),
                verify=self.verify_ssl,
                timeout=self.timeout_seconds,
            )
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.warning("Failed to add label '%s' to photo %s: %s", label_name, uid, e)
            return False

    def get_counts(self) -> dict[str, int]:
        """Fetch catalog counts from PhotoPrism config endpoint."""
        try:
            self.authenticate()
            resp = self._session.get(
                f"{self.base_url}/api/v1/config",
                headers=self._get_headers(),
                verify=self.verify_ssl,
                timeout=self.timeout_seconds,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("count") or {}
        except Exception as e:
            logger.warning("Failed to fetch photo counts: %s", e)
        return {}

    def trigger_index(
        self,
        path: str = "",
        rescan: bool = False,
        cleanup: bool = True,
        wait_if_busy: bool = False,
        max_retries: int = 3,
        retry_delay_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Trigger PhotoPrism indexing for a subfolder or the entire library.

        :param path: Subfolder relative to originals (e.g. "2025/02"). If empty, indexes all originals.
        :param rescan: If True, forces rescan of unchanged files.
        :param cleanup: If True, removes orphaned index entries and missing files from search results.
        :param wait_if_busy: If True and PhotoPrism returns 'Already running', wait and retry.
        :param max_retries: Number of retries when busy.
        :param retry_delay_seconds: Seconds between retries.
        """
        if not self._session_token:
            self.authenticate()

        url = f"{self.base_url}/api/v1/index"
        payload = {
            "path": path,
            "rescan": rescan,
            "cleanup": cleanup,
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                    verify=self.verify_ssl,
                    timeout=self.timeout_seconds,
                )
                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 500 and "Already running" in resp.text:
                    if wait_if_busy and attempt < max_retries:
                        logger.info(
                            "PhotoPrism indexing is already running. Retrying in %.1fs (%d/%d)...",
                            retry_delay_seconds,
                            attempt,
                            max_retries,
                        )
                        time.sleep(retry_delay_seconds)
                        continue
                    logger.warning("PhotoPrism indexing is already running. Notification skipped.")
                    return {"status": "busy", "error": "Already running"}

                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.warning("Failed to trigger PhotoPrism indexing for '%s': %s", path, e)
                if attempt == max_retries:
                    return {"status": "error", "error": str(e)}
                time.sleep(retry_delay_seconds)

        return {"status": "busy", "error": "Already running"}


