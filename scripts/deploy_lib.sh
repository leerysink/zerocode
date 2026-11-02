#!/usr/bin/env bash
# zerocode 部署判定层（被 scripts/deploy.sh 与包内拷贝 source，不单独执行）
#
# 这层存在的唯一理由：deploy.sh 要在**不碰 docker、不碰宿主网络、不碰部署根**的前提下把分支跑尽。
# 所以本文件的每个函数要么纯文本进出，要么只动**显式传进来的路径**——不读全局变量、不 source .env、
# 不碰 docker、不写死 /opt/zerocode。桩测见 tests/test_deploy_lib.py（规格 §5 接缝 B）。
#
# 约定：
#   - stdout 只放可断言的结果（规范化后的路径、渲染后的文本、1/0、名单行）；解释与建议一律走 stderr。
#   - 判定型函数用退出码表达结论（zc_busy 的 2 = 无法判定），绝不在末尾挂 `|| true`——挂上就恒真，
#     端口预检于是每次都报「已被占」，这是本仓库实撞过的坑。
#   - 不用 `set -e`：调用方 deploy.sh 自己管，这里每支函数都以明确的 return 收尾。

# 「这一行要摘掉」的标记：键值合并时先原位标好，最后一次成型，免得在数组里边遍历边删。
ZC_DROP='__ZC_DROP_THIS_LINE__'

# 打印行数组并摘掉被标记的行。数组为空时也合法（bash 5.2 下 set -u 不炸空展开）。
zc_emit_lines() {
  local l
  for l in "$@"; do
    [ "$l" = "$ZC_DROP" ] && continue
    printf '%s\n' "$l"
  done
}

# ---------------------------------------------------------------- 部署根（ADR-0034）

