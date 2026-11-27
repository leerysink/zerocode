#!/usr/bin/env bash
# zerocode 演示部署包 —— 一键部署（纯交互内核）
#
# 用法就这一条，没有任何命令行参数：
#     bash deploy.sh
#   首次部署：把包传到服务器任意目录，进去跑这条，选「新增部署」。
#   以后发布：把新包传到**部署根的同级**目录，进去跑这条，选「升级部署」。
#
# 为什么没有参数（已定决策 D-11）：删库、顶掉别人的容器、动网关这类危险动作改成现场提问 +
# 手打 yes，于是「参数敲错导致跑错分支」在入口层就不可能发生。将来真需要无人值守，别在这里
# 加 flag——给 .deploy-answers 做一个重放入口（规格 §7 已记下这条）。
#
# 判定逻辑一律在 deploy_lib.sh（纯函数，桩测见 tests/test_deploy_lib.py）；本文件只做输入、
# 命令执行与副作用。想往这里加 if 之前先想清楚：它能不能挪进 lib 里被桩测覆盖。
set -uo pipefail

FIXED_NAMES=(zerocode-pg16 zerocode-backend zerocode-browser)
COMPOSE_PROJECT=zerocode
APP_PORT_FROM=8210
APP_PORT_TO=8399
PACKAGED_ROOT_FALLBACK=/opt/zerocode        # 只在包里的 .env.example 读不到 DEPLOY_ROOT 时用

# say/ok/warn 一律走 stderr：这几支会被 $(ask …) 之类的命令替换间接调用，走 stdout 就会被
# 当成答案捕获回去——「进度输出」不是数据，本就不该占 stdout。fail 同理。
say()  { printf '\n▶ %s\n' "$*" >&2; }
ok()   { printf '  ✓ %s\n' "$*" >&2; }
warn() { printf '  ! %s\n' "$*" >&2; }
fail() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. 定位、自检、抬权

# 拒收参数（D-11）：老用法里的 flag 一个个点名，比只说「不认识这个参数」有用得多。
if [ "$#" -gt 0 ]; then
  printf '本脚本不接受命令行参数（已定决策 D-11，危险动作改为现场提问）。\n' >&2
  printf '  以前那些 flag 现在的去处：--check→直接跑（预检仍在起栈之前）；--no-build→部署根没变时自动不重建；\n' >&2
  printf '  --gateway→第 4 步问你要不要起网关；--takeover→第 3/5 步现场问；--fresh-db→第 3 步手打 yes。\n' >&2
  printf '  直接跑：bash deploy.sh\n' >&2
  exit 2
fi

SELF="$0"
cd "$(dirname -- "$SELF")" 2>/dev/null || fail "进不去脚本所在目录：$(dirname -- "$SELF")"
PKG="$(pwd -P)"
SCRIPT="$PKG/$(basename -- "$SELF")"
LIB="$PKG/deploy_lib.sh"

# CRLF 自检（US-03）：包里两份脚本都由打包器剥过 \r，这里是最后一道——手改、git autocrlf
# checkout、或从记事本另存过一次都会中招，而症状是「bash: /bin/bash^M: bad interpreter」。
for f in "$SCRIPT" "$LIB"; do
  [ -f "$f" ] || fail "缺 $(basename -- "$f")：交付包不完整（deploy.sh 与 deploy_lib.sh 必须成对，重跑 build-output.bat）"
  grep -q $'\r' "$f" && fail "$(basename -- "$f") 被存成了 CRLF，bash 会把行尾 \r 当字符：sed -i 's/\r\$//' $(basename -- "$f")"
done

# 判定层必须能 source 且函数齐全：少一支会在很后面才炸，那时已经改过系统了。
# shellcheck source=./deploy_lib.sh
. "$LIB" || fail "source $LIB 失败"
for want in zc_normalize_root zc_valid_password zc_env_render zc_env_backup zc_merge_plan \
            zc_env_gaps zc_pkg_manager zc_busy zc_port_holder_kind zc_port_holder_pid \
            zc_install_commands zc_answers_get zc_answers_put \
            zc_docker_repo_suite zc_images_ready zc_build_plan \
            zc_add_docker_group_command zc_untouchables zc_daemon_json_plan zc_daemon_json_write; do
  declare -F "$want" >/dev/null || fail "deploy_lib.sh 里缺 $want——这份 lib 比 deploy.sh 旧，重打包再传"
done

