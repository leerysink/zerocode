# tests/test_deploy_lib.py
"""`scripts/deploy_lib.sh` 的桩测（规格 §5 接缝 B）。

判定层与执行层分开，是为了能在**不碰 docker、不碰宿主网络**的前提下把分支跑尽：
lib 里每个函数要么纯文本进出、要么只动显式传入的路径，副作用一律可由测试控制。

前置：本机要有可用的 bash（Git for Windows 的 MSYS bash 即可）。没有就整组 skip，
并在交付说明里写明这一层未验证——绝不假装跑过。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "deploy_lib.sh"

BASH = shutil.which("bash") or shutil.which("bash.exe")
pytestmark = pytest.mark.skipif(BASH is None, reason="本机没有 bash，判定层桩测无法执行")

# 允许测试注入时间戳，否则 .env 备份名在同一秒里会撞车
STUB_DIR = Path(__file__).parent / "deploy_stubs"


def sh(*args, stdin: str = "", env: dict | None = None, path_prepends=()):
    """在 bash 里 source 判定层后调用一个函数；参数经 "$@" 传，绝不拼进脚本文本。"""
    assert LIB.is_file(), f"缺 {LIB}"
    full_env = dict(os.environ)
    full_env["MSYS_NO_PATHCONV"] = "1"          # 别让 MSYS 把 /opt/… 当 Windows 路径改写
    if path_prepends:
        full_env["PATH"] = os.pathsep.join([str(p) for p in path_prepends] + [full_env["PATH"]])
    if env:
        full_env.update(env)
    script = f'set -uo pipefail; source "{LIB.as_posix()}"; "$@"'
    r = subprocess.run([BASH, "-c", script, "zc-test", *args],
                       input=stdin, capture_output=True, text=True, encoding="utf-8",
                       env=full_env, timeout=60)
    return r


def out(r) -> str:
    return (r.stdout or "").replace("\r\n", "\n").strip()


def err(r) -> str:
    return (r.stderr or "").replace("\r\n", "\n").strip()


def host_has(*names: str) -> bool:
    """桩目录**之外**的真实 PATH 里有没有这些工具。

    有几支用例断言的是「工具不存在时怎么办」，而 PATH 前置只能添桩、挡不住宿主上真装着的
    那份——在 Linux 上跑就会拿到真 ss / 真 apt-get 而假失败。本机（Windows + Git Bash）
    三者皆无，用例照跑；到有这些工具的机器上则明确 skip，不假装通过。
    """
    return any(shutil.which(n) for n in names)


# ---------------------------------------------------------------- 部署根规范化

def test_normalize_root_accepts_plain_absolute():
    assert out(sh("zc_normalize_root", "/opt/zerocode")) == "/opt/zerocode"


def test_normalize_root_collapses_slashes_and_trims_trailing(tmp_path):
    assert out(sh("zc_normalize_root", "//srv//zc///")) == "/srv/zc"
    assert out(sh("zc_normalize_root", "/srv/zc/")) == "/srv/zc"


def test_normalize_root_refuses_relative_and_root():
    for bad in ("zerocode", "", "/"):
        r = sh("zc_normalize_root", bad)
        assert r.returncode != 0, f"{bad!r} 竟被当成合法部署根"
        assert out(r) == "", f"{bad!r} 不该有 stdout 输出"


def test_normalize_root_names_windows_drive_as_wsl_mistake():
    """输入 D:\\zerocode 的人多半把 Windows 路径填成了部署根，报错要说清该填什么。"""
    r = sh("zc_normalize_root", "D:/zerocode")
    assert r.returncode != 0
    assert "WSL" in err(r), err(r)


@pytest.mark.parametrize("bad", ["/opt/zerocode&co", "/opt/zerocode|a"])
def test_normalize_root_refuses_sed_metacharacters(bad):
    """`|` 与 `&` 会被 apply-init.sh 当成 sed 的分隔符与「整段匹配」替换元字符：放行的症状不是
    报错，而是 init.sql 里预置应用的 root_path 落进一条变形路径，之后每个新建应用都指向一个
    不存在的目录——所以闸门要在提问时就关掉这条路。"""
    r = sh("zc_normalize_root", bad)
    assert r.returncode != 0, f"{bad!r} 竟被放行"
    assert "sed" in err(r), err(r)


# ---------------------------------------------------------------- 口令字符集闸门

def test_password_allows_documented_charset():
    assert sh("zc_valid_password", "Zc-demo_9").returncode == 0


@pytest.mark.parametrize("ch", ["@", ":", "/", "#", "%", "&", "?", " ", "中"])
def test_password_rejects_url_breaking_chars(ch):
    """这些字符会把 asyncpg 连接串截断——今天只写在 .env.example 注释里，现在是闸门。"""
    r = sh("zc_valid_password", f"a{ch}b")
    assert r.returncode != 0
    assert ch in err(r)


def test_password_rejects_empty():
    assert sh("zc_valid_password", "").returncode != 0


# ---------------------------------------------------------------- 应答文件

def test_answers_roundtrip_and_miss_key(tmp_path):
    f = tmp_path / ".deploy-answers"
    assert sh("zc_answers_put", str(f), "PLATFORM_PORT", "8100").returncode == 0
    assert out(sh("zc_answers_get", str(f), "PLATFORM_PORT")) == "8100"
    assert out(sh("zc_answers_get", str(f), "NOT_THERE")) == ""
    assert out(sh("zc_answers_put", str(f), "PLATFORM_PORT", "8111")) == ""
    assert out(sh("zc_answers_get", str(f), "PLATFORM_PORT")) == "8111"


def test_answers_put_never_stores_password_values(tmp_path):
    """口令的真值住 .env；应答文件里出现它就等于把凭据多复制了一份。"""
    f = tmp_path / ".deploy-answers"
    r = sh("zc_answers_put", str(f), "POSTGRES_PASSWORD", "s3cret")
    assert r.returncode != 0, "凭据键应当被拒绝写入，而不是悄悄收下"
    if f.exists():
        assert "s3cret" not in f.read_text(encoding="utf-8")
        assert "POSTGRES_PASSWORD" not in f.read_text(encoding="utf-8")


def test_answers_file_is_key_value_and_single_sourced(tmp_path):
    f = tmp_path / ".deploy-answers"
    sh("zc_answers_put", str(f), "DEPLOY_ROOT", "/srv/zc")
    sh("zc_answers_put", str(f), "PLATFORM_PORT", "8100")
    lines = f.read_text(encoding="utf-8").splitlines()
    assert lines == ["DEPLOY_ROOT=/srv/zc", "PLATFORM_PORT=8100"], lines


# ---------------------------------------------------------------- .env 合并

def test_env_render_preserves_unknown_keys_and_comments():
    existing = "# 注释要留着\nFOO=bar\nPOSTGRES_PASSWORD=old\nEMPTY_ONE=\n"
    r = sh("zc_env_render", "POSTGRES_PASSWORD=new", "PLATFORM_PORT=8120", stdin=existing)
    text = out(r)
    assert "# 注释要留着" in text
    assert "FOO=bar" in text
    assert "EMPTY_ONE=" in text
    assert "POSTGRES_PASSWORD=new" in text and "POSTGRES_PASSWORD=old" not in text
    assert "PLATFORM_PORT=8120" in text                          # 新键追加在末尾
    assert text.count("POSTGRES_PASSWORD=") == 1


def test_env_render_on_missing_file_yields_complete_set():
    """新增部署时没有旧 .env：渲染结果必须自成一个完整文件（含被问到的每个键）。"""
    text = out(sh("zc_env_render", "DEPLOY_ROOT=/srv/zc", "PG_PORT=5433", stdin=""))
    assert text.splitlines() == ["DEPLOY_ROOT=/srv/zc", "PG_PORT=5433"]


def test_env_render_never_invents_a_key_it_was_not_given():
    text = out(sh("zc_env_render", "A=1", stdin="B=2\n"))
    assert "A=1" in text and "B=2" in text and "C" not in text


# ---------------------------------------------------------------- .env 备份

def test_env_backup_keeps_most_recent_five(tmp_path):
    """备份是「不加误选拦截」之下唯一的后路（D-07），但留一堆等于往部署根扔垃圾。"""
    src = tmp_path / ".env"
    src.write_text("POSTGRES_PASSWORD=v1\n", encoding="utf-8")
    for i in range(7):
        stamp = f"2026110{i+1}T000000Z"
        r = sh("zc_env_backup", str(src), str(tmp_path), env={"ZC_BACKUP_STAMP": stamp})
        assert r.returncode == 0, err(r)
        assert out(r).endswith(stamp), out(r)
    backs = sorted(p.name for p in tmp_path.glob(".env.bak-*"))
    assert len(backs) == 5, backs
    assert ".env.bak-20261101T000000Z" not in backs          # 最旧的两份被淘汰
    assert ".env.bak-20261107T000000Z" in backs


def test_env_backup_is_silent_when_source_absent(tmp_path):
    """新增部署没有旧 .env 可备份：不许报错，也不许造出空备份。"""
    r = sh("zc_env_backup", str(tmp_path / ".env"), str(tmp_path))
    assert r.returncode == 0, err(r)
    assert out(r) == ""
    assert not list(tmp_path.glob(".env.bak-*"))


# ---------------------------------------------------------------- 现场产物与合并计划

def test_untouchables_cover_the_eight_items_plus_answers(tmp_path):
    """名单本身是护栏的全部依据（D-06），少一项就是下次发布少一项保护。"""
    items = out(sh("zc_untouchables", "/opt/zerocode")).splitlines()
    for want in (".env", "apps", "data", "store", "gateway/certs",
                 "gateway/routes", "gateway/.env", "pgdata", ".deploy-answers"):
        assert any(want in i for i in items), f"名单里缺 {want}"


def test_merge_plan_upgrade_never_lists_untouchables_as_touched():
    """升级计划里「会动」那一栏出现 apps/ 或 .env，就等于把人肉五步的坑写进了代码。"""
    plan = out(sh("zc_merge_plan", "upgrade", "/opt/zerocode"))
    touched, guarded = plan.split("不会动：")
    for item in ("apps", "data/", "store", "gateway/certs", "gateway/routes", "pgdata"):
        assert item not in touched, f"升级计划把 {item} 列成了会动"
    assert "backend/" in touched and "frontend/" in touched and "browser/" in touched
    assert "--delete" in touched
    assert ".env" in guarded


def test_merge_plan_new_lays_the_whole_bundle():
    plan = out(sh("zc_merge_plan", "new", "/srv/zc"))
    assert "/srv/zc" in plan
    assert "铺包" in plan or "整体" in plan


def test_merge_plan_rejects_unknown_mode():
    r = sh("zc_merge_plan", "maybe", "/opt/zerocode")
    assert r.returncode != 0
    assert "maybe" in err(r)


# ---------------------------------------------------------------- 包管理器与端口判定

@pytest.mark.skipif(host_has("apt-get", "dnf", "yum"), reason="宿主已装包管理器，桩不出「优先级」与「none」这两条分支")
def test_pkg_manager_prefers_apt_then_dnf_then_yum(tmp_path):
    for name in ("apt-get", "dnf", "yum"):
        stub = tmp_path / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
        stub.chmod(0o755)
        assert out(sh("zc_pkg_manager", path_prepends=[str(tmp_path)])) == name
        stub.unlink()


@pytest.mark.skipif(host_has("apt-get", "dnf", "yum"), reason="宿主已装包管理器，桩不出「优先级」与「none」这两条分支")
def test_pkg_manager_reports_none(tmp_path):
    assert out(sh("zc_pkg_manager", path_prepends=[str(tmp_path)])) == "none"


def test_busy_predicate_is_a_real_predicate(tmp_path):
    """回归钉：busy() 末尾挂 `|| true` 会恒返回 0，端口预检于是恒报「已被占」。"""
    text = LIB.read_text(encoding="utf-8")
    line = next((l for l in text.splitlines() if l.strip().startswith("zc_busy() {")), None)
    assert line is not None, "deploy_lib.sh 里找不到 zc_busy() 判定函数"
    assert "|| true" not in line, f"zc_busy() 又挂上 || true 了：{line}"


def test_busy_detects_listening_port_from_stubbed_ss(tmp_path):
    stub = tmp_path / "ss"
    stub.write_text('#!/bin/sh\nprintf "LISTEN 0 128 *:8100 *\\n"\n', encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    assert sh("zc_busy", "8100", path_prepends=[str(tmp_path)]).returncode == 0
    assert sh("zc_busy", "8101", path_prepends=[str(tmp_path)]).returncode != 0


@pytest.mark.skipif(host_has("ss"), reason="宿主已装 ss，桩不出「无法判定」这条分支")
def test_busy_without_ss_is_not_reported_as_free(tmp_path):
    """没有 ss 时不许判成「空闲」——静默放行端口冲突等于把故障推迟到 compose 起栈。"""
    r = sh("zc_busy", "8100", path_prepends=[str(tmp_path)])
    assert r.returncode == 2, f"缺 ss 时返回了 {r.returncode}，应显式返回 2 表示无法判定"


# ---------------------------------------------------------------- daemon.json

def test_daemon_json_plan_write_new(tmp_path):
    target = tmp_path / "daemon.json"
    assert out(sh("zc_daemon_json_plan", str(target), "https://mirror.example.com")) == "write-new"


def test_daemon_json_plan_detects_existing_mirror(tmp_path):
    target = tmp_path / "daemon.json"
    target.write_text('{"registry-mirrors": ["https://mirror.example.com"]}\n', encoding="utf-8")
    assert out(sh("zc_daemon_json_plan", str(target), "https://mirror.example.com")) == "already-configured"


def test_daemon_json_plan_refuses_to_clobber_other_keys(tmp_path):
    """已有别的键（如 log-driver）时不许覆盖：直接写会把宿主的 docker 行为改掉。"""
    target = tmp_path / "daemon.json"
    target.write_text('{"log-driver": "json-file"}\n', encoding="utf-8")
    assert out(sh("zc_daemon_json_plan", str(target), "https://mirror.example.com")) == "merge-required"


def test_daemon_json_write_new_produces_valid_json(tmp_path):
    target = tmp_path / "daemon.json"
    r = sh("zc_daemon_json_write", str(target), "https://a.m,https://b.m")
    assert r.returncode == 0, err(r)
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["registry-mirrors"] == ["https://a.m", "https://b.m"]


def test_daemon_json_write_creates_a_missing_parent_dir(tmp_path):
    """daemon.json 的位置由打包方式决定，docker 不是 apt 装来的机器上 /etc/docker 常是缺的。
    判定已经说「可以写新文件」却因为目录不存在而失败，症状是一句「这台机器的 docker 需要你
    手工配镜像加速」——把本可以代劳的事又推回给人。（2026-11-02 内核冒烟实撞。）
    路径传 POSIX 斜杠：判定层是给 Linux 用的，反斜杠路径在 bash 里连 dirname 都读不对。"""
    target = (tmp_path / "docker" / "daemon.json").as_posix()
    r = sh("zc_daemon_json_write", target, "https://a.m")
    assert r.returncode == 0, err(r)
    assert Path(target).is_file(), "父目录不存在时没写，也没建"


# ---------------------------------------------------------------- 环境缺口（US-10）
#
# 这一组全部把 PATH 收成只有一个桩目录：本机可能真的装着 docker，而这几支用例断言的正是
# 「缺了会怎样」。前置桩目录挡不住宿主上真有的那份，只有换掉整个 PATH 才谈得上判定。

def _only(tmp_path, *args, env: dict | None = None):
    """在「PATH 里只有这个桩目录」的宿主上跑一支判定函数。"""
    return sh(*args, env={"PATH": str(tmp_path), **(env or {})})


def _docker_stub(tmp_path, *, compose_ok=True, info_ok=True):
    body = "#!/bin/sh\ncase \"$*\" in\n"
    body += '  "compose version") exit %d ;;\n' % (0 if compose_ok else 1)
    body += '  "info") exit %d ;;\n' % (0 if info_ok else 1)
    body += "  *) exit 0 ;;\nesac\n"
    p = tmp_path / "docker"
    p.write_text(body, encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p


def test_env_gaps_reports_only_the_cli_when_docker_is_absent(tmp_path):
    """没有 docker CLI 时其余缺口都只是它的回声：报一堆等于让人去重装已经装好的东西。"""
    assert out(_only(tmp_path, "zc_env_gaps", env={"ZC_USER": "root"})) == "docker-cli"


def test_env_gaps_empty_when_docker_is_complete(tmp_path):
    _docker_stub(tmp_path)
    assert out(_only(tmp_path, "zc_env_gaps", env={"ZC_USER": "root", "ZC_DOCKER_SOCK": ""})) == ""


def test_env_gaps_names_the_compose_v2_plugin_gap(tmp_path):
    """缺 compose v2 插件是本仓库实撞过的独立缺口（python 版 docker-compose 不算）。"""
    _docker_stub(tmp_path, compose_ok=False)
    assert out(_only(tmp_path, "zc_env_gaps", env={"ZC_USER": "root", "ZC_DOCKER_SOCK": ""})) == "compose-plugin"


def test_env_gaps_names_the_daemon_gap(tmp_path):
    _docker_stub(tmp_path, info_ok=False)
    assert out(_only(tmp_path, "zc_env_gaps", env={"ZC_USER": "root", "ZC_DOCKER_SOCK": ""})) == "daemon"


def test_env_gaps_names_missing_docker_sock(tmp_path):
    """平台要在宿主起应用栈，compose 里那条 docker.sock 挂载直接失败比让人猜友好得多。"""
    _docker_stub(tmp_path)
    gaps = out(_only(tmp_path, "zc_env_gaps", env={
        "ZC_USER": "root", "ZC_DOCKER_SOCK": str(tmp_path / "nope.sock")}))
    assert gaps == "docker-sock", gaps


def test_env_gaps_accepts_a_docker_sock_that_exists(tmp_path):
    sock = tmp_path / "docker.sock"
    sock.write_text("", encoding="utf-8")          # WSL2 里它就是指向命名管道的链接，-e 认得住
    _docker_stub(tmp_path)
    assert out(_only(tmp_path, "zc_env_gaps", env={
        "ZC_USER": "root", "ZC_DOCKER_SOCK": str(sock)})) == ""


def test_env_gaps_reports_docker_group_only_for_non_root_members(tmp_path):
    _docker_stub(tmp_path)
    plain = {"ZC_USER": "deploy", "ZC_GROUPS": "deploy wheel", "ZC_DOCKER_SOCK": ""}
    assert out(_only(tmp_path, "zc_env_gaps", env=plain)) == "docker-group"
    member = {"ZC_USER": "deploy", "ZC_GROUPS": "deploy docker", "ZC_DOCKER_SOCK": ""}
    assert out(_only(tmp_path, "zc_env_gaps", env=member)) == ""
    root = {"ZC_USER": "root", "ZC_GROUPS": "root", "ZC_DOCKER_SOCK": ""}
    assert out(_only(tmp_path, "zc_env_gaps", env=root)) == ""


# ---------------------------------------------------------------- 端口占用者分诊

def test_port_holder_kind_prefers_the_named_container():
    """占用者是容器时处置是「问完人 stop」；是宿主进程时绝不代劳 kill。"""
    line = 'users:(("docker-proxy",pid=909,fd=4))'
    assert out(sh("zc_port_holder_kind", "zerocode-backend", stdin=line + "\n")) == "container"
    assert out(sh("zc_port_holder_kind", "", stdin='users:(("node",pid=4321,fd=18))\n')) == "host-process"
    assert out(sh("zc_port_holder_kind", "", stdin="")) == "unknown"


def test_port_holder_pid_extracts_the_number_or_stays_silent():
    assert out(sh("zc_port_holder_pid", stdin='users:(("node",pid=4321,fd=18))\n')) == "4321"
    assert out(sh("zc_port_holder_pid", stdin="")) == ""


# ---------------------------------------------------------------- 装 Docker 的命令（US-10 / D-09）

def test_install_commands_apt_branch_installs_the_compose_v2_plugin():
    text = out(sh("zc_install_commands", "apt-get", ""))
    assert "docker-compose-plugin" in text, "装成 python 版 docker-compose 等于没装"
    assert "systemctl enable --now docker" in text
    assert "ca-certificates" in text and "curl" in text
    assert "download.docker.com/linux/debian" in text


def test_install_commands_are_emitted_without_sudo_for_the_root_runner():
    """调用方已经把自己抬到 root；命令里再嵌 sudo 会让「非交互部署」卡在密码提示上。"""
    for pm in ("apt-get", "dnf", "yum"):
        for line in out(sh("zc_install_commands", pm, "")).splitlines():
            assert not line.startswith("sudo "), f"{pm} 分支的命令带了 sudo：{line}"


def test_install_commands_honours_the_mirror_host():
    """境内机直连 download.docker.com 拉不通——镜像站走 /docker-ce/linux/<distro> 约定路径。"""
    text = out(sh("zc_install_commands", "apt-get", "mirrors.aliyun.com"))
    assert "https://mirrors.aliyun.com/docker-ce/linux/debian" in text
    assert "download.docker.com" not in text, text


def test_install_commands_dnf_and_yum_use_their_own_repo_tooling():
    dnf = out(sh("zc_install_commands", "dnf", ""))
    assert "dnf-plugins-core" in dnf and "dnf config-manager --add-repo" in dnf
    assert "dnf-config-manager" not in dnf, "那是 yum 的命令名，dnf 上没有"
    yum = out(sh("zc_install_commands", "yum", ""))
    assert "yum-utils" in yum and "yum-config-manager --add-repo" in yum


def test_install_commands_refuses_an_unknown_manager():
    r = sh("zc_install_commands", "pacman", "")
    assert r.returncode != 0
    assert "pacman" in err(r), "报错要指名我们不认识的那个包管理器"
    assert "docker-ce" not in err(r), "认不出时不许照样吐出一串可能跑错的命令"


def test_add_docker_group_command_names_the_current_user():
    assert out(sh("zc_add_docker_group_command", "deploy")) == "usermod -aG docker deploy"
    assert out(sh("zc_add_docker_group_command", "")) == ""