zc_normalize_root() { # $1=用户输入 ; stdout=规范化路径；非法则非零退出并给原因
  local raw="${1:-}" p
  p="${raw#"${raw%%[![:space:]]*}"}"              # 只去首尾空白：粘贴路径常带空格，内部空格是合法的
  p="${p%"${p##*[![:space:]]}"}"
  case "$p" in
    [A-Za-z]:*)
      printf '部署根必须是 WSL/Linux 绝对路径，收到的是 Windows 盘符路径：%s\n' "$raw" >&2
      printf '  Windows 侧要填的是 WSL 发行版**内部**的路径（如 /opt/zerocode）；填 D:\\ 等于把部署根放进跨系统 9P 慢盘。\n' >&2
      return 2 ;;
    *\\*)
      printf '部署根不许含反斜杠（Windows 路径分隔符）：%s —— 请填 WSL/Linux 绝对路径，如 /opt/zerocode\n' "$raw" >&2
      return 2 ;;
    *'|'*|*'&'*)
      # apply-init.sh 用 sed 把 {{DEPLOY_ROOT}} 渲染进 init.sql 的 root_path，`|` 是它的分隔符、
      # `&` 是「整段匹配」替换元字符。放任不管的症状不是报错而是库里落一条变形路径，
      # 之后每个新建应用都指向一个不存在的目录。
      printf '部署根不许含 | 与 &（apply-init.sh 渲染 root_path 时它们是 sed 的元字符）：%s\n' "$raw" >&2
      return 2 ;;
  esac
  while [[ "$p" == *//* ]]; do p="${p//\/\//\/}"; done   # 收敛重复斜杠，别把它带进 root_path
  p="${p%/}"
  [ -n "$p" ] || { printf '部署根不能是文件系统根 /（会把整台机器的目录当应用根）\n' >&2; return 2; }
  [ "${p:0:1}" = "/" ] || { printf '部署根必须是 Linux 绝对路径（形如 /opt/zerocode），收到：%s\n' "$raw" >&2; return 3; }
  printf '%s\n' "$p"
}

# ---------------------------------------------------------------- 口令字符集（US-05）

zc_valid_password() { # $1=口令 ; 0=可用
  local pw="${1:-}" bad="" i ch
  [ -n "$pw" ] || { printf '口令不能为空（compose 里 POSTGRES_PASSWORD 是 :? 硬闸门，空值直接拒收整个文件）\n' >&2; return 2; }
  for ((i = 0; i < ${#pw}; i++)); do
    ch="${pw:i:1}"
    case "$ch" in
      [A-Za-z0-9_-]) ;;
      *) bad="$bad$ch" ;;
    esac
  done
  [ -n "$bad" ] || return 0
  printf '口令含不允许的字符：%s\n  只允许字母、数字与 - _；它会被原样拼进 asyncpg 连接串，含 @ : / # %% & ? 空格会把它截断。\n' "$bad" >&2
  return 3
}

# ---------------------------------------------------------------- 应答文件（US-07）
#
# 部署根下的 .deploy-answers：下次运行先读它当默认值，于是「一路回车」在升级时成立。
# 它是现场产物（CONTEXT.md），升级合并不删不改；本层只按显式路径读写。

zc_answers_get() { # $1=文件 $2=KEY ; stdout=值（缺失时空）
  local file="${1:-}" key="${2:-}"
  [ -f "$file" ] || return 0
  grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'
}

zc_answers_put() { # $1=文件 $2=KEY $3=值 ; 静默（更新与新建都不许有 stdout）
  local file="${1:-}" key="${2:-}" val="${3:-}"
  case "$key" in
    *PASSWORD*|*SECRET*|*TOKEN*|*KEY*)
      printf '应答文件不存 %s 的真值（凭据只住 .env，复制一份就多一处泄漏面）\n' "$key" >&2
      return 4 ;;
  esac
  [ -n "$key" ] || { printf '应答写入缺键名\n' >&2; return 5; }
  local -a lines=()
  local i t seen=""
  if [ -f "$file" ]; then
    mapfile -t lines < "$file"
  fi
  for i in "${!lines[@]}"; do
    t="${lines[$i]#"${lines[$i]%%[![:space:]]*}"}"          # 去左空白再比键名
    case "$t" in
      "${key}="*)
        # 原位替换保持首次插入的顺序；人手抖出重复键时只留第一条，其余摘掉——
        # 应答读取取的是最后一行，留着旧的第二条会让下次运行悄悄复用旧值。
        if [ -z "$seen" ]; then lines[$i]="${key}=${val}"; seen=1; else lines[$i]="$ZC_DROP"; fi ;;
    esac
  done
  [ -n "$seen" ] || lines+=("${key}=${val}")
  zc_emit_lines "${lines[@]}" > "$file"
}

# ---------------------------------------------------------------- .env 合并（US-08）
#
# 「只改被问到的键」是 D-06 护栏在 .env 上的投影：服务器现场手加过的键（模型密钥、调过的超时）
# 不能因为一次升级消失。所以这里绝不用 .env.example 重新生成整份文件。

zc_env_render() { # $@=KEY=VALUE ; stdin=既有 .env 文本 ; stdout=合并后的完整文本
  local existing pair key val line t hit line_idx
  local -a lines=() add=()
  existing="$(cat)"
  [ -n "$existing" ] && mapfile -t lines <<<"$existing"
  for pair in "$@"; do
    key="${pair%%=*}"
    val="${pair#*=}"
    hit=""
    for line_idx in "${!lines[@]}"; do
      t="${lines[$line_idx]#"${lines[$line_idx]%%[![:space:]]*}"}"
      case "$t" in
        "${key}"=*)
          if [ -z "$hit" ]; then lines[$line_idx]="${key}=${val}"; hit=1; else lines[$line_idx]="$ZC_DROP"; fi ;;
      esac
    done
    [ -n "$hit" ] || add+=("${key}=${val}")        # 没被问到的既有键一律原样留在上面，新键追加在末尾
  done
  zc_emit_lines "${lines[@]}" "${add[@]}"
  return 0
}