# 抬权（US-13 / D-10）：装 docker、写部署根、管容器都要 root。能抬就抬，抬不动就说清怎么跑。
# 放在播报之前：重跑自己会把输出再来一遍，先抬再播才只播一次。
if [ "$(id -u)" != 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    printf '需要 root 权限（装环境、写部署根、管容器），用 sudo 重新拉起自己…\n' >&2
    exec sudo -E bash "$SCRIPT" || fail "sudo 起不来：请用 root 直接跑 bash $SCRIPT"
  fi
  fail "当前不是 root 且没有 sudo：请用 root 跑 bash $SCRIPT（装 docker 这一步绕不过去）"
fi

# ---------------------------------------------------------------- 输入小工具（必须先于使用定义）

# 只挑需要的键，绝不 source 整个 .env（那会把明文口令带进本进程环境）。
# 尾随空格必须剥掉：compose 会把它带进值里，症状是「端口/域名看着没错但就是连不上」。
envval() { # $1=KEY $2=文件
  grep -E "^[[:space:]]*$1=" "$2" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' \
    | sed 's/[[:space:]]*$//' || true
}

ans_get() { zc_answers_get "$ANSF" "$1"; }

# 默认值取法：现场 .env 优先（那是运行中的真值），再退到包里的 .env.example（打包值），
# 最后退到上次答过的 .deploy-answers（US-07）。
default_of() { # $1=KEY
  local v
  v="$(envval "$1" "$ENVF")"
  [ -n "$v" ] || v="$(envval "$1" "$PKG/.env.example")"
  [ -n "$v" ] || v="$(ans_get "$1")"
  printf '%s' "$v"
}

ask() { # $1=提示 $2=默认 ; stdout=答案（回车取默认；提示走 stderr 以免被 $() 捕获）
  local q="${1:-}" d="${2:-}" a
  printf '  %s%s：' "$q" "$([ -n "$d" ] && printf '［回车 = %s］' "$d" || printf '［可留空］')" >&2
  IFS= read -r a || a="$d"
  a="${a%$'\r'}"
  [ -n "$a" ] || a="$d"
  printf '%s\n' "$a"
}

ask_yes() { # $1=提示 $2=默认(y|n) ; 0=yes
  local a
  a="$(ask "$1" "$2")"
  case "$a" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

ask_choice() { # $1=提示 $2=默认 $3=可选值（空格分隔）; stdout=选中值
  local q="${1:-}" d="${2:-}" opts="${3:-}" a v
  while :; do
    printf '  %s%s：' "$q" "$([ -n "$d" ] && printf '［回车 = %s］' "$d")" >&2
    IFS= read -r a || a="$d"
    a="${a%$'\r'}"; [ -n "$a" ] || a="$d"
    for v in $opts; do [ "$a" = "$v" ] && { printf '%s\n' "$v"; return 0; }; done
    warn "只能填：$(printf '%s ' $opts)"
  done
}

# 本机一个可达 IP，供预览展示地址直接回车（US-04）。探测不到就退回占位符，让人自己填。
guess_host_ip() {
  local ip=""
  command -v hostname >/dev/null 2>&1 && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [ -z "$ip" ] && command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  fi
  printf '%s\n' "${ip:-<服务器IP>}"
}

valid_port() { # $1=端口 ; 0=合法
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

# 镜像站地址会被拼进 apt 源与 daemon.json，再由 root 执行——字符集先收窄，不指望人有分寸。
valid_mirror() { # $1=地址（可空）; 0=可用
  local v="${1:-}"
  [ -z "$v" ] && return 0
  case "$v" in
    *[!A-Za-z0-9.:/_-]*) return 1 ;;
  esac
  return 0
}

# ---------------------------------------------------------------- 1. 播报、模式、部署根

GEN="$(sed -n 's/^生成时间：\([^　]*\).*/\1/p' "$PKG/README-deploy.md" 2>/dev/null | head -n1)"
BAKED_ROOT="$(envval DEPLOY_ROOT "$PKG/.env.example")"
BAKED_ROOT="${BAKED_ROOT:-$PACKAGED_ROOT_FALLBACK}"
printf 'zerocode 部署内核\n' >&2
printf '  包版本      %s\n' "${GEN:-未知（包里缺 README-deploy.md 或它的抬头变了）}" >&2
printf '  包所在目录  %s\n' "$PKG" >&2
printf '  打包默认根  %s（只是本次提问的默认值，部署期可改：ADR-0034）\n' "$BAKED_ROOT" >&2

say "1/9 这次是新增还是升级"
# 不做自动探测，也不在误选时加拦截（D-05 / D-07）：唯一的后路是写 .env 前的静默备份。
printf '  新增 = 铺一个全新的部署根；升级 = 部署根已在跑，只把新包合进去，现场产物一个不碰。\n' >&2
MODE="$(ask_choice "部署类型" new "new upgrade")"
printf '  已选：%s\n' "$MODE" >&2

ENVF="" ANSF=""                                  # 这两个在第 1 步末尾才随部署根定下来，先占好位
DEPLOY_ROOT=""
while :; do
  if DEPLOY_ROOT="$(zc_normalize_root "$(ask '部署根（Linux 绝对路径，同时是容器内路径）' "$BAKED_ROOT")")"; then
    break
  fi
  warn "部署根不合规，请重填（要 Linux 绝对路径；Windows 侧填的是 WSL 发行版内部的路径，不是 D:\\）"
done
ENVF="$DEPLOY_ROOT/.env"
ANSF="$DEPLOY_ROOT/.deploy-answers"
# 换根判定（US-18）必须在第 6 步写 .env 之前取旧值：写完之后 .env 里就只剩本次的根了，
# 再读一次比较会得到「根没变」，换根提醒整条漏掉——镜像 WORKDIR 停在旧根且全程不报错。
OLD_ROOT="$(envval DEPLOY_ROOT "$ENVF")"
[ "$DEPLOY_ROOT" = "$PKG" ] && IN_PLACE=1 || IN_PLACE=0
ok "部署根 $DEPLOY_ROOT"
# US-01 要在这里就说清「包是否就在部署根里」：它决定第 6 步做不做搬运，让人在答完题之后、
# 脚本动文件之前就知道自己走的是哪条路。
if [ "$IN_PLACE" = 1 ]; then
  printf '  包就在部署根里：第 6 步不做搬运，只合并 .env\n' >&2
else
  printf '  包在 %s：第 6 步把包合进部署根（按 %s 的护栏）\n' "$PKG" "$MODE" >&2
fi
if [ "$MODE" = upgrade ] && [ ! -f "$ENVF" ]; then
  warn "$ENVF 不存在：你选的是升级，但这个根上没有 .env。.env 会按新增方式从模板生成，"
  warn "其余动作仍按升级合并执行（现场产物一个不碰）。如果这台机器其实没有旧部署，改选 new 更诚实。"
elif [ ! -f "$ENVF" ]; then
  warn "$ENVF 尚不存在：本次从包里的 .env.example 起步生成。"
fi

# ---------------------------------------------------------------- 2. 环境保障（US-10 / US-11 / US-13）

apply_registry_mirror() { # $1=镜像站地址 ; 把 registry-mirrors 落进 /etc/docker/daemon.json
  local m="${1:-}" url plan
  [ -n "$m" ] || return 0
  case "$m" in
    *://*) url="$m" ;;
    *) url="https://$m"; ;;
  esac
  plan="$(zc_daemon_json_plan /etc/docker/daemon.json "$url")"
  case "$plan" in
    already-configured) ok "/etc/docker/daemon.json 里已经有 $url" ;;
    write-new)
      zc_daemon_json_write /etc/docker/daemon.json "$url" >/dev/null \
        || fail "写 /etc/docker/daemon.json 失败：这台机器的 docker 需要你手工配镜像加速"
      systemctl restart docker >/dev/null 2>&1 || warn "daemon.json 写好了，systemctl restart docker 没成：手工重启才生效"
      ok "已写入 registry mirror 并重启 docker"
      ;;
    *)
      # 已有别的顶层键：交回 write 做「备份 + 只动 registry-mirrors」的合并；合不了（没 python、
      # 原文件是坏 JSON）才退回人工——只警告不落笔，这个恢复步骤等于空转（2026-11-26 实撞）。
      if zc_daemon_json_write /etc/docker/daemon.json "$url" >/dev/null; then
        systemctl restart docker >/dev/null 2>&1 || warn "daemon.json 合并好了，systemctl restart docker 没成：手工重启才生效"
        ok "已把 registry mirror 合并进现有 daemon.json（原文件已备份）并重启 docker"
      else
        warn "/etc/docker/daemon.json 已存在且含别的顶层键（判定为 $plan），自动合并没成：按上面的提示手工加 registry-mirrors 再 systemctl restart docker。"
      fi
      ;;
  esac
}

# 刚 systemctl enable/restart 过的 daemon 要几秒才接得住 `docker info`。装完就立刻判缺口，会把
# 「装好了」误报成「环境仍缺 daemon」，新装机器每次死在 2/9——所以给它有界等待，等不到再说。
wait_docker_ready() { # $1=轮次（默认 10） ; 0=缺口已闭
  local n="${1:-10}"
  while [ "$n" -gt 0 ]; do
    [ -z "$(zc_env_gaps)" ] && return 0
    n=$((n - 1))
    sleep 1
  done
  return 1
}

