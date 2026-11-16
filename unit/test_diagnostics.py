"""请求诊断行为：真实 HTTP 使用本地独立服务器，不依赖商城或数据库。"""
import asyncio
import importlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests
from types import SimpleNamespace

from api.api_client import ApiClient
from server.main import app


def diagnostics():
    module = importlib.util.find_spec("test_support.diagnostics")
    assert module is not None, "缺少请求诊断模块"
    return importlib.import_module("test_support.diagnostics")


def test_recursive_redaction_and_limits():
    d = diagnostics()
    value = d.sanitize({"Authorization": "Bearer secret-auth", "nested": [{"access_token": "secret-token", "DB_PASSWORD": "secret-db", "ok": "x" * 9000}]})
    text = json.dumps(value)
    assert "secret-" not in text
    assert value["nested"][0]["ok"].startswith("xxx")
    assert len(value["nested"][0]["ok"]) <= d.MAX_TEXT
    assert len(d.sanitize(list(range(10000)))) <= d.MAX_ITEMS


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_failure_stage_and_no_exception_message_leak(tmp_path, phase):
    d = diagnostics()
    nodeid = "../unit/test_example.py::test_it[password=unsafe]"
    d.begin_case(nodeid)
    d.record_request({"request_id": "request-one", "response": {"token": "hidden-token"}})
    d.finish_case(phase, "failed", ValueError("password=hidden-password"))
    path = d.write_failure(tmp_path, nodeid)
    assert path.parent == tmp_path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phases"][-1] == {"phase": phase, "outcome": "failed", "error": "ValueError"}
    assert data["requests"][0]["request_id"] == "request-one"
    assert "hidden-" not in path.read_text(encoding="utf-8")
    assert "unsafe" not in path.name
    d.end_case(nodeid)


def test_phases_preserved_and_output_isolated(tmp_path):
    d = diagnostics()
    paths = []
    for nodeid in ("a/b::test[x]", "a_b::test[x]"):
        d.begin_case(nodeid)
        d.finish_case("call", "failed", AssertionError("private"))
        d.finish_case("teardown", "failed", RuntimeError("other-private"))
        path = d.write_failure(tmp_path, nodeid)
        assert len(json.loads(path.read_text())["phases"]) == 2
        paths.append(path)
        d.end_case(nodeid)
    assert paths[0] != paths[1]


def test_write_errors_are_nonfatal(tmp_path):
    d = diagnostics()
    d.begin_case("write-error")
    target = tmp_path / "file"
    target.write_text("occupied")
    assert d.write_failure(target, "write-error") is None
    d.end_case("write-error")


@pytest.fixture
def http_service():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("X-Request-ID"))
            body = b'{"token":"private-response-token","visible":42}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()
    server.server_close()
    worker.join()


def test_real_http_requests_from_threads_are_recorded(tmp_path, http_service):
    d = diagnostics()
    base_url, seen = http_service
    d.begin_case("threaded")

    def send(_):
        client = ApiClient(base_url)
        try:
            assert client._request("GET", "/login?password=private-query-password").status_code == 200
        finally:
            client.session.close()

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(send, range(2)))
    data = json.loads(d.write_failure(tmp_path, "threaded").read_text())
    assert len(data["requests"]) == 2
    assert len(set(seen)) == 2 and all(seen)
    assert {r["request_id"] for r in data["requests"]} == set(seen)
    assert "private-" not in json.dumps(data)
    d.end_case("threaded")


def test_retries_have_unique_ids_without_logging_exception_secrets(monkeypatch, caplog, tmp_path):
    d = diagnostics()
    d.begin_case("retries")
    client = ApiClient("http://isolated.invalid", retry=2)
    ids = []

    def fail(*args, **kwargs):
        ids.append(kwargs["headers"]["X-Request-ID"])
        raise requests.ReadTimeout("password=private-password&token=private-token")

    monkeypatch.setattr(client.session, "request", fail)
    monkeypatch.setattr("api.api_client.time.sleep", lambda _: None)
    with caplog.at_level(logging.INFO), pytest.raises(requests.ReadTimeout):
        client._request("GET", "/read?token=private-url-token")
    assert len(set(ids)) == 3
    data = json.loads(d.write_failure(tmp_path, "retries").read_text())
    assert len(data["requests"]) == 3
    assert all(r["error"] == "ReadTimeout" for r in data["requests"])
    assert "private-" not in caplog.text + json.dumps(data)
    d.end_case("retries")
    client.session.close()


def test_diagnostic_failure_preserves_business_response_and_error(monkeypatch):
    d = diagnostics()
    monkeypatch.setattr(d, "record_request", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk failure")))
    client = ApiClient("http://isolated.invalid", retry=0)
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(client.session, "request", lambda *a, **kw: response)
    assert client.health() is response
    error = requests.ReadTimeout("original")
    monkeypatch.setattr(client.session, "request", lambda *a, **kw: (_ for _ in ()).throw(error))
    with pytest.raises(requests.ReadTimeout) as raised:
        client.health()
    assert raised.value is error
    client.session.close()


def asgi_get(path="/health", headers=None):
    async def run():
        sent = []
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        route, _, query = path.partition("?")
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": route, "raw_path": route.encode(), "query_string": query.encode(), "root_path": "", "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()], "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        return SimpleNamespace(status_code=start["status"], headers={k.decode().lower(): v.decode() for k, v in start["headers"]})

    return asyncio.run(run())