zc_env_backup() { # $1=.env 路径 $2=落地目录 ; stdout=备份路径（源不存在时静默且成功）
  local src="${1:-}" dir="${2:-}" stamp dest
  [ -f "$src" ] || return 0
  [ -n "$dir" ] || dir="$(dirname "$src")"
  stamp="${ZC_BACKUP_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
  dest="${dir}/.env.bak-${stamp}"
  cp -p -- "$src" "$dest" || { printf '.env 备份失败：%s → %s\n' "$src" "$dest" >&2; return 2; }
  # 只留最近 5 份：这是「不加误选拦截」之下唯一的后路（D-07），但留一堆等于往部署根扔垃圾。
  local -a backs=()
  mapfile -t backs < <(for f in "$dir"/.env.bak-*; do [ -e "$f" ] || continue; printf '%s\n' "$f"; done | LC_ALL=C sort)
  local drop=$(( ${#backs[@]} - 5 ))
  if (( drop > 0 )); then
    local i
    for ((i = 0; i < drop; i++)); do rm -f -- "${backs[$i]}"; done
  fi
  printf '%s\n' "$dest"
}

# ---------------------------------------------------------------- 现场产物与合并计划（US-16 / D-06）

zc_untouchables() { # $1=部署根 ; stdout=每行一项，绝对路径
  local root="${1:-}"
  cat <<EOF
${root}/.env
${root}/.deploy-answers
${root}/apps
${root}/data
${root}/store
${root}/gateway/certs
${root}/gateway/routes
${root}/gateway/.env
pgdata 卷（docker volume：平台库与全部应用分库）
EOF
}

zc_merge_plan() { # $1=new|upgrade $2=部署根 ; stdout=「会动什么 / 不会动什么」两张表
  local mode="${1:-}" root="${2:-}"
  case "$mode" in
    new)
      cat <<EOF
新增部署 · 部署根 ${root}

铺包（整体一次，此根下没有现场产物要保）：
  把交付包内容 rsync -a 到 ${root}（不带 --delete；包就是全新的）
  mkdir -p ${root}/apps ${root}/data ${root}/store ${root}/gateway/certs ${root}/gateway/routes
  生成 ${root}/.env（没有旧文件，无需备份）
  首次 docker compose up -d --build：建 pgdata 卷、建平台库、重放 init.sql
EOF
      ;;
    upgrade)
      cat <<EOF
升级部署 · 部署根 ${root}

会动（只有这三棵纯源码目录带 --delete，其余一律只覆盖不删）：
  rsync -a --delete  backend/  →  ${root}/backend/
  rsync -a --delete  browser/  →  ${root}/browser/
  rsync -a --delete  frontend/ →  ${root}/frontend/
  只覆盖不删的顶层生成物：docker-compose.yml  init.sql  apply-init.sh  deploy.sh  deploy_lib.sh  deploy.ps1  .env.example  README-deploy.md  .dockerignore
  网关侧只覆盖 gateway/ 下的配置与脚本；容器重建与否现场问
不会动：八项现场产物，一项都不进上面的清单（术语见 CONTEXT.md「现场产物」）
  ${root}/.env            —— 本脚本只往里合并被问到的键，且动笔前先备份
  ${root}/.deploy-answers —— 上次的答案，用来给本次当默认值
  ${root}/apps/           —— 服务器上新建的应用工程（root_path 指向它）
  ${root}/data/           —— 运行期落盘的技能库、截图
  ${root}/store/          —— 商店包
  ${root}/gateway/certs/  —— TLS 证书与私钥，只在服务器现场生成
  ${root}/gateway/routes/ —— 运行期路由表
  ${root}/gateway/.env    —— 网关侧域名覆盖
  pgdata 卷               —— 平台库与全部应用分库
EOF
      ;;
    *)
      printf '未知的部署模式：%s（只认 new / upgrade）\n' "$mode" >&2
      return 2 ;;
  esac
}