ensure_docker() {
  local gaps pm mh line me pgv
  gaps="$(zc_env_gaps)"
  if [ -z "$gaps" ]; then
    ok "docker CLI、compose v2、daemon、docker.sock、docker 组全部就位"
  else
    warn "检测到缺口：$(printf '%s ' $gaps)"
    case "$gaps" in
      *docker-cli*|*compose-plugin*)
        pm="$(zc_pkg_manager)"
        [ "$pm" = none ] && fail "认不出包管理器（apt-get / dnf / yum 都没有），没法代劳装 docker。
  请照 https://docs.docker.com/engine/install/ 装好 Docker Engine 与 compose v2 插件后重跑本脚本。"
        # 装的是 Docker Engine + compose v2 插件；不装 Docker Desktop（ADR-0035：license 与
        # docker CLI 的接管权都会跟这条路打架）。
        printf '\n▶ 用 %s 安装 Docker Engine 与 compose v2 插件\n' "$pm" >&2
        MIRROR_HOST=""
        # 只问 apt 安装通道：境内直连 download.docker.com 取 GPG 常被 reset（2026-11-26 实撞）。
        # registry 加速（daemon.json）是另一条通道——apt 镜像站不讲 Registry v2，写进去只会
        # 404 回落直连——留给第 7 步拉不动时的提问，这里不写。
        if ask_yes "境内直连 download.docker.com 常被 reset。用镜像站装 Docker Engine？(Y/n)" y; then
          mh="$(ask 'apt 安装镜像站（只管 docker-ce 的 deb/GPG，如 mirrors.tuna.tsinghua.edu.cn）' 'mirrors.tuna.tsinghua.edu.cn')"
          valid_mirror "$mh" || fail "镜像站地址只允许字母、数字与 . : / _ - ：$mh"
          # 先摘出主机名：apt 源里的 docker-ce 仓库走镜像站的 /docker-ce/linux/<distro> 约定路径。
          MIRROR_HOST="$(printf '%s' "$mh" | sed -n 's|^https\?://\([^/]\+\).*|\1|p')"
          [ -n "$MIRROR_HOST" ] || MIRROR_HOST="${mh%/}"
        fi
        while IFS= read -r line; do
          [ -n "$line" ] || continue
          printf '  $ %s\n' "$line" >&2
          # 这些命令由 zc_install_commands 生成（含 apt 源里那两个 $(...) 取架构/代号），
          # 必须在 root 的当前 shell 里展开，所以用 eval；镜像站地址已经过 valid_mirror 收窄。
          eval "$line" || fail "这条命令没跑过去：$line"
        done < <(zc_install_commands "$pm" "$MIRROR_HOST")
        ;;
      *)
        printf '  docker CLI 与 compose 都在，缺的是「能连上守护进程」那一环。\n' >&2
        ;;
    esac
    case "$gaps" in
      *daemon*|*docker-sock*)
        printf '  → systemctl enable --now docker\n' >&2
        systemctl enable --now docker >/dev/null 2>&1 || warn "systemctl 起不来 docker（这台机器可能没有 systemd）"
        ;;
    esac
    gaps="$(zc_env_gaps)"
    case "$gaps" in
      *docker-group*)
        me="${SUDO_USER:-$(id -un 2>/dev/null || echo '')}"
        if [ -n "$me" ] && ask_yes "把用户 $me 加进 docker 组，省得以后每条 docker 命令都敲 sudo（y/N）" y; then
          line="$(zc_add_docker_group_command "$me")"
          [ -n "$line" ] && { printf '  $ %s\n' "$line" >&2; eval "$line" || warn "usermod 没跑成"; }
          warn "组变更要**重新登录**或 sg docker 才生效；本脚本已经以 root 在跑，不影响这次部署。"
        fi
        ;;
    esac
  fi
  wait_docker_ready || fail "环境仍缺：$(printf '%s ' $(zc_env_gaps))
  常见卡点：
    - WSL 发行版里 daemon 起不来 → /etc/wsl.conf 的 [boot] systemd=true 没开；Windows 侧跑 deploy.ps1 会代劳这一件。
    - 刚被加进 docker 组 → 重新登录，或 sg docker -c 'docker info'。
    - Docker Desktop 的别名 docker→docker compose 冲突 → 直接用 /usr/bin/docker。
  处理后把同一条 bash deploy.sh 再跑一次：已答项会从 $ANSF 取默认值。"
  ok "docker 环境就位"

  # rsync 是升级合并的唯一执行手段（八项护栏靠它逐目录走 --delete）；缺了就装，别留第二条路径。
  if ! command -v rsync >/dev/null 2>&1; then
    pm="$(zc_pkg_manager)"
    [ "$pm" = none ] && fail "缺 rsync 且认不出包管理器：请手工装 rsync（升级合并要用它）"
    printf '  → 装 rsync\n' >&2
    case "$pm" in
      apt-get) apt-get update -qq && apt-get install -y -qq rsync ;;
      *) "$pm" install -y rsync ;;
    esac || fail "rsync 没装上：升级合并要用它"
    command -v rsync >/dev/null 2>&1 || fail "rsync 装完了但 PATH 里找不到"
  fi
  ok "rsync 就位"

  # 第 7 步拿 curl 等控制台回 200。缺 curl 的机器上那条循环会一路判「没回 200」，把人指向
  # backend 日志——所以在这里就说清缺的是工具，别让它冒充成平台故障。
  command -v curl >/dev/null 2>&1 || fail "宿主没有 curl：第 7 步要用它探控制台。装一个（apt-get/dnf/yum install -y curl）后把同一条 bash deploy.sh 再跑一次"

  # compose v2 子命令必须可用：第 7 步的 config/pull/up 全走 `docker compose`（不是 v1 的 docker-compose）。
  pgv="$(docker compose version --short 2>/dev/null || true)"
  [ -n "$pgv" ] || fail "docker compose 子命令跑不通：确认装的是 compose v2 插件（docker compose version）"
  ok "compose v2 版本 $pgv"
}

say "2/9 环境保障"
ensure_docker

# ---------------------------------------------------------------- 3. 新增模式的删库闸门（US-14）

say "3/9 腾地方"
DROP_DB=0
if [ "$MODE" = new ]; then
  # 卷名是 <项目名>_pgdata（compose 里声明的键叫 pgdata），所以按结尾匹配，不写死前缀。
  pgvols="$(docker volume ls -q --format '{{.Name}}' 2>/dev/null | grep -E 'pgdata$' || true)"
  if [ -n "$pgvols" ]; then
    warn "机器上已存在数据卷：$(printf '%s ' $pgvols)"
    printf '  !! 新增部署会连这些卷一起删：平台库（账号 / 应用行 / 会话）与全部应用分库一起没。\n' >&2
    printf '  !! 到这里为止我只动了环境与配置，没碰过你的库。手打 yes 才继续：' >&2
    read -r ans
    [ "$ans" = yes ] || fail "未确认，已中止（此刻尚未对系统做任何改动；想保住数据就重跑并选 upgrade）"
    DROP_DB=1
    ok "已确认删卷（真正执行排在铺包之后、起栈之前）"
  else
    ok "没有历史 pgdata 卷"
  fi
  # 新增模式不许把包铺到它自己的父目录里：那会让 rsync 自己拷自己。
  if [ "$IN_PLACE" = 0 ] && [ "${PKG#"$DEPLOY_ROOT"/}" != "$PKG" ]; then
    fail "包在部署根里面（$PKG 在 $DEPLOY_ROOT 之下）：新增部署的部署根就应该是包所在目录。
  重跑一次，把部署根填成 $PKG；或者把包移到 $DEPLOY_ROOT 的同级再跑。"
  fi
fi

for n in "${FIXED_NAMES[@]}"; do
  docker container inspect "$n" >/dev/null 2>&1 || continue
  own="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$n" 2>/dev/null || true)"
  if [ "$own" = "$COMPOSE_PROJECT" ]; then
    ok "容器 $n 属于本项目（$COMPOSE_PROJECT），起栈时就地重建"
    continue
  fi
  # 只动容器不动卷：卷的事在上面那道 yes 闸门里单独问。
  if ask_yes "容器 $n 属于别的项目（${own:-未知}），新项目会死在 container_name 冲突上。移除它？数据卷不动（y/N)" n; then
    docker rm -f "$n" >/dev/null && warn "已移除旧容器 $n（数据卷未动）"
  else
    fail "旧容器 $n 还在：确认可以移除后重跑，并在提问时回答 y"
  fi
done

# ---------------------------------------------------------------- 4. 参数收集（US-04 ~ US-08）

