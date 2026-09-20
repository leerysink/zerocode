#!/bin/sh
# app/deploy/sqlrunner/run.sh —— 规格 07 §4.3 执行算法
# 注意：shebang 必须是文件第一行（否则 ENTRYPOINT 直接 exec 会 ENOEXEC），故路径标记注释置于其后。
# 注意：本文件在 alpine/busybox 下运行，只能用 POSIX sh 与 busybox 工具集
#       （date +%s / md5sum / cut / basename / ls / sort），不可用 bash 数组与 [[ ]]。
# 退出码：0 成功 / 2 迁移失败 / 3 篡改。
set -u
: "${TARGET_DB:?TARGET_DB required}"
: "${TARGET_USER:?TARGET_USER required}"
export PGPASSWORD="${TARGET_PASSWORD}"
HOST="${TARGET_HOST:-zerocode-pg16}"
PSQL="psql -h $HOST -U $TARGET_USER -d $TARGET_DB -v ON_ERROR_STOP=1"

$PSQL -q -c "CREATE TABLE IF NOT EXISTS sqlchange_history (
  filename VARCHAR(200) PRIMARY KEY,
  checksum VARCHAR(32),
  success BOOLEAN NOT NULL,
  error TEXT,
  duration_ms INT,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now())" || exit 2

for f in $(ls /sql/V*.sql 2>/dev/null | sort); do
  base=$(basename "$f")
  sum=$(md5sum "$f" | cut -d' ' -f1)
  # 坑：boolean::text 在 PG 里是 'true'/'false'，不是 psql 显示的 't'/'f'；
  #     这里显式 CASE 出单字符，保持下面 ok="t" 的比较稳定（否则已应用文件会被重跑、篡改检测失效）。
  row=$($PSQL -tA -c "SELECT (CASE WHEN success THEN 't' ELSE 'f' END) || '|' || COALESCE(checksum,'') FROM sqlchange_history WHERE filename='$base'")
  if [ -n "$row" ]; then
    ok=${row%%|*}; oldsum=${row#*|}
    if [ "$ok" = "t" ]; then
      if [ -n "$oldsum" ] && [ "$oldsum" != "$sum" ]; then
        echo "TAMPER $base: 已应用文件被修改，拒绝继续（恢复原文件或人工清理 history）"
        exit 3
      fi
      echo "SKIP $base"
      continue
    fi
  fi
  start=$(date +%s)
  if $PSQL --single-transaction -q -f "$f"; then
    dur=$(( ($(date +%s) - start) * 1000 ))
    $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, duration_ms)
      VALUES ('$base', '$sum', true, $dur)
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=true, error=NULL,
        duration_ms=EXCLUDED.duration_ms, applied_at=now()"
    echo "OK $base"
  else
    $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, error)
      VALUES ('$base', '$sum', false, 'execution failed')
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=false, error='execution failed', applied_at=now()"
    echo "FAIL $base"
    exit 2
  fi
done
echo "ALL DONE"
exit 0
