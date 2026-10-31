"""浏览器 sidecar 纯函数测试：不需要 playwright 或 sidecar 进程。"""
from browser.inspect_core import (allow_request, clean_text, is_private_host,
                                  parse_allowed_hosts, shape_result, validate_target)


def test_parse_allowed_hosts_defaults_and_trim():
    assert parse_allowed_hosts("") == ("host.docker.internal", "127.0.0.1", "localhost")
    assert parse_allowed_hosts(" Example.COM , 10.0.0.1 ") == ("example.com", "10.0.0.1")


def test_validate_target_accepts_allowlisted_http():
    url, err = validate_target("http://host.docker.internal:8210/orders?x=1")
    assert err == ""
    assert url == "http://host.docker.internal:8210/orders?x=1"


def test_validate_target_rejects_non_http_and_bad_hosts():
    for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://localhost/a",
                "http://example.com/", "http://user:pass@localhost/",
                "http://localhost:bad/", "", None):
        got, err = validate_target(url)
        assert got is None
        assert err


def test_validate_target_rejects_outside_allowlist_with_custom_hosts():
    got, err = validate_target("http://app.local:3000/", ("localhost",))
    assert got is None
    assert "app.local" in err


def test_allow_request_main_navigation_only_target_origin():
    target = "http://host.docker.internal:8210/orders"
    assert allow_request(target, "http://host.docker.internal:8210/login",
                         is_main_navigation=True)
    assert not allow_request(target, "https://example.com/", is_main_navigation=True)
    assert not allow_request(target, "http://host.docker.internal:8100/api",
                             is_main_navigation=True)


def test_allow_request_subresources_public_allowed_private_blocked():
    target = "http://host.docker.internal:8210/"
    assert allow_request(target, "http://host.docker.internal:8210/assets/a.js",
                         is_main_navigation=False)
    assert allow_request(target, "https://cdn.example.com/lib.js", is_main_navigation=False)
    assert not allow_request(target, "http://127.0.0.1:5433/", is_main_navigation=False)
    assert not allow_request(target, "http://10.1.2.3/internal", is_main_navigation=False)
    assert not allow_request(target, "http://zerocode-backend:8100/api",
                             is_main_navigation=False)
    assert not allow_request(target, "http://host.docker.internal:8100/api",
                             is_main_navigation=False)


def test_is_private_host_matrix():
    for host in ("localhost", "app.internal", "zerocode-pg16", "127.0.0.1", "10.0.0.1",
                 "172.16.0.5", "192.168.1.2", "169.254.1.1", "::1"):
        assert is_private_host(host), host
    for host in ("example.com", "cdn.example.org", "8.8.8.8"):
        assert not is_private_host(host), host


def test_clean_text_strips_ansi_and_control_chars():
    assert clean_text("\x1b[31merror\x1b[0m\r\nline\x00 two") == "error\nline two"


def test_shape_result_caps_and_marks_truncation():
    raw_text = "x" * 9000
    console = [{"level": "error", "text": f"err {i}"} for i in range(60)]
    console.append({"level": "log", "text": "noise"})
    errors = [f"page error {i}" for i in range(15)]
    failed = [{"method": "get", "url": f"http://localhost:8210/api/{i}", "status": 500,
               "type": "fetch"} for i in range(40)]
    failed.append({"method": "GET", "url": "http://localhost:8210/blocked", "status": 0,
                   "type": "script", "blocked": True})
    out = shape_result(title="T", text=raw_text, console=console, page_errors=errors,
                       failed_requests=failed, elapsed_ms=12)
    assert out["ok"] is True
    assert out["text_truncated"] is True
    assert len(out["text"]) < 9000
    assert len(out["console"]) == 40
    assert all(c["level"] in ("error", "warning") for c in out["console"])
    assert len(out["page_errors"]) == 10
    assert len(out["failed_requests"]) == 30
    assert out["truncated"] is True
    assert out["failed_requests"][0]["method"] == "GET"


def test_shape_result_dedupes_failed_requests():
    item = {"method": "GET", "url": "http://localhost:1/a", "status": 404, "type": "fetch"}
    out = shape_result(title="", text="", console=[], page_errors=[],
                       failed_requests=[item, dict(item)], elapsed_ms=1)
    assert len(out["failed_requests"]) == 1
    assert out["truncated"] is False
