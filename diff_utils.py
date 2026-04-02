"""
Diff 处理与 Review 逻辑模块
- filter_lock_files:        过滤 lock 文件
- split_diff_into_files:    按文件拆分 diff
- split_diff_into_shards:   按字符上限分片
- review_single_shard:      单片 review（调用 LLM + RAG）
- merge_review_results:     合并多片 review 结果
- review_diff:              对外暴露的完整 review 入口
"""

import json
import logging
import re

from llm_client import Settings, call_llm
from prompts import SYSTEM_PROMPT
from rag import retrieve, build_query_from_diff, index_exists

logger = logging.getLogger(__name__)

# 需要跳过 review 的 lock 文件
_LOCK_FILE_PATTERNS = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum"]


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

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{pr_context}{shard_hint}{rag_context}\n\n代码变更 Diff：\n\n{diff_content}"}
    ]

    logger.info("🤖 LLM 分析中...\n")
    raw = call_llm(messages, settings).strip()

    # 提取最外层 JSON 对象
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("❌ LLM 返回内容无法解析为 JSON：%s", e)
        logger.debug("原始输出：\n%s", raw)
        raise ValueError("LLM 未返回有效 JSON，请重试或检查 prompt") from e


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