say "4/9 运行参数"
PAIRS=()                 # 要写进 .env 的 KEY=VALUE（空值不写，让 compose 里 ${VAR:-打包值} 生效）
KV=()                    # 要写进 .deploy-answers 的 KEY=VALUE：绝不含口令，lib 还有一道拒绝闸门

put() { # $1=KEY $2=VALUE
  local k="$1" v="${2:-}"
  [ -n "$v" ] || return 0
  PAIRS+=("$k=$v")
  case "$k" in
    *PASSWORD*|*SECRET*|*TOKEN*|*KEY*) ;;
    *) KV+=("$k=$v") ;;
  esac
}

PGUSER="$(ask '平台库用户 POSTGRES_USER（需超级用户：平台要建库建角色来供给各应用分库）' "$(default_of POSTGRES_USER)")"
PGUSER="${PGUSER:-zerocode}"
put POSTGRES_USER "$PGUSER"

# 口令：应答文件里永远不存真值，所以它只能靠现场 .env 的既有值复用，不能靠 .deploy-answers。
PW_EXISTING="$(envval POSTGRES_PASSWORD "$ENVF")"
while :; do
  if [ -n "$PW_EXISTING" ]; then
    printf '  平台库口令 POSTGRES_PASSWORD：回车沿用 .env 里已有的（%s 位），或输入新值：' "${#PW_EXISTING}" >&2
  else
    printf '  平台库口令 POSTGRES_PASSWORD（只用字母、数字与 - _）：' >&2
  fi
  # read 失败=stdin 到头了。这里必须退出而不是 continue：口令没有默认值可退，
  # 继续在原地打转就是死循环（桩测里喂的答案行数不够时真撞出来过）。
  if ! IFS= read -rs pw; then
    [ -n "$PW_EXISTING" ] || fail "输入已结束但口令还没拿到：本脚本要交互着跑，别把 stdin 重定向到空文件"
    pw=""
  fi
  printf '\n' >&2
  if [ -z "$pw" ]; then
    [ -n "$PW_EXISTING" ] || { warn "新增部署必须有口令（compose 里 POSTGRES_PASSWORD 是 :? 硬闸门）"; continue; }
    ok "口令沿用 .env 里已有的"
    break
  fi
  if zc_valid_password "$pw"; then
    if [ -n "$PW_EXISTING" ]; then
      warn "改写口令要留意：首次 up 之后口令已经写进 pgdata 卷，改 .env 不会改库里的口令。"
      warn "真改了会连不上库，且现象与「口令写错」无法区分——要连库一起重做就得选新增部署。"
    fi
    put POSTGRES_PASSWORD "$pw"
    break
  fi
  warn "口令不合规，请重填"
done

while :; do
  PLAT="$(ask '控制台端口 PLATFORM_PORT（对外地址的一部分，要与云安全组放行的一致）' "$(default_of PLATFORM_PORT)")"
  PLAT="${PLAT:-8100}"
  valid_port "$PLAT" || { warn "端口要填 1-65535 的数字"; continue; }
  break
done
put PLATFORM_PORT "$PLAT"

while :; do
  PGP="$(ask '平台库宿主端口 PG_PORT（演示机通常不必对外）' "$(default_of PG_PORT)")"
  PGP="${PGP:-5433}"
  valid_port "$PGP" || { warn "端口要填 1-65535 的数字"; continue; }
  break
done
put PG_PORT "$PGP"

# 预览展示地址：打包默认值是 localhost（本机演示用的），在服务器上就是「地址指向访问者自己的机器」。
PH="$(default_of ZEROCODE_PREVIEW_HOST)"
if [ -z "$PH" ] || [ "$PH" = "localhost" ]; then PH="$(guess_host_ip)"; fi
PREVIEW_HOST="$(ask '预览展示地址 ZEROCODE_PREVIEW_HOST（用户在浏览器里打开预览用的主机）' "$PH")"
PREVIEW_HOST="${PREVIEW_HOST:-$(guess_host_ip)}"
put ZEROCODE_PREVIEW_HOST "$PREVIEW_HOST"
ok "预览地址基座 http://${PREVIEW_HOST}:<dev端口>（宿主防火墙与安全组要放行应用端口 $APP_PORT_FROM-$APP_PORT_TO）"

# 三个镜像源：pip/npm 有运行期消费方（服务器上改一次 .env 就同时管住平台自己与它起出来的每个
# 应用栈）；apt 只作用于**镜像构建期**（browser 镜像的 build.args），改它必须 --build 才生效。
PYPI="$(ask 'PyPI 镜像源 ZEROCODE_PYPI_MIRROR（回车 = 沿用打包值）' "$(default_of ZEROCODE_PYPI_MIRROR)")"
valid_mirror "$PYPI" || fail "PyPI 镜像源只允许字母、数字与 . : / _ - ：$PYPI"
put ZEROCODE_PYPI_MIRROR "$PYPI"
NPM="$(ask 'npm registry ZEROCODE_NPM_REGISTRY（回车 = 沿用打包值）' "$(default_of ZEROCODE_NPM_REGISTRY)")"
valid_mirror "$NPM" || fail "npm registry 只允许字母、数字与 . : / _ - ：$NPM"
put ZEROCODE_NPM_REGISTRY "$NPM"
APT="$(ask 'apt 镜像站 ZEROCODE_APT_MIRROR（只作用于浏览器 sidecar 镜像的构建期；回车 = 沿用打包值）' "$(default_of ZEROCODE_APT_MIRROR)")"
valid_mirror "$APT" || fail "apt 镜像站只允许字母、数字与 . : / _ - ：$APT"
put ZEROCODE_APT_MIRROR "$APT"

# 网关：Linux 侧默认建议起（演示机要给人看域名访问），Windows 侧由 deploy.ps1 建议不起
# （宿主 80/443 常被占、通配 DNS 指不到本机）。那是「皮给的建议值」，不是命令行参数。
GW_DEFAULT="${ZC_GATEWAY_DEFAULT:-y}"
if ask_yes "要起中央网关吗（通配域名 HTTPS，占宿主 80/443）" "$GW_DEFAULT"; then
  GATEWAY=1
  DOMAIN="$(ask '网关域名 ZEROCODE_GATEWAY_DOMAIN（应用地址是 https://<slug>.<这个域名>）' "$(default_of ZEROCODE_GATEWAY_DOMAIN)")"
  [ -n "$DOMAIN" ] || fail "要起网关就得给域名：没域名控制台里的应用地址拼不出来，gen_ssl.sh 也不知道该签哪个域名"
  # 收窄字符集不是洁癖：这个值落进 .env 后会被 gen_ssl.sh 拼进 `docker run … sh -c "… '<域名>'"`
  # 的单引号里，带引号或分号的「域名」就是让脚本以 root 执行任意命令。顺带挡掉带空格的粘贴。
  case "$DOMAIN" in
    *[!A-Za-z0-9._-]*) fail "网关域名只允许字母、数字与 . - _（收到：$DOMAIN）" ;;
  esac
  put ZEROCODE_GATEWAY_DOMAIN "$DOMAIN"
  SSL="$(ask '应用地址走 https 域名形态吗 ZEROCODE_SSL_ENABLE（域名未备案或 443 被拦时填 false，改用 IP 直连 prod 端口）' "$(default_of ZEROCODE_SSL_ENABLE)")"
  SSL="${SSL:-true}"
  case "$SSL" in
    true|false|1|0|yes|no) put ZEROCODE_SSL_ENABLE "$SSL" ;;
    *) fail "ZEROCODE_SSL_ENABLE 只认 true/false（收到 $SSL）" ;;
  esac
  # 证书签发不在本脚本里（第 9 步只认 gateway/certs/ 里现成的证书）：acme 要联网、要 DNSPod
  # 凭据、还会被 Let's Encrypt 限流，让平台升级取决于证书通道那天健不健康不值当。
  printf '  证书由 gen_ssl.sh 现场签发：首次或换证书跑 %s\n' "$DEPLOY_ROOT/gen_ssl.sh" >&2
