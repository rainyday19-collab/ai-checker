from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import allowed_frontend_origins, app


class CorsConfigurationTests(unittest.TestCase):
    def test_local_origins_are_always_allowed(self):
        self.assertEqual(allowed_frontend_origins(None), [
            "http://localhost:5173", "http://127.0.0.1:5173",
        ])

    def test_configured_origin_is_normalized_without_wildcards(self):
        origins = allowed_frontend_origins("  HTTPS://Frontend.Example.com/  ")
        self.assertEqual(origins[-1], "https://frontend.example.com")
        self.assertNotIn("*", origins)
        self.assertEqual(origins.count("http://localhost:5173"), 1)
        self.assertEqual(
            allowed_frontend_origins("http://localhost:5173/"),
            ["http://localhost:5173", "http://127.0.0.1:5173"],
        )

    def test_invalid_or_overbroad_origins_are_rejected(self):
        for value in ["*", "frontend.example.com", "https://frontend.example.com/path",
                      "https://user:password@frontend.example.com", "https://frontend.example.com?x=1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                allowed_frontend_origins(value)

    def test_localhost_preflight_response(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.enterContext(patch("database.DB_PATH", Path(temporary) / "test.db"))
        with TestClient(app) as client:
            response = client.options("/health", headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
