"""
前端 Code Review Agent - GitHub PR 版本
通过 GitHub API 拉 PR Diff，交给 LLM 做结构化 review
不需要 gh CLI 登录，公开仓库直接用即可
"""

import argparse
import logging
import pathlib
import sys

from llm_client import Settings
from github_client import parse_pr_url, get_pr_info, get_pr_diff, post_pr_comment
from diff_utils import filter_lock_files, review_diff
from formatter import print_review, format_review_comment
from rag import build_index


# ---- 日志配置 ----

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",   # 终端友好：只输出消息本身，不带 level 前缀
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---- 全局设置 ----

settings = Settings.load()


# ---- 主流程 ----

def review_pr(pr_url: str, post_to_pr: bool = False) -> dict:
    logger.info("🚀 开始 Review PR: %s\n", pr_url)

    owner, repo, pr_number = parse_pr_url(pr_url)
    logger.info("📦 仓库: %s/%s  PR: #%d\n", owner, repo, pr_number)

    # 1. 拿 PR 信息
    pr_info = get_pr_info(owner, repo, pr_number, settings.github_token)
    logger.info("📋 标题: %s", pr_info["title"])
    logger.info("👤 作者: %s", pr_info["user"]["login"])
    logger.info("📁 变更文件: %d  +%d -%d\n",
                pr_info["changed_files"], pr_info["additions"], pr_info["deletions"])

    # 2. 拉 Diff（过滤 lock 文件）
    diff = get_pr_diff(owner, repo, pr_number, settings.github_token)
    diff = filter_lock_files(diff)

    # 3. Review
    result = review_diff(diff, pr_info, settings)
    print_review(result)

    # 4. 可选：发回 PR 评论（需要 GITHUB_TOKEN）
    if post_to_pr:
        comment = format_review_comment(result)
        logger.info("\n📤 发送评论到 PR...")
        post_pr_comment(owner, repo, pr_number, comment, settings.github_token)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Code Review Agent — 对 GitHub PR 进行 AI Review")
    parser.add_argument("pr_url", nargs="?", help="GitHub PR URL，例如 https://github.com/owner/repo/pull/123")
    parser.add_argument("--post", action="store_true", help="将 review 结果作为评论发回 PR（需设置 GITHUB_TOKEN）")
    parser.add_argument("--build-index", action="store_true", help="重建规范文档向量索引（docs/ 目录）")
    args = parser.parse_args()

    if args.build_index:
        docs_dir = pathlib.Path(__file__).parent / "docs"
        total = build_index(docs_dir)
        logger.info("共索引 %d 个规范 chunk，可开始使用规范检索功能", total)
        sys.exit(0)

    pr_url = args.pr_url
    if not pr_url:
        pr_url = input("请输入 GitHub PR URL：").strip()

    if args.post:
        # --post 参数：review 完直接发回 PR，不询问
        review_pr(pr_url, post_to_pr=True)
    else:
        # 默认：先 review，再询问是否发回
        result = review_pr(pr_url, post_to_pr=False)
        answer = input("\n是否将 review 结果发回 PR 评论？(y/N) ").strip().lower()
        if answer == "y":
            owner, repo, pr_number = parse_pr_url(pr_url)
            comment = format_review_comment(result)
            logger.info("\n📤 发送评论到 PR...")
            post_pr_comment(owner, repo, pr_number, comment, settings.github_token)