zc_needs_rebuild() { # $1=.env 里的旧部署根 $2=本次部署根 ; stdout=1|0
  local old="${1:-}" new="${2:-}"
  old="${old%/}"; new="${new%/}"
  # 读不到旧值时保守选重建：WORKDIR 是构建期烘进镜像的，只 up -d 会让容器内路径与
  # 宿主挂载分家，症状正是 ADR-0013 警告的「新建应用落进容器可写层」，而且全程不报错。
  [ -n "$old" ] || { printf '1\n'; return 0; }
  if [ "$old" = "$new" ]; then printf '0\n'; else printf '1\n'; fi
}

# ---------------------------------------------------------------- 环境保障（US-10 / US-11）

zc_pkg_manager() { # stdout=apt-get|dnf|yum|none
  local c
  for c in apt-get dnf yum; do
    command -v "$c" >/dev/null 2>&1 && { printf '%s\n' "$c"; return 0; }
  done
  printf 'none\n'
}

# 判定型函数：0=被占 1=空闲 2=无法判定。回归钉 tests/test_deploy_lib.py 会查这一行的文本，
# 所以这里只写结论，不写「不许挂什么」——那三个字一旦出现在本行，钉就自撞了。
zc_busy() { # $1=端口 ; 0=被占 1=空闲 2=无法判定
  local port="${1:-}"
  # 缺 ss 一律判「无法判定」而不是「空闲」：静默放行端口冲突等于把故障推迟到 compose 起栈那一刻。
  [ -n "$port" ] || return 2
  command -v ss >/dev/null 2>&1 || return 2
  # 判定交给 awk 走完整份输出：grep -q 命中即退，在 set -o pipefail 下 awk 会吃到 SIGPIPE，
  # 于是「明明命中」也可能返回 141——调用方按 0/1 分支，收到 141 就走错腾端口那条路。
  # 锚定的 $ 走动态正则而不是 -v 里的反斜杠：`-v pat=":80\$"` 的转义处理按 awk 实现而异
  # （gawk 认成锚点、mawk 会留成字面 $），判错方向就是「明明被占却报空闲」，预检形同虚设。
  ss -ltnH 2>/dev/null | awk -v p="$port" '$4 ~ (":" p "$") { found = 1 } END { exit !found }'
}

# ---------------------------------------------------------------- 环境缺口（US-10）
#
# 每行一个缺口名。调用方（deploy.sh）据此决定「直接起栈 / 进安装分支 / 停下让人腾」，
# 而本函数不装任何东西——它只回答「缺什么」，所以桩一个 docker 就能把四五种坏境况跑尽。

zc_env_gaps() { # stdout=每行一个缺口：docker-cli|compose-plugin|daemon|docker-sock|docker-group
  local sock="${ZC_DOCKER_SOCK:-}" me groups
  if ! command -v docker >/dev/null 2>&1; then
    printf 'docker-cli\n'
    return 0                    # 没有 CLI 时，后面每一项都只是它的回声；一次只报真正的因
  fi
  docker compose version >/dev/null 2>&1 || printf 'compose-plugin\n'
  docker info >/dev/null 2>&1 || printf 'daemon\n'
  # docker.sock 那条只在 Linux 宿主上判（服务器与 WSL 发行版都要判）。ZC_DOCKER_SOCK 是显式开关：
  # 设成某个路径就查那个路径，设成空串就明确「不查」（桩测与非 Linux 入口用），完全没设才按 uname 兜底。
  if [ "${ZC_DOCKER_SOCK+set}" != "set" ] && [ "$(uname -s)" = Linux ]; then sock=/var/run/docker.sock; fi
  if [ -n "$sock" ]; then
    # -e 而不是 -S：WSL2 里这条路径常是指向 Windows 命名管道的符号链接，-S 会把它判成「没有」，
    # 于是每次预检都让人去装已经装好的东西。这里只关心「有没有那个入口」。
    [ -e "$sock" ] || printf 'docker-sock\n'
  fi
  # 不在 docker 组里的人即便装了也跑不动 docker 命令；组变更要重新登录或 sg docker 才生效，
  # 所以它单独成一个缺口，由调用方决定「现在就加、稍后再提示」。
  me="${ZC_USER:-$(id -un 2>/dev/null || echo unknown)}"
  groups="${ZC_GROUPS:-$(id -nG 2>/dev/null || true)}"
  if [ "$me" != "root" ]; then
    case " $groups " in
      *" docker "*) ;;
      *) printf 'docker-group\n' ;;
    esac
  fi
  return 0
}

