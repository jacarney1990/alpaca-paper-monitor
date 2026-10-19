"""Offline verification of Nexus public sharing, PWA installation, source ZIP.

Tests do not contact GitHub, providers, brokers or government APIs.
"""
import importlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class DistributionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_db = os.environ.get("STUDIO_DB_PATH")
        cls.old_token = os.environ.get("STUDIO_ACCESS_TOKEN")
        os.environ["STUDIO_DB_PATH"] = os.path.join(cls.temp.name, "distribution.sqlite3")
        os.environ["STUDIO_ACCESS_TOKEN"] = "test-public-distribution-only"
        import app
        cls.studio = importlib.reload(app)
        cls.studio.app.testing = True
        cls.client = cls.studio.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        for name, value in (("STUDIO_DB_PATH", cls.old_db),
                            ("STUDIO_ACCESS_TOKEN", cls.old_token)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_public_preview_is_shareable_but_private_api_is_not(self):
        demo = self.client.get("/demo")
        markup = demo.get_data(as_text=True)
        self.assertEqual(demo.status_code, 200)
        self.assertIn("PUBLIC PRACTICE ENVIRONMENT", markup)
        self.assertIn('id="demo-run"', markup)
        self.assertIn('src="/static/demo.js"', markup)
        self.assertIn('src="/static/install.js"', markup)
        self.assertIn('href="/download"', markup)
        self.assertIn('href="/static/demo-manifest.webmanifest"', markup)
        self.assertIn("prewritten sample content", markup)
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<style>", markup)
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/providers").status_code, 401)
        self.assertEqual(self.client.get("/api/sources/status").status_code, 401)
        self.assertEqual(self.client.post("/api/providers/check").status_code, 401)

    def test_private_workspace_has_install_and_safe_share_controls(self):
        markup = self.client.get("/").get_data(as_text=True)
        for entry in ('id="share-nexus"', 'id="install-nexus"',
                      'id="share-status"', 'href="/demo"',
                      'href="/download"', 'rel="manifest"',
                      '/static/install.js'):
            self.assertIn(entry, markup)
        self.assertNotIn('<script>', markup)

    def test_install_manifest_contains_real_png_app_icons(self):
        response = self.client.get("/static/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data["start_url"], "/?installed=1")
        self.assertTrue(data["name"].startswith("Nexus"))
        for size in (192, 512):
            location = "/static/nexus-icon-%d.png" % size
            entry = next((x for x in data["icons"]
                          if x["src"] == location), None)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["type"], "image/png")
            image = self.client.get(location)
            self.assertEqual(image.status_code, 200)
            binary = image.data
            self.assertEqual(binary[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack("!II", binary[16:24]),
                             (size, size))

    def test_public_install_launches_demo_not_private_workspace(self):
        response = self.client.get("/static/demo-manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        manifest = json.loads(response.get_data(as_text=True))
        self.assertEqual(manifest["start_url"], "/demo?installed=1")
        self.assertNotEqual(manifest["id"], "/nexus-ai-studio")
        self.assertTrue(manifest["name"].endswith("Public Preview"))
        self.assertTrue(any(x["src"].endswith("nexus-icon-512.png")
                            for x in manifest["icons"]))

    def test_worker_caches_only_public_assets_not_api_or_private_history(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        script = response.get_data(as_text=True)
        self.assertIn("PUBLIC_ASSETS", script)
        self.assertIn("request.mode === 'navigate'", script)
        self.assertIn("fetch(request).catch(", script)
        self.assertNotIn("cache.put(request", script)
        self.assertNotIn("caches.open('api", script)
        self.assertIn("/static/offline.html", script)
        self.assertIn("no-store", self.client.get("/").headers["Cache-Control"])

    def test_public_demo_is_predefined_simulation_only(self):
        script = self.client.get("/static/demo.js").get_data(as_text=True)
        self.assertIn("PREWRITTEN SAMPLE", script)
        self.assertIn("No API key", self.client.get("/demo").get_data(as_text=True))
        self.assertNotIn("fetch(", script)
        self.assertNotIn("/api/", script)
        self.assertNotIn("X-Studio-Token", script)
        share = self.client.get("/static/install.js").get_data(as_text=True)
        self.assertIn("const PUBLIC_PREVIEW_PATH = '/demo'", share)
        self.assertNotIn("localStorage", share)
        self.assertNotIn("sessionStorage", share)
        self.assertNotIn("X-Studio-Token", share)

    def test_download_is_real_zip_of_only_public_studio_source(self):
        secret = "NOT_A_REAL_TOKEN_PRIVATE_DIST_TEST_9083"
        with patch.dict(os.environ, {"GROQ_API_KEY": secret,
                                     "STUDIO_ACCESS_TOKEN": secret}):
            response = self.client.get("/download")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Type"].startswith("application/zip"))
        self.assertIn("Nexus-AI-Studio-Source.zip",
                      response.headers.get("Content-Disposition", ""))
        self.assertNotIn(secret.encode("utf-8"), response.data)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            entries = archive.namelist()
            self.assertEqual(archive.testzip(), None)
            self.assertIn("Nexus-AI-Studio/README-START-HERE.md", entries)
            self.assertIn("Nexus-AI-Studio/.env.example", entries)
            self.assertIn("Nexus-AI-Studio/requirements.txt", entries)
            self.assertIn("Nexus-AI-Studio/ai_studio/app.py", entries)
            self.assertIn("Nexus-AI-Studio/ai_studio/static/nexus-icon-512.png", entries)
            self.assertEqual(
                set(entries) -
                set("Nexus-AI-Studio/ai_studio/" + p
                    for p in self.studio.STUDIO_SHARE_FILES),
                {"Nexus-AI-Studio/README-START-HERE.md",
                 "Nexus-AI-Studio/.env.example",
                 "Nexus-AI-Studio/requirements.txt"}
            )
            self.assertFalse(any(x.endswith(".sqlite3") or
                                 "__pycache__" in x or
                                 "paper_pilot" in x or
                                 "/.git/" in x for x in entries))
            env = archive.read("Nexus-AI-Studio/.env.example").decode()
            self.assertIn("STUDIO_ALLOW_API_USAGE=false", env)
            self.assertIn("STUDIO_DAILY_CALL_CAP=0", env)
            self.assertIn("OPENROUTER_API_KEY=\n", env)
            howto = archive.read(
                "Nexus-AI-Studio/README-START-HERE.md").decode()
            self.assertIn("not a signed Android APK", howto)
            self.assertIn("NO API keys", howto)


if __name__ == "__main__":
    unittest.main()