def test_middleware_correlates_and_logs_without_query_or_body(monkeypatch, caplog):
    monkeypatch.setenv("TEST_RUN_ID", "run-unit")
    monkeypatch.setenv("TEST_ENVIRONMENT_ID", "environment-unit")
    with caplog.at_level(logging.INFO, logger="server.requests"):
        response = asgi_get("/health?password=private-query", headers={"X-Request-ID": "known-request", "Authorization": "Bearer private-auth"})
    assert response.headers["x-request-id"] == "known-request"
    assert response.headers["x-test-run-id"] == "run-unit"
    assert response.headers["x-test-environment-id"] == "environment-unit"
    assert "known-request" in caplog.text and "200" in caplog.text
    assert "private-" not in caplog.text


def test_middleware_replaces_invalid_id_and_omits_unconfigured_run(monkeypatch):
    monkeypatch.delenv("TEST_RUN_ID", raising=False)
    first = asgi_get(headers={"X-Request-ID": "bad id"})
    second = asgi_get()
    assert first.headers["x-request-id"] != "bad id"
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert "x-test-run-id" not in first.headers


def test_url_credentials_and_nested_query_tokens_are_removed():
    d = diagnostics()
    url = d.safe_url("http://private-user:private-pass@localhost/read?access_token=private-token&ok=yes#private-fragment")
    assert "private-" not in url
    assert "ok=yes" in url


@pytest.mark.parametrize("params", [
    {"access_token": "PRIVATE_QUERY_TOKEN", "keyword": ["mouse", "keyboard"]},
    [("access_token", "PRIVATE_QUERY_TOKEN"), ("keyword", "mouse"), ("keyword", "keyboard")],
    "access_token=PRIVATE_QUERY_TOKEN&keyword=mouse&keyword=keyboard",
], ids=["dict", "pairs", "encoded"])
def test_request_query_params_are_redacted_without_changing_sent_values(params, monkeypatch, tmp_path):
    d = diagnostics()
    d.begin_case("query-params")
    client = ApiClient("http://isolated.invalid", retry=0)
    response = requests.Response()
    response.status_code = 200
    response._content = b"{}"
    sent = []

    def request(method, url, **kwargs):
        sent.append(requests.Request(method, url, params=kwargs["params"]).prepare().url)
        return response

    monkeypatch.setattr(client.session, "request", request)
    expected_url = requests.Request("GET", client.base_url + "/products", params=params).prepare().url
    try:
        client._request("GET", "/products", params=params)
        raw = d.write_failure(tmp_path, "query-params").read_text(encoding="utf-8")
        assert "PRIVATE_QUERY_TOKEN" not in raw
        data = json.loads(raw)
        recorded = data["requests"][0]["params"]
        assert recorded == [
            ["access_token", "[REDACTED]"], ["keyword", "mouse"], ["keyword", "keyboard"],
        ]
        assert sent == [expected_url]
        assert "access_token=PRIVATE_QUERY_TOKEN" in sent[0]
        assert "keyword=mouse&keyword=keyboard" in sent[0]
    finally:
        client.session.close()
        d.end_case("query-params")


def test_diagnostic_context_lookup_failure_does_not_change_response(monkeypatch):
    d = diagnostics()
    monkeypatch.setattr(d, "get_case", lambda: (_ for _ in ()).throw(RuntimeError("recorder broken")))
    client = ApiClient("http://isolated.invalid")
    response = requests.Response()
    response.status_code = 200
    monkeypatch.setattr(client.session, "request", lambda *a, **kw: response)
    assert client.health() is response
    client.session.close()


def test_middleware_unhandled_exception_keeps_original_error_and_correlated_500(caplog):
    from server.main import RequestDiagnosticsMiddleware
    failure = RuntimeError("private-error")
    sent = []

    async def failing_app(scope, receive, send):
        raise failure

    async def run():
        async def send(message):
            sent.append(message)
        await RequestDiagnosticsMiddleware(failing_app)({"type": "http", "method": "GET", "headers": []}, None, send)

    with caplog.at_level(logging.INFO, logger="server.requests"), pytest.raises(RuntimeError) as raised:
        asyncio.run(run())
    assert raised.value is failure
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 500
    assert dict(start["headers"])[b"x-request-id"]
    assert "status=500" in caplog.text
    assert "private-error" not in caplog.text


def test_late_requests_are_not_misattributed_to_next_case(tmp_path):
    d = diagnostics()
    d.begin_case("old")
    old = d.get_case()
    d.end_case("old")
    d.begin_case("new")
    d.record_request({"request_id": "late"}, case=old)
    data = json.loads(d.write_failure(tmp_path, "new").read_text())
    assert data["requests"] == []
    d.end_case("new")


def test_streaming_response_is_not_consumed_by_diagnostics(http_service, tmp_path):
    d = diagnostics()
    d.begin_case("stream")
    client = ApiClient(http_service[0])
    response = client._request("GET", "/stream", stream=True)
    try:
        assert not response._content_consumed
        assert response.json()["visible"] == 42
    finally:
        response.close()
        client.session.close()
        d.end_case("stream")


def test_request_started_outside_case_does_not_attach_to_later_case(tmp_path):
    d = diagnostics()
    d.begin_case("later")
    d.record_request({"request_id": "outside"}, case=None)
    data = json.loads(d.write_failure(tmp_path, "later").read_text())
    assert data["requests"] == []
    d.end_case("later")


def test_request_log_is_emitted_with_default_uvicorn_logging():
    import subprocess
    import sys
    code = """
import logging.config
import runpy
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
namespace = runpy.run_path('unit/test_diagnostics.py')
namespace['asgi_get']('/health', {'X-Request-ID': 'default-uvicorn-logging'})
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert "request_id=default-uvicorn-logging" in result.stdout + result.stderr