else
  GATEWAY=0
  warn "不起网关：控制台里的应用地址仍是 https://<slug>.<域名> 形态，点进去会 404。"
  warn "只想用 IP 直连就把 .env 的 ZEROCODE_SSL_ENABLE 设成 false（README §2），网关不必停。"
fi

# 探针宿主：不问（几乎不该改），但值错了必须当场拦——它就是「预览等满健康窗口报未就绪」的真因。
PROBE="$(envval ZEROCODE_PREVIEW_PROBE_HOST "$ENVF")"
[ -n "$PROBE" ] || PROBE="$(envval ZEROCODE_PREVIEW_PROBE_HOST "$PKG/.env.example")"
[ "$PROBE" = "127.0.0.1" ] && fail ".env 把 ZEROCODE_PREVIEW_PROBE_HOST 设成 127.0.0.1：容器里的回环是它自己，每次点预览都会等满健康窗口报「未就绪」。改成 host.docker.internal"
ok "探针宿主 ${PROBE:-host.docker.internal(默认)}"

# ---------------------------------------------------------------- 5. 宿主端口预检

say "5/9 宿主端口"
check_port() { # $1=端口 $2=用途
  local rc who="" cid="" cname="" kind pid=""
  zc_busy "$1"; rc=$?
  case "$rc" in
    1) ok "宿主 $1（$2）空闲"; return 0 ;;
    2) warn "没有 ss，$1（$2）没法预检（真撞上时 compose 会报 port is already allocated）"; return 0 ;;
  esac
  # 意外退出码按「可能被占」处理：宁可多问一句，也不要静默放行到 compose 起栈才炸。
  [ "$rc" = 0 ] || warn "端口 $1 的占用判定返回了意外退出码 $rc，按「可能被占」继续"
  who="$(ss -ltnpH 2>/dev/null | awk -v p=":$1" '$4 ~ p"$"' | head -n1 || true)"
  warn "宿主 $1（$2）被占：${who:-ss 拿不到进程信息}"
  cid="$(docker ps -q --filter "publish=$1" 2>/dev/null | head -n1 || true)"
  [ -n "$cid" ] && cname="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||' || true)"
  kind="$(printf '%s\n' "$who" | zc_port_holder_kind "$cid")"
  case "$kind" in
    container)
      # 只 stop 不删：docker start <name> 就能恢复，比 rm 温和。
      if ! ask_yes "占用 $1 的是容器 ${cname:-$cid}。stop 它腾地方？（不删容器，docker start ${cname:-$cid} 可恢复）(y/N)" n; then
        fail "端口 $1 得先腾出来。不建议改 PLATFORM_PORT——它是对外的控制台地址，还要与云安全组放行的一致；这次是别人没清干净，不该让部署来买单。"
      fi
      docker stop "${cname:-$cid}" >/dev/null || fail "stop ${cname:-$cid} 失败"
      warn "已 stop 容器 ${cname:-$cid} 腾出 $1（要恢复：docker start ${cname:-$cid}）"
      for _ in 1 2 3 4 5; do zc_busy "$1" || { ok "$1 已空出"; return 0; }; sleep 1; done
      fail "stop 掉 ${cname:-$cid} 后 $1 仍被占：占用者不止一个，按上面 ss 的 PID 手工腾"
      ;;
    host-process)
      # 宿主进程一律不自动 kill：认不出它是 ssh 还是数据库的代价，不该由部署来付。
      pid="$(printf '%s\n' "$who" | zc_port_holder_pid)"
      [ -n "$pid" ] || fail "认不出占用 $1 的进程：手工查 ss -ltnp | grep :$1"
      fail "占用 $1 的是宿主进程 $pid（$(ps -o args= -p "$pid" 2>/dev/null || echo 未知)）：确认它不是别的服务后 kill $pid，再重跑"
      ;;
    *)
      fail "认不出占用 $1 的进程（ss 没给 PID，也不是发布该端口的容器）：手工查 ss -ltnp | grep :$1"
      ;;
  esac
}
check_port "$PLAT" "控制台"
if [ "$GATEWAY" = 1 ]; then
  check_port 80 "网关"
  check_port 443 "网关"
fi

# ---------------------------------------------------------------- 6. 落盘：.env 合并 + 铺包/升级合并

say "6/9 落盘"
mkdir -p "$DEPLOY_ROOT" || fail "建不出 $DEPLOY_ROOT"

# 动笔前先备份（D-07 唯一的后路）；新增模式下没有旧文件，zc_env_backup 静默跳过。
BAK="$(zc_env_backup "$ENVF" "$DEPLOY_ROOT")"
[ -n "$BAK" ] && ok "旧 .env 已备份为 $(basename -- "$BAK")（只保留最近 5 份）"

put DEPLOY_ROOT "$DEPLOY_ROOT"          # 同路径挂载、WORKDIR、root_path 三处同源的根值（ADR-0034）

if [ ! -f "$ENVF" ] && [ -f "$PKG/.env.example" ]; then
  # 新增：从模板起步再合并本次答案，于是服务器上的 .env 自带全部键位与注释。
  # 现场已有 .env 时绝不走这条——那等于拿模板覆盖现场配置。
  cp -- "$PKG/.env.example" "$ENVF" || fail "复制 .env.example 到 $ENVF 失败"
  ok ".env 从包里的 .env.example 起步"
fi
if [ ! -f "$ENVF" ]; then
  # 连包里的 .env.example 也没了（缺文件本该在打包时就拦住）。这里必须先造一个空文件：
  # 下面那条管道左边是 `cat $ENVF`，在 set -o pipefail 下它失败会让整条合并管道报红，
  # 症状是一句「合并 .env 失败」——而文件其实写得好好的。
  : > "$ENVF" || fail "建不出 $ENVF"
  warn "$ENVF 是新建的空文件：包里缺 .env.example，键位只有本次问到的这些"
fi

# 合并写：只动被问到的键，没被问到的既有键与注释一律原样保留（D-06 在 .env 上的投影）。
TMP_ENV="$(mktemp)"
{ [ -f "$ENVF" ] && cat -- "$ENVF"; } | zc_env_render "${PAIRS[@]}" > "$TMP_ENV" \
  || { rm -f -- "$TMP_ENV"; fail "合并 .env 失败"; }
mv -- "$TMP_ENV" "$ENVF" || { rm -f -- "$TMP_ENV"; fail "写 $ENVF 失败（权限或磁盘）"; }
ok "已合并 ${#PAIRS[@]} 个键进 $ENVF（既有键与注释保留）"

for d in apps data store gateway/certs gateway/routes; do
  mkdir -p "$DEPLOY_ROOT/$d" || fail "建不出 $DEPLOY_ROOT/$d"
done
ok "同路径挂载点 apps/ data/ store/ gateway/certs/ gateway/routes/ 就位"

