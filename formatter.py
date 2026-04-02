"""
格式化输出模块
- print_review:           终端打印 review 结果
- format_review_comment:  生成 GitHub PR 评论 Markdown 文本
"""

_SEVERITY_EMOJI = {"error": "🔴", "warning": "🟡", "suggestion": "🟢"}


def print_review(result: dict) -> None:
    """终端打印 review 结果"""
    print("\n" + "=" * 60)
    print(f"📊 评分：{result['score']}/10")
    print(f"📝 {result['summary']}")
    print(f"✨ 亮点：{result.get('highlight', '—')}")
    issues = result.get("issues", [])
    print(f"\n🔍 发现 {len(issues)} 个问题：\n")
    for i, issue in enumerate(issues, 1):
        emoji = _SEVERITY_EMOJI.get(issue["severity"], "⚪")
        print(f"{i}. {emoji} [{issue['severity'].upper()}] {issue['file']}")
        print(f"   代码：`{issue['line_hint']}`")
        print(f"   问题：{issue['description']}")
        print(f"   建议：{issue['suggestion']}")
        print()


def format_review_comment(result: dict) -> str:
    """生成 GitHub PR 评论 Markdown 文本"""
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
            emoji = _SEVERITY_EMOJI.get(issue["severity"], "⚪")
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

