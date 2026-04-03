"""TLS fingerprinting adapter wrapping curl_cffi to match httpx interface."""

from __future__ import annotations

import json
import random

import httpx


class ResponseAdapter:
    """Wraps a curl_cffi response to match httpx.Response interface."""

    def __init__(self, resp):
        self._resp = resp
        self.status_code: int = resp.status_code
        self.text: str = resp.text
        self.url = resp.url

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return json.loads(self.text)


class TLSClientAdapter:
    """Wraps curl_cffi.requests.Session with browser impersonation.

    Provides the same .get()/.post() interface as httpx.Client so that
    BaseScraper._get_with_retry / _post_with_retry work unchanged.
    """

    _browsers = ["chrome120", "chrome119", "safari17_0", "edge101"]

    def __init__(self, proxy: str | None = None, timeout: int = 15):
        from curl_cffi.requests import Session

        self._session = Session(
            impersonate=random.choice(self._browsers),
            timeout=timeout,
        )
        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}

    def get(self, url: str, **kwargs) -> ResponseAdapter:
        try:
            resp = self._session.get(url, **kwargs)
            return ResponseAdapter(resp)
        except Exception as e:
            raise httpx.HTTPError(str(e)) from e

    def post(self, url: str, **kwargs) -> ResponseAdapter:
        try:
            # curl_cffi uses 'json' kwarg natively like requests
            resp = self._session.post(url, **kwargs)
            return ResponseAdapter(resp)
        except Exception as e:
            raise httpx.HTTPError(str(e)) from e

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def create_tls_client(proxy: str | None = None, timeout: int = 15) -> TLSClientAdapter:
    return TLSClientAdapter(proxy=proxy, timeout=timeout)