printf '%s\n' "$(zc_merge_plan "$MODE" "$DEPLOY_ROOT")" >&2
if [ "$IN_PLACE" = 1 ]; then
  ok "包就在部署根里，跳过合并搬运"
elif [ "$MODE" = new ]; then
  printf '  → 铺包 %s/ → %s/（不带 --delete：这个根下没有现场产物要保；images/ 不铺，第 7 步直接 load 进 docker）\n' "$PKG" "$DEPLOY_ROOT" >&2
  # --exclude 放在 SRC 之后：红线测试按行首 rsync -a "$PKG"/ 认「新模式」那一行
  rsync -a "$PKG"/ --exclude images/ "$DEPLOY_ROOT"/ || fail "铺包失败：$PKG → $DEPLOY_ROOT"
  ok "包已整体铺进 $DEPLOY_ROOT"
else
  cd "$DEPLOY_ROOT" || fail "进不去 $DEPLOY_ROOT"
  for d in backend browser sandbox frontend; do
    [ -d "$PKG/$d" ] || { warn "包里缺 $d/，跳过这一棵（缺文件本该在打包时就拦住）"; continue; }
    printf '  → 镜像同步 %s/（带 --delete，全场只有这四棵带）\n' "$d" >&2
    rsync -a --delete "$PKG/$d/" "$DEPLOY_ROOT/$d/" || fail "镜像 $d/ 失败"
  done
  for f in docker-compose.yml init.sql apply-init.sh deploy.sh deploy_lib.sh gen_ssl.sh deploy.ps1 deploy.bat \
           .env.example README-deploy.md .dockerignore; do
    [ -f "$PKG/$f" ] || continue
    cp -- "$PKG/$f" "$DEPLOY_ROOT/$f" || fail "覆盖 $f 失败"
  done
  ok "顶层生成物只覆盖未删"
  # 网关：不猜现场 compose 的结构，只覆盖配置与脚本；certs/、routes/、.env 一个不碰（US-17）。
  for f in nginx.conf docker-compose.yml make-cert.sh make-cert.ps1 404.html lua/router.lua .env.example; do
    [ -f "$PKG/gateway/$f" ] || continue
    mkdir -p "$DEPLOY_ROOT/gateway/$(dirname -- "$f")"
    cp -- "$PKG/gateway/$f" "$DEPLOY_ROOT/gateway/$f" || fail "覆盖 gateway/$f 失败"
  done
  ok "gateway/ 只覆盖配置与脚本（certs/ routes/ .env 未碰）"
fi

# 答案落盘（口令不在 KV 里）；放在起栈之前，好让「要重启的机器」重跑时一路回车（US-13）。
for kv in "${KV[@]}"; do
  zc_answers_put "$ANSF" "${kv%%=*}" "${kv#*=}" || fail "写应答文件失败：$kv"
done
ok "本次答案已存进 $ANSF（口令不进这里，只进 .env）"

cd "$DEPLOY_ROOT" || fail "进不去 $DEPLOY_ROOT"

# ---------------------------------------------------------------- 7. 起栈

say "7/9 起平台栈"
# compose 落后于包时最阴的症状：整块没有 browser 服务，平台照常起、预览照常，
# 只有 Agent 每次 preview_page 报一句没有出路的「浏览器检查服务不可用」（2026-11-01 演示机实撞）。
SVC="$(docker compose config --services 2>&1)" || fail "compose 解析失败：$SVC"
printf '%s\n' "$SVC" | grep -qx browser || fail \
  "compose 里没有 browser 服务：这份 docker-compose.yml 比 preview_page 还旧。把新包的 docker-compose.yml 与 browser/ 目录传上来再重跑（换整份 compose 前先 diff 保住服务器本地改过的默认值）"
ok "compose 里有 browser sidecar 服务"

# 部署根是运行期取的（ADR-0034）：镜像 WORKDIR 在构建期定值，换根必须重建镜像。
# OLD_ROOT 是第 1 步定的，读的是合并前的 .env——在这里重读会拿到本次刚写进去的新值，判定恒为假。
if [ -n "$OLD_ROOT" ] && [ "$OLD_ROOT" != "$DEPLOY_ROOT" ]; then
  warn "部署根从 $OLD_ROOT 换成 $DEPLOY_ROOT：本次重建会让镜像 WORKDIR 跟上"
  warn "换根后记得核对 $DEPLOY_ROOT/apps 里既有工程的位置，与库里的 root_path 是否一致"
fi

# 镜像包（ADR-0072）：打包器在 images/ 里放了平台安装期五个镜像的快照 + manifest。本机镜像 ID
# 与清单全一致就跳过 load；否则 load 一次（升级包带新快照时，同 tag 覆盖旧镜像）。load 成功后
# 整段联网预热与 compose pull 都跳过——境内机上那一步就是安装耗时的大头。没有镜像包
# （默认小包、未加 --images 的包、或更早的包）时保持原行为，下面两条腿照跑。
IMAGES_READY=0
IMG_MANIFEST="$PKG/images/manifest.tsv"
IMG_TAR="$PKG/images/zerocode-images.tar.gz"
if [ -f "$IMG_MANIFEST" ]; then
  if [ ! -f "$IMG_TAR" ]; then
    warn "images/ 里有清单却缺 tar（$IMG_TAR）：本次回退到联网预热"
  elif zc_images_ready "$IMG_MANIFEST"; then
    ok "本地镜像与包内清单一致，跳过 docker load"
    IMAGES_READY=1
  else
    say "docker load 包内镜像（数百 MB，视磁盘几分钟；ADR-0072）"
    docker load -i "$IMG_TAR" \
      || fail "包内镜像加载失败：$IMG_TAR（文件损坏或磁盘满）。删掉 images/ 重跑本脚本可回退到联网拉取"
    zc_images_ready "$IMG_MANIFEST" \
      || fail "镜像已从包内加载，但仍与 manifest 不一致：包被改过或加载不完整，重新打包再传"
    IMAGES_READY=1
    ok "包内镜像已就位（本次安装免拉取）"
  fi
fi

# 预热分两条腿，各管各的镜像，因为「能 pull」这件事只有 compose 自己说得准：
#   - Dockerfile 的 FROM 基础镜像（python / node / openresty…）是镜像构建期的真卡点，逐个拉、
#     拉不动当场指名（US-12）。
#   - 服务镜像交给 `docker compose pull --ignore-buildable`。不能把 `docker compose config
#     --images` 的清单直接拿来 pull：它连 backend/browser 两个**构建目标**（zerocode-platform:demo
#     这类本地 tag）一起列出来，而那些 tag 不在任何 registry 里——全新机器会死在自己的预检上
#     （本地实测 config --images 返回 3 项，其中 2 项根本拉不到）。
pull_bases() {          # stdout=拉不动的镜像名（空格分隔；全到位就是空行）
  local out="" i
  # 逐文件读，不用一条 awk 带多个文件名：awk 遇到第一个缺失文件就**致命中断**，
  # 后面的一个都不读——名单里任何一棵没铺上，整轮预热会静默变成空清单。
  for i in $(for f in backend/Dockerfile browser/Dockerfile \
                      sandbox/Dockerfile sandbox/egress/Dockerfile; do
               [ -f "$f" ] && awk '/^FROM[[:space:]]/ {print $2}' "$f"
             done | grep -vE '^[$<]' | sort -u); do
    docker image inspect "$i" >/dev/null 2>&1 && { ok "本地已有 $i"; continue; }
    printf '  → 预热 %s\n' "$i" >&2
    if docker pull "$i" >/dev/null 2>&1; then ok "$i 就位"; else out="$out $i"; fi
  done
  printf '%s\n' "$out"
}

