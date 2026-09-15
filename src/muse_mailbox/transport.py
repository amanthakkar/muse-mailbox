"""Dev HTTP transport used by tests. Not a production Muse client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HttpError(Exception):
    def __init__(self, status: int, payload: dict) -> None:
        super().__init__(f"{status}: {payload}")
        self.status = status
        self.payload = payload


class InProcessTransport:
    def __init__(self, relay, mailbox_id: str) -> None:
        self.relay = relay
        self.mailbox_id = mailbox_id

    def __call__(
        self,
        method: str,
        envelope: dict | None,
        *,
        token: str | None = None,
        since: int = 0,
        limit: int = 50,
    ) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if method == "POST":
            body = json.dumps(envelope).encode("utf-8")
            status, _, raw = self.relay.handle(
                "POST",
                f"/v1/mailboxes/{self.mailbox_id}/messages",
                headers=headers,
                body=body,
            )
        elif method == "GET":
            status, _, raw = self.relay.handle(
                "GET",
                f"/v1/mailboxes/{self.mailbox_id}/messages",
                headers=headers,
                body=b"",
                query={"since": str(since), "limit": str(limit)},
            )
        elif method == "STATS":
            status, _, raw = self.relay.handle(
                "GET",
                f"/v1/mailboxes/{self.mailbox_id}/stats",
                headers={},
                body=b"",
            )
        else:
            raise ValueError(method)
        payload = json.loads(raw.decode("utf-8"))
        if status >= 400:
            raise HttpError(status, payload)
        return payload


class HttpTransport:
    def __init__(self, base: str, mailbox_id: str, timeout: float = 5.0) -> None:
        self.base = base.rstrip("/")
        self.mailbox_id = mailbox_id
        self.timeout = timeout

    def __call__(
        self,
        method: str,
        envelope: dict | None,
        *,
        token: str | None = None,
        since: int = 0,
        limit: int = 50,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if method == "POST":
            url = f"{self.base}/v1/mailboxes/{self.mailbox_id}/messages"
            data = json.dumps(envelope).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        elif method == "GET":
            url = (
                f"{self.base}/v1/mailboxes/{self.mailbox_id}/messages"
                f"?since={since}&limit={limit}"
            )
            req = urllib.request.Request(url, headers=headers, method="GET")
        elif method == "STATS":
            url = f"{self.base}/v1/mailboxes/{self.mailbox_id}/stats"
            req = urllib.request.Request(url, method="GET")
        else:
            raise ValueError(method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            raise HttpError(exc.code, payload) from exc
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        return payload
