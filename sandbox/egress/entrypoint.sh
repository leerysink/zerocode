#!/bin/sh
# 规则由平台生成并只读挂进来（网段表与 guard.is_internal_ip 同源，见 app/agent/sandbox_egress.py）。
# 本容器只负责应用规则并常驻：沙箱共享它的 netns，它一退出规则就随之消失。
set -eu

RULES=/ruleset/ruleset.nft

# 挂的是目录不是文件：compose 先起 router、平台后写规则，所以这里要等。
# 等不到就非零退出，交给 restart 策略重试——绝不「没规则也照常跑」，那等于静默失去过滤。
i=0
while [ ! -f "$RULES" ]; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "[sandbox-egress] 等不到 $RULES（平台未写规则），退出重试" >&2
    exit 1
  fi
  sleep 1
done

# DNS 例外按**运行环境实际**的解析器放行，不写死：传统 Linux 是 127.0.0.11，
# Docker Desktop 是宿主网关上的代理（如 192.168.65.7，落在私网段里）。
# 写死任一个都会在另一种环境上把域名解析全掐死，症状是 pip/curl「连不上」，离根因很远。
awk '/^nameserver/ {
  fam = (index($2, ":") > 0) ? "ip6" : "ip";
  printf "    %s daddr %s udp dport 53 accept\n", fam, $2;
  printf "    %s daddr %s tcp dport 53 accept\n", fam, $2;
}' /etc/resolv.conf > /tmp/dns-accept.nft

sed -e "/__DNS_ACCEPT__/r /tmp/dns-accept.nft" -e "/__DNS_ACCEPT__/d" \
    "$RULES" > /tmp/final.nft

nft -f /tmp/final.nft
echo "[sandbox-egress] ruleset applied (dns exceptions: $(wc -l < /tmp/dns-accept.nft) rules)"
exec sleep infinity