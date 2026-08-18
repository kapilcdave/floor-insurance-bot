from __future__ import annotations

import logging

import requests

LOG = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        timeout_seconds: int,
        session: requests.Session | None = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, message: str) -> None:
        if not self.enabled:
            LOG.info("telegram disabled", extra={"notification": message})
            return
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException:
            LOG.exception("telegram notification failed")

