"""
GitHub API 客户端模块
- parse_pr_url:             解析 PR URL 获取 owner/repo/pr_number
- github_api:               GET 请求封装
- get_pr_info:              获取 PR 基本信息
- get_pr_diff:              获取 PR Diff 内容
- post_pr_comment:          发送或更新 PR 评论
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """从 PR URL 解析 owner/repo/pr_number"""
    import re
    # https://github.com/owner/repo/pull/123
    match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not match:
        raise ValueError(f"无效的 GitHub PR URL: {pr_url}")
    owner, repo, pr_number = match.group(1), match.group(2), int(match.group(3))
    return owner, repo, pr_number


def github_api(path: str, github_token: str = "",
               accept: str = "application/vnd.github.v3+json") -> bytes:
    """发送 GET 请求到 GitHub API"""
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": accept,
            "User-Agent": "code-review-agent/1.0",
            **({"Authorization": f"Bearer {github_token}"} if github_token else {}),
        }
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def get_pr_info(owner: str, repo: str, pr_number: int, github_token: str = "") -> dict:
    """获取 PR 基本信息"""
    data = github_api(f"/repos/{owner}/{repo}/pulls/{pr_number}", github_token)
    return json.loads(data)


def get_pr_diff(owner: str, repo: str, pr_number: int, github_token: str = "") -> str:
    """获取 PR Diff 内容"""
    data = github_api(
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        github_token,
        accept="application/vnd.github.v3.diff"
    )
    return data.decode("utf-8", errors="replace")


def _find_existing_bot_comment(owner: str, repo: str, pr_number: int,
                                github_token: str = "") -> int | None:
    """查找 PR 中已有的 bot review 评论，返回 comment_id，未找到则返回 None"""
    try:
        data = github_api(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100",
            github_token
        )
        comments = json.loads(data)
        for comment in comments:
            if (comment.get("body", "").startswith("## 🤖 AI Code Review")
                    and comment.get("user", {}).get("type") == "Bot"):
                return comment["id"]
    except Exception as e:
        logger.warning("查询已有评论失败（跳过去重检查）: %s", e)
    return None


def _github_api_write(url: str, payload: bytes, method: str, github_token: str) -> dict:
    """发送写操作请求（POST / PATCH）到 GitHub API"""
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {github_token}",
            "Content-Type": "application/json",
            "User-Agent": "code-review-agent/1.0",
        }
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_pr_comment(owner: str, repo: str, pr_number: int,
                    body: str, github_token: str = "") -> None:
    """发送或更新 PR 评论（有已有 bot 评论则更新，否则新建）"""
    if not github_token:
        logger.warning("⚠️  未配置 GITHUB_TOKEN，无法发评论（只读模式）")
        return

    payload = json.dumps({"body": body}).encode()

    try:
        existing_id = _find_existing_bot_comment(owner, repo, pr_number, github_token)
        if existing_id:
            # 已有评论：PATCH 更新，避免刷屏
            url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{existing_id}"
            result = _github_api_write(url, payload, "PATCH", github_token)
            logger.info("🔄 已更新已有 review 评论: %s", result["html_url"])
        else:
            # 无已有评论：POST 新建
            url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
            result = _github_api_write(url, payload, "POST", github_token)
            logger.info("✅ 评论已发送: %s", result["html_url"])
    except urllib.error.HTTPError as e:
        logger.error("❌ 发评论失败: %s %s", e.code, e.reason)

