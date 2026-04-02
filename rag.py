"""
RAG 模块：规范文档向量化检索
- build_index(docs_dir): 扫描 docs/ 构建 ChromaDB 索引
- retrieve(query, k):    检索最相关的规范片段
- build_query_from_diff: 从 diff 自动构造检索 query
- index_exists():        检测索引是否已建立
"""

import os
import re
import pathlib
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# ---- 配置 ----

CHROMA_DIR = pathlib.Path(__file__).parent / ".chroma"
COLLECTION_NAME = "code_standards"
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"  # 中文优化，约 95MB，完全离线

# 懒加载单例，避免 import 时加载模型影响启动速度
_embedding_model: Optional[SentenceTransformer] = None
_chroma_client: Optional[chromadb.PersistentClient] = None


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        print("加载 Embedding 模型（首次需下载约 95MB）...")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def _get_chroma_client() -> chromadb.PersistentClient:
    global _chroma_client
    if _chroma_client is None:
        CHROMA_DIR.mkdir(exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _chroma_client


# ---- 自定义 Embedding Function ----

class _LocalEmbeddingFunction(chromadb.EmbeddingFunction):
    """让 ChromaDB 使用本地 sentence-transformers 计算 embedding"""

    def __call__(self, input: list[str]) -> list[list[float]]:
        model = _get_embedding_model()
        embeddings = model.encode(input, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()


# ---- 文档加载与切块 ----

def _load_and_chunk(md_path: pathlib.Path) -> list[dict]:
    """
    按 ## 标题切割 Markdown 文件，每个 section 作为一个 chunk。
    返回格式：[{"text": str, "metadata": {...}}, ...]
    """
    text = md_path.read_text(encoding="utf-8")
    lang = _infer_language(md_path.stem)

    chunks = []
    sections = re.split(r'\n(?=## )', text)

    for section in sections:
        section = section.strip()
        if len(section) < 20:
            continue

        title_match = re.match(r'^#+\s+(.+)', section)
        title = title_match.group(1) if title_match else "通用规范"

        chunks.append({
            "text": section,
            "metadata": {
                "source": md_path.name,
                "language": lang,
                "section": title,
            }
        })

    return chunks


def _infer_language(stem: str) -> str:
    stem_lower = stem.lower()
    mapping = {
        "typescript": "typescript", "react": "react",
        "css": "css", "python": "python",
        "nodejs": "nodejs", "node": "nodejs",
        "frontend": "frontend", "backend": "backend",
    }
    for key, lang in mapping.items():
        if key in stem_lower:
            return lang
    return "general"


# ---- 建索引 ----

def build_index(docs_dir: str | pathlib.Path) -> int:
    """
    扫描 docs_dir 下所有 .md 文件，构建 ChromaDB 索引（幂等，先删后建）。
    返回写入的 chunk 数量。
    """
    docs_dir = pathlib.Path(docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"规范文档目录不存在: {docs_dir}")

    md_files = list(docs_dir.glob("**/*.md"))
    if not md_files:
        raise ValueError(f"在 {docs_dir} 下未找到任何 .md 文件")

    print(f"找到 {len(md_files)} 个规范文档，开始构建索引...")

    all_chunks = []
    for md_file in md_files:
        chunks = _load_and_chunk(md_file)
        all_chunks.extend(chunks)
        print(f"  {md_file.name}: {len(chunks)} 个 chunk")

    print(f"共 {len(all_chunks)} 个 chunk，开始 embedding（首次较慢）...")

    client = _get_chroma_client()
    embedding_fn = _LocalEmbeddingFunction()

    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 500
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i:i + batch_size]
        collection.add(
            ids=[f"chunk_{i + j}" for j in range(len(batch))],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )

    print(f"✅ 索引构建完成，共写入 {len(all_chunks)} 个 chunk")
    return len(all_chunks)


# ---- 检索 ----

def retrieve(query: str, k: int = 5) -> list[str]:
    """
    检索最相关的 k 个规范片段，返回带来源标注的文本列表。
    索引不存在或检索失败时返回空列表（不阻断 review 流程）。
    """
    client = _get_chroma_client()

    try:
        collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=_LocalEmbeddingFunction(),
        )
    except Exception:
        return []

    count = collection.count()
    if count == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(k, count),
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0]
    distances = results["distances"][0]
    metadatas = results["metadatas"][0]

    filtered = []
    for doc, dist, meta in zip(docs, distances, metadatas):
        if dist <= 0.7:  # 余弦距离 > 0.7 认为无关，过滤掉
            source_tag = f"[{meta['source']} § {meta['section']}]"
            filtered.append(f"{source_tag}\n{doc}")

    return filtered


# ---- Query 构造 ----

def build_query_from_diff(diff: str) -> str:
    """
    从 diff 内容提取语义化 query，用于向量检索。
    策略：提取变更文件扩展名 + 新增行中的关键词，拼成自然语言 query。
    """
    # 提取文件扩展名
    file_exts = re.findall(r'diff --git a/\S+\.(\w+)', diff)
    ext_to_lang = {
        "ts": "TypeScript", "tsx": "React TypeScript",
        "js": "JavaScript", "jsx": "React JavaScript",
        "py": "Python", "css": "CSS", "scss": "CSS SCSS",
        "vue": "Vue",
    }
    lang_tags = list(dict.fromkeys(
        ext_to_lang[ext] for ext in file_exts if ext in ext_to_lang
    ))

    # 提取新增行关键词
    added_lines = [line[1:] for line in diff.split("\n")
                   if line.startswith("+") and not line.startswith("+++")]
    added_text = " ".join(added_lines[:200])

    keywords = []
    patterns = [
        (r'\bany\b', "any 类型"),
        (r'use[A-Z]\w+\(', "React Hook"),
        (r'\binterface\b|\btype\b', "TypeScript 类型定义"),
        (r'\bexcept\b', "异常处理"),
        (r'\bprint\b', "日志 print"),
        (r'console\.(log|error|warn)', "console 调用"),
        (r'\basync\b|\bawait\b', "异步处理"),
        (r'useState|useEffect|useCallback|useMemo', "React Hooks"),
        (r':\s*any\b|<any>', "TypeScript any"),
        (r'key=\{index\}|key=\{i\}', "列表 key index"),
        (r'os\.getenv|os\.environ', "环境变量"),
        (r'!important', "CSS important"),
        (r'z-index\s*:\s*\d+', "z-index"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, added_text):
            keywords.append(label)

    parts = []
    if lang_tags:
        parts.append(f"语言：{', '.join(lang_tags)}")
    if keywords:
        parts.append(f"涉及规范：{', '.join(keywords[:5])}")
    parts.append("代码规范 最佳实践 编码标准")

    return " | ".join(parts)


# ---- 索引存在性检测 ----

def index_exists() -> bool:
    """检测向量索引是否已建立且非空"""
    try:
        client = _get_chroma_client()
        col = client.get_collection(COLLECTION_NAME)
        return col.count() > 0
    except Exception:
        return False
