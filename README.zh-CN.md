<div align="center">

# DeepReviewer-2.0（仅后端，最小化开源版）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Backend Only](https://img.shields.io/badge/Mode-Backend_Only-0ea5e9?style=for-the-badge)](#)

DeepReviewer-2.0 开源最小后端流程：

**PDF -> MinerU Markdown -> Review Agent 工具循环 -> Final Markdown -> Final PDF**

无前端、无数据库、无需 uvicorn。  
直接使用 `python main.py ...` 运行。

[English README](./README.md)

[快速开始](#快速开始) •
[配置说明](#配置说明) •
[CLI 用法](#cli-用法) •
[输出格式](#输出格式) •
[外部服务](#外部服务) •
[故障排查](#故障排查)

</div>

---

## 功能特性

- 最小化仅后端架构（本地 JSON 存储）
- 端到端异步任务执行
- CLI 实时状态轮询
- 严格的 review 工具工作流（MCP 风格）
- 使用量统计：
  - token 用量
  - 工具调用用量（含每个工具调用次数）
  - paper search 用量
- 最终产物：
  - `final_report.md`
  - `final_report.pdf`（网站风格导出，含原文附录与自动批注叠加）

---

## 架构说明

每个任务独立落盘到：

```text
data/jobs/<job_id>/
```

核心流程：

1. 提交 PDF
2. 调用 MinerU v4 上传并解析
3. 构建 review runtime context 并运行 agent
4. Agent 调用工具（`pdf_read_lines`、`pdf_annotate`、`paper_search` 等）
5. 通过 `review_final_markdown_write` 落盘最终 markdown
6. 以网站对齐的导出器生成最终 PDF：
   - 品牌封面（含 logo）
   - token 使用摘要（不含积分）
   - 追加原始论文 PDF 页面
   - 基于批注自动绘制 overlay callout

---

## 环境要求

- Python `>=3.11`
- Linux/macOS（Windows 需调整命令）
- 需可访问网络服务：
  - LLM 接口（OpenAI-compatible）
  - MinerU（推荐用于生产级 PDF 解析）
  - 可选论文检索/阅读服务（PASA 或兼容服务）

---

## 快速开始

### 1) 安装

```bash
cd <repo_root>
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 2) 配置

```bash
cp .env.example .env
```

最小可用配置示例：

```bash
# LLM（OpenAI-compatible）
BASE_URL=http://127.0.0.1:8004/v1
AGENT_MODEL=gpt-5.2
# 当你的提供商支持时可开启 Responses API（否则保持 false）
OPENAI_USE_RESPONSES_API=false
# 本地网关若不鉴权，可不填
# OPENAI_API_KEY=...
# 建议本地开启：避免 OpenAI tracing 401 日志噪声
OPENAI_AGENTS_DISABLE_TRACING=1

# MinerU
MINERU_API_TOKEN=your_mineru_token

# 可选 PASA 适配
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
# 本地无鉴权 PASA 可留空
PAPER_SEARCH_API_KEY=
PAPER_SEARCH_ENDPOINT=/pasa/search
```

### 3) 提交并跟踪任务

```bash
python main.py submit --pdf /path/to/paper.pdf --wait-seconds 0
python main.py status --job-id <job_id>
python main.py watch --job-id <job_id> --interval 2 --timeout 1800
```

### 4) 获取结果

```bash
python main.py result --job-id <job_id> --format all
python main.py result --job-id <job_id> --format md
python main.py result --job-id <job_id> --format pdf
```

---

## 配置说明

先复制 `.env.example` 到 `.env`。

### LLM（OpenAI-compatible）

DeepReviewer-2.0 支持通用 OpenAI-compatible 提供商。  
大多数本地部署场景仅设置 `BASE_URL` 即可。

- `BASE_URL`（优先）
- `OPENAI_BASE_URL`（别名）
- `LLM_BASE_URL`（别名）
- `OPENAI_API_KEY` / `API_KEY` / `LLM_API_KEY`（视网关是否鉴权）
- `AGENT_MODEL`（默认 `gpt-5.2`）
- `OPENAI_USE_RESPONSES_API`（默认 `false`；设为 `true` 时使用 OpenAI Responses API）
- `AGENT_RESUME_ATTEMPTS`（最大硬限制 2）
- `OPENAI_AGENTS_DISABLE_TRACING`（本地网关建议设为 `1`）

### Finalization Gates

- `ENABLE_FINAL_GATES`（默认 `false`；设为 `true` 后启用最终写入门槛）
- `MIN_PAPER_SEARCH_CALLS_FOR_PDF_ANNOTATE`（默认 `3`）
- `MIN_PAPER_SEARCH_CALLS_FOR_FINAL`（默认 `3`）
- `MIN_DISTINCT_PAPER_QUERIES_FOR_FINAL`（默认 `3`）
- `MIN_ANNOTATIONS_FOR_FINAL`（默认 `10`）
- `MIN_ENGLISH_WORDS_FOR_FINAL`（默认 `0`，即关闭）
- `FORCE_ENGLISH_OUTPUT`（默认 `true`）

### MinerU

- `MINERU_BASE_URL`（默认 `https://mineru.net/api/v4`）
- `MINERU_API_TOKEN`
- `MINERU_MODEL_VERSION`（默认 `vlm`）
- `MINERU_ALLOW_LOCAL_FALLBACK`（默认 `false`）

### Paper Search / Read

- `PAPER_SEARCH_BASE_URL`
- `PAPER_SEARCH_API_KEY`（本地无鉴权 PASA 可选）
- `PAPER_SEARCH_ENDPOINT`（默认 `/pasa/search`）
- `PAPER_READ_BASE_URL`
- `PAPER_READ_API_KEY`
- `PAPER_READ_ENDPOINT`（默认 `/read`）

未配置外部服务时，会回退到 arXiv metadata 级别检索/阅读。

---

## CLI 用法

### Submit

```bash
python main.py submit --pdf /path/to/paper.pdf --title "Optional title" --wait-seconds 10
```

行为：

- 若在 `wait-seconds` 窗口内完成：返回 `completed=true`
- 否则返回当前状态 + 用量快照

### Status

```bash
python main.py status --job-id <job_id>
```

### Watch

```bash
python main.py watch --job-id <job_id> --interval 2 --timeout 1800
```

### Result

```bash
python main.py result --job-id <job_id> --format all
python main.py result --job-id <job_id> --format md
python main.py result --job-id <job_id> --format pdf
```

---

## 输出格式

### `status` 响应结构示例

```json
{
  "job_id": "uuid",
  "status": "agent_running",
  "message": "...",
  "annotation_count": 0,
  "final_report_ready": false,
  "pdf_ready": false,
  "usage": {
    "token": {
      "requests": 0,
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0
    },
    "tool": {
      "total_calls": 0,
      "distinct_tools": 0,
      "per_tool": {}
    },
    "paper_search": {
      "total_calls": 0,
      "successful_calls": 0,
      "effective_calls": 0,
      "papers_found": 0,
      "distinct_queries": 0
    }
  },
  "artifacts": {
    "final_markdown_path": null,
    "report_pdf_path": null
  }
}
```

### 完成后的核心产物

- `data/jobs/<job_id>/final_report.md`
- `data/jobs/<job_id>/final_report.pdf`
- `data/jobs/<job_id>/events.jsonl`

`final_report.pdf` 包含：

- Final markdown 报告正文
- Token 使用摘要（input/output/total/requests）
- 原始论文 PDF 附录页面
- 自动批注 overlay（当 MinerU 行级 bbox 可用时）

### 导出文件格式说明

`final_report.md`（机器可读为主）：

- 由 `review_final_markdown_write` 生成的 UTF-8 markdown
- 包含完整评审内容，适合归档与二次处理
- 可直接用于下游 RAG、QA、后编辑等流程

`final_report.pdf`（人读导出为主）：

- 报告封面与运行元数据
- Token 用量字段（不含积分字段）
- Final markdown 渲染正文
- 原始 PDF 追加附录
- 从 review annotations 自动映射的 source-page 区域 callout 叠加
- 使用 `assets/logo-small.png` / `assets/logo.png` 品牌资源

### 任务目录文件作用说明

每次任务落盘在：

```text
data/jobs/<job_id>/
```

主要文件：

- `job.json`：任务权威状态（status、usage、artifact 路径、metadata）
- `events.jsonl`：追加式事件时间线（状态迁移、工具调用、导出统计、失败原因）
- `source.pdf`：用户上传原论文
- `mineru_full.md`：MinerU 解析得到的完整 markdown
- `mineru_content_list.json`：MinerU 结构化布局（page/text/bbox），用于 overlay 位置映射
- `mineru_result_raw.json`：MinerU 原始返回，便于审计与故障排查
- `annotations.json`：`pdf_annotate` 产生的规范化批注
- `agent_prompt.txt`：该任务实际使用的 prompt 快照（便于 parity 审计）
- `final_report.md`：最终 markdown 报告
- `final_report.pdf`：最终 PDF（报告正文 + 原文附录 + 批注 callout 叠加）
- `worker.stdout.log`：worker 标准输出日志（通常较少）
- `worker.stderr.log`：worker 错误/告警日志（排障优先查看）

可选文件（视模型行为而定）：

- `agent_final_output.txt`：模型最后一次原始 final output
- `agent_final_output_attempt_<n>.txt`：resume 模式下每轮 attempt 的原始 final output

---

## 外部服务

### MinerU（严格模式下必需）

1. 访问并注册：`https://mineru.net/`
2. 在控制台生成 API token
3. 在 `.env` 设置 `MINERU_API_TOKEN`

### PASA（建议用于高质量论文检索）

- 本仓库中文文档：`pasa/README.zh-CN.md`
- 本仓库本地部署文档：`pasa/README.md`
- 官方仓库：`https://github.com/bytedance/pasa`
- 官方 README：`https://github.com/bytedance/pasa/blob/main/README.md`
- 按官方文档 + 本仓库文档完成模型下载与服务部署
- 前往 `https://serper.dev/` 获取 token（PASA 的 Google Search 工作流通常需要）

对外提供兼容 HTTP 接口（可直接提供或通过 adapter 转接）：

- `POST /pasa/search`（本仓库默认）
- `POST /search`（可选兼容层）

然后在本项目通过 `PAPER_SEARCH_*` 与 `PAPER_READ_*` 配置接入。

---

## Prompt 一致性说明

本仓库保持 review agent 的 **system prompt** 与原始 DeepReviewer 逻辑对齐。  
初始 **user prompt** 也使用相同的完整 review prompt 字符串。

---

## 故障排查

- `RuntimeError: Agent finished without successful review_final_markdown_write`
  - 模型在完成最终写入前结束。
  - 请检查 `events.jsonl` 中阶段推进与工具调用记录。

- final-write 重复调用
  - 当前版本已包含 final 持久化后短路与 run cancel 保护。

- MinerU 超时/失败
  - 检查 token、网络可达性、端点配置是否正确。

- PASA 超时/失败
  - 检查服务健康状态与端点路径（`/pasa/search` vs `/search`）。

---

## 开发与自检

静态检查：

```bash
python -m compileall deepreview main.py
```

---

## 许可证

MIT License，详见 `LICENSE`。
第三方组件说明见 `THIRD_PARTY_NOTICES.md`。
