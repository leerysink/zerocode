# app/source/hosting.py
"""公开托管站的 URL 形态与端点表（当前只接 Gitee）。

四个端点形态对照 2026-11-17 实测 200 的真实地址写死，不是猜的：
- 仓库元数据  https://gitee.com/api/v5/repos/{owner}/{repo}            → default_branch
- 递归文件树  https://gitee.com/api/v5/repos/{owner}/{repo}/git/trees/{ref}?recursive=1
- raw 正文    https://gitee.com/{owner}/{repo}/raw/{ref}/{path}
递归树一次调用就给全仓库路径（实测 1002 条 / 253 KB），这就是「往下钻取」的入口；
正文走 raw，不占 API 配额（匿名 API 只有 60 次/小时）。

刻意不做的事（详见 ADR-0051）：不接 GitHub/GitLab（形态相近但配额与鉴权语义不同，
等真需要时另票）、不带 token（私有仓不在本票范围）、不解析 blob 网页形态
（`/-/blob/` 与 `/blob/` 返回 HTML，正文一律走 raw）。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse

SUPPORTED_HOST_LABEL = "gitee.com"
_GITEE_HOSTS = ("gitee.com", "www.gitee.com")
_API_ROOT = "https://gitee.com/api/v5/repos"


class HostingError(Exception):
    """托管站 URL 形态或端点解析失败；api 层映射为 400。"""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    repo: str
    ref: str | None
    subpath: str | None


def parse_repo_url(url: str) -> RepoRef | None:
    """gitee.com/owner/repo[/tree/<ref>[/<subpath>]] → RepoRef；非 Gitee 返回 None。

    返回 None 是「这不是仓库，走别的通道」；抛 HostingError 是「这看着像仓库但形态不对」——
    静默把坏形态当直链处理，用户只会看到一个看不懂的成功。
    """
    parsed = urlparse(url)
    if parsed.hostname not in _GITEE_HOSTS:
        return None
    segs = [s for s in parsed.path.split("/") if s]
    # 只有「文件直链」形态才让路给技能直链通道：`/raw/` 与 `/blob/` 指向单个文件，
    # 末段是 SKILL.md 时判成非仓库（口径同 installer.is_skill_md_url）。
    # `/tree/<ref>/<path>` 是目录形态，末段叫 SKILL.md 也仍然是仓库地址——浏览器复制目录链接
    # 常常带到最后一段，把它判成「不是仓库」会把真仓库拒在门外（终审 Important 的裁定）。
    if (len(segs) >= 5 and segs[2] in ("raw", "blob")
            and segs[-1].upper() == "SKILL.MD"):
        return None
    if len(segs) < 2:
        raise HostingError("Gitee 仓库地址至少要有 owner/repo")
    repo = segs[1][:-4] if segs[1].lower().endswith(".git") else segs[1]
    if not repo:
        # 末段只有 `.git`：剥完后缀仓库名是空串，端点会拼成 `…/repos/o/` 这种必然 404 的地址。
        raise HostingError("Gitee 仓库地址缺少仓库名")
    out = RepoRef(host="gitee.com", owner=segs[0], repo=repo, ref=None, subpath=None)
    if len(segs) > 2:
        if segs[2] != "tree" or len(segs) < 4:
            raise HostingError("仅支持仓库根 URL 或 /tree/<ref>[/<subpath>] 形态")
        out = RepoRef(host=out.host, owner=out.owner, repo=out.repo,
                      ref=segs[3], subpath="/".join(segs[4:]) or None)
    return out


def api_repo_url(ref: RepoRef) -> str:
    return f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"


def api_tree_url(ref: RepoRef, branch: str) -> str:
    return (f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/git/trees/{quote(branch, safe='/')}?recursive=1")


def raw_file_url(ref: RepoRef, branch: str, path: str) -> str:
    return (f"https://{ref.host}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/raw/{quote(branch, safe='/')}/{quote(path)}")


def default_branch_of(repo_json: dict) -> str:
    branch = str(repo_json.get("default_branch") or "").strip()
    if not branch:
        raise HostingError("仓库没有默认分支，请用 /tree/<ref> 指定分支")
    return branch
