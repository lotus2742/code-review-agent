# Code Review Agent

输入 GitHub PR URL，自动拉取代码变更，结合私有编码规范知识库，调用 LLM 生成结构化 Code Review 报告，并可将结果自动发回 PR 评论。

## 功能特性

- 🔗 **GitHub PR 全自动拉取** — 只需提供 PR URL，无需安装 gh CLI
- 📚 **RAG 规范检索** — 将内部编码规范向量化，按 diff 语义检索相关规范片段注入 Prompt
- 🧹 **Lock 文件自动过滤** — 跳过 `package-lock.json`、`yarn.lock` 等无需 review 的文件
- 📊 **结构化报告** — 输出评分（1-10）、亮点和三级问题（error / warning / suggestion）
- 💬 **自动发回评论** — 可选将 review 结果以 Markdown 格式发回 GitHub PR 评论

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 首次安装会下载约 95MB 的本地 Embedding 模型（BAAI/bge-small-zh-v1.5），请确保网络畅通。

### 2. 配置环境变量

复制示例文件并填入你的配置：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# LLM 配置（兼容 OpenAI 接口的服务均可，如 qwen、DeepSeek 等）
OPENAI_API_KEY=sk-你的key
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# 模型上下文窗口大小（可选，单位：tokens）
# MODEL_CONTEXT_TOKENS=128000

# GitHub Token（可选，不填则只读模式，无法发回 PR 评论）
GITHUB_TOKEN=ghp_你的token
```

> `GITHUB_TOKEN` 需要有 `repo` 权限，在 GitHub → Settings → Developer settings → Personal access tokens 生成。

#### MODEL_CONTEXT_TOKENS 说明

以下模型**无需填写**此项，程序会自动识别其上下文窗口大小：

| 模型系列 | 自动识别的关键词 | 推断上下文大小 |
|----------|----------------|---------------|
| 通义千问 | `qwen-turbo`、`qwen-plus`、`qwen-max` | 128K |
| OpenAI | `gpt-4o`、`gpt-4-turbo` | 128K |
| OpenAI | `gpt-4-32k` | 32K |
| OpenAI | `gpt-3.5-turbo-16k` | 16K |
| Anthropic | `claude-3`（含 claude-3.5 等） | 128K |
| DeepSeek | `deepseek`（含所有版本） | 128K |
| Google | `gemini`（含所有版本） | 128K |

使用**不在上表中**的模型（如 `qwen-long`、`llama3`、私有部署模型等）时，建议在 `.env` 中手动指定，否则默认保守使用 8K，可能导致大 PR 被分片过多：

```env
# 示例：qwen-long 支持 100 万 token
MODEL_CONTEXT_TOKENS=1000000
```

### 3. 建立规范索引（可选，开启 RAG）

```bash
python reviewer.py --build-index
```

此命令会扫描 `docs/` 目录下的所有 `.md` 规范文件，构建本地向量索引。跳过此步骤则 review 不会使用规范检索。

### 4. Review 一个 PR

```bash
python reviewer.py https://github.com/owner/repo/pull/123
```

运行后会打印结构化 review 结果，并询问是否发回 PR 评论。

**直接发回评论（不询问）：**

```bash
python reviewer.py https://github.com/owner/repo/pull/123 --post
```

## 输出说明

```
📊 评分：8/10
📝 整体评价（3句话以内）
✨ 亮点：...

🔍 发现 N 个问题：

1. 🔴 [ERROR] src/api/user.ts
   代码：`const res: any = await fetch(...)`
   问题：使用了 any 类型，绕过类型检查
   建议：定义具体的响应类型 interface
```

- 🔴 **ERROR**：必须修，会引发 bug 或类型错误
- 🟡 **WARNING**：建议修，影响可维护性或性能
- 🟢 **SUGGESTION**：可优化，锦上添花

## 规范知识库

`docs/` 目录存放编码规范文档（Markdown 格式），修改后需重新运行 `--build-index`：

| 文件 | 内容 |
|------|------|
| `frontend-typescript.md` | TypeScript 编码规范 |
| `frontend-react.md` | React 组件规范 |
| `frontend-css.md` | CSS / SCSS 规范 |
| `backend-nodejs.md` | Node.js 后端规范 |
| `backend-python.md` | Python 后端规范 |

可以直接修改或新增 `.md` 文件来扩展规范库，然后重建索引即可生效。

## 在其他项目中接入

无需复制代码，直接在目标项目的 workflow 中引用本仓库即可自动 review 每一个 PR。

在目标项目中创建 `.github/workflows/code-review.yml`：

```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  review:
    runs-on: ubuntu-latest
    if: github.actor != 'dependabot[bot]'
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: lotus2742/code-review-agent@main
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          openai_api_base: ${{ secrets.OPENAI_API_BASE }}
          openai_model: ${{ secrets.OPENAI_MODEL }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

然后在目标项目的 **Settings → Secrets → Actions** 中配置 `OPENAI_API_KEY`、`OPENAI_API_BASE`、`OPENAI_MODEL` 即可。PR 创建或更新时会自动触发，review 结果以评论形式出现在 PR 页面。

> 如需使用自定义规范文档，fork 本仓库并修改 `docs/` 目录后，将 `uses` 指向你的 fork 即可。

## 注意事项

- `.env` 文件包含敏感信息，已在 `.gitignore` 中排除，**请勿提交到 git**
- 公开仓库无需 `GITHUB_TOKEN` 即可拉取 PR diff；私有仓库必须配置
- diff 超出模型上下文限制时会**自动按文件粒度分片**，逐片 review 后合并结果，不再丢弃任何文件
