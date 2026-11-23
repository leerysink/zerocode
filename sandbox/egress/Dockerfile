# 沙箱出口 router（ADR-0066 决策 3）：持 NET_ADMIN，把 nft 规则装在自己的 netns 里。
# 沙箱以 --network container:本容器 共享该 netns，规则因此对沙箱生效；沙箱自身无 NET_ADMIN，
# 改不了规则。**宿主防火墙一行不动**。
#
# 基础镜像沿用平台已有的 python:3.12-slim-trixie（本地/服务器都已为沙箱与 browser 拉过），
# 不为一个几十 MB 的 router 再引一个新 registry 依赖。代号钉死的理由同 sandbox/Dockerfile。
FROM python:3.12-slim-trixie

ARG ZEROCODE_APT_MIRROR=

RUN set -eux; \
    if [ -n "$ZEROCODE_APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -e "$f" ] || continue; \
        sed -i -e "s|deb.debian.org|$ZEROCODE_APT_MIRROR|g" -e "s|security.debian.org|$ZEROCODE_APT_MIRROR|g" "$f"; \
      done; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends nftables; \
    rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]