pull_service_images() { # 0=都到位（或这版插件没这个能力，已降级）
  # 探测本身不能用 grep -q：命中即退会在 pipefail 下吃到 SIGPIPE，把「有」判成「没有」。
  docker compose pull --help 2>/dev/null \
    | awk '/--ignore-buildable/ { found = 1 } END { exit !found }' \
    || { warn "这版 docker compose 插件没有 --ignore-buildable：跳过服务镜像预热（pg 留到 up 时拉）"; return 0; }
  docker compose pull --quiet --ignore-buildable || return 1
  ok "compose 侧服务镜像就位（本包的构建目标已跳过）"
}

if [ "$IMAGES_READY" = 1 ]; then
  ok "包内镜像就是本次要用的：跳过联网预热与 compose pull（新鲜度由打包端 --refresh-images 负责）"
else
  PULLFAIL="$(pull_bases)"
  COMPOSE_PULL_BAD=0
  pull_service_images || COMPOSE_PULL_BAD=1
  # 第 2 步的镜像站提问只管 apt 安装通道（不写 daemon.json）；registry 加速只在这里问——
  # 「机器上早就有 docker、只是拉不动 Docker Hub」才是境内演示机最常见的形态，那时候安装
  # 分支根本不会进，所以这里必须是 registry 加速的唯一入口。
  if { [ -n "$PULLFAIL" ] || [ "$COMPOSE_PULL_BAD" = 1 ]; } \
     && ask_yes "上面这些镜像拉不动。现在配 registry 镜像加速吗？写 /etc/docker/daemon.json 并重启 docker 后重拉（y/N）" n; then
    # 默认值取本机实测可达的 daocloud 公共加速站；阿里云/腾讯的官方加速器只对自家云内机器开放，
    # 本地机器访问是 403（2026-11 实测），别照着旧提示填。
    PULL_MIRROR="$(ask '镜像站地址（如 https://docker.m.daocloud.io，或 https://docker.1ms.run）' 'https://docker.m.daocloud.io')"
    valid_mirror "$PULL_MIRROR" || fail "镜像站地址只允许字母、数字与 . : / _ - ：$PULL_MIRROR"
    apply_registry_mirror "$PULL_MIRROR"
    wait_docker_ready || warn "重启 docker 后环境判定仍有缺口：$(printf '%s ' $(zc_env_gaps))"
    PULLFAIL="$(pull_bases)"
    COMPOSE_PULL_BAD=0
    pull_service_images || COMPOSE_PULL_BAD=1
  fi
  [ -z "$PULLFAIL" ] && [ "$COMPOSE_PULL_BAD" = 0 ] || fail "基础镜像拉不动：${PULLFAIL:-（见上面 compose 的报错）}
  build 会在同一个地方卡住（表现是半路没动静），所以停在这里比让它跑二十分钟诚实。
  处置：上面那句提问回答 y（本步就能写 daemon.json 并重启 docker）；或手工写
  /etc/docker/daemon.json 的 registry-mirrors 再 systemctl restart docker；或换可达网络。
  应用构建期的 pip/npm 源是另一条通道，用第 4 步问过的两个键。"
fi

if [ "$DROP_DB" = 1 ]; then
  printf '  → docker compose down -v（删的是你在第 3 步确认过的 pgdata 卷）\n' >&2
  docker compose down -v --remove-orphans || fail "down -v 没跑干净：docker volume ls 核对后重跑"
  warn "旧栈与 pgdata 卷已清除"
fi

# 平台代码与前端 dist 都烘在镜像里（backend/Dockerfile 只 COPY backend/app 与 frontend/dist）。
# 快照就绪且部署根＝打包根：包内快照就是本次要跑的镜像（升级的新代码经新快照 load 进来，
# compose up -d 见镜像 ID 变化会重建容器），跳过 --build——新机器由此免掉整段 pip/apt/Playwright
# 构建（2 核演示机全量 build 实测约 2 小时，2026-11-26）。无快照或换根仍 --build：WORKDIR 是
# 构建期值（ADR-0034）。判定在 zc_build_plan（桩测覆盖）。
BUILD_PLAN="$(zc_build_plan "$IMAGES_READY" "$DEPLOY_ROOT" "$BAKED_ROOT")"
if [ "$BUILD_PLAN" = skip ]; then
  say "docker compose up -d（快照就绪且部署根未变：免重建）"
  docker compose up -d || fail "起栈失败：docker compose logs backend --tail 100"
else
  say "docker compose up -d --build（无快照或换根：必须重建；浏览器镜像含 Chromium，首次几分钟起）"
  docker compose up -d --build || fail "起栈失败：docker compose logs backend --tail 100（镜像里的依赖装不动就回头看第 7 步的镜像源提示）"
fi

# 沙箱两个镜像：快照里已含（BUILT_IMAGE_REFS），就绪即跳过显式 build；无快照时必须显式 build
# （ADR-0066）：sandbox-image 带 profiles: [build-only]——它不是常驻服务，平台按需 docker run
# 起一次性容器，`up -d --build` 不会构建它。漏这一步的表现是 run_python/run_shell 恒回
# 「沙箱镜像未构建」，而平台其余功能一切正常，很难联想到这里。
if [ "$IMAGES_READY" = 1 ]; then
  ok "沙箱两镜像已含在包内快照，跳过显式 build"
else
  say "docker compose build sandbox-image sandbox-egress（沙箱执行环境 + 出口 router；首次装 pandas 等轮子，数分钟）"
  docker compose build sandbox-image sandbox-egress \
    || fail "沙箱镜像构建失败：docker compose build sandbox-image 看日志；网络受限先设 .env 里的 ZEROCODE_APT_MIRROR / ZEROCODE_PYPI_MIRROR"
fi

say "等就绪"
pg=""
for _ in $(seq 1 60); do
  pg="$(docker inspect -f '{{.State.Health.Status}}' zerocode-pg16 2>/dev/null || echo unknown)"
  [ "$pg" = healthy ] && break
  sleep 2
done
[ "$pg" = healthy ] || fail "pg 没进到 healthy（当前 $pg）：docker compose logs pg --tail 100"
ok "pg healthy"
web=0
for _ in $(seq 1 45); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PLAT/" 2>/dev/null; then web=1; break; fi
  sleep 2
done
[ "$web" -eq 1 ] || fail "控制台 http://127.0.0.1:$PLAT/ 没回 200：docker compose logs backend --tail 100"
ok "平台控制台已回 200"

# ---------------------------------------------------------------- 8. init.sql 与运行参数自检

say "8/9 全量 init.sql（重放安全：只补不覆盖，可重复执行）"
# 镜像里根本没有 changelog/，backend 首启的 run_migrations 面对不存在的目录会静默空转——
# 这一步是每次发布不可省的唯一 schema 通道（ADR-0014）。忘了跑的症状是「新表没出现但日志一切正常」。
bash apply-init.sh || fail "apply-init.sh 失败：核对 .env 里的 POSTGRES_USER / POSTGRES_PASSWORD 与 pg 容器状态"

cenv() { docker exec zerocode-backend printenv "$1" 2>/dev/null || true; }

