"""
Diff 处理与 Review 逻辑模块
- filter_lock_files:        过滤 lock 文件
- split_diff_into_files:    按文件拆分 diff
- split_diff_into_shards:   按字符上限分片
- parse_diff_to_structured: 将原始 diff 解析为结构化对象列表
- render_structured_diff:   将结构化对象渲染为 LLM 友好的文本
- review_single_shard:      单片 review（调用 LLM + RAG）
- merge_review_results:     合并多片 review 结果
- review_diff:              对外暴露的完整 review 入口
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from llm_client import Settings, call_llm
from prompts import SYSTEM_PROMPT
from rag import retrieve, build_query_from_diff, index_exists

logger = logging.getLogger(__name__)

# 需要跳过 review 的 lock 文件
_LOCK_FILE_PATTERNS = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum"]


# ---------------------------------------------------------------------------
# 结构化 Diff 数据模型
# ---------------------------------------------------------------------------

@dataclass
class HunkLine:
    """Hunk 中的单行，保留原始顺序"""
    kind: Literal["added", "removed", "context"]   # added=新增, removed=删除, context=上下文
    content: str                                    # 行内容（不含前缀 +/-/空格）


@dataclass
class Hunk:
    """一个变更块（@@...@@），按原始顺序保留所有行"""
    new_start: int                                  # 新文件起始行号
    lines: list[HunkLine] = field(default_factory=list)

    # 便捷属性
    @property
    def added_lines(self) -> list[str]:
        return [l.content for l in self.lines if l.kind == "added"]

    @property
    def removed_lines(self) -> list[str]:
        return [l.content for l in self.lines if l.kind == "removed"]

    @property
    def has_changes(self) -> bool:
        return any(l.kind != "context" for l in self.lines)


@dataclass
class FileDiff:
    """单个文件的变更摘要"""
    filename: str
    change_type: Literal["added", "deleted", "modified", "renamed"]
    old_filename: str | None                                     # rename 时的旧文件名
    hunks: list[Hunk] = field(default_factory=list)


def filter_lock_files(diff: str) -> str:
    """过滤掉 diff 中的 lock 文件片段"""
    filtered: list[str] = []
    skip_current = False
    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            skip_current = any(p in line for p in _LOCK_FILE_PATTERNS)
        if not skip_current:
            filtered.append(line)
    return "\n".join(filtered)


def split_diff_into_files(diff_content: str) -> list[str]:
    """将整个 diff 按文件边界拆分为独立的文件 diff 列表"""
    files: list[str] = []
    current: list[str] = []
    for line in diff_content.split("\n"):
        if line.startswith("diff --git") and current:
            files.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        files.append("\n".join(current))
    return files


def split_diff_into_shards(diff_content: str, max_chars: int) -> tuple[list[str], list[str]]:
    """将 diff 按文件粒度分片，每片不超过 max_chars。
    单个文件超出 max_chars 时直接跳过，记录到 skipped_files。
    返回 (分片列表, 跳过的文件名列表)。
    """
    file_diffs = split_diff_into_files(diff_content)
    shards: list[str] = []
    skipped_files: list[str] = []
    current_shard: list[str] = []
    current_len = 0

    for fd in file_diffs:
        fd_len = len(fd)
        if fd_len > max_chars:
            match = re.search(r"diff --git a/.+ b/(.+)", fd.split("\n")[0])
            fname = match.group(1) if match else "unknown"
            skipped_files.append(fname)
            logger.warning("⚠️  跳过文件 [%s]：diff 过长（%d 字符），建议人工审查", fname, fd_len)
            continue
        if current_shard and current_len + fd_len > max_chars:
            shards.append("\n".join(current_shard))
            current_shard = []
            current_len = 0
        current_shard.append(fd)
        current_len += fd_len

    if current_shard:
        shards.append("\n".join(current_shard))

    return shards, skipped_files


# ---------------------------------------------------------------------------
# Diff 解析 & 渲染
# ---------------------------------------------------------------------------

def parse_diff_to_structured(diff_content: str) -> list[FileDiff]:
    """将原始 unified diff 解析为 FileDiff 结构化对象列表。"""
    file_diffs: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None

    for line in diff_content.split("\n"):
        # 文件头：diff --git a/xxx b/yyy
        if line.startswith("diff --git "):
            if current_hunk is not None and current_file is not None:
                current_file.hunks.append(current_hunk)
                current_hunk = None
            if current_file is not None:
                file_diffs.append(current_file)
            m = re.search(r"diff --git a/(.+) b/(.+)", line)
            if m:
                current_file = FileDiff(
                    filename=m.group(2),
                    change_type="modified",
                    old_filename=None,
                )
            continue

        if current_file is None:
            continue

        # 文件状态标记
        if line.startswith("new file mode"):
            current_file.change_type = "added"
        elif line.startswith("deleted file mode"):
            current_file.change_type = "deleted"
        elif line.startswith("rename from "):
            current_file.old_filename = line[len("rename from "):]
            current_file.change_type = "renamed"
        elif line.startswith("rename to "):
            current_file.filename = line[len("rename to "):]

        # hunk 头：@@ -a,b +c,d @@
        elif line.startswith("@@"):
            if current_hunk is not None:
                current_file.hunks.append(current_hunk)
            m = re.search(r"\+([0-9]+)", line)
            current_hunk = Hunk(new_start=int(m.group(1)) if m else 0)

        # 变更行
        elif current_hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current_hunk.lines.append(HunkLine(kind="added", content=line[1:]))
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk.lines.append(HunkLine(kind="removed", content=line[1:]))
            elif line.startswith(" "):
                current_hunk.lines.append(HunkLine(kind="context", content=line[1:]))

    # 收尾
    if current_hunk is not None and current_file is not None:
        current_file.hunks.append(current_hunk)
    if current_file is not None:
        file_diffs.append(current_file)

    return file_diffs


def render_structured_diff(file_diffs: list[FileDiff]) -> str:
    """将结构化 FileDiff 列表渲染为 LLM 友好的文本。

    设计原则：
    - 所有行按原始顺序交织展示在同一代码块内，保证 LLM 能理解完整上下文
    - 新增行（+ 行）：正常显示，行尾加 [NEW] 标记，是 review 对象
    - 删除行（- 行）：行首加 // [DELETED] 注释，明确标注"仅供理解上下文，不参与评审"
    - 上下文行（空格行）：正常显示，无标记
    """
    _CHANGE_LABEL = {
        "added": "新增文件", "deleted": "已删除文件",
        "modified": "已修改",  "renamed": "已重命名",
    }
    _EXT_LANG = {
        "ts": "ts", "tsx": "tsx", "js": "js", "jsx": "jsx",
        "py": "python", "css": "css", "scss": "scss", "vue": "vue",
    }
    out: list[str] = []

    for fd in file_diffs:
        label = _CHANGE_LABEL.get(fd.change_type, "已修改")
        rename_hint = f"  (原名: {fd.old_filename})" if fd.old_filename else ""
        out.append(f"### 文件: {fd.filename}  [{label}{rename_hint}]")

        if not fd.hunks:
            out.append("（无代码变更）\n")
            continue

        ext = fd.filename.rsplit(".", 1)[-1] if "." in fd.filename else ""
        lang = _EXT_LANG.get(ext, "")

        for i, hunk in enumerate(fd.hunks, 1):
            out.append(f"\n#### 变更块 {i}（新文件第 {hunk.new_start} 行起）")
            out.append(f"```{lang}")

            for hl in hunk.lines:
                if hl.kind == "added":
                    # 新增行：行首加 [+NEW] 前缀，避免破坏 JSX/多行表达式语法
                    out.append(f"/*[+NEW]*/ {hl.content}")
                elif hl.kind == "removed":
                    # 删除行：注释化，明确告知 LLM 此行已删除、不参与评审
                    out.append(f"/*[-DEL, 不评审]*/ {hl.content.strip()}")
                else:
                    # 上下文行：原样输出，供 LLM 理解完整语义
                    out.append(hl.content)

            out.append("```")

        out.append("")

    return "\n".join(out)


def _build_rag_context(diff_content: str, shard_index: int) -> str:
    """从 RAG 检索相关规范片段，构建上下文字符串"""
    try:
        if index_exists():
            query = build_query_from_diff(diff_content)
            relevant_standards = retrieve(query, k=5)
            if relevant_standards:
                standards_text = "\n\n---\n\n".join(relevant_standards)
                logger.info("📚 检索到 %d 条相关规范片段", len(relevant_standards))
                return f"\n\n## 本次变更涉及的相关编码规范\n\n{standards_text}\n"
        elif shard_index == 1:
            logger.info("提示：未检测到规范索引，运行 `python reviewer.py --build-index` 可建立索引以启用规范检索")
    except Exception as e:
        logger.warning("RAG 检索异常（已跳过）: %s", e)
    return ""


def review_single_shard(diff_content: str, pr_info: dict, settings: Settings,
                         shard_index: int = 1, total_shards: int = 1) -> dict:
    """对单片 diff 调用 LLM 进行 review，返回结构化结果。"""
    pr_context = (
        f"PR #{pr_info['number']}: {pr_info['title']}\n"
        f"作者: {pr_info['user']['login']}\n"
        f"描述: {pr_info.get('body') or '无'}"
    )
    shard_hint = (
        f"\n\n[注意：本次仅为 PR 的第 {shard_index}/{total_shards} 片代码，请专注于本片内容进行 review]"
        if total_shards > 1 else ""
    )

    rag_context = _build_rag_context(diff_content, shard_index)

    # 结构化渲染：将原始 diff 解析后重新渲染为 LLM 友好格式
    # 新增代码作为主体（代码块），删除代码折叠为只读注释，从输入层面消除歧义
    file_diffs = parse_diff_to_structured(diff_content)
    rendered_diff = render_structured_diff(file_diffs)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{pr_context}{shard_hint}{rag_context}\n\n代码变更：\n\n{rendered_diff}"}
    ]

    logger.info("🤖 LLM 分析中...\n")
    raw = call_llm(messages, settings).strip()

    # 提取最外层 JSON 对象
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("❌ LLM 返回内容无法解析为 JSON：%s", e)
        logger.debug("原始输出：\n%s", raw)
        raise ValueError("LLM 未返回有效 JSON，请重试或检查 prompt") from e

    # 后处理：过滤基于推断/假设而非直接代码证据的 issue
    result["issues"] = _filter_speculative_issues(result.get("issues", []))
    return result


# 纯推断性短语：这类 description 说明 LLM 在猜测而非描述可见代码的问题
# 注意：只匹配「缺少某属性/参数/字段」这类跨文件推断，不匹配技术性的"可能为 undefined"
_MISSING_PROP_PATTERNS = re.compile(
    r"缺少.*属性|缺少.*字段|缺少.*参数|缺失.*属性|缺失.*参数"
    r"|missing.*prop|missing.*field|missing.*param"
    r"|应该(包含|有|添加).*(属性|字段|参数)"
    r"|需要(添加|补充).*(属性|字段|参数)",
    re.IGNORECASE,
)


def _filter_speculative_issues(issues: list[dict]) -> list[dict]:
    """过滤掉没有代码直接证据的 issue。

    过滤规则（按可靠性排序，只保留最精准的）：
    1. evidence 字段为空或缺失 → 过滤（LLM 自己找不到代码证据）
    2. description 明确是"缺少属性/字段/参数"的跨文件推断 → 过滤
       （注意：不过滤"可能为 undefined"、"类型不安全"等技术性描述）
    """
    kept, dropped = [], []
    for issue in issues:
        evidence = (issue.get("evidence") or "").strip()
        description = issue.get("description", "")

        if not evidence:
            # evidence 为空：LLM 无法给出直接代码证据
            dropped.append(issue)
            logger.debug("🗑️  过滤无证据 issue：%s", description[:80])
        elif _MISSING_PROP_PATTERNS.search(description):
            # 明确的跨文件属性推断：缺少某属性/字段
            dropped.append(issue)
            logger.debug("🗑️  过滤跨文件推断 issue：%s", description[:80])
        else:
            kept.append(issue)

    if dropped:
        logger.info("🔍 已过滤 %d 条无证据/推断性 issue，保留 %d 条", len(dropped), len(kept))

    return kept


def merge_review_results(results: list[dict], settings: Settings) -> dict:
    """将多个分片的 review 结果合并为一个整体结果。
    - issues：全部汇总
    - score：取加权平均（按各片 issues 数量加权，无 issues 时等权）
    - summary / highlight：拼接后让 LLM 再精炼一次
    """
    if len(results) == 1:
        return results[0]

    all_issues: list[dict] = []
    scores: list[float] = []
    summaries: list[str] = []
    highlights: list[str] = []

    for r in results:
        all_issues.extend(r.get("issues", []))
        scores.append(float(r.get("score", 5)))
        summaries.append(r.get("summary", ""))
        highlights.append(r.get("highlight", ""))

    # 加权平均分（以各片 issue 数 + 1 为权重，避免零权）
    weights = [len(r.get("issues", [])) + 1 for r in results]
    weighted_score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    merged_score = round(weighted_score, 1)

    # 用 LLM 精炼多段 summary / highlight
    combined_summaries = "\n".join(f"- {s}" for s in summaries if s)
    combined_highlights = "\n".join(f"- {h}" for h in highlights if h)
    refine_prompt = (
        f"以下是对同一个 PR 多个分片的 review 总结，请综合归纳成 1 句话的整体 summary，"
        f"以及 1 句话的最大亮点 highlight，直接返回 JSON：\n"
        f"{{\"summary\": \"...\", \"highlight\": \"...\"}}\n\n"
        f"各分片 summary：\n{combined_summaries}\n\n"
        f"各分片 highlight：\n{combined_highlights}"
    )
    try:
        raw = call_llm([{"role": "user", "content": refine_prompt}], settings).strip()
        start, end = raw.find("{"), raw.rfind("}")
        refined = json.loads(raw[start:end + 1]) if start != -1 and end > start else {}
    except Exception:
        refined = {}

    return {
        "summary": refined.get("summary", " | ".join(s for s in summaries if s)),
        "score": merged_score,
        "highlight": refined.get("highlight", next((h for h in highlights if h), "—")),
        "issues": all_issues,
    }


def review_diff(diff_content: str, pr_info: dict, settings: Settings) -> dict:
    """对 diff 进行完整 review：自动分片 → 逐片 review → 合并结果"""
    max_chars = settings.max_diff_chars
    logger.info("📐 当前模型 [%s] max_diff_chars = %d", settings.openai_model, max_chars)

    shards, skipped_files = split_diff_into_shards(diff_content, max_chars)

    if len(shards) > 1:
        logger.info("📦 Diff 过大（%d 字符），已分为 %d 片分批 review", len(diff_content), len(shards))
        shard_results = []
        for idx, shard in enumerate(shards, 1):
            logger.info("🔍 正在 review 第 %d/%d 片（%d 字符）...", idx, len(shards), len(shard))
            shard_results.append(
                review_single_shard(shard, pr_info, settings, shard_index=idx, total_shards=len(shards))
            )
        logger.info("🔗 合并 %d 片 review 结果...", len(shards))
        result = merge_review_results(shard_results, settings)
    else:
        result = review_single_shard(diff_content, pr_info, settings)

    # 将跳过的超大文件作为 issue 追加，提醒人工审查
    for fname in skipped_files:
        result.setdefault("issues", []).append({
            "file": fname,
            "line_hint": "（整个文件）",
            "severity": "warning",
            "description": "该文件 diff 过长，已跳过自动 review",
            "suggestion": "请人工审查此文件的完整变更",
        })

    return result

