# app/source/hosting.py
"""公开托管站的 URL 形态与端点表（当前只接 Gitee）。

四个端点形态对照 2026-11-17 实测 200 的真实地址写死，不是猜的：
- 仓库元数据  https://gitee.com/api/v5/repos/{owner}/{repo}            → default_branch
- 递归文件树  https://gitee.com/api/v5/repos/{owner}/{repo}/git/trees/{ref}?recursive=1
- raw 正文    https://gitee.com/{owner}/{repo}/raw/{ref}/{path}
递归树一次调用就给全仓库路径（实测 1002 条 / 253 KB），这就是「往下钻取」的入口；
正文走 raw，不占 API 配额（匿名 API 只有 60 次/小时）。

解析层同时是路径形态闸（见 `unsafe_path_segment`）：`.`/`..`/空段/反斜杠在 parse_repo_url
就拒掉。理由是 `quote(..., safe='/')` 原样放行斜杠，脏段会经服务端点段归一改写成同域另一条
API 路径，还会被冻进 RepoRef 一路流到落盘侧。这里纯字符串判定，不出网、不依赖守卫。

刻意不做的事（详见 ADR-0051）：不接 GitHub/GitLab（形态相近但配额与鉴权语义不同，
等真需要时另票）、不带 token（私有仓不在本票范围；URL 里内嵌 `user:token@` 的形态由
`guard.validate_http_url` 在解析之前拒收——本模块的端点全部按硬编码 host 重拼，凭证既不生效
又会随 manifest 泄漏，终审 F1）、不解析 blob 网页形态
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


UNSAFE_SHAPE_HINT = "形态非法：不允许 . / .. / 空路径段 / 反斜杠"


def unsafe_path_segment(value: str | None) -> str | None:
    """返回 value 里第一个不安全的相对路径段，全安全则返回 None（纯函数，不出网）。

    为什么这一道闸必须在拼端点之前：`quote(x, safe='/')` 让斜杠原样进 URL，于是
    `git/trees/..` 这类串经服务端的点段归一会把 `/git/trees/` 这一截吃掉，一条用户粘的
    URL 就能打到同域任意 API 路径；`..`/`a//b`/反斜杠还会被原样冻进 manifest 的 ref/subpath，
    下游（Task 4 的 README 出处行）拿到的是没洗过的路径垃圾。
    空段（`a//b`）也在这里拒 —— 它是归一改写的另一种入口，不是「长得怪但无害」。
    空值不算坏段：None/"" 表示「压根没有这一段」（显式 subpath="" 是「就要仓库根」的合法意图）。
    """
    if not value:
        return None
    for seg in value.split("/"):
        if not seg or seg == "." or seg == ".." or "\\" in seg:
            return seg
    return None


def _reject_unsafe_shape(value: str | None, label: str) -> None:
    bad = unsafe_path_segment(value)
    if bad is not None:
        raise HostingError(f"Gitee 仓库地址的{label}{UNSAFE_SHAPE_HINT}（问题段 {bad!r}：{value!r}）")


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
    # 末段是 SKILL.md 时判成非仓库。
    # **别把它读成 `installer.is_skill_md_url` 的等价判据**（这里曾写过「口径同」，是假的）：
    # 那条是纯 basename 判定，任意 URL 只要末段是 SKILL.md 就为真；本条**额外**要求
    # `segs[2] in ("raw","blob")` 且段数 ≥5 的文件直链形态，因为 `/tree/<ref>/<path>` 是目录形态，
    # 末段叫 SKILL.md 也仍然是仓库地址——浏览器复制目录链接常常带上这一层，
    # 判成「不是仓库」会把真仓库拒在门外（终审 Important 的裁定）。两条各自被测试钉住，改一条别顺手改另一条。
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
    # owner/repo 走同一道形态闸：`https://gitee.com/../r` 里 quote 不改点段，拼出的
    # `/repos/../r` 归一后照样改写端点。名字里的普通点号（v1.2、my.repo）不受牵连。
    _reject_unsafe_shape(segs[0], "owner")
    _reject_unsafe_shape(repo, "仓库名")
    out = RepoRef(host="gitee.com", owner=segs[0], repo=repo, ref=None, subpath=None)
    if len(segs) > 2:
        if segs[2] != "tree" or len(segs) < 4:
            raise HostingError("仅支持仓库根 URL 或 /tree/<ref>[/<subpath>] 形态")
        # 解析层就把点段拒掉：`/tree/..` 看着只是「分支名怪一点」，实际会改写整条 API 路径，
        # 而且脏值还会被冻进 ref.ref / ref.subpath 一路带到落盘侧的出处行（终审 Important 1）。
        sub_part = "/".join(segs[4:]) or None
        _reject_unsafe_shape(segs[3], "分支")
        _reject_unsafe_shape(sub_part, "子路径")
        out = RepoRef(host=out.host, owner=out.owner, repo=out.repo,
                      ref=segs[3], subpath=sub_part)
    return out


def api_repo_url(ref: RepoRef) -> str:
    return f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"


def api_tree_url(ref: RepoRef, branch: str) -> str:
    """树端点。owner/repo 由 parse_repo_url 判过形态，branch 由调用方判过（见
    unsafe_path_segment）—— 这两个值都带斜杠原样进 URL，闸门必须在拼串之前，不在拼串里。"""
    return (f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/git/trees/{quote(branch, safe='/')}?recursive=1")


def raw_file_url(ref: RepoRef, branch: str, path: str) -> str:
    """raw 端点。同 api_tree_url：branch 与 path 的形态由调用方（parse_repo_url /
    is_eligible）在进入本函数之前判完。"""
    return (f"https://{ref.host}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/raw/{quote(branch, safe='/')}/{quote(path)}")


def default_branch_of(repo_json: dict) -> str:
    branch = str(repo_json.get("default_branch") or "").strip()
    if not branch:
        raise HostingError("仓库没有默认分支，请用 /tree/<ref> 指定分支")
    return branch
