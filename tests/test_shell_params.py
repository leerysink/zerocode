from app.deploy import shell


def test_package_name_reverses_domain_and_appends_slug():
    assert shell.package_name("zerocode.com", "erp") == "com.zerocode.erp"
    assert shell.package_name("apps.example.cn", "crm") == "cn.example.apps.crm"


def test_package_name_sanitizes_hyphens_and_digit_segments():
    assert shell.package_name("my-app.9lives.com", "erp") == "com.a9lives.myapp.erp"


def test_package_name_falls_back_when_domain_empty():
    assert shell.package_name("", "erp") == "com.zerocode.erp"


def test_version_name_tracks_shell_version():
    assert shell.version_name(1) == "1.0.1"
    assert shell.version_name(7) == "1.0.7"


def test_auto_icon_is_deterministic_and_escapes_initial():
    a = shell.auto_icon_svg("订单 & 发票", "erp")
    b = shell.auto_icon_svg("订单 & 发票", "erp")
    assert a == b
    assert "<text" in a
    assert "订" in a
    assert "&amp;" not in a          # 首字符是「订」，标签里的 & 只出现在无名称时
    assert "hsl(" in a


def test_auto_icon_xml_escapes_special_initial():
    svg = shell.auto_icon_svg("<b>", "erp")
    assert "&lt;" in svg
    assert "<b>" not in svg


def test_params_hash_changes_with_each_shell_param_but_not_publish_version():
    base = dict(name="订单", slug="erp", domain="zerocode.com",
                start_url="https://erp.zerocode.com/h5/", custom_icon=None)
    h = shell.params_hash(shell.shell_params(**base))

    assert h == shell.params_hash(shell.shell_params(**base))
    assert h != shell.params_hash(shell.shell_params(**{**base, "name": "订单2"}))
    assert h != shell.params_hash(shell.shell_params(**{
        **base, "start_url": "https://erp2.zerocode.com/h5/"}))
    assert h != shell.params_hash(shell.shell_params(**{**base, "custom_icon": b"\x89PNG\r\n\x1a\nx"}))
    # 包名随域名变化
    assert h != shell.params_hash(shell.shell_params(**{**base, "domain": "example.com"}))
    # 发布版本不在入参里，没有任何入口能改变它
    assert set(shell.shell_params(**base)) == {"revision", "package", "label", "start_url", "icon"}


def test_params_hash_changes_with_shell_revision(monkeypatch):
    base = dict(name="订单", slug="erp", domain="zerocode.com",
                start_url="https://erp.zerocode.com/h5/", custom_icon=None)
    h1 = shell.params_hash(shell.shell_params(**base))
    monkeypatch.setattr(shell, "SHELL_REVISION", shell.SHELL_REVISION + 1)
    h2 = shell.params_hash(shell.shell_params(**base))
    assert h1 != h2


def test_manifest_switches_cleartext_and_escapes_version_name():
    https = shell.manifest_xml(package="com.zerocode.erp", version_code=3,
                               version_name_="1.0.3", cleartext=False)
    assert 'android:usesCleartextTraffic="false"' in https
    assert 'android:versionCode="3"' in https
    assert 'package="com.zerocode.erp"' in https

    http = shell.manifest_xml(package="com.zerocode.erp", version_code=3,
                              version_name_="1.0.3", cleartext=True)
    assert 'android:usesCleartextTraffic="true"' in http


def test_strings_xml_escapes_android_resource_syntax():
    xml = shell.strings_xml("Tom & Jerry's <App>")
    assert "Tom &amp; Jerry\\'s &lt;App&gt;" in xml
    assert "<string name=\"app_name\">" in xml


def test_strings_xml_escapes_leading_resource_reference():
    assert "\\@app" in shell.strings_xml("@app")
    assert "\\?attr" in shell.strings_xml("?attr")


def test_shell_config_java_emits_quoted_url():
    java = shell.shell_config_java('https://erp.example.com/h5/"x"')
    assert "package com.zerocode.shell;" in java
    assert 'START_URL = "https://erp.example.com/h5/\\"x\\"";' in java


def test_detect_icon_ext_accepts_png_and_jpeg_only():
    assert shell.detect_icon_ext(b"\x89PNG\r\n\x1a\nrest") == "png"
    assert shell.detect_icon_ext(b"\xff\xd8\xffrest") == "jpg"
    assert shell.detect_icon_ext(b"GIF89a") is None


def test_is_cleartext():
    assert shell.is_cleartext("http://127.0.0.1:8201/h5/") is True
    assert shell.is_cleartext("https://erp.zerocode.com/h5/") is False
    assert shell.is_cleartext("") is False