say "运行参数自检"
got="$(cenv ZEROCODE_PREVIEW_PROBE_HOST)"
[ -n "$got" ] || fail "容器里没有 ZEROCODE_PREVIEW_PROBE_HOST：compose 不是这一版（探针会退回 127.0.0.1，预览必等满超时）"
docker exec zerocode-backend python -c \
  'import socket; socket.gethostbyname("host.docker.internal")' >/dev/null 2>&1 \
  || fail "容器里解析不了 host.docker.internal：compose 缺 extra_hosts: [host.docker.internal:host-gateway]"
ok "探针宿主 $got 已就位且可解析"

# sidecar 要探实连，不能只看 compose 里有服务：根文件系统只读 + tmpfs + cap_drop 这套约束下
# Chromium 起不来时容器会反复重启，而 preview_page 只会吐一句「浏览器检查服务不可用」。
burl="$(cenv ZEROCODE_BROWSER_URL)"
[ -n "$burl" ] || fail "容器里没有 ZEROCODE_BROWSER_URL：compose 的 backend environment 不是这一版（后端回落连 127.0.0.1:8101，preview_page 固定失败）"
sidecar=0
for _ in $(seq 1 10); do
  if docker exec -e ZCB_URL="$burl" zerocode-backend python -c \
       'import os,httpx; httpx.get(os.environ["ZCB_URL"].rstrip("/")+"/healthz",timeout=8).raise_for_status()' \
       >/dev/null 2>&1; then sidecar=1; break; fi
  sleep 2
done
[ "$sidecar" -eq 1 ] || fail "后端容器里连不通 $burl/healthz：docker compose ps browser 看它是否在反复重启，再 docker compose logs browser --tail 100 看 Chromium 启动错误；内存紧的机器先 docker inspect -f '{{.State.OOMKilled}}' zerocode-browser"
ok "浏览器 sidecar 就位（$burl，/healthz 已通）"

# 沙箱要探两件事（ADR-0066）：镜像在 + 出口 router 在跑。只探镜像会漏掉最阴的一种——
# 镜像 build 出来了但 router 没起，此时沙箱 fail-closed 回「出口 router 未在运行」，
# 而平台其余功能一切正常，不探这里就联想不到根因。
simg="$(cenv ZEROCODE_SANDBOX_IMAGE)"
[ -n "$simg" ] || fail "容器里没有 ZEROCODE_SANDBOX_IMAGE：compose 的 backend environment 不是这一版（后端回落找 :dev tag，run_python/run_shell 固定失败）"
docker image inspect "$simg" >/dev/null 2>&1 \
  || fail "沙箱镜像 $simg 不在：第 7 步的 docker compose build sandbox-image sandbox-egress 没跑或失败（它带 profiles: [build-only]，up -d --build 不会构建它）"
router="$(docker inspect -f '{{.State.Running}}' zerocode-sandbox-egress 2>/dev/null || echo missing)"
[ "$router" = true ] \
  || fail "出口 router zerocode-sandbox-egress 没在跑（$router）：docker compose logs sandbox-egress --tail 50 看它是不是等不到规则文件；规则由平台启动时写进 <部署根>/data/sandbox_egress/"
ok "沙箱就位（镜像 $simg + 出口 router running）"

for k in ZEROCODE_GATEWAY_DOMAIN ZEROCODE_PREVIEW_HOST ZEROCODE_PYPI_MIRROR ZEROCODE_NPM_REGISTRY ZEROCODE_SSL_ENABLE; do
  v="$(cenv "$k")"; ok "$k=${v:-（未设）}"
done
[ "$(cenv ZEROCODE_GATEWAY_DOMAIN)" = "$(envval ZEROCODE_GATEWAY_DOMAIN "$ENVF")" ] || \
  warn "容器里的域名与 .env 不一致：shell 里 export 的同名变量会顶掉 .env（compose 插值优先级）"
[ "$(cenv DEPLOY_ROOT)" = "$DEPLOY_ROOT" ] || \
  warn "容器里的 DEPLOY_ROOT=$(cenv DEPLOY_ROOT) 与本次的 $DEPLOY_ROOT 不一致：镜像 WORKDIR 还停在旧根，重跑一次带 --build 的起栈"

# ---------------------------------------------------------------- 9. 网关与收尾

if [ "$GATEWAY" = 1 ]; then
  say "9/9 中央网关（独立 compose 项目，读 gateway/.env）"
  [ -n "${DOMAIN:-}" ] || fail "缺 ZEROCODE_GATEWAY_DOMAIN：网关起起来也不认任何域名"
  mkdir -p gateway/certs

  # 本脚本不签证书。签发（acme 真证书或内网自签）住在 gen_ssl.sh——那里才需要联网与 DNSPod 凭据，
  # 把它们留在这里就等于让平台升级取决于证书通道那天健不健康。
  # 缺证书就不起网关：openresty 会死在 ssl_certificate 指向的文件不存在上，那种半死状态比不起难查。
  if [ -f gateway/certs/gateway.crt ] && [ -f gateway/certs/gateway.key ]; then
    ok "沿用现场 gateway/certs/ 里的证书（本脚本不重签、不覆盖；换证书跑 bash gen_ssl.sh）"
    if [ "$MODE" = upgrade ] && ask_yes "网关容器要按新的 gateway/docker-compose.yml 重建吗？（配置改过才需要，y/N）" n; then
      docker compose -f gateway/docker-compose.yml up -d --force-recreate \
        || fail "网关重建失败：docker compose -f gateway/docker-compose.yml logs gateway"
    else
      docker compose -f gateway/docker-compose.yml up -d \
        || fail "网关起不来：docker compose -f gateway/docker-compose.yml logs gateway"
    fi
    ok "网关已起（还要 DNS 的 * 与 @ 两条 A 记录指向本机，才认 https://<slug>.$DOMAIN）"
  else
    warn "gateway/certs/ 里还没有 gateway.crt / gateway.key：缺证书网关起来也是报错，本次不起网关。"
    warn "签证书：cd $DEPLOY_ROOT && bash gen_ssl.sh（acme 真证书或自签两条都在里面，签完顺手把网关起起来）"
    warn "证书就位后重跑本脚本，或手工 docker compose -f gateway/docker-compose.yml up -d；在那之前应用地址点不开。"
  fi
else
  say "9/9 网关（本次未起，跳过 80/443）"
fi

say "完成"
cat <<EOF
  控制台        http://${PREVIEW_HOST}:${PLAT}   （注册的第一个账号即创始人，并接管所有预置应用）
  预览地址基座  http://${PREVIEW_HOST}:<dev端口>——宿主防火墙与安全组要放行应用端口 $APP_PORT_FROM-$APP_PORT_TO
  起预览        控制台点「启动预览」；首次会在容器里装 pip/npm 依赖，等满 240s 才判超时（vue3-springboot 走 900s）
  预置应用      先点一次「预览」让平台供给分库与凭据，之后才点「发布」（直接发布会被 500 拦下并提示）
  本次部署根    $DEPLOY_ROOT（模式：$MODE）
  .env 备份     ${BAK:-没有旧 .env 需要备份}
  应答文件      $ANSF（下次运行的默认值来源；口令不在里面）
  下次发布      新包传到 $DEPLOY_ROOT 的同级目录，进去 bash deploy.sh，选 upgrade
EOF
if [ "$GATEWAY" = 1 ] && [ ! -f "$DEPLOY_ROOT/gateway/certs/gateway.crt" ]; then
  printf '  还差证书      cd %s && bash gen_ssl.sh（签完顺手起网关；在那之前应用域名地址点不开）\n' "$DEPLOY_ROOT"
fi
