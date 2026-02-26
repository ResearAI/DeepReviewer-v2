# PASA 本地安装与启动指南（DeepReviewer-2.0）

本文档是 `pasa/` 目录的中文运行说明，基于你当前仓库里的实际实现：

- `start_pasa_server.sh`：一键启动脚本
- `pasa_server.py`：Flask 编排服务
- `pasa/pasa/utils.py`：检索与 arXiv 工具（已改为环境变量读取 Serper key）

该服务启动后，DeepReviewer 使用以下配置调用：

- `PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001`
- `PAPER_SEARCH_ENDPOINT=/pasa/search`

---

## 0. 官方资料

- 官方仓库：<https://github.com/bytedance/pasa>
- 官方 README：<https://github.com/bytedance/pasa/blob/main/README.md>
- Crawler 模型：<https://huggingface.co/bytedance-research/pasa-7b-crawler>
- Selector 模型：<https://huggingface.co/bytedance-research/pasa-7b-selector>
- 数据集：<https://huggingface.co/datasets/CarlanLark/pasa-dataset>
- Serper API 申请：<https://serper.dev/>

---

## 1. 架构说明（与你本地代码一致）

当前 `pasa/` 目录是“解耦 + 常驻推理”结构：

1. `vLLM crawler`：OpenAI 兼容服务（默认 `127.0.0.1:8101/v1`）
2. `vLLM selector`：OpenAI 兼容服务（默认 `127.0.0.1:8102/v1`）
3. `pasa_server.py`：Flask 服务（默认 `0.0.0.0:8001`）

`start_pasa_server.sh` 会统一负责：

- 启动两个 vLLM 服务
- 等待它们健康
- 再启动 Flask 编排服务

---

## 2. 环境要求

- Linux + NVIDIA GPU
- Python 3.10+（建议 3.11）
- CUDA 环境可用，版本与 PyTorch / vLLM 匹配
- 能访问 Hugging Face / arXiv / Serper

---

## 3. 安装依赖

在你要运行 PASA 的 Python 环境中执行：

```bash
cd <repo_root>/pasa
pip install --upgrade pip
pip install \
  torch transformers \
  vllm "openai>=1.52,<1.76" \
  flask flask-cors \
  requests httpx arxiv \
  beautifulsoup4 lxml
```

说明：

- `start_pasa_server.sh` 启动前会检查 `import vllm`，不通过会直接退出。
- `pasa/pasa/utils.py` 在导入时会读取本地 paper DB，路径必须存在。

---

## 4. 下载模型与数据

### 4.1 下载 PASA 模型

示例（`huggingface-cli`）：

```bash
# crawler
huggingface-cli download bytedance-research/pasa-7b-crawler \
  --local-dir /data/models/pasa-7b-crawler

# selector
huggingface-cli download bytedance-research/pasa-7b-selector \
  --local-dir /data/models/pasa-7b-selector
```

### 4.2 准备本地 paper DB（当前代码会加载）

`pasa/pasa/utils.py` 默认读取：

- `PASA_PAPER_DB`（例如 `cs_paper_2nd.zip`）
- `PASA_PAPER_ID`（例如 `id2paper.json`）

你需要把对应文件下载并放到本地路径，然后在 `.pasa_env` 中配置。

---

## 5. 配置 PASA 环境文件

建议先复制模板，再编辑：

```bash
cd <repo_root>/pasa
cp .pasa_env.example .pasa_env.local
vim .pasa_env.local
```

说明：
- `pasa_server.py` 会按顺序读取：`$PASA_ENV_FILE` -> `.pasa_env.local` -> `.pasa_env`
- 推荐把本机私有配置放在 `.pasa_env.local`，避免误提交

关键项（最小可用）：

```bash
# GPU
PASA_GPU_ID=1

# Flask
PASA_SERVER_HOST=0.0.0.0
PASA_SERVER_PORT=8001

# 模型路径（必须存在）
PASA_CRAWLER_PATH=/data/models/pasa-7b-crawler
PASA_SELECTOR_PATH=/data/models/pasa-7b-selector
PASA_PROMPTS_PATH=pasa/agent_prompt.json

# vLLM
PASA_VLLM_HOST=127.0.0.1
PASA_VLLM_CRAWLER_PORT=8101
PASA_VLLM_SELECTOR_PORT=8102
PASA_VLLM_CRAWLER_URL=http://127.0.0.1:8101/v1
PASA_VLLM_SELECTOR_URL=http://127.0.0.1:8102/v1
PASA_VLLM_CRAWLER_MODEL_NAME=pasa-crawler
PASA_VLLM_SELECTOR_MODEL_NAME=pasa-selector

# Serper（已改为环境变量配置，不再硬编码在代码中）
PASA_SERPER_API_KEY=your_serper_api_key
PASA_SERPER_SEARCH_URL=https://google.serper.dev/search

# 本地 paper DB（必须指向真实文件）
PASA_PAPER_DB=/data/pasa/cs_paper_2nd.zip
PASA_PAPER_ID=/data/pasa/id2paper.json
```

---

## 6. 启动 / 停止

### 前台启动（调试推荐）

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh
```

### 后台启动（常驻推荐）

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --background
```

### 停止所有进程

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --stop
```

### 重启

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --restart
```

---

## 7. 健康检查与接口验证

### 7.1 健康检查

```bash
curl http://127.0.0.1:8001/health
```

关键字段应为：

- `"status": "healthy"`
- `"crawler_ready": true`
- `"selector_ready": true`

### 7.2 搜索接口

```bash
curl -X POST http://127.0.0.1:8001/pasa/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Papers about contrastive learning",
    "expand_layers": 1,
    "search_queries": 2,
    "search_papers": 5,
    "expand_papers": 5,
    "threads_num": 0
  }'
```

### 7.3 自带测试脚本

```bash
cd <repo_root>/pasa
python test_pasa_decoupling.py
```

---

## 8. 对外接口列表

- `GET /`
- `GET /health`
- `POST /pasa/search`
- `POST /pasa/search_async`
- `GET /pasa/jobs/<job_id>`
- `GET /pasa/jobs/<job_id>/result`
- `DELETE /pasa/jobs/<job_id>`

---

## 9. 与 DeepReviewer 对接

在 `<repo_root>/.env` 中设置：

```bash
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_API_KEY=
```

---

## 10. 常见问题

1. `vllm` 导入失败
- 运行脚本的 Python 环境未安装 vLLM，补装后重试。

2. 模型路径不存在
- 检查 `.pasa_env` 中 `PASA_CRAWLER_PATH` / `PASA_SELECTOR_PATH`。

3. `/health` 不健康
- 检查 `PASA_VLLM_*_MODEL_NAME` 是否与 vLLM `--served-model-name` 对齐。
- 查看日志：`/tmp/pasa_vllm_crawler.log`、`/tmp/pasa_vllm_selector.log`、`/tmp/pasa_server.log`。

4. `/pasa/search` 返回空或报错
- 重点检查 `PASA_SERPER_API_KEY` 是否正确。
- 检查代理与网络是否能访问 `google.serper.dev` 和 arXiv。

5. 启动时在 `utils.py` 导入阶段崩溃
- 通常是 `PASA_PAPER_DB` / `PASA_PAPER_ID` 文件路径不正确。
