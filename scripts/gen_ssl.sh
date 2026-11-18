#!/usr/bin/env bash
# zerocode 演示部署包 —— 中央网关的 TLS 证书签发（独立一支，不在 deploy.sh 里）
#
# 用法就这一条，没有任何命令行参数（与 deploy.sh 同守 D-11）：
#     bash gen_ssl.sh
#   在包目录里跑、在部署根里跑都行——脚本以自己所在位置找 gateway/。
#   想预先给值就 export：ZEROCODE_GATEWAY_DOMAIN / CERT_MODE / DP_Id / DP_Key，交互时直接回车即沿用。
#
# 为什么从 deploy.sh 第 9 步拆出来：签发要联网、要 DNSPod API 凭据、还会撞上 Let's Encrypt 的
# 限流。留在平台升级路径里，等于让「新包能不能装上」取决于证书通道那天健不健康。拆开后
# deploy.sh 只认 gateway/certs/ 里现成的证书（没有就跳过起网关并指回这里），首次签发与换证书都走这里。
#
# 产物名固定 gateway.crt / gateway.key：nginx.conf 读的就是这对，与域名解耦，换域名或换 CA
# 都不用改网关配置（ADR-0016）。
#
# 判定逻辑一律在 deploy_lib.sh（纯函数，桩测见 tests/test_deploy_lib.py）；本文件只做输入、
# 命令执行与副作用。
set -uo pipefail

say()  { printf '\n▶ %s\n' "$*" >&2; }
ok()   { printf '  ✓ %s\n' "$*" >&2; }
warn() { printf '  ! %s\n' "$*" >&2; }
fail() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

if [ "$#" -gt 0 ]; then
  printf '本脚本不接受命令行参数（危险动作改成现场提问，同 deploy.sh 的 D-11）。\n' >&2
  printf '  要预先给值：export ZEROCODE_GATEWAY_DOMAIN=… CERT_MODE=acme DP_Id=… DP_Key=…\n' >&2
  printf '  直接跑：bash gen_ssl.sh\n' >&2
  exit 2
fi

SELF="$0"
cd "$(dirname -- "$SELF")" 2>/dev/null || fail "进不去脚本所在目录：$(dirname -- "$SELF")"
PKG="$(pwd -P)"
SCRIPT="$PKG/$(basename -- "$SELF")"
LIB="$PKG/deploy_lib.sh"
GW="$PKG/gateway"
GWCOMPOSE="$GW/docker-compose.yml"
CERTS="$GW/certs"

# CRLF 自检（与 deploy.sh 同口径）：带着 \r 上服务器就是「bad interpreter」。make-cert.sh 不进
# 这条名单——它是在容器里经 `tr -d '\r'` 后执行的，宿主侧行尾本来就由那道命令兜住。
for f in "$SCRIPT" "$LIB"; do
  [ -f "$f" ] || fail "缺 $(basename -- "$f")：交付包不完整（gen_ssl.sh 与 deploy_lib.sh 必须整套上传）"
  grep -q $'\r' "$f" && fail "$(basename -- "$f") 被存成了 CRLF，bash 会把行尾 \r 当字符：sed -i 's/\r\$//' $(basename -- "$f")"
done
[ -f "$GWCOMPOSE" ] || fail "找不到 $GWCOMPOSE：gen_ssl.sh 要和 gateway/ 同一层放（包根或部署根，与 deploy.sh 齐平）"
[ -f "$GW/make-cert.sh" ] || fail "找不到 $GW/make-cert.sh：自签这条路要用它，重打包再传"

# 判定层必须能 source 且函数齐全：少一支会在签发中途才炸，那时 acme.sh 已经装上了。
# shellcheck source=./deploy_lib.sh
. "$LIB" || fail "source $LIB 失败"
for want in zc_acme_dnsapi zc_acme_creds_ok zc_acme_issue_cmd zc_acme_install_cmd \
            zc_gateway_reloadcmd; do
  declare -F "$want" >/dev/null || fail "deploy_lib.sh 里缺 $want——这份 lib 比 gen_ssl.sh 旧，重打包再传"
done

