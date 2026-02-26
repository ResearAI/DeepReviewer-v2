# PASA 本地部署说明（DeepReviewer-2.0 适配版）

本目录提供的是一个可直接被 DeepReviewer 调用的 PASA 服务形态：

- 2 个 vLLM OpenAI 兼容推理服务（crawler + selector）
- 1 个 Flask 编排服务（`pasa_server.py`）
- 1 个统一启停脚本（`start_pasa_server.sh`）

DeepReviewer 通过以下配置访问 PASA：

- `PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001`
- `PAPER_SEARCH_ENDPOINT=/pasa/search`

---

## 官方参考

- PASA 官方仓库：<https://github.com/bytedance/pasa>
- 官方 README：<https://github.com/bytedance/pasa/blob/main/README.md>
- Crawler 模型：<https://huggingface.co/bytedance-research/pasa-7b-crawler>
- Selector 模型：<https://huggingface.co/bytedance-research/pasa-7b-selector>
- 数据集：<https://huggingface.co/datasets/CarlanLark/pasa-dataset>
- Serper API 申请：<https://serper.dev/>

---

## 1. 环境要求

- Linux + NVIDIA GPU
- Python 3.10+（建议 3.11）
- 可用 CUDA 环境（与 PyTorch / vLLM 版本匹配）
- 可访问 Hugging Face / arXiv / Serper 网络

---

## 2. 安装依赖

在你的 Python 环境中执行：

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

- `start_pasa_server.sh` 会在启动前检查 `import vllm`。
- `pasa/pasa/utils.py` 会在导入阶段加载本地 paper DB，请先配置好对应文件路径。

---

## 3. 下载模型与数据

### 3.1 下载 PASA 模型

示例（`huggingface-cli`）：

```bash
# crawler
huggingface-cli download bytedance-research/pasa-7b-crawler \
  --local-dir /data/models/pasa-7b-crawler

# selector
huggingface-cli download bytedance-research/pasa-7b-selector \
  --local-dir /data/models/pasa-7b-selector
```

然后在 `.pasa_env` 中把路径指向你的本地目录。

### 3.2 准备 PASA 检索数据（本地 paper DB）

当前代码默认会读取：

- `PASA_PAPER_DB`（例如 `cs_paper_2nd.zip`）
- `PASA_PAPER_ID`（例如 `id2paper.json`）

可从官方数据集页面获取并放到本地后，在 `.pasa_env` 中配置。

---

## 4. 配置 PASA 环境文件

建议先复制模板，再填写：

```bash
cd <repo_root>/pasa
cp .pasa_env.example .pasa_env.local
vim .pasa_env.local
```

说明：
- `pasa_server.py` 会按顺序读取：`$PASA_ENV_FILE` -> `.pasa_env.local` -> `.pasa_env`
- 推荐把本机私有配置放在 `.pasa_env.local`，不要提交到仓库

关键配置示例：

```bash
# GPU
PASA_GPU_ID=1

# Flask server
PASA_SERVER_HOST=0.0.0.0
PASA_SERVER_PORT=8001

# 模型路径（必须存在）
PASA_CRAWLER_PATH=/data/models/pasa-7b-crawler
PASA_SELECTOR_PATH=/data/models/pasa-7b-selector
PASA_PROMPTS_PATH=pasa/agent_prompt.json

# vLLM 服务地址
PASA_VLLM_HOST=127.0.0.1
PASA_VLLM_CRAWLER_PORT=8101
PASA_VLLM_SELECTOR_PORT=8102
PASA_VLLM_CRAWLER_URL=http://127.0.0.1:8101/v1
PASA_VLLM_SELECTOR_URL=http://127.0.0.1:8102/v1
PASA_VLLM_CRAWLER_MODEL_NAME=pasa-crawler
PASA_VLLM_SELECTOR_MODEL_NAME=pasa-selector

# Serper key（已改为环境变量读取，不再硬编码）
PASA_SERPER_API_KEY=your_serper_api_key
PASA_SERPER_SEARCH_URL=https://google.serper.dev/search

# 本地 paper DB（按实际路径填写）
PASA_PAPER_DB=/data/pasa/cs_paper_2nd.zip
PASA_PAPER_ID=/data/pasa/id2paper.json
```

---

## 5. 启动与停止

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

## 6. 验证服务

### 健康检查

```bash
curl http://127.0.0.1:8001/health
```

期望字段：

- `"status": "healthy"`
- `"crawler_ready": true`
- `"selector_ready": true`

### 搜索接口测试

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

### 自带测试脚本

```bash
cd <repo_root>/pasa
python test_pasa_decoupling.py
```

---

## 7. 接口说明

- `GET /`
- `GET /health`
- `POST /pasa/search`
- `POST /pasa/search_async`
- `GET /pasa/jobs/<job_id>`
- `GET /pasa/jobs/<job_id>/result`
- `DELETE /pasa/jobs/<job_id>`

---

## 8. 与 DeepReviewer 集成

在 `<repo_root>/.env` 中设置：

```bash
PAPER_SEARCH_BASE_URL=http://127.0.0.1:8001
PAPER_SEARCH_ENDPOINT=/pasa/search
PAPER_SEARCH_API_KEY=
```

---

## 9. 常见问题

1. `vllm` 导入失败
- 确认启动脚本使用的 Python 环境已安装 `vllm`。

2. 模型路径不存在
- 检查 `.pasa_env` 的 `PASA_CRAWLER_PATH` 与 `PASA_SELECTOR_PATH`。

3. `/health` 不健康
- 检查 `PASA_VLLM_*_MODEL_NAME` 是否与 vLLM `--served-model-name` 一致。
- 查看日志：`/tmp/pasa_vllm_crawler.log`、`/tmp/pasa_vllm_selector.log`、`/tmp/pasa_server.log`。

4. `/pasa/search` 报错或结果为空
- 确认 `PASA_SERPER_API_KEY` 已正确配置。
- 确认网络与代理配置可访问 `google.serper.dev` 与 arXiv。

5. 启动时导入 `pasa/pasa/utils.py` 失败
- 通常是 `PASA_PAPER_DB` 或 `PASA_PAPER_ID` 路径错误，修正后重启。