# ---------------------------------------------------------------- 端口占用者（US-10 之后的腾端口）
#
# 沿用现有脚本的立场：容器可以问完人再 stop，宿主进程一律不代劳 kill——认不出它是 ssh 还是
# 数据库的代价，不该由部署来付。分诊写成纯文本判定，是为了能在桩里把三种情况都跑一遍。
#
# ss 那一行是任意外部文本，走 stdin 而不是 argv：MSYS bash 会把带引号/括号的参数改写到面目全非
# （桩测实撞过），Linux 上没这问题，但判定层不该只在一种宿主上可测。

zc_port_holder_kind() { # $1=docker ps 查到的容器名（可空）; stdin=ss 里那一行占用信息
  local who="" cid="${1:-}"
  IFS= read -r who || true
  if [ -n "$cid" ]; then printf 'container\n'; return 0; fi
  case "$who" in
    *pid=*) printf 'host-process\n'; return 0 ;;
  esac
  printf 'unknown\n'
}

zc_port_holder_pid() { # stdin=ss 里那一行占用信息 ; stdout=pid（认不出则空）
  local who=""
  IFS= read -r who || true
  printf '%s\n' "$who" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p'
}

# ---------------------------------------------------------------- 装 Docker（US-10 / D-09）
#
# 每行一条命令，交调用方以 root 逐条执行。装的是 **Docker Engine + compose v2 插件**，绝不装
# docker-compose（Python 版）：本仓库的预检与 deploy 全流程都用 `docker compose` 子命令。
# $2 给镜像站主机名时，docker-ce 仓库走约定的 /docker-ce/linux/<distro> 路径——境内机直连
# download.docker.com 拉不通，这一条决定「一键」在真实目标机上是否成立。

zc_install_commands() { # $1=apt-get|dnf|yum $2=镜像站主机名（可空） ; stdout=每行一条命令
  local pm="${1:-}" mirror="${2:-}" base="https://download.docker.com"
  [ -n "$mirror" ] && base="https://${mirror%/}/docker-ce"
  case "$pm" in
    apt-get)
      cat <<EOF
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL ${base}/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] ${base}/linux/debian \$(. /etc/os-release && echo "\${VERSION_CODENAME:-stable}") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
EOF
      ;;
    dnf)
      cat <<EOF
dnf install -y dnf-plugins-core ca-certificates curl
dnf config-manager --add-repo ${base}/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
EOF
      ;;
    yum)
      cat <<EOF
yum install -y yum-utils ca-certificates curl
yum-config-manager --add-repo ${base}/linux/centos/docker-ce.repo
yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
EOF
      ;;
    *)
      printf '不认识包管理器 %s，没法代劳安装。\n' "$pm" >&2
      printf '  请按 https://docs.docker.com/engine/install/ 手工装好 Docker Engine 与 compose v2 插件，再重跑本脚本。\n' >&2
      return 2 ;;
  esac
}

zc_add_docker_group_command() { # $1=用户名 ; stdout=一条 usermod（空用户名则不出命令）
  local me="${1:-}"
  [ -n "$me" ] || return 0
  printf 'usermod -aG docker %s\n' "$me"
}

# ---------------------------------------------------------------- registry mirror（US-11）
#
# /etc/docker/daemon.json 是宿主上 docker 的行为定义。已有别的键（log-driver、data-root）时
# 直接写新文件会把它们抹掉——那是「部署一个演示平台，顺手改了整台机器的 docker」，不可接受。
# 所以判定与写入分成两支：plan 只说不写，write 只在安全时落笔。

