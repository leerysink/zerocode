#!/bin/sh
# app/deploy/sqlrunner/run.sh —— 规格 07 §4.3 执行算法（数据轨三段见 ADR-0040）
# 注意：shebang 必须是文件第一行（否则 ENTRYPOINT 直接 exec 会 ENOEXEC），故路径标记注释置于其后。
# 注意：本文件在 alpine/busybox 下运行，只能用 POSIX sh 与 busybox 工具集
#       （date +%s / md5sum / cut / basename / ls / sort），不可用 bash 数组与 [[ ]]。
# 注意：本文件必须是 LF 换行。CRLF 会让 busybox 把 shebang 读成 `/bin/sh\r` 而 ENOEXEC。
# 退出码：0 成功 / 2 迁移失败 / 3 篡改。
set -u
: "${TARGET_DB:?TARGET_DB required}"
: "${TARGET_USER:?TARGET_USER required}"
export PGPASSWORD="${TARGET_PASSWORD}"
HOST="${TARGET_HOST:-zerocode-pg16}"
# 本环境段必填：缺值时静默只跑公共段 = 「prod 种子没进线上库但不报错」，比闸门红一次贵得多。
TARGET_ENV="${TARGET_ENV:?TARGET_ENV required (dev|prod)}"
case "$TARGET_ENV" in dev|prod) ;;
  *) echo "BAD TARGET_ENV $TARGET_ENV: 只接受 dev/prod"; exit 2;;
esac
PSQL="psql -h $HOST -U $TARGET_USER -d $TARGET_DB -v ON_ERROR_STOP=1"

$PSQL -q -c "CREATE TABLE IF NOT EXISTS sqlchange_history (
  filename VARCHAR(200) PRIMARY KEY,
  checksum VARCHAR(32),
  success BOOLEAN NOT NULL,
  error TEXT,
  duration_ms INT,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now())" || exit 2

# 结构轨：若存在声明式快照则先全量幂等 fold（可重放，永不 DROP）。
# fold 不进 history（每次重放，幂等无害），仅作结构投影；缺失 schema 目录时静默跳过（向后兼容存量 V* 应用）。
if [ -f /schema/schema.sql ]; then
  echo "FOLD schema.sql"
  if ! $PSQL -q -f /schema/schema.sql; then
    echo "FAIL schema.sql"
    exit 2
  fi
fi

# 一次执行的口径固定为「公共段 + 本环境段」（ADR-0040）：另一段的文件对本库永不可见。
# 两段文件允许同名（各库各记各的），故记账用相对 /sql 的路径而不是 basename：
# 用 basename 时 dev/x.sql 与 prod/x.sql 会在同一张 sqlchange_history 上撞主键、误判篡改。
# 顶层文件名首字符是 V/D（大写），环境目录名小写，`sort` 后公共段天然排在环境段之前。
for f in $(ls /sql/*.sql /sql/$TARGET_ENV/*.sql 2>/dev/null | sort); do
  rel=${f#/sql/}
  sum=$(md5sum "$f" | cut -d' ' -f1)
  # 坑：boolean::text 在 PG 里是 'true'/'false'，不是 psql 显示的 't'/'f'；
  #     这里显式 CASE 出单字符，保持下面 ok="t" 的比较稳定（否则已应用文件会被重跑、篡改检测失效）。
  row=$($PSQL -tA -c "SELECT (CASE WHEN success THEN 't' ELSE 'f' END) || '|' || COALESCE(checksum,'') FROM sqlchange_history WHERE filename='$rel'")
  if [ -n "$row" ]; then
    ok=${row%%|*}; oldsum=${row#*|}
    if [ "$ok" = "t" ]; then
      if [ -n "$oldsum" ] && [ "$oldsum" != "$sum" ]; then
        echo "TAMPER $rel: 已应用文件被修改，拒绝继续（恢复原文件或人工清理 history）"
        exit 3
      fi
      echo "SKIP $rel"
      continue
    fi
  fi
  start=$(date +%s)
  if $PSQL --single-transaction -q -f "$f"; then
    dur=$(( ($(date +%s) - start) * 1000 ))
    # 坑（审查 N-3）：迁移成功但 history UPSERT 失败时，丢弃返回码 = 容器退 0、UI 报绿、行却不存在，
    #     下次执行会重跑同一文件（契约未强制幂等，种子数据类 INSERT 翻倍）。宁可闸门红一次不给假成功。
    if ! $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, duration_ms)
      VALUES ('$rel', '$sum', true, $dur)
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=true, error=NULL,
        duration_ms=EXCLUDED.duration_ms, applied_at=now()"; then
      echo "FAIL RECORD $rel"
      exit 2
    fi
    echo "OK $rel"
  else
    $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, error)
      VALUES ('$rel', '$sum', false, 'execution failed')
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=false, error='execution failed', applied_at=now()"
    echo "FAIL $rel"
    exit 2
  fi
done
echo "ALL DONE"
exit 0
