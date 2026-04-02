"""
前端 Code Review Agent - GitHub PR 版本
通过 GitHub API 拉 PR Diff，交给 LLM 做结构化 review
不需要 gh CLI 登录，公开仓库直接用即可
"""

import argparse
import os
import json
import pathlib
import urllib.request
import urllib.error
from dotenv import load_dotenv
from openai import OpenAI
from rag import retrieve, build_query_from_diff, build_index, index_exists

load_dotenv()


# ---- LLM 配置 ----

# 美团内部可选配置，外部用户不需要此文件，函数会静默返回空 dict
def get_extra_headers():
    cfg_path = pathlib.Path(os.path.expanduser("~/.openclaw/openclaw.json"))
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("models", {}).get("providers", {}).get("kubeplex-maas", {}).get("headers", {})
    return {}


def call_llm(messages: list) -> str:
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE"),
    )
    full_content = ""
    stream = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL"),
        messages=messages,
        temperature=0.1,
        stream=True,
        extra_headers=get_extra_headers(),
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
            print(delta.content, end="", flush=True)
    print()
    return full_content


# ---- GitHub API ----

def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """从 PR URL 解析 owner/repo/pr_number"""
    # https://github.com/owner/repo/pull/123
    parts = pr_url.rstrip("/").split("/")
    pr_number = int(parts[-1])
    repo = parts[-3]
    owner = parts[-4]
    return owner, repo, pr_number

# test & test
def github_api(path: str, accept: str = "application/vnd.github.v3+json") -> bytes:
    token = os.getenv("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": accept,
            "User-Agent": "code-review-agent/1.0",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        }
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def get_pr_info(owner: str, repo: str, pr_number: int) -> dict:
    data = github_api(f"/repos/{owner}/{repo}/pulls/{pr_number}")
    return json.loads(data)


def get_pr_diff(owner: str, repo: str, pr_number: int) -> str:
    data = github_api(
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        accept="application/vnd.github.v3.diff"
    )
    return data.decode("utf-8", errors="replace")


def post_pr_comment(owner: str, repo: str, pr_number: int, body: str):
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("⚠️  未配置 GITHUB_TOKEN，无法发评论（只读模式）")
        return
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "code-review-agent/1.0",
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            print(f"✅ 评论已发送: {result['html_url']}")
    except urllib.error.HTTPError as e:
        print(f"❌ 发评论失败: {e.code} {e.reason}")


# ---- Prompt ----

SYSTEM_PROMPT = """你是一个专业的 Code Review 专家，擅长 TypeScript、React、Node.js、Python 等全栈开发。

你将收到：
1. PR 基本信息（标题、作者、描述）
2. 【可能包含】本次变更涉及的相关编码规范（从规范库动态检索）
3. 代码变更 Diff

review 时根据提供的规范片段进行针对性检查，同时关注：逻辑正确性、错误处理、类型安全、测试覆盖、性能和安全风险。

输出纯 JSON，不要加代码块包裹：
{
  "summary": "整体评价（3句话内）",
  "score": 评分(1-10),
  "highlight": "本次PR最大的亮点",
  "issues": [
    {
      "file": "文件名",
      "line_hint": "问题代码片段（简短）",
      "severity": "error|warning|suggestion",
      "description": "问题描述",
      "suggestion": "修改建议"
    }
  ]
}"""


def review_diff(diff_content: str, pr_info: dict) -> dict:
    max_chars = 15000
    if len(diff_content) > max_chars:
        # 按文件边界截断，避免在文件中间断开
        files = []
        current_file_lines = []
        for line in diff_content.split("\n"):
            if line.startswith("diff --git") and current_file_lines:
                files.append("\n".join(current_file_lines))
                current_file_lines = []
            current_file_lines.append(line)
        if current_file_lines:
            files.append("\n".join(current_file_lines))

        kept, skipped = [], []
        total = 0
        for f in files:
            if total + len(f) <= max_chars:
                kept.append(f)
                total += len(f)
            else:
                # 提取文件名用于提示
                first_line = f.split("\n")[0]
                skipped.append(first_line.replace("diff --git ", "").split(" b/")[-1])
        diff_content = "\n".join(kept)
        if skipped:
            diff_content += f"\n\n[... 以下文件因 diff 过大已省略：{', '.join(skipped)} ...]"

    pr_context = f"PR #{pr_info['number']}: {pr_info['title']}\n作者: {pr_info['user']['login']}\n描述: {pr_info.get('body') or '无'}"

    # RAG：检索相关规范片段
    rag_context = ""
    try:
        if index_exists():
            query = build_query_from_diff(diff_content)
            relevant_standards = retrieve(query, k=5)
            if relevant_standards:
                standards_text = "\n\n---\n\n".join(relevant_standards)
                rag_context = f"\n\n## 本次变更涉及的相关编码规范\n\n{standards_text}\n"
                print(f"📚 检索到 {len(relevant_standards)} 条相关规范片段")
        else:
            print("提示：未检测到规范索引，运行 `python reviewer.py --build-index` 可建立索引以启用规范检索")
    except Exception as e:
        print(f"RAG 检索异常（已跳过）: {e}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{pr_context}{rag_context}\n\n代码变更 Diff：\n\n{diff_content}"}
    ]

    print("🤖 LLM 分析中...\n")
    raw = call_llm(messages).strip()
    # 提取最外层 JSON 对象
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n❌ LLM 返回内容无法解析为 JSON：{e}")
        print(f"原始输出：\n{raw}")
        raise ValueError("LLM 未返回有效 JSON，请重试或检查 prompt") from e


