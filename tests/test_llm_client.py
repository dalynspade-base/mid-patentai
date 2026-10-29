"""Shared model transport must not expose credentials on failure."""
import unittest
from unittest.mock import Mock
import requests
from modules.llm_client import LLMClient


class LLMClientTests(unittest.TestCase):
    def test_llm_http_failure_never_exposes_key(self) -> None:
        response = Mock()
        response.status_code = 401
        response.raise_for_status.side_effect = requests.HTTPError("secret-key")
        client = LLMClient("secret-key", "https://example.test/v1", "model", post=lambda *a, **k: response)
        with self.assertRaises(RuntimeError) as context:
            client.complete("system", "user")
        self.assertNotIn("secret-key", str(context.exception))
