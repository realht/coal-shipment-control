#!/usr/bin/env python3
"""Target probe proving that a reverse proxy does not publish MEDIA_ROOT.

Authentication is supplied only through the COAL_ACCEPTANCE_COOKIE environment
variable.  The cookie is sent to the protected Django download and is never sent
to the direct /uploads/ URL or written to the evidence report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    result = urljoin(base, path.lstrip("/"))
    if urlparse(result).netloc != urlparse(base).netloc:
        raise ValueError("path must stay on the base URL origin")
    return result


def _request(url: str, *, cookie: str | None, timeout: float) -> dict:
    headers = {"User-Agent": "coal-shipments-uploads-probe/1"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(url, headers=headers, method="GET")
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    body = response.read()
    safe_header_names = (
        "Content-Type", "Content-Length", "Content-Disposition", "Cache-Control",
        "ETag", "Last-Modified", "X-Content-Type-Options", "X-Frame-Options",
    )
    return {
        "status": response.status,
        "headers": {
            name.lower(): response.headers[name]
            for name in safe_header_names
            if response.headers.get(name) is not None
        },
        "content_length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _identity(package_root: Path) -> dict:
    version_path = package_root / "VERSION"
    build_path = package_root / "BUILD_INFO.json"
    if not version_path.is_file() or not build_path.is_file():
        raise ValueError("package root must contain VERSION and BUILD_INFO.json")
    version = version_path.read_text(encoding="utf-8").strip()
    build = json.loads(build_path.read_text(encoding="utf-8"))
    build_version = str(build.get("app_version", "")).strip()
    if not version or build_version != version:
        raise ValueError("VERSION does not match BUILD_INFO.json app_version")
    required = ("build_id", "git_commit", "built_at")
    missing = [key for key in required if not str(build.get(key, "")).strip()]
    if missing:
        raise ValueError(f"BUILD_INFO.json has empty required fields: {', '.join(missing)}")
    return {
        "version": version,
        "build_id": build.get("build_id"),
        "commit": build.get("git_commit"),
        "built_at": build.get("built_at"),
    }


def run_probe(args: argparse.Namespace) -> dict:
    parsed = urlparse(args.base_url)
    if parsed.scheme not in ({"https", "http"} if args.allow_http else {"https"}):
        raise ValueError("base URL must use HTTPS (use --allow-http only for an isolated test contour)")
    cookie = os.environ.get("COAL_ACCEPTANCE_COOKIE", "").strip()
    if not cookie:
        raise ValueError("COAL_ACCEPTANCE_COOKIE is required for the protected download")

    protected_url = _url(args.base_url, args.document_path)
    direct_url = _url(args.base_url, args.uploads_path)
    if not urlparse(direct_url).path.startswith("/uploads/"):
        raise ValueError("--uploads-path must start with /uploads/")

    protected = _request(protected_url, cookie=cookie, timeout=args.timeout)
    direct = _request(direct_url, cookie=None, timeout=args.timeout)
    failures = []
    if protected["status"] != 200:
        failures.append(f"protected document returned {protected['status']}, expected 200")
    if direct["status"] not in {403, 404}:
        failures.append(f"direct uploads URL returned {direct['status']}, expected 403/404")
    if protected["content_length"] and direct["sha256"] == protected["sha256"]:
        failures.append("direct uploads response disclosed the protected document body")

    return {
        "schema": "coal-shipments.uploads-probe.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "identity": _identity(args.package_root.resolve()),
        "target": {"base_url": args.base_url, "document_path": args.document_path, "uploads_path": args.uploads_path},
        "checks": {"protected_download": protected, "direct_uploads": direct},
        "result": "PASS" if not failures else "FAIL",
        "failures": failures,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--document-path", required=True, help="protected /documents/<id>/serve/ path")
    parser.add_argument("--uploads-path", required=True, help="physical /uploads/<relative-path> URL path")
    parser.add_argument("--package-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--allow-http", action="store_true", help="only for an isolated non-production contour")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_probe(args)
    except (ValueError, OSError, URLError, json.JSONDecodeError, ssl.SSLError) as exc:
        print(f"uploads probe error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"uploads reverse-proxy probe: {report['result']} ({args.output})")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