# ---- 格式化输出 ----

def print_review(result: dict):
    severity_emoji = {"error": "🔴", "warning": "🟡", "suggestion": "🟢"}
    print("\n" + "=" * 60)
    print(f"📊 评分：{result['score']}/10")
    print(f"📝 {result['summary']}")
    print(f"✨ 亮点：{result.get('highlight', '—')}")
    issues = result.get("issues", [])
    print(f"\n🔍 发现 {len(issues)} 个问题：\n")
    for i, issue in enumerate(issues, 1):
        emoji = severity_emoji.get(issue["severity"], "⚪")
        print(f"{i}. {emoji} [{issue['severity'].upper()}] {issue['file']}")
        print(f"   代码：`{issue['line_hint']}`")
        print(f"   问题：{issue['description']}")
        print(f"   建议：{issue['suggestion']}")
        print()


def format_review_comment(result: dict, pr_url: str) -> str:
    severity_emoji = {"error": "🔴", "warning": "🟡", "suggestion": "🟢"}
    lines = [
        "## 🤖 AI Code Review",
        "",
        f"**整体评分：{result['score']}/10**",
        "",
        f"**评价：** {result['summary']}",
        "",
        f"**亮点：** {result.get('highlight', '—')}",
        "",
    ]
    issues = result.get("issues", [])
    if issues:
        lines.append(f"### 发现 {len(issues)} 个问题")
        lines.append("")
        for i, issue in enumerate(issues, 1):
            emoji = severity_emoji.get(issue["severity"], "⚪")
            lines += [
                f"**{i}. {emoji} [{issue['severity'].upper()}]** `{issue['file']}`",
                f"> `{issue['line_hint']}`",
                "",
                f"**问题：** {issue['description']}",
                "",
                f"**建议：** {issue['suggestion']}",
                "",
                "---",
                "",
            ]
    else:
        lines.append("✅ 未发现明显问题，LGTM！")
    lines += ["", "*由 Code Review Agent 自动生成*"]
    return "\n".join(lines)


# ---- 主流程 ----

def review_pr(pr_url: str, post_to_pr: bool = False):
    print(f"🚀 开始 Review PR: {pr_url}\n")

    owner, repo, pr_number = parse_pr_url(pr_url)
    print(f"📦 仓库: {owner}/{repo}  PR: #{pr_number}\n")

    # 1. 拿 PR 信息
    pr_info = get_pr_info(owner, repo, pr_number)
    print(f"📋 标题: {pr_info['title']}")
    print(f"👤 作者: {pr_info['user']['login']}")
    print(f"📁 变更文件: {pr_info['changed_files']}  +{pr_info['additions']} -{pr_info['deletions']}\n")

    # 2. 拉 Diff（过滤 lock 文件）
    diff = get_pr_diff(owner, repo, pr_number)
    skip_patterns = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum"]
    filtered_diff = []
    skip_current = False
    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            skip_current = any(p in line for p in skip_patterns)
        if not skip_current:
            filtered_diff.append(line)
    diff = "\n".join(filtered_diff)

    # 3. Review
    result = review_diff(diff, pr_info)
    print_review(result)

    # 4. 可选：发回 PR 评论（需要 GITHUB_TOKEN）
    if post_to_pr:
        comment = format_review_comment(result, pr_url)
        print("\n📤 发送评论到 PR...")
        post_pr_comment(owner, repo, pr_number, comment)

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
        print(f"共索引 {total} 个规范 chunk，可开始使用规范检索功能")
        exit(0)

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
            comment = format_review_comment(result, pr_url)
            print("\n📤 发送评论到 PR...")
            post_pr_comment(owner, repo, pr_number, comment)
# trigger GitHub Actions CI
