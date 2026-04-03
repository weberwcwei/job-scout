"""Tests for TLS fingerprinting adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from job_scout.scrapers.tls import ResponseAdapter, TLSClientAdapter


class TestResponseAdapter:
    def test_status_code(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "hello"
        mock_resp.url = "http://example.com"
        adapter = ResponseAdapter(mock_resp)
        assert adapter.status_code == 200
        assert adapter.text == "hello"

    def test_is_success_true(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = ""
        mock_resp.url = ""
        assert ResponseAdapter(mock_resp).is_success is True

    def test_is_success_false_for_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = ""
        mock_resp.url = ""
        assert ResponseAdapter(mock_resp).is_success is False

    def test_json_parsing(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"key": "value"}'
        mock_resp.url = ""
        adapter = ResponseAdapter(mock_resp)
        assert adapter.json() == {"key": "value"}


class TestTLSClientAdapter:
    def test_context_manager(self):
        with patch("job_scout.scrapers.tls.TLSClientAdapter.__init__", return_value=None):
            adapter = TLSClientAdapter.__new__(TLSClientAdapter)
            adapter._session = MagicMock()
            with adapter as client:
                assert client is adapter
            adapter._session.close.assert_called_once()

    def test_get_wraps_exception_as_httpx_error(self):
        with patch("job_scout.scrapers.tls.TLSClientAdapter.__init__", return_value=None):
            adapter = TLSClientAdapter.__new__(TLSClientAdapter)
            adapter._session = MagicMock()
            adapter._session.get.side_effect = Exception("connection failed")
            with pytest.raises(httpx.HTTPError, match="connection failed"):
                adapter.get("http://example.com")

    def test_post_wraps_exception_as_httpx_error(self):
        with patch("job_scout.scrapers.tls.TLSClientAdapter.__init__", return_value=None):
            adapter = TLSClientAdapter.__new__(TLSClientAdapter)
            adapter._session = MagicMock()
            adapter._session.post.side_effect = Exception("timeout")
            with pytest.raises(httpx.HTTPError, match="timeout"):
                adapter.post("http://example.com")
