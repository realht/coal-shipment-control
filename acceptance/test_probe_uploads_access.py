from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from probe_uploads_access import run_probe


DOCUMENT = b"private acceptance document"


class _Handler(BaseHTTPRequestHandler):
    direct_status = 404
    leak_directly = False

    def do_GET(self):
        if self.path == "/documents/7/serve/":
            if self.headers.get("Cookie") != "sessionid=secret":
                self.send_response(302)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(DOCUMENT)
            return
        if self.path == "/uploads/auto/test.txt":
            self.send_response(self.direct_status)
            self.end_headers()
            self.wfile.write(DOCUMENT if self.leak_directly else b"not found")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class UploadsProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (root / "BUILD_INFO.json").write_text(json.dumps({
            "app_version": "1.2.3", "build_id": "build-1", "git_commit": "abc", "built_at": "now"
        }), encoding="utf-8")
        self.root = root
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.tmp.cleanup()
        _Handler.direct_status = 404
        _Handler.leak_directly = False

    def args(self):
        return argparse.Namespace(
            base_url=f"http://127.0.0.1:{self.server.server_port}",
            document_path="/documents/7/serve/", uploads_path="/uploads/auto/test.txt",
            package_root=self.root, timeout=2, allow_http=True,
        )

    @patch.dict(os.environ, {"COAL_ACCEPTANCE_COOKIE": "sessionid=secret"}, clear=False)
    def test_passes_and_binds_evidence_to_build(self):
        report = run_probe(self.args())
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["identity"]["build_id"], "build-1")
        self.assertNotIn("secret", json.dumps(report))

    @patch.dict(os.environ, {"COAL_ACCEPTANCE_COOKIE": "sessionid=secret"}, clear=False)
    def test_fails_if_proxy_publishes_document(self):
        _Handler.direct_status = 200
        _Handler.leak_directly = True
        report = run_probe(self.args())
        self.assertEqual(report["result"], "FAIL")
        self.assertTrue(any("disclosed" in item for item in report["failures"]))

    @patch.dict(os.environ, {"COAL_ACCEPTANCE_COOKIE": "sessionid=secret"}, clear=False)
    def test_fails_on_mismatched_build_identity(self):
        (self.root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_probe(self.args())


if __name__ == "__main__":
    unittest.main()
