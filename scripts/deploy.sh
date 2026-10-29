#!/usr/bin/env bash
# zerocode 演示部署包 —— 服务器一键部署
#
# 用法：bash deploy.sh [--check] [--no-build] [--gateway] [--takeover] [--fresh-db]
#   --check      只跑前置检查，不改任何东西（预检失败原因与实跑一致）
#   --no-build   不重建平台镜像，直接 up -d（只改 .env 时用）
#   --gateway    顺带起中央网关（要 DNS 已解析、宿主 80/443 空闲；缺证书会自签）
#   --takeover   允许动旧栈：移除占着 zerocode-pg16/zerocode-backend 写死名字的容器，
#                并 stop 掉占着控制台/网关端口的容器（都不碰数据卷，宿主进程一律不碰）
#   --fresh-db   连 pgdata 卷一起删再重来（平台库与所有应用分库全没，需手打 yes）
set -euo pipefail
cd "$(dirname "$0")"

# 打包时烘死的部署根：compose 的同路径挂载、镜像 WORKDIR、init.sql 里的 root_path 三处同源，
# .env 改不了它。包放错目录时容器会在挂载点建出空目录，症状是「应用列表空、新建应用找不到」。
DEPLOY_ROOT="/opt/zerocode"
FIXED_NAMES=(zerocode-pg16 zerocode-backend)
APP_PORT_FROM=8210
APP_PORT_TO=8399

GATEWAY=0 TAKEOVER=0 FRESH_DB=0 SKIP_BUILD=0 ONLY_CHECK=0
for a in "$@"; do
  case "$a" in
    --gateway)  GATEWAY=1 ;;
    --takeover) TAKEOVER=1 ;;
    --fresh-db) FRESH_DB=1 ;;
    --no-build) SKIP_BUILD=1 ;;
    --check)    ONLY_CHECK=1 ;;
    -h|--help)  sed -n '2,10p' "$0"; exit 0 ;;
    *) printf '✗ 未知参数：%s（bash deploy.sh --help）\n' "$a" >&2; exit 2 ;;
  esac
done

