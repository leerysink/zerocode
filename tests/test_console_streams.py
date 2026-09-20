# tests/test_console_streams.py
"""启动日志的第二通道（backend 控制台窗口）在中文 Windows 上不能自己炸掉。

步骤行带 `✓ ✗ ▶`（U+2713/U+2717/U+25B6），GBK 代码页编不出这些字符：默认 strict 编码时
`StreamHandler.emit` 抛 UnicodeEncodeError，logging 会把**整条日志**换成一段
「--- Logging error ---」堆栈——用户本来想在窗口里看「启动走到哪一步」，结果只看到 traceback。
`_harden_console_streams()` 把 stdout/stderr 的 errors 降级成 replace（字形变 `?`，行不丢）。
"""
import io
import logging
import sys

from app.main import _harden_console_streams

MARKS = "[zerocode] ✓ 迁移完成（+3.5s） / ✗ 启动失败 / ▶ 启动预览"


def _gbk_strict_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict", newline="\n")


def test_harden_makes_gbk_console_survive_step_markers(monkeypatch):
    out, err = _gbk_strict_stream(), _gbk_strict_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _harden_console_streams()
    assert out.errors == "replace" and err.errors == "replace"
    out.write(MARKS)          # 降级前这里直接抛 UnicodeEncodeError
    out.flush()
    raw = out.buffer.getvalue()
    assert b"\xc7\xa8" in raw           # 「迁」的 GBK 字节：整行照样落出去
    assert raw                          # 没有因为一个字形丢掉整行


def test_logging_emit_does_not_degrade_into_logging_error(monkeypatch):
    """真经 logging 走一遍：修复后不能再出现「--- Logging error ---」。"""
    sink = _gbk_strict_stream()
    monkeypatch.setattr(sys, "stdout", sink)
    _harden_console_streams()
    h = logging.StreamHandler(sink)
    h.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("zc.test.console")
    log.handlers[:] = [h]
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        log.info(MARKS)
    finally:
        log.handlers[:] = []
    sink.flush()
    raw = sink.buffer.getvalue()
    assert b"Logging error" not in raw
    assert b"[zerocode]" in raw


def test_harden_is_quiet_when_streams_are_not_reconfigurable(monkeypatch):
    """宿主（pytest 捕获、uvicorn 自定义流）可能换成没有 reconfigure 的对象——只许沉默跳过。"""
    class Opaque:
        def write(self, *a):
            return 0

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", Opaque())
    monkeypatch.setattr(sys, "stderr", Opaque())
    _harden_console_streams()           # 不抛即通过
