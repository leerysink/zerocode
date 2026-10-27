# tests/test_gateway_domain_param.py
"""网关域名参数化：写死域名清零 + router.lua 纯函数在真 LuaJIT 里的行为。

接缝一（源码级，无外部依赖）：直接读 gateway/ 下的配置文件本身。靶心是「换域名只改 env、
一个文件都不用动」这个承诺。残留的写死域名不会报错，只会以三种静默方式发作——全站 404、
404 页对着真域名撒谎、网关因找不到证书起不来——没有一种会指向真因，所以只能由测试守住。
同一接缝下还盯着 frontend/src：ADR-0016 否决了「在前端也放一份域名常量」，否决的东西需要有人守。

接缝二（行为级，需 Docker）：把 gateway/lua/router.lua 挂进 openresty 镜像用 luajit 真跑
纯函数。断言的是 Lua 自己的模式语义而非 Python 复刻——`.` 是「任意字符」、`-` 是惰性量词、
gsub 替换串里 `%` 是捕捉引用，这些坑复刻版一个都测不出来。Docker 不可用时整组 skip 并说明。

断言以「给定 host + 域名 → slug / 归一结果」为靶心，router.lua 内部实现可动而测试不动。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GW = ROOT / "gateway"

# 四份文件各自带一个「env 未设时」的回退值，必须是同一个字符串（见 test_fallback_domain_*）。
FALLBACK_DOMAIN = "zerocode.com"
FALLBACK_ALLOWED_ONCE = [
    "lua/router.lua",       # local DEFAULT_DOMAIN
    "docker-compose.yml",   # ${ZEROCODE_GATEWAY_DOMAIN:-...}
    "make-cert.sh",         # ${GATEWAY_DOMAIN:-...}
    "make-cert.ps1",        # param 默认值
]
# 这两个文件连回退值都不该出现：nginx.conf 的 server_name 是 catch-all、证书名与域名无关；
# 404.html 的域名由 lua 填 {{DOMAIN}}。
FALLBACK_FORBIDDEN = ["nginx.conf", "404.html"]

IMAGE = "openresty/openresty:alpine"

FRONTEND_SRC = ROOT / "frontend" / "src"
FRONTEND_SUFFIXES = {".vue", ".js"}


def _code_lines(text: str) -> list[str]:
    """只剔「整行就是注释」的行，不动行内内容。

    剥行内 `//` 看着更彻底，但它会把 `https://` 里的双斜杠当成注释起点，正好把最该扫的
    URL 字面量吃掉——假阴性比假阳性危险得多。代价是行尾注释里的域名会被当成代码，
    而那是可接受的：把域名从行尾注释里拿掉不是损失。
    """
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(("//", "*", "/*", "<!--", "#")):
            continue
        out.append(ln)
    return out


def _frontend_sources():
    return sorted(p for p in FRONTEND_SRC.rglob("*")
                  if p.is_file() and p.suffix in FRONTEND_SUFFIXES)


def _raw(rel: str) -> str:
    return (GW / rel).read_text(encoding="utf-8")


def _strip_comments(text: str, *, line: str | None = None, block: tuple[str, str] | None = None) -> str:
    """收窄判定域：只在「真正生效的内容」里找写死域名。

    注释里出现示例域名是合法的——404.html 就有一句解释「为什么域名不能写死」的注释提到了它。
    不剥注释的话，守卫会把最有价值的那段说明判成违规，下一个人只好把说明删掉换绿。
    """
    if block:
        text = re.sub(re.escape(block[0]) + r"[\s\S]*?" + re.escape(block[1]), "", text)
    if line:
        text = "\n".join(ln.split(line, 1)[0] for ln in text.splitlines())
    return text


def _code(rel: str) -> str:
    """去掉注释后的配置正文。"""
    if rel.endswith(".html"):
        return _strip_comments(_raw(rel), block=("<!--", "-->"))
    if rel.endswith(".lua"):
        return _strip_comments(_raw(rel), line="--")
    return _strip_comments(_raw(rel), line="#")


# ---------------------------------------------------------------- 源码级守卫

def test_no_fallback_domain_in_files_that_must_be_domain_agnostic():
    """nginx.conf 与 404.html 里一个域名都不许有。

    nginx.conf 的 server_name 用 catch-all（真正的主机名分发在 lua 里做），证书用与域名无关的
    固定文件名，所以它压根不需要知道域名。404.html 的域名由 lua 填 {{DOMAIN}}——写死的话，
    演示当场会看到「域名 xxx.zerocode.com 对应的应用未运行」这种指着错误方向的谎话。
    """
    for rel in FALLBACK_FORBIDDEN:
        body = _code(rel)
        assert FALLBACK_DOMAIN not in body, f"{rel} 里还写死了域名"
        assert ".com" not in body, f"{rel} 里出现了别的写死域名：{[l for l in body.splitlines() if '.com' in l]}"


@pytest.mark.parametrize("rel", FALLBACK_ALLOWED_ONCE)
def test_fallback_domain_appears_exactly_once(rel):
    """回退值每份文件只准出现一次。

    四份文件分处 lua / compose / 两个签证书脚本，谁都不会去校验对方。出现两次就意味着
    有一处是漏改的硬编码；一次都没有则说明回退值被改成了别的域名——两种漂移的症状都是
    「签出来的证书域名与网关匹配的域名不同」，浏览器报证书错误，而两边配置看着都对。
    """
    got = _code(rel).count(FALLBACK_DOMAIN)
    assert got == 1, f"{rel} 里 {FALLBACK_DOMAIN} 出现 {got} 次，期望恰好 1 次（env 未设时的回退）"


def test_nginx_conf_declares_env_gateway_domain_in_main_context():
    """nginx 主上下文必须声明 env GATEWAY_DOMAIN;——这是最阴的一个坑。

    nginx 默认清空继承来的环境变量，没声明时 lua 里 os.getenv 拿到 nil，不报错不告警，
    只是静默退回默认域名 → 换域名后全站 404。`env` 指令只在主上下文合法，所以要断言它
    出现在 `http {` 之前，而不是「文件里某处有这么一行」。
    """
    conf = _raw("nginx.conf")
    assert "env GATEWAY_DOMAIN;" in conf, "缺 env 声明：lua 里 os.getenv 会静默返回 nil"
    assert conf.index("env GATEWAY_DOMAIN;") < conf.index("http {"), "env 指令必须在主上下文（http 块之外）"


def test_nginx_server_name_is_catch_all():
    """两个 server 块都用 catch-all，server_name 里不得出现域名。"""
    conf = _code("nginx.conf")
    assert conf.count("server {") == 2, "server 块数量变了，本断言需要跟着复核"
    assert re.findall(r"server_name\s+([^;]+);", conf) == ["_", "_"], "server_name 又写死域名了"


def test_nginx_cert_paths_are_domain_independent():
    """自签与 Let's Encrypt 写到同一对文件名上，这是换 CA 不改配置的接缝。

    nginx.conf 指向 gateway.crt/gateway.key，make-cert.sh 也产出这对名字；acme.sh 的
    --install-cert 同样写这对名字。任一侧改名，另一侧就会静默失效——网关起不来，
    或者续期成功但浏览器还报旧证书过期。
    """
    conf = _code("nginx.conf")
    assert re.search(r"ssl_certificate\s+/etc/nginx/certs/gateway\.crt;", conf), conf
    assert re.search(r"ssl_certificate_key\s+/etc/nginx/certs/gateway\.key;", conf), conf
    sh = _code("make-cert.sh")
    assert "-out gateway.crt" in sh, "make-cert.sh 不再产出 nginx.conf 指向的那个文件名"
    assert "-out gateway.key" in sh, "make-cert.sh 不再产出 nginx.conf 指向的那个私钥名"


def test_404_page_domain_is_filled_by_lua():
    """404.html 出 {{DOMAIN}} 占位符，router.lua 必须真的去填它。"""
    assert "{{SLUG}}.{{DOMAIN}}" in _code("404.html")
    lua = _raw("lua/router.lua")
    assert 'gsub("{{DOMAIN}}"' in lua, "404.html 的 {{DOMAIN}} 没人填，会原样显示给用户"
    assert 'gsub("{{SLUG}}"' in lua


def test_compose_passes_gateway_domain_into_container():
    """compose 必须把域名透传成容器内的 GATEWAY_DOMAIN。"""
    env = yaml.safe_load(_raw("docker-compose.yml"))["services"]["gateway"]["environment"]
    assert "GATEWAY_DOMAIN" in env, "lua 里 os.getenv('GATEWAY_DOMAIN') 只会拿到 nil"
    assert env["GATEWAY_DOMAIN"] == "${ZEROCODE_GATEWAY_DOMAIN:-" + FALLBACK_DOMAIN + "}"


def test_platform_and_gateway_read_the_same_env_var():
    """平台侧与网关侧必须读同一个变量名，否则「只改一处 env」的承诺是假的。

    两边对不上的症状是「控制台显示的地址看着正常，点进去全 404」——最难查的一类。
    """
    env = yaml.safe_load(_raw("docker-compose.yml"))["services"]["gateway"]["environment"]
    nics = (ROOT / "app" / "deploy" / "nics.py").read_text(encoding="utf-8")
    assert "ZEROCODE_GATEWAY_DOMAIN" in env["GATEWAY_DOMAIN"]
    assert 'os.environ.get("ZEROCODE_GATEWAY_DOMAIN"' in nics


def test_make_cert_ps1_passes_domain_down_to_the_shell_script():
    """.ps1 传 $1、.sh 收 $1，这条握手断了就会静默签成默认域名。"""
    assert "sh /tmp/mk.sh '$Domain'" in _code("make-cert.ps1")
    assert re.search(r'DOMAIN="\$\{1:-', _code("make-cert.sh"))


def test_make_cert_ps1_rejects_non_hostname_domain():
    """域名要过校验再往下走：非法域名会签出一张什么都匹配不上的证书，静默失效。"""
    ps1 = _code("make-cert.ps1")
    assert "-notmatch" in ps1 and "throw" in ps1, "make-cert.ps1 没有对 -Domain 做 fail-closed 校验"


def test_make_cert_ps1_stays_ascii_only():
    """文件自己声明了必须 ASCII-only，这条得有人守。

    Windows PowerShell 5.1 默认按 ANSI/GBK 解码脚本，非 ASCII 会让它 tokenize 出错，
    报错信息还指向莫名其妙的行号。
    """
    raw = (GW / "make-cert.ps1").read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    assert not bad, f"make-cert.ps1 出现非 ASCII 字节（前 5 个）：{bad[:5]}"


@pytest.mark.parametrize("rel", ["nginx.conf", "make-cert.sh"])
def test_container_fed_files_stay_lf(rel):
    """`.gitattributes` 把 *.sh / *.conf 钉成 eol=lf，但那条规则只在 checkout 时生效。

    编辑器（或改文件的工具）在 Windows 上重写一次就会把整个文件翻成 CRLF，而 git 因为
    入库时的换行归一依然报「干净」，只在 diff 时丢一行很容易被忽略的 warning。
    这两个文件是直接挂进 Linux 容器跑的：make-cert.sh 由 busybox sh 执行，CRLF 会让它报
    `\\r: not found`；nginx.conf 由 nginx 读，容忍度好一些但同样违反了仓库约定。
    """
    data = (GW / rel).read_bytes()
    assert b"\r\n" not in data, f"{rel} 被翻成 CRLF 了，.gitattributes 要求 eol=lf"


def test_frontend_holds_no_gateway_domain_constant():
    """前端一份网关域名常量都不许有（ADR-0016 否决的第三个方案，在这里钉住）。

    地址的唯一事实来源是后端：`nics.app_urls` 拼 `https://{slug}.{GATEWAY_DOMAIN}`，而且
    在 slug 缺位时**故意**回空串而不是拼一个必然无效的地址。前端一旦放一份常量就成了
    第二个事实来源，换域名时必然漂移；而它的失效方式是把后端那个刻意的留白填成假地址，
    而假地址在界面上和真地址长得一模一样。
    要举例请用 RFC 2606 保留的 `.test` 顶级域。
    """
    offenders = []
    for p in _frontend_sources():
        for i, ln in enumerate(_code_lines(p.read_text(encoding="utf-8")), 1):
            if FALLBACK_DOMAIN in ln:
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {ln.strip()}")
    assert not offenders, "frontend/src 里又出现了写死的网关域名：\n" + "\n".join(offenders)


def test_frontend_does_not_compose_a_url_out_of_a_slug():
    """钉住 bug 的形状：用模板字符串把 slug 拼成一个 http(s) 地址。

    上一条盯的是「那个特定域名」，本条盯的是「拼地址」这个动作本身——换成别的域名也一样错。
    只卡 `http(s)://${...slug...}` 这个形状，所以 `http://localhost:${a.dev_port}`（预览端口直连，
    与域名无关）与 UI 文案里举例的第三方地址都不会被误伤。
    """
    pat = re.compile(r"""[`'\"]https?://\$\{[^`'\"]*slug""", re.IGNORECASE)
    offenders = []
    for p in _frontend_sources():
        for i, ln in enumerate(_code_lines(p.read_text(encoding="utf-8")), 1):
            if pat.search(ln):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {ln.strip()}")
    assert not offenders, "前端在自己拼访问地址，该拿后端 app_urls 的回值：\n" + "\n".join(offenders)


# ------------------------------------------------- 行为级：真 LuaJIT 跑纯函数

# M.normalize_domain 的边界（env 无关，纯函数）
NORMALIZE_CASES = [
    ("nil_to_default", "nil", FALLBACK_DOMAIN),                    # 未设 env
    ("empty_to_default", '""', FALLBACK_DOMAIN),                   # 空串绝不能变成「匹配一切」
    ("lowercases", '"Example.COM"', "example.com"),                # Host 头大小写不可信
    ("strips_leading_dot", '".leerysink.com"', "leerysink.com"),   # nginx server_name 习惯写法
    ("strips_root_dot", '"leerysink.com."', "leerysink.com"),      # DNS 结尾根点
    ("strips_both", '".LeerySink.COM."', "leerysink.com"),         # 两种写法同时出现
    ("plain_unchanged", '"leerysink.com"', "leerysink.com"),       # 正常值不动
    ("lone_dot_is_not_turned_into_default", '"."', "."),           # 畸形值保持畸形→匹配不上→fail-closed
]

# M.slug_of(host, domain) 的边界（显式传 domain，env 无关）
SLUG_CASES = [
    ("plain_subdomain", '"test1.leerysink.com"', '"leerysink.com"', "test1"),
    ("uppercase_host_and_domain", '"TEST1.LeerySink.COM"', '"LeerySink.com"', "test1"),
    ("host_with_port", '"test1.leerysink.com:8443"', '"leerysink.com"', "test1"),
    ("host_with_root_dot", '"test1.leerysink.com."', '"leerysink.com"', "test1"),
    ("port_and_root_dot_together", '"test1.leerysink.com.:8443"', '"leerysink.com"', "test1"),
    ("apex_returns_nil", '"leerysink.com"', '"leerysink.com"', None),
    ("multi_level_returns_nil", '"a.b.leerysink.com"', '"leerysink.com"', None),   # 通配证书只覆盖一级；也守住模式开头的 ^ 锚点
    ("suffix_attack_returns_nil", '"test1.leerysink.com.evil.com"', '"leerysink.com"', None),  # 守住结尾的 $ 锚点：不然别人把自己的域名挂在本域名下面就能借道转发
    ("separator_dot_is_literal", '"test1Xleerysink.com"', '"leerysink.com"', None),  # slug 与域名之间那个 `%.` 必须是真的点
    ("domain_dot_is_literal", '"test1.leerysinkXcom"', '"leerysink.com"', None),      # 域名里的 `.` 未转义就是「任意字符」，这个会命中
    ("hyphen_domain_matches", '"app.my-demo.com"', '"my-demo.com"', "app"),        # `-` 未转义就是惰性量词
    ("domain_leading_dot", '"app.leerysink.com"', '".leerysink.com"', "app"),
    ("domain_root_dot", '"app.leerysink.com"', '"leerysink.com."', "app"),
    ("empty_domain_falls_back_to_default", '"app.' + FALLBACK_DOMAIN + '"', '""', "app"),
    ("empty_domain_does_not_match_everything", '"app.anything.test"', '""', None),
    ("nil_host", "nil", '"leerysink.com"', None),
    ("empty_host", '""', '"leerysink.com"', None),
    ("bogus_domain_fails_closed", '"app.example.com"', '"."', None),   # 畸形域名宁可不匹配，不能匹配一切
    ("other_domain_returns_nil", '"test1.other.com"', '"leerysink.com"', None),
    ("hyphen_in_slug_returns_nil", '"my-app.leerysink.com"', '"leerysink.com"', None),
    # 平台侧 slugs.SLUG_RE 是 ^[a-z][a-z0-9]{1,39}$，lua 这边刻意更宽：宽的一侧只会去
    # routes.json 查不到然后给 404 页，不会误拒合法 slug。钉住现状，将来要收紧是个自觉决定。
    ("digit_leading_slug_accepted", '"1app.leerysink.com"', '"leerysink.com"', "1app"),
    ("overlong_slug_accepted", '"' + "a" * 41 + '.leerysink.com"', '"leerysink.com"', "a" * 41),
]

# M.slug_of(host) 单参数——生产路径 dispatch() 就是这么调的，域名只能来自 env。
# 三种 env 组合下对两个 host 的期望值。
ENV_SLUG_HOSTS = [
    ("real_domain", '"test1.leerysink.com"'),
    ("default_domain", '"test1.' + FALLBACK_DOMAIN + '"'),
]
ENV_SLUG_EXPECT = {
    ("env_set", "real_domain"): "test1",
    ("env_set", "default_domain"): None,
    # 下面这条就是 nginx.conf 漏写 `env GATEWAY_DOMAIN;` 的真实症状：不报错，只是真域名全 404。
    ("env_unset", "real_domain"): None,
    ("env_unset", "default_domain"): "test1",
    ("env_dot", "real_domain"): "test1",
    ("env_dot", "default_domain"): None,
}
DOMAIN_EXPECT = {"env_set": "leerysink.com", "env_unset": FALLBACK_DOMAIN, "env_dot": "leerysink.com"}
RUNS = {
    "env_set": {"GATEWAY_DOMAIN": "leerysink.com"},
    "env_unset": {},
    "env_dot": {"GATEWAY_DOMAIN": ".leerysink.com"},
}

# luajit 独立跑器里没有 cjson，而 router.lua 顶层 require 它；打桩即可，纯函数用不到。
_LUA_DRIVER = r"""
package.loaded["cjson.safe"] = { decode = function() return nil end }
package.path = "/gw/lua/?.lua;" .. package.path
local R = require "router"
local rows = {}
local function emit(kind, id, got)
    rows[#rows + 1] = kind .. "\t" .. id .. "\t" .. (got == nil and "<nil>" or tostring(got))
end
__EMITS__
io.write(table.concat(rows, "\n") .. "\n")
"""


def _emit_lines() -> str:
    out = [f'emit("N", "{i}", R.normalize_domain({a}))' for i, a, _ in NORMALIZE_CASES]
    out += [f'emit("S", "{i}", R.slug_of({h}, {d}))' for i, h, d, _ in SLUG_CASES]
    out += [f'emit("E", "{i}", R.slug_of({h}))' for i, h in ENV_SLUG_HOSTS]
    out += ['emit("D", "-", R.domain())']
    return "\n".join(out)


@pytest.fixture(scope="module")
def lua_runs(tmp_path_factory):
    """在真 LuaJIT 里把 router.lua 的纯函数跑三遍（三种 env），返回 {run: {(kind,id): got}}。"""
    if shutil.which("docker") is None:
        pytest.skip("docker 不在 PATH：Lua 模式语义只能在真 Lua 里验，Python 复刻测不出 `.`/`-`/`%` 的坑")
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        pytest.skip(f"本地没有 {IMAGE}：docker pull {IMAGE} 后重跑（离线环境下 Lua 行为断言无法执行）")

    stage = tmp_path_factory.mktemp("gw-lua")
    (stage / "lua").mkdir()
    shutil.copy(GW / "lua" / "router.lua", stage / "lua" / "router.lua")
    (stage / "drv.lua").write_text(_LUA_DRIVER.replace("__EMITS__", _emit_lines()),
                                   encoding="ascii", newline="\n")

    runs = {}
    for name, env in RUNS.items():
        cmd = ["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{stage.as_posix()}:/gw:ro"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        # tr -d '\r'：驱动脚本在 Windows 上落盘，busybox sh 会把 \r 当成 token 的一部分
        cmd += [IMAGE, "-c", r"tr -d '\r' < /gw/drv.lua > /tmp/d.lua; luajit /tmp/d.lua"]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        assert p.returncode == 0, f"luajit 跑失败（env={env}）：\n--stdout--\n{p.stdout}\n--stderr--\n{p.stderr}"
        got = {}
        for line in p.stdout.splitlines():
            if not line.strip():
                continue
            kind, ident, val = line.split("\t")
            got[(kind, ident)] = None if val == "<nil>" else val
        assert got, f"驱动脚本一行都没输出（env={env}）：{p.stdout!r}"
        runs[name] = got
    return runs


@pytest.mark.parametrize("ident,_arg,expected", NORMALIZE_CASES, ids=[c[0] for c in NORMALIZE_CASES])
def test_normalize_domain(lua_runs, ident, _arg, expected):
    assert lua_runs["env_set"][("N", ident)] == expected


@pytest.mark.parametrize("ident,_h,_d,expected", SLUG_CASES, ids=[c[0] for c in SLUG_CASES])
def test_slug_of_with_explicit_domain(lua_runs, ident, _h, _d, expected):
    assert lua_runs["env_set"][("S", ident)] == expected


@pytest.mark.parametrize("run", sorted(RUNS))
@pytest.mark.parametrize("ident,_host", ENV_SLUG_HOSTS, ids=[c[0] for c in ENV_SLUG_HOSTS])
def test_slug_of_reads_domain_from_env(lua_runs, run, ident, _host):
    """生产路径 dispatch() 调的是单参数 slug_of，域名只能来自 env。"""
    assert lua_runs[run][("E", ident)] == ENV_SLUG_EXPECT[(run, ident)]


@pytest.mark.parametrize("run", sorted(RUNS))
def test_domain_reflects_env(lua_runs, run):
    """404 页文案用的域名必须跟着 env 走，不能是写死的默认值。"""
    assert lua_runs[run][("D", "-")] == DOMAIN_EXPECT[run]


def test_all_cases_actually_ran(lua_runs):
    """防「驱动脚本少生成一行 → 参数化用例去查一个不存在的键」被误读成通过。"""
    want = (len(NORMALIZE_CASES) + len(SLUG_CASES) + len(ENV_SLUG_HOSTS) + 1)
    for run, got in lua_runs.items():
        assert len(got) == want, f"{run} 只跑出来 {len(got)} 条，期望 {want} 条"
