# 前端 Code Review Agent - Phase 1

输入一段 React/TypeScript 代码，自动输出结构化 review 结果。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 如果 pip 不可用，用 pip3：`pip3 install -r requirements.txt`

### 2. 配置 API Key

```bash
cp .env.example .env
```

用编辑器打开 `.env`，填入你的 OpenAI API Key：

```
OPENAI_API_KEY=sk-你的key
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o
```

### 3. 运行

```bash
python reviewer.py
```

## 自定义输入

修改 `reviewer.py` 底部的 `SAMPLE_CODE` 变量，替换为你想 review 的代码，或调用 `review_code()` 函数：

```python
from reviewer import review_code, print_review

code = """
// 你的代码
"""

result = review_code(code)
print_review(result)
```

## 输出说明

- 🔴 ERROR：必须修，会引发 bug 或类型错误
- 🟡 WARNING：建议修，影响可维护性或性能
- 🟢 SUGGESTION：可优化，锦上添花

## 注意事项

- `.env` 文件不要上传 git（已在 .gitignore 排除）
- 如果使用美团内网接口，接口强制 SSE 流式返回，代码已做兼容处理
- 使用 OpenAI 官方接口无此限制
