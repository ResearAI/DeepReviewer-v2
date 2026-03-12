<div align="center">

# <img src="assets/logo-small.png" alt="DeepReviewer Logo" width="36" valign="middle" /> DeepReviewer 2.0

[![ACL 2025](https://img.shields.io/badge/ACL-2025-1f6feb?style=for-the-badge)](https://aclanthology.org/2025.acl-long.1420/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

**面向论文评审的类人深度思考流程。**  
`PDF -> MinerU Markdown -> Review Agent Tool Loop -> Final Markdown -> Final PDF`

[快速开始](#快速开始) • [在线平台](#在线平台与-api) • [技术报告](#技术报告) • [配置说明](#配置说明) • [CLI 用法](#cli-用法) • [引用](#引用)

[English README](./README.md)

</div>

---

## 最新动态

- **[2026-03-04]** **DeepReviewer 2.0 在线平台**已上线，面向所有学者免费开放。立即体验：[deepscientist.cc](https://deepscientist.cc)。
- **[2026-03-04]** 注册后可使用 **DeepReviewer 2.0 API 服务**： [AI Review API Workflow](https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow)。
- **[2026-03-04]** 视频演示：[YouTube Demo](https://www.youtube.com/watch?v=mMg5XzcaDCw)。

---

## 功能特性

| 功能 | 说明 |
| :--- | :--- |
| 端到端评审 | 从 PDF 上传到最终 Markdown/PDF 报告，全流程异步执行。 |
| 工具驱动推理 | Agent 通过 `pdf_read_lines`、`pdf_annotate`、`paper_search` 等工具生成可追踪结果。 |
| 用量统计 | 每个任务记录 token 用量、工具调用次数和 paper-search 统计。 |
| 出版风格导出 | 生成 `final_report.pdf`，包含品牌封面、用量摘要、原文附录和批注叠加。 |

---

## 工作流程

每个评审任务会落盘到：

```text
data/jobs/<job_id>/
```

流程如下：

1. 提交论文 PDF。
2. 使用 MinerU v4 解析为 markdown 与布局元数据。
3. 构建运行时上下文并启动 review agent。
4. Agent 迭代调用工具（`pdf_read_lines`、`pdf_annotate`、`paper_search` 等）。
5. 通过 `review_final_markdown_write` 持久化最终 markdown。
6. 导出最终 PDF（含原文附录与批注 callout 叠加）。

---

## 在线平台与 API

- 在线平台：[https://deepscientist.cc](https://deepscientist.cc)
- API 文档（需注册）：[https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow](https://deepscientist.cc/docs/English/API/AI_Review_API_Workflow)
- 视频演示：[https://www.youtube.com/watch?v=mMg5XzcaDCw](https://www.youtube.com/watch?v=mMg5XzcaDCw)

---

## 技术报告

- PDF：[DeepReviewer-v2.pdf](./technical_report/DeepReviewer-v2.pdf)

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

最小可用配置：

```bash
# LLM（OpenAI-compatible）
BASE_URL=http://127.0.0.1:8004/v1
AGENT_MODEL=gpt-5.2
OPENAI_USE_RESPONSES_API=false
OPENAI_AGENTS_DISABLE_TRACING=1
# OPENAI_API_KEY=...  # 网关要求鉴权时再填写

# MinerU
MINERU_API_TOKEN=your_mineru_token

# 可选外部检索
PAPER_SEARCH_ENABLED=false

# 如需启用 PASA 或兼容检索服务
# PAPER_SEARCH_ENABLED=true
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_API_KEY=
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_HEALTH_ENDPOINT=/health
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

DeepReviewer 2.0 支持通用 OpenAI-compatible 服务与可选论文检索适配。

### LLM 配置

| 变量 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `BASE_URL` | 首选 OpenAI-compatible base URL | - |
| `OPENAI_BASE_URL` / `LLM_BASE_URL` | base URL 别名 | - |
| `OPENAI_API_KEY` / `API_KEY` / `LLM_API_KEY` | 网关鉴权密钥 | 可选 |
| `AGENT_MODEL` | 评审模型 | `gpt-5.2` |
| `OPENAI_USE_RESPONSES_API` | 提供方支持时可启用 Responses API | `false` |
| `OPENAI_AGENTS_DISABLE_TRACING` | 关闭本地网关 tracing 噪声 | 建议 `1` |
| `AGENT_RESUME_ATTEMPTS` | 恢复尝试次数（硬上限） | `2` |

### Finalization Gates

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `ENABLE_FINAL_GATES` | `false` | 启用最终写入门槛 |
| `MIN_PAPER_SEARCH_CALLS_FOR_PDF_ANNOTATE` | `3` | 进入密集批注前最少检索次数 |
| `MIN_PAPER_SEARCH_CALLS_FOR_FINAL` | `3` | 允许最终写入前最少检索次数 |
| `MIN_DISTINCT_PAPER_QUERIES_FOR_FINAL` | `3` | 最少不同检索 query 数 |
| `MIN_ANNOTATIONS_FOR_FINAL` | `10` | 最少批注数量 |
| `MIN_ENGLISH_WORDS_FOR_FINAL` | `0` | 最少英文词数（`0` 表示关闭） |
| `FORCE_ENGLISH_OUTPUT` | `true` | 强制英文输出 |

### MinerU 与检索服务

| 变量组 | 说明 |
| :--- | :--- |
| `MINERU_BASE_URL`, `MINERU_API_TOKEN`, `MINERU_MODEL_VERSION` | MinerU 解析配置 |
| `PAPER_SEARCH_*`, `PAPER_READ_*` | 可选检索/阅读服务配置 |

如果 `PAPER_SEARCH_ENABLED=false`，或 `PAPER_SEARCH_BASE_URL` 为空，或 `GET /health` 检测失败，`paper_search` 会返回 `status=not_started`，任务会以 retrieval-disabled mode 继续，而不是继续尝试外部检索。

---

## CLI 用法

| 命令 | 作用 |
| :--- | :--- |
| `python main.py submit --pdf /path/to/paper.pdf` | 提交新任务 |
| `python main.py status --job-id <job_id>` | 获取一次状态快照 |
| `python main.py watch --job-id <job_id> --interval 2 --timeout 1800` | 轮询直到完成/超时 |
| `python main.py result --job-id <job_id> --format all` | 拉取 markdown 与 pdf 结果 |

### 输出产物

- `data/jobs/<job_id>/final_report.md`
- `data/jobs/<job_id>/final_report.pdf`
- `data/jobs/<job_id>/events.jsonl`

`final_report.pdf` 包含：

- 最终 markdown 报告正文
- token 用量摘要（input/output/total/requests）
- 原文 PDF 附录页面
- 自动批注叠加（当 MinerU 行级 bbox 可用时）

---

## 外部服务

### MinerU（严格模式下必需）

1. 注册：[https://mineru.net/](https://mineru.net/)
2. 在控制台生成 API Token
3. 在 `.env` 设置 `MINERU_API_TOKEN`

### PASA（建议用于更强检索）

- 本地文档：`pasa/README.md`
- 中文文档：`pasa/README.zh-CN.md`
- 官方仓库：[https://github.com/bytedance/pasa](https://github.com/bytedance/pasa)
- Serper token（PASA Google 工作流通常需要）：[https://serper.dev/](https://serper.dev/)
- 如果未配置 PASA/外部检索服务，则该轮任务默认不会启动 search 功能，因此无法自动进行新颖性对比。

建议暴露兼容端点：

- `POST /pasa/search`（默认）
- `POST /search`（可选兼容路径）

---

## 故障排查

- `RuntimeError: Agent finished without successful review_final_markdown_write`
  - 模型在完成最终写入前结束。
  - 可检查 `events.jsonl` 的阶段推进和工具调用。

- MinerU 超时/失败
  - 检查 token 与端点连通性。

- PASA 超时/失败
  - 检查服务状态与端点路径（`/pasa/search` vs `/search`）。

---

## 引用

如果你在研究中使用了 DeepReview，请引用：

```bibtex
@inproceedings{zhu-etal-2025-deepreview,
    title = "{D}eep{R}eview: Improving {LLM}-based Paper Review with Human-like Deep Thinking Process",
    author = "Zhu, Minjun  and
      Weng, Yixuan  and
      Yang, Linyi  and
      Zhang, Yue",
    editor = "Che, Wanxiang  and
      Nabende, Joyce  and
      Shutova, Ekaterina  and
      Pilehvar, Mohammad Taher",
    booktitle = "Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)",
    month = jul,
    year = "2025",
    address = "Vienna, Austria",
    publisher = "Association for Computational Linguistics",
    url = "https://aclanthology.org/2025.acl-long.1420/",
    doi = "10.18653/v1/2025.acl-long.1420",
    pages = "29330--29355",
    ISBN = "979-8-89176-251-0"
}
```

---

## 社区与支持

**微信交流群**  
扫描下方二维码加入交流群。
<table>
  <tr>
    <td><img src="wechat/wechat1.jpg" width="220" alt="微信交流群 1"/></td>
    <td><img src="wechat/wechat2.jpg" width="220" alt="微信交流群 2"/></td>
  </tr>
</table>

---

## 许可证

MIT License，详见 `LICENSE`。  
第三方组件说明见 `THIRD_PARTY_NOTICES.md`。