# 抬权：证书目录常被 deploy.sh 以 root 建过，容器也要 root 才管得动。
if [ "$(id -u)" != 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    printf '需要 root 权限（写 %s、管网关容器），用 sudo 重新拉起自己…\n' "$CERTS" >&2
    exec sudo -E bash "$SCRIPT" || fail "sudo 起不来：请用 root 直接跑 bash $SCRIPT"
  fi
  fail "当前不是 root 且没有 sudo：请用 root 跑 bash $SCRIPT"
fi

# ---------------------------------------------------------------- 输入小工具

# 只挑需要的键，绝不 source 整个 .env（那会把明文口令带进本进程环境）。
envval() { # $1=KEY $2=文件
  grep -E "^[[:space:]]*$1=" "$2" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' \
    | sed 's/[[:space:]]*$//' || true
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

# ---------------------------------------------------------------- 1. 域名

say "1/3 签发目标域名"
# 默认值取法与 deploy.sh 一致：现场 .env 是运行中的真值，其次网关自己的 .env，再退到打包值。
DOMAIN="${ZEROCODE_GATEWAY_DOMAIN:-}"
[ -n "$DOMAIN" ] || DOMAIN="$(envval ZEROCODE_GATEWAY_DOMAIN "$PKG/.env")"
[ -n "$DOMAIN" ] || DOMAIN="$(envval ZEROCODE_GATEWAY_DOMAIN "$GW/.env")"
[ -n "$DOMAIN" ] || DOMAIN="$(envval ZEROCODE_GATEWAY_DOMAIN "$PKG/.env.example")"
[ -n "$DOMAIN" ] || DOMAIN="$(envval ZEROCODE_GATEWAY_DOMAIN "$GW/.env.example")"
DOMAIN="$(ask '网关域名（签 <域名> 与 *.<域名> 这一对；与 .env 的 ZEROCODE_GATEWAY_DOMAIN 保持一致）' "$DOMAIN")"
[ -n "$DOMAIN" ] || fail "没有域名就没法签：要么现场填，要么先在 .env 里写好 ZEROCODE_GATEWAY_DOMAIN"
# 收窄字符集不是洁癖：这个值会被拼进 `docker run … sh -c "… '$DOMAIN'"` 的单引号里，带引号或
# 分号的「域名」就是让本脚本以 root 执行任意命令。顺带挡掉带空格的粘贴。
case "$DOMAIN" in
  *[!A-Za-z0-9._-]*) fail "网关域名只允许字母、数字与 . - _（收到：$DOMAIN）" ;;
esac
ok "签发目标：$DOMAIN 与 *.$DOMAIN"

# 域名有两条独立生效的通道（ADR-0016）：平台 .env 拼展示地址，gateway/.env 决定 nginx 认哪个
# 后缀。不一致的症状是「证书是新的、地址是对的，点进去全 404」且日志一句错都不报。
if [ -f "$GW/.env" ]; then
  GWDOM="$(envval ZEROCODE_GATEWAY_DOMAIN "$GW/.env")"
  if [ -n "$GWDOM" ] && [ "$GWDOM" != "$DOMAIN" ]; then
    warn "$GW/.env 里的 ZEROCODE_GATEWAY_DOMAIN 是 $GWDOM，与本次签的 $DOMAIN 不一致。"
    warn "签完手工把它改成 $DOMAIN（那是现场产物，本脚本不代改），否则 nginx 仍按旧后缀认域名。"
  fi
else
  warn "$GW/.env 不存在：网关会用 compose 里的打包默认域名认后缀，现场不一致就手工补一份。"
fi

# ---------------------------------------------------------------- 2. 签发

say "2/3 证书来源"
mkdir -p "$CERTS" || fail "建不出 $CERTS"

# 现场已有证书先问一句：换域名或换 CA 才需要重签，日常重跑不该动现成的私钥。
# 回答「不重签」就带着现有证书直接进第 3 步——来源与凭据的提问一并跳过，用不着的不张口要。
CERT_READY=0
if [ -f "$CERTS/gateway.crt" ] && [ -f "$CERTS/gateway.key" ]; then
  if ask_yes "现场已有 gateway/certs/gateway.crt 与 gateway.key，要重签并覆盖吗？（y/N）" n; then
    warn "确认重签：会覆盖现有的 gateway.crt / gateway.key"
  else
    ok "沿用现场已有的 $CERTS/（不重签、不覆盖）"
    CERT_READY=1
  fi
fi

if [ "$CERT_READY" = 1 ]; then
  CERT_MODE=keep
else
  CERT_MODE="${CERT_MODE:-acme}"
  CERT_MODE="$(ask_choice 'acme=Let'"'"'s Encrypt 泛域名真证书（需 DNSPod API 凭据）；selfsign=内网演示自签' "$CERT_MODE" 'acme selfsign')"
  if [ "$CERT_MODE" = acme ]; then
    DP_ID="${DP_Id:-}"; DP_KEY="${DP_Key:-}"
    if zc_acme_creds_ok "$DP_ID" "$DP_KEY"; then
      ok "沿用环境变量里的 DP_Id / DP_Key（不回显、不落盘）"
      warn "要换 DNSPod 账号先 unset DP_Id DP_Key——旧凭据会被直接沿用，症状是「Token 明明是对的却报 10003/9」"
    else
      # 凭据只进本进程环境，再由 acme.sh 存进它自己的 account.conf；绝不写 .env / 应答文件。
      printf '  DNSPod API Id（DP_Id，腾讯云 DNSPod 控制台「密钥管理」里的 ID）：' >&2
      IFS= read -r DP_ID || DP_ID=""; printf '\n' >&2
      printf '  DNSPod API Key（DP_Key，不回显）：' >&2
      IFS= read -rs DP_KEY || DP_KEY=""; printf '\n' >&2
      zc_acme_creds_ok "$DP_ID" "$DP_KEY" || fail "DNSPod 凭据没给全：DP_Id / DP_Key 都要有，或改用 selfsign"
    fi
  fi
fi

# 自签：*.域名 一张本地 CA 签的证书，产物名与真证书同一对，客户端要手工信任 certs/ca.crt。
cert_selfsign() {
  warn "自签 *.${DOMAIN}（仅内网演示：客户端要信任 $CERTS/ca.crt）"
  docker run --rm -v "$CERTS:/out" -v "$GW/make-cert.sh:/mk.sh" \
    openresty/openresty:alpine sh -c "tr -d '\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh '$DOMAIN'" \
    || fail "自签证书失败：gateway/make-cert.sh 与 openresty 镜像要能跑起来（境内机先确认镜像拉得动）"
}

# 真证书：acme.sh + DNSPod DNS-01。失败返回 1，由调用方决定怎么办，不把脚本死在一条命令上。
cert_acme() {
  local acme_home=/root/.acme.sh acme_bin=/root/.acme.sh/acme.sh dnsapi email reloadcmd issue install
  dnsapi="$(zc_acme_dnsapi dnspod)" || return 1
  email="${ACME_EMAIL:-admin@${DOMAIN}}"
  if [ ! -x "$acme_bin" ]; then
    ok "首次使用，安装 acme.sh 到 $acme_home（注册邮箱 $email）"
    curl -fsSL https://get.acme.sh | HOME=/root sh -s "email=$email" || { warn "acme.sh 安装失败（查 curl 与网络）"; return 1; }
    [ -x "$acme_bin" ] || { warn "装完仍找不到 $acme_bin"; return 1; }
  fi
  # acme.sh 会把这两个值以 SAVED_DP_Id / SAVED_DP_Key 存进自己的 account.conf（续期要用）；
  # 它们不进 .env / .deploy-answers。
  export DP_Id="${DP_ID:-}" DP_Key="${DP_KEY:-}"
  warn "DNSPod 凭据由 acme.sh 存进 $acme_home/account.conf（自动续期需要），不会写进 .env 或应答文件"
  reloadcmd="$(zc_gateway_reloadcmd "$GWCOMPOSE")"
  issue="$(zc_acme_issue_cmd "$acme_bin" "$DOMAIN" "$dnsapi")"
  install="$(zc_acme_install_cmd "$acme_bin" "$DOMAIN" "$CERTS/gateway.key" "$CERTS/gateway.crt" "$reloadcmd")"
  ok "签发 $DOMAIN + *.$DOMAIN（DNS-01 / DNSPod）"
  eval "$issue"   || { warn "acme.sh 签发失败（多为 DNS API 凭据、域名解析或 LE 限流）"; return 1; }
  eval "$install" || { warn "acme.sh 安装证书失败：证书签出来了但没能写进 $CERTS/"; return 1; }
  chmod 600 "$CERTS/gateway.key" 2>/dev/null || true
  ok "真证书已写入 $CERTS/gateway.crt|key；acme.sh cron 续期后会按 reloadcmd 重启网关"
}

if [ "$CERT_READY" = 0 ]; then
  if [ "$CERT_MODE" = acme ]; then
    if cert_acme; then
      CERT_READY=1
    else
      printf '  排障方向（这三条各自独立，别一上来就改 DNS）：\n' >&2
      printf '    - Token 类型：要 DNSPod 控制台「密钥管理」里的 DNSPod Token，不是腾讯云 AKID/SecretId（后者报 10003）。\n' >&2
      printf '    - 「invalid domain」是插件的兜底报错，真实码被吞；用 acme.sh --cron --debug 2 或 curl dnsapi.cn/Domain.List 定性。\n' >&2
      printf '    - 环境变量里若有旧凭据会跳过输入直接用它们：env | grep '"'"'^DP_'"'"'，要换先 unset。\n' >&2
      if ask_yes "真证书没拿到。改用自签让网关先起来吗？（换好真证书后重跑本脚本即可覆盖，y/N）" y; then
        warn '回退自签：浏览器会报证书不可信（issuer 是 Zerocode Local Root CA，不是 Let'"'"'s Encrypt），那是自签而不是 ACME 不可信'
        cert_selfsign
        CERT_READY=1
      else
        fail "既没拿到真证书也没自签：修好 DNS API 凭据后重跑 bash gen_ssl.sh"
      fi
    fi
  else
    cert_selfsign
    CERT_READY=1
  fi
fi
[ -f "$CERTS/gateway.crt" ] && [ -f "$CERTS/gateway.key" ] || \
  fail "签完仍找不到 $CERTS/gateway.crt|key：文件名必须就是这对（nginx.conf 只读它们）"
ok "证书就位：$(ls -1 "$CERTS" | tr '\n' ' ')"

# ---------------------------------------------------------------- 3. 让网关用上新证书

say "3/3 网关"
if [ "$(docker container inspect -f '{{.State.Running}}' zerocode-gateway 2>/dev/null || echo missing)" = true ]; then
  # 证书目录是 :ro 挂载，换文件不会让容器里的 nginx 重读——必须重启，否则「续期成功、
  # 网关还在用旧证书」这种静默失效就是这儿来的。
  printf '  → docker compose -f gateway/docker-compose.yml restart\n' >&2
  docker compose -f "$GWCOMPOSE" restart || fail "网关重启失败：docker compose -f gateway/docker-compose.yml logs gateway"
  ok "网关已重启，新证书生效"
else
  printf '  → docker compose -f gateway/docker-compose.yml up -d\n' >&2
  docker compose -f "$GWCOMPOSE" up -d || fail "网关起不来：docker compose -f gateway/docker-compose.yml logs gateway"
  ok "网关已起"
fi

case "$CERT_MODE" in
  keep)     SRC="沿用现场已有的证书（本次没重签）" ;;
  selfsign) SRC="内网自签：客户端要信任 $CERTS/ca.crt，否则浏览器一律拦成「不安全」" ;;
  *)        SRC="Let's Encrypt（acme.sh DNS-01，cron 续期后按 reloadcmd 重启网关）" ;;
esac
cat <<EOF
  证书        $CERTS/gateway.crt|key
  来源        $SRC
  生效前提    DNS 里 *.$DOMAIN 与 @.$DOMAIN 都指向本机，宿主 80/443 空闲且安全组放行
  域名一致性  平台 .env 与 gateway/.env 的 ZEROCODE_GATEWAY_DOMAIN 都要是 $DOMAIN，
              不一致的症状是「地址对的、点进去全 404」且日志一句错都不报（ADR-0016）
EOF
