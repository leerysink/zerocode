"""浏览器 sidecar 纯函数测试：不需要 playwright 或 sidecar 进程。"""
import socket

from browser.inspect_core import (allow_request, clean_text, is_ip_literal, is_private_host,
                                  parse_allowed_hosts, pick_connect_ip, rewrite_url_host,
                                  shape_result, validate_target)


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


def test_is_ip_literal_covers_v4_v6_and_names():
    assert is_ip_literal("192.168.65.254") is True
    assert is_ip_literal("[fdc4::254]") is True
    assert is_ip_literal("host.docker.internal") is False
    assert is_ip_literal("") is False


def test_pick_connect_ip_prefers_ipv4_over_return_order():
    info = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fdc4:f303:9324::254", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", 8213)),
    ]
    assert pick_connect_ip(info) == "192.168.65.254"
    assert pick_connect_ip([info[0]]) == "fdc4:f303:9324::254"
    assert pick_connect_ip([]) is None


def test_rewrite_url_host_keeps_port_path_and_query():
    url = "http://host.docker.internal:8213/orders?x=1#frag"
    assert rewrite_url_host(url, "192.168.65.254") == "http://192.168.65.254:8213/orders?x=1#frag"
    assert rewrite_url_host(url, "127.0.0.1") == "http://127.0.0.1:8213/orders?x=1#frag"


def test_rewrite_url_host_wraps_ipv6_in_brackets():
    url = "http://host.docker.internal:8213/"
    assert rewrite_url_host(url, "fdc4::254") == "http://[fdc4::254]:8213/"


def test_allow_request_uses_the_rewritten_origin():
    target = rewrite_url_host("http://host.docker.internal:8213/", "192.168.65.254")
    assert allow_request(target, "http://192.168.65.254:8213/src/main.js",
                         is_main_navigation=False) is True
    assert allow_request(target, "http://host.docker.internal:8213/src/main.js",
                         is_main_navigation=True) is False