zc_daemon_json_plan() { # $1=daemon.json 路径 $2=mirror URL ; stdout=write-new|already-configured|merge-required
  local file="${1:-}" mirror="${2:-}" keys
  [ -s "$file" ] || { printf 'write-new\n'; return 0; }
  keys="$(zc_daemon_json_keys "$file")"
  [ -n "$keys" ] || { printf 'write-new\n'; return 0; }        # 空对象 / 只有空白：等同于没有配置
  if [ "$keys" = "registry-mirrors" ]; then
    grep -qF -- "\"${mirror}\"" "$file" && { printf 'already-configured\n'; return 0; }
  fi
  printf 'merge-required\n'
}

zc_daemon_json_keys() { # $1=文件 ; stdout=每行一个顶层键名（够用即可：手写 daemon.json 不会嵌套到需要解析器）
  grep -oE '"[^"]+"[[:space:]]*:' "${1:-}" 2>/dev/null | sed 's/[":[:space:]]//g' | LC_ALL=C sort -u
}

zc_daemon_json_write() { # $1=文件 $2=逗号分隔的 mirror 列表 ; 只在该文件不存在或只含 registry-mirrors 时落笔
  local file="${1:-}" mirrors="${2:-}" keys url seen m sep i f
  local -a raw_list=() wanted=() existing=()
  IFS=',' read -r -a raw_list <<<"$mirrors"
  for url in "${raw_list[@]}"; do
    url="${url#"${url%%[![:space:]]*}"}"; url="${url%"${url##*[![:space:]]}"}"
    [ -n "$url" ] && wanted+=("$url")
  done
  [ ${#wanted[@]} -gt 0 ] || { printf '没有可用的镜像源地址，不写 %s\n' "$file" >&2; return 4; }
  if [ -s "$file" ]; then
    keys="$(zc_daemon_json_keys "$file")"
    if [ -n "$keys" ] && [ "$keys" != "registry-mirrors" ]; then
      printf '%s 还含有别的顶层键（%s），本脚本不做覆盖式写入。\n' "$file" "$keys" >&2
      printf '  请手工把 "registry-mirrors" 加进去再 systemctl restart docker——自动合并别的键，风险不该由部署来冒。\n' >&2
      return 3
    fi
    mapfile -t existing < <(grep -oE '"https?://[^"]+"' "$file" 2>/dev/null | tr -d '"')
    for url in "${existing[@]}"; do
      [ -n "$url" ] || continue
      seen=""
      for f in "${wanted[@]}"; do [ "$f" = "$url" ] && seen=1; done
      [ -n "$seen" ] || wanted+=("$url")                         # 既有的镜像源排在后面，不丢
    done
  fi
  # 先写临时文件再 mv：daemon.json 是 docker 启动时读的，写一半等于把宿主整个 daemon 弄成坏配置。
  # 父目录未必存在：daemon.json 的位置由打包方式决定，docker 不是 apt 装来的机器上 /etc/docker
  # 就常是缺的——判定说「可以写新文件」却因为目录不存在而失败，症状是一句「需要你手工配镜像加速」。
  local dir
  dir="$(dirname "$file")"
  [ -d "$dir" ] || mkdir -p -- "$dir" || { printf '建不出 %s，没法写 daemon.json\n' "$dir" >&2; return 2; }
  local tmp="${file}.tmp.$$"
  {
    printf '{\n  "registry-mirrors": [\n'
    for ((i = 0; i < ${#wanted[@]}; i++)); do
      m="${wanted[$i]}"
      sep=","
      [ "$i" -eq $(( ${#wanted[@]} - 1 )) ] && sep=""
      printf '    "%s"%s\n' "$m" "$sep"
    done
    printf '  ]\n}\n'
  } > "$tmp" || { printf '写 %s 失败\n' "$tmp" >&2; return 2; }
  mv -- "$tmp" "$file" || { printf '就位 %s 失败\n' "$file" >&2; rm -f -- "$tmp"; return 2; }
  printf '%s\n' "$file"
}
