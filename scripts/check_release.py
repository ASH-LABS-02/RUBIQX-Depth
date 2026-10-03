"""Read-only HTTP/asset audit; prints JSON and never starts jobs or deploys.

Example: python scripts/check_release.py --base-url http://127.0.0.1:8010
Optional --checkpoint records local hashes without exposing its path/config paths.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import json
import math
from pathlib import Path
import re
import socket
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 2 * 1024 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


def bounded_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or not 0 < timeout <= 10:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 10 seconds")
    return timeout


def base_url(value):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("use an HTTP(S) base URL without credentials, query or fragment") from error
    return value.rstrip("/") + "/"


def fetch(url, timeout):
    """No redirects/proxies; cap response size and remaining socket/read time."""
    parsed = urlsplit(url)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    started = time.monotonic()
    result = {"http_status": None, "error": None}
    body = bytearray()
    try:
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("GET", target, headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"})
        network_socket = connection.sock
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        network_socket.settimeout(remaining)
        response = connection.getresponse()
        result["http_status"] = response.status
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            network_socket.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_BODY + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ValueError("response size limit")
            if response.isclosed():
                break
    except (OSError, ValueError, http.client.HTTPException) as error:
        result["error"] = "timeout" if isinstance(error, (TimeoutError, socket.timeout)) else type(error).__name__
        body.clear()
    finally:
        connection.close()
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result, bytes(body)


def token(value):
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else None


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        source = attrs.get("src") if tag == "script" else attrs.get("href")
        kind = "script" if tag == "script" and attrs.get("type") == "module" else (
            "stylesheet" if tag == "link" and attrs.get("rel") == "stylesheet" else None)
        if source and kind:
            parsed = urlsplit(source)
            versions = parse_qs(parsed.query).get("v", [])
            self.items.append({"asset": Path(parsed.path).name, "kind": kind,
                               "version": token(versions[0]) if len(versions) == 1 else None,
                               "source": source})


def parse_assets(body):
    parser = Assets()
    parser.feed(body.decode("utf-8-sig"))
    return parser.items


def json_response(result, body):
    if result["error"] or result["http_status"] != 200:
        return None
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        result["error"] = "invalid_json"
        return None


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_receipt(path):
    receipt = {"provided": True, "hashes": {}, "config": {}, "missing_metadata": []}
    try:
        for name in ("config.json", "model.safetensors", "pytorch_model.bin"):
            if (path / name).is_file():
                receipt["hashes"][name] = sha256(path / name)
        if "config.json" not in receipt["hashes"]:
            receipt["missing_metadata"].append("config.json")
        if not any(name in receipt["hashes"] for name in ("model.safetensors", "pytorch_model.bin")):
            receipt["missing_metadata"].append("supported_weights")
        config = json.loads((path / "config.json").read_text(encoding="utf-8-sig"))
        for name in ("model_type", "depthwizard_target", "depthwizard_pixel_height", "depthwizard_train_net_gsd"):
            value = config.get(name)
            receipt["config"][name] = value if (token(value) or isinstance(value, (int, float)) and math.isfinite(value)) else None
            if receipt["config"][name] is None:
                receipt["missing_metadata"].append(name)
        receipt["missing_metadata"].append("model_version")
        receipt["path_fields_redacted"] = True
    except (OSError, ValueError, TypeError, AttributeError) as error:
        receipt["error"] = type(error).__name__
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", "--base", type=base_url, default=base_url("http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=bounded_timeout, default=10.0, help="per-request timeout, at most 10 s")
    parser.add_argument("--checkpoint", type=Path, help="optional local checkpoint receipt; paths are redacted")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "base_url": args.base_url, "timeout_seconds": args.timeout, "checks": {}}
    payloads = {}
    for name, route in (("health", "api/health"), ("scenes", "api/scenes"), ("local_model", "api/local-model"), ("index", "")):
        result, body = fetch(urljoin(args.base_url, route), args.timeout)
        report["checks"][name] = result
        payloads[name] = body if name == "index" else json_response(result, body)
    health = report["checks"]["health"]
    health["ok"] = isinstance(payloads["health"], dict) and payloads["health"].get("ok") is True
    health["missing_metadata"] = ["server_build_version", "checkpoint_sha256"]
    if not isinstance(payloads["health"], dict) or not isinstance(payloads["health"].get("ok"), bool):
        health["missing_metadata"].append("ok")
    scenes, data = report["checks"]["scenes"], payloads["scenes"]
    scenes["schema_valid"] = isinstance(data, list) and all(isinstance(row, dict) for row in data)
    scenes["count"] = len(data) if scenes["schema_valid"] else None
    scenes["missing_metadata"] = {key: sum(row.get(key) is None for row in data) for key in
                                  ("id", "name", "units", "method", "has_reference")} if scenes["schema_valid"] else ["scene_list"]
    model, data = report["checks"]["local_model"], payloads["local_model"]
    model["ready"] = data.get("ready") if isinstance(data, dict) and isinstance(data.get("ready"), bool) else None
    model["stage"] = token(data.get("stage")) if isinstance(data, dict) else None
    model["epochs_done"] = data.get("epochs_done") if isinstance(data, dict) and isinstance(data.get("epochs_done"), int) else None
    model["checkpoint_path_reported"] = bool(data.get("path")) if isinstance(data, dict) else False
    model["error_reported"] = bool(data.get("error")) if isinstance(data, dict) else None
    model["missing_metadata"] = [key for key in ("ready", "stage", "epochs_done") if model[key] is None] + ["model_version", "checkpoint_sha256"]
    index = report["checks"]["index"]
    try:
        observed = parse_assets(payloads["index"]) if index["http_status"] == 200 and not index["error"] else []
        expected = parse_assets((ROOT / "web" / "index.html").read_bytes())
        index["assets"] = [{key: item[key] for key in ("asset", "kind", "version")} for item in observed]
        index["sha256"] = hashlib.sha256(payloads["index"]).hexdigest() if observed else None
        index["missing_metadata"] = [item["asset"] + ":version" for item in observed if item["version"] is None]
        app = next((item for item in observed if item["kind"] == "script" and item["asset"] == "app.js"), None)
        expected_versions = {(item["kind"], item["asset"]): item["version"] for item in expected}
        observed_versions = {(item["kind"], item["asset"]): item["version"] for item in observed}
        index["missing_assets"] = sorted(asset for kind, asset in expected_versions.keys() - observed_versions.keys())
        index["versions_match_local"] = bool(observed) and all(item["version"] is not None for item in observed) and observed_versions == expected_versions
        if not app:
            index["missing_metadata"].append("app_script")
        else:
            asset_url = urljoin(args.base_url, app["source"])
            if urlsplit(asset_url).netloc != urlsplit(args.base_url).netloc or urlsplit(asset_url).scheme != urlsplit(args.base_url).scheme:
                report["checks"]["app_script"] = {"error": "cross_origin_asset", "matches_local": False}
            else:
                result, body = fetch(asset_url, args.timeout)
                result["version"] = app["version"]
                result["sha256"] = hashlib.sha256(body).hexdigest() if body and not result["error"] else None
                result["matches_local"] = result["http_status"] == 200 and result["sha256"] == sha256(ROOT / "web" / "app.js")
                report["checks"]["app_script"] = result
    except (OSError, ValueError, UnicodeError) as error:
        index["error"] = type(error).__name__
    if args.checkpoint is not None:
        report["checkpoint_receipt"] = checkpoint_receipt(args.checkpoint)
    report["audit_ok"] = health["ok"] and scenes["schema_valid"] and model["ready"] is True and index.get("versions_match_local", False) and report["checks"].get("app_script", {}).get("matches_local", False)
    report["scope"] = "HTTP and served-source observations; not deployment approval or model-accuracy validation"
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if report["audit_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
