"""
前端 Code Review Agent - Phase 1 Demo
输入：一段 TS/React 代码
输出：结构化 review 结果（问题列表 + 严重等级 + 修改建议）

使用 stream=True + 手动收集 content 的方式兼容。
"""

import os
import json
import pathlib
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---- 读取额外 headers ----
def get_extra_headers():
    cfg_path = pathlib.Path(os.path.expanduser("~/.openclaw/openclaw.json"))
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("models", {}).get("providers", {}).get("kubeplex-maas", {}).get("headers", {})
    return {}


# ---- Prompt 定义 ----

SYSTEM_PROMPT = """你是一个专业的前端代码 Review 专家，专注于 React + TypeScript 代码质量。

你的 review 标准：
1. **类型安全**：杜绝 any、保证类型完备、正确使用泛型
2. **React 最佳实践**：Hook 规则、依赖数组完整性、避免不必要 re-render、key 的正确使用
3. **性能**：避免在 render 中创建对象/函数、合理使用 useMemo/useCallback、懒加载
4. **潜在 Bug**：竞态条件、内存泄漏、边界情况未处理
5. **代码风格**：命名规范、单一职责、可读性

必须输出严格的 JSON 格式，不要输出任何其他内容（不要有 ```json 代码块标记）。"""

USER_PROMPT_TEMPLATE = """请 review 以下代码：

```typescript
{code}
```

输出以下 JSON 格式（不加任何代码块包裹）：
{{
  "summary": "整体评价（2-3句话）",
  "score": 评分（1-10的整数）,
  "issues": [
    {{
      "line_hint": "问题代码片段（不超过50字符）",
      "category": "type-safety 或 react-best-practice 或 performance 或 code-style 或 potential-bug",
      "severity": "error 或 warning 或 suggestion",
      "description": "问题描述，说清楚为什么有问题",
      "suggestion": "具体修改建议，最好给出改后示例"
    }}
  ]
}}"""


# ---- 流式调用并收集完整内容 ----

def call_llm_stream(messages: list) -> str:
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE"),
    )
    extra_headers = get_extra_headers()

    full_content = ""
    stream = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "qwen-turbo"),
        messages=messages,
        temperature=0.1,
        stream=True,
        extra_headers=extra_headers,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
            print(delta.content, end="", flush=True)

    print()  # 换行
    return full_content


# ---- 主流程 ----

def review_code(code: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(code=code)},
    ]

    print("🤖 LLM 分析中...\n")
    raw = call_llm_stream(messages)

    # 清理可能的代码块包裹
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def print_review(result: dict):
    print("\n" + "=" * 60)
    print(f"📊 代码质量评分：{result['score']}/10")
    print(f"📝 整体评价：{result['summary']}")
    print(f"\n🔍 发现 {len(result['issues'])} 个问题：\n")

    severity_emoji = {"error": "🔴", "warning": "🟡", "suggestion": "🟢"}
    category_cn = {
        "type-safety": "类型安全",
        "react-best-practice": "React最佳实践",
        "performance": "性能",
        "code-style": "代码风格",
        "potential-bug": "潜在Bug",
    }

    for i, issue in enumerate(result["issues"], 1):
        emoji = severity_emoji.get(issue["severity"], "⚪")
        cat = category_cn.get(issue["category"], issue["category"])
        print(f"{i}. {emoji} [{issue['severity'].upper()}] {cat}")
        print(f"   代码：`{issue['line_hint']}`")
        print(f"   问题：{issue['description']}")
        print(f"   建议：{issue['suggestion']}")
        print()


# ---- 测试样本（故意埋了6个问题）----

SAMPLE_CODE = """
import React, { useState, useEffect } from 'react';

interface Props {
  userId: any;          // 问题1: any 类型
  onSuccess: Function;  // 问题2: Function 类型太宽泛
}

const UserProfile: React.FC<Props> = ({ userId, onSuccess }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/user/${userId}`)
      .then(res => res.json())
      .then(data => {
        setData(data);
        setLoading(false);
      });
  }, []);  // 问题3: 依赖数组缺少 userId，且没有处理竞态/清理

  const style = { color: 'red', fontSize: 14 };  // 问题4: 每次 render 创建新对象

  const handleClick = () => {  // 问题5: 应该 useCallback
    onSuccess(data);
  };

  if (loading) return <div>loading...</div>;

  return (
    <div style={style}>
      <h1>{data.name}</h1>   {/* 问题6: data 可能为 null，未做空值处理 */}
      <p>{data.email}</p>
    </div>
  );
};

export default UserProfile;
"""


if __name__ == "__main__":
    print("🚀 前端 Code Review Agent - Phase 1 Demo")
    print("=" * 60)
    print("📄 分析代码：UserProfile 组件\n")

    result = review_code(SAMPLE_CODE)
    print_review(result)