say()  { printf '\n▶ %s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

# 只挑需要的键，绝不 source 整个 .env（那会把明文口令带进本进程环境）
envval() { grep -E "^[[:space:]]*$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r ' || true; }
cenv()   { docker exec zerocode-backend printenv "$1" 2>/dev/null || true; }

grep -q $'\r' "$0" && fail "deploy.sh 被存成了 CRLF，bash 会把行尾 \r 当字符：sed -i 's/\r\$//' deploy.sh"

say "1/6 前置检查"
[ -f .env ] || fail "缺 .env：包里应自带；丢了就 cp .env.example .env 并填 POSTGRES_PASSWORD"
[ -n "$(envval POSTGRES_PASSWORD)" ] || fail ".env 的 POSTGRES_PASSWORD 为空（compose 里它是 :? 硬闸门，空值直接拒收整个文件）"
command -v docker >/dev/null || fail "docker CLI 没装：先装 docker-ce 与 docker-compose-plugin"
docker compose version >/dev/null 2>&1 || fail "缺 compose v2 插件：apt install docker-compose-plugin"
docker info >/dev/null 2>&1 || fail "docker 守护进程连不上：daemon 没起，或当前用户不在 docker 组"
if [ "$(uname -s)" = Linux ] && [ ! -S /var/run/docker.sock ]; then
  fail "缺 /var/run/docker.sock：平台要在宿主起应用 dev/prod 栈，compose 里那条挂载会直接失败"
fi
ok "docker、compose v2、docker.sock 就位"

PROBE="$(envval ZEROCODE_PREVIEW_PROBE_HOST)"
[ "$PROBE" = "127.0.0.1" ] && fail \
  ".env 把 ZEROCODE_PREVIEW_PROBE_HOST 设成 127.0.0.1：容器里的回环是它自己，每次点预览都会等满健康窗口报「未就绪」。改成 host.docker.internal"
DOMAIN="$(envval ZEROCODE_GATEWAY_DOMAIN)"
PLAT="$(envval PLATFORM_PORT)"; PLAT=${PLAT:-8100}
PREVIEW_HOST="$(envval ZEROCODE_PREVIEW_HOST)"; PREVIEW_HOST=${PREVIEW_HOST:-<服务器IP>}
ok "运行参数：探针宿主 ${PROBE:-host.docker.internal(默认)}｜域名 ${DOMAIN:-未设}｜控制台端口 $PLAT"

if [ "$ONLY_CHECK" -eq 1 ]; then
  warn "--check 模式跳过部署根校验（实跑要求本目录就是 $DEPLOY_ROOT）"
elif [ "$(pwd -P)" != "$DEPLOY_ROOT" ]; then
  fail "本包必须位于 $DEPLOY_ROOT，当前 $(pwd -P)：换目录请重打包 build-output.bat --deploy-root $(pwd -P)"
else
  ok "部署根 $(pwd -P)"
fi

TARGETS=("${FIXED_NAMES[@]}")
[ "$GATEWAY" -eq 1 ] && TARGETS+=("zerocode-gateway")
for n in "${TARGETS[@]}"; do
  if docker container inspect -f '{{.Name}}' "$n" >/dev/null 2>&1; then
    if [ "$ONLY_CHECK" -eq 1 ]; then warn "容器 $n 已存在，实跑需要 --takeover 顶掉它"; continue; fi
    [ "$TAKEOVER" -eq 1 ] && { docker rm -f "$n" >/dev/null; warn "--takeover：已移除旧容器 $n（数据卷未动）"; continue; }
    fail "旧容器 $n 还在，新项目会死在 container_name 冲突上。确认可以停它之后加 --takeover"
  fi
done

if ! command -v ss >/dev/null 2>&1; then
  warn "没有 ss，端口预检跳过（真撞上时 compose 报 port is already allocated）"
else
  # grep 的退出码就是判定结果，末尾不能挂 || true：set -e 在 && 左侧本就不生效，
  # 挂了只会把「空闲」也判成「被占」，且永远走不到查真占用者那一步。
  busy() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE ":$1\$"; }
  # 端口真被占时先指名道姓再决定动作。控制台端口是对外地址的一部分，
  # 让人改 PLATFORM_PORT 等于把上一次部署没清干净的锅甩给使用者。
  free_port() { # $1=端口 $2=用途
    busy "$1" || return 0
    local who="" cid="" cname="" pid=""
    who="$(ss -ltnpH 2>/dev/null | awk -v p=":$1" '$4 ~ p"$"' | head -n1 || true)"
    warn "宿主 $1（$2）被占：${who:-ss 拿不到进程信息}"
    if [ "$ONLY_CHECK" -eq 1 ]; then
      warn "--check：$1 被占，实跑要么先腾掉它，要么加 --takeover 让本脚本 stop 占用容器"
      return 0
    fi
    cid="$(docker ps -q --filter "publish=$1" 2>/dev/null | head -n1 || true)"
    if [ -n "$cid" ]; then
      cname="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||' || true)"
      [ "$TAKEOVER" -eq 1 ] \
        || fail "占用 $1 的是容器 ${cname:-$cid}。确认可以停它再加 --takeover（只 stop 不删，docker start ${cname:-$cid} 可恢复）"
      docker stop "$cid" >/dev/null
      warn "--takeover：已 stop 容器 ${cname:-$cid} 腾出 $1（要恢复：docker start ${cname:-$cid}）"
      for _ in 1 2 3 4 5; do busy "$1" || return 0; sleep 1; done
      fail "stop 掉 ${cname:-$cid} 后 $1 仍被占：占用者不止一个，按上面 ss 的 PID 手工腾"
    fi
    # 宿主进程一律不自动 kill：认不出它是 ssh 还是数据库的代价，不该由部署来付。
    pid="$(printf '%s' "$who" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p')"
    [ -n "$pid" ] || fail "认不出占用 $1 的进程：手工查 ss -ltnp | grep :$1"
    fail "占用 $1 的是宿主进程 $pid（$(ps -o args= -p "$pid" 2>/dev/null || echo 未知)）：确认它不是别的服务后 kill $pid，再重跑"
  }
  free_port "$PLAT" "控制台"
  if [ "$GATEWAY" -eq 1 ]; then
    free_port 80 "网关"
    free_port 443 "网关"
  fi
  ok "宿主端口空闲（$PLAT$([ "$GATEWAY" -eq 1 ] && echo '、80、443')）"
fi

[ "$FRESH_DB" -eq 1 ] && [ "$ONLY_CHECK" -eq 0 ] && {
  printf '  !! --fresh-db 会删掉 pgdata 卷：平台库（账号/应用行/会话）与全部应用分库一起没。输入 yes 继续：'
  read -r ans
  [ "$ans" = yes ] || fail "未确认，已中止（未做任何改动）"
  docker compose down -v --remove-orphans
  warn "旧栈与 pgdata 卷已清除"
}

[ "$ONLY_CHECK" -eq 1 ] && { say "预检通过（--check：未做任何改动）"; exit 0; }

say "2/6 同路径挂载点"
mkdir -p apps data store gateway/certs gateway/routes
ok "apps/ data/ store/ gateway/certs/ gateway/routes/ 就位（apps/store 为空是预期：本包不带预置应用）"

say "3/6 起平台栈"
if [ "$SKIP_BUILD" -eq 1 ]; then docker compose up -d; else docker compose up -d --build; fi

say "4/6 等就绪"
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

say "5/6 全量 init.sql（重放安全：只补不覆盖，可重复执行）"
bash apply-init.sh

say "6/6 运行参数自检"
got="$(cenv ZEROCODE_PREVIEW_PROBE_HOST)"
[ -n "$got" ] || fail "容器里没有 ZEROCODE_PREVIEW_PROBE_HOST：compose 不是这一版（探针会退回 127.0.0.1，预览必等满超时）"
docker exec zerocode-backend python -c \
  'import socket; socket.gethostbyname("host.docker.internal")' >/dev/null 2>&1 \
  || fail "容器里解析不了 host.docker.internal：compose 缺 extra_hosts: [host.docker.internal:host-gateway]"
ok "探针宿主 $got 已就位且可解析"
for k in ZEROCODE_GATEWAY_DOMAIN ZEROCODE_PREVIEW_HOST ZEROCODE_PYPI_MIRROR ZEROCODE_NPM_REGISTRY; do
  v="$(cenv "$k")"; ok "$k=${v:-（未设）}"
done
[ "$(cenv ZEROCODE_GATEWAY_DOMAIN)" = "$DOMAIN" ] || \
  warn "容器里的域名与 .env 不一致：shell 里 export 的同名变量会顶掉 .env（compose 插值优先级）"

if [ "$GATEWAY" -eq 1 ]; then
  say "网关（独立 compose 项目，读 gateway/.env）"
  [ -n "$DOMAIN" ] || fail "缺 ZEROCODE_GATEWAY_DOMAIN，没法签证书"
  if [ ! -f gateway/certs/gateway.crt ] || [ ! -f gateway/certs/gateway.key ]; then
    warn "缺 gateway/certs/gateway.crt|key，用包内脚本自签 *.${DOMAIN}（生产请换 acme.sh DNS-01 真证书）"
    docker run --rm -v "$PWD/gateway/certs:/out" -v "$PWD/gateway/make-cert.sh:/mk.sh" \
      openresty/openresty:alpine sh -c "tr -d '\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh '$DOMAIN'"
    warn "自签 CA 在 gateway/certs/ca.crt：客户端要导入「受信任的根证书颁发机构」，否则浏览器报证书链断"
  fi
  docker compose -f gateway/docker-compose.yml up -d
  ok "网关已起（还要 DNS 的 * 与 @ 两条 A 记录指向本机，才认 https://<slug}.${DOMAIN}）"
fi

say "完成"
cat <<EOF
  控制台        http://${PREVIEW_HOST}:$PLAT   （注册的第一个账号即创始人）
  预览地址基座  http://${PREVIEW_HOST}:<dev端口>——宿主防火墙与安全组要放行应用端口 $APP_PORT_FROM-$APP_PORT_TO
  起预览        控制台点「启动预览」；首次会在容器里装 pip/npm 依赖，等满 240s 才判超时（vue3-springboot 走 900s）
  增量发布      重传包（务必排除 .env、gateway/certs/、gateway/routes/）后重跑 bash deploy.sh
EOF
