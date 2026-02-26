# PASA Tool Decoupling Architecture (vLLM Edition)

> Legacy/internal migration note: this document contains historical paths and
> deployment examples from earlier environments. For OSS deployment, use
> `pasa/README.md` and `pasa/README.zh-CN.md` as the authoritative guides.

## 概述 Overview

PASA工具已解耦为**客户端-服务器架构**，并进一步升级为**vLLM 常驻推理**：
- crawler / selector 两个模型由 vLLM 常驻加载（同一张 GPU）
- PASA Flask 服务仅负责流程编排，通过 HTTP 调用 vLLM
- 目标：支持大批量并发请求，提高 GPU 利用率与吞吐

### 架构变更

**之前 (pasa_tool_heavy.py):**
```
DirectorAgent (MCP) → pasa_tool_heavy.py → 加载模型 → GPU推理 → 返回结果
                       ↑ 每次调用都加载模型(慢)
```

**现在 (vLLM 解耦架构):**
```
DirectorAgent (MCP) → pasa_tool.py (轻量级HTTP客户端)
                           ↓ HTTP请求
                     pasa_server.py (独立Flask编排服务)
                           ↓ HTTP请求
               vLLM(OpenAI Server) crawler + selector (模型常驻GPU)
                      GPU推理 → 返回结果
```

---

## 文件结构 File Structure

```
pasa/
├── pasa_server.py              # Flask服务 (流程编排,通过HTTP调用vLLM)
├── .pasa_env                   # vLLM+PASA配置 (GPU、模型路径、端口、代理)
├── start_pasa_server.sh        # 启动脚本 (启动2个vLLM + 1个Flask)
├── test_pasa_decoupling.py     # 测试脚本
└── pasa/                       # PASA pipeline代码 (含 vllm_agent)

<project_root>/
└── .env                        # 主配置 (PASA服务URL)
```

---

## 配置文件说明 Configuration Files

### 1. `.pasa_env` (vLLM+PASA 配置)

**位置:** `pasa/.pasa_env`

**用途:** 配置 vLLM+PASA 的 GPU、模型路径、端口、网络和代理

**关键配置项:**
```bash
# GPU设备（按要求使用 1 号卡）
PASA_GPU_ID=1

# 模型路径 (必须存在)
PASA_CRAWLER_PATH=/path/to/pasa-7b-crawler
PASA_SELECTOR_PATH=/path/to/pasa-7b-selector
PASA_PROMPTS_PATH=pasa/agent_prompt.json

# 服务器网络配置
PASA_SERVER_HOST=0.0.0.0
PASA_SERVER_PORT=8001

# vLLM OpenAI server
PASA_VLLM_CRAWLER_URL=http://127.0.0.1:8101/v1
PASA_VLLM_SELECTOR_URL=http://127.0.0.1:8102/v1
PASA_VLLM_CRAWLER_MODEL_NAME=pasa-crawler
PASA_VLLM_SELECTOR_MODEL_NAME=pasa-selector

# arXiv访问代理
http_proxy=http://127.0.0.1:58887
https_proxy=http://127.0.0.1:58887
```

**修改方法:**
```bash
vim pasa/.pasa_env
# 或
nano pasa/.pasa_env
```

### 2. `.env` (主配置文件)

**位置:** `<project_root>/.env`

**用途:** 配置后端工具连接到PASA服务器的URL

**关键配置项:**
```bash
# PASA服务配置 (后端工具使用)
PASA_SERVICE_URL=http://localhost:8001
```

**说明:**
- 如果PASA服务器在远程机器,修改为: `PASA_SERVICE_URL=http://remote-host:8001`
- 此URL被`pasa_tool.py`用于HTTP请求

---

## 使用方法 Usage

### 1. 启动 vLLM + PASA 服务

#### 方式A: 前台运行 (推荐用于调试)

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh
```

**特点:**
- 日志实时输出到终端
- Ctrl+C 可停止服务器
- 适合调试和测试

#### 方式B: 后台运行 (推荐用于生产)

```bash
cd <repo_root>/pasa
bash start_pasa_server.sh --background
```

**特点:**
- 服务器在后台运行
- 日志保存到 `/tmp/pasa_server.log`
- PID保存到 `/tmp/pasa_server.pid`

**查看日志:**
```bash
tail -f /tmp/pasa_server.log
```

**停止服务器:**
```bash
# 方法1: 使用kill命令
kill $(cat /tmp/pasa_server.pid)
rm /tmp/pasa_server.pid

# 方法2: 手动查找进程
ps aux | grep pasa_server
kill <PID>
```

### 2. 验证服务器状态

```bash
# 检查健康状态
curl http://localhost:8001/health

# 预期输出 (模型已加载):
{
  "status": "healthy",
  "models_loaded": true,
  "crawler_ready": true,
  "selector_ready": true,
  "error": null,
  "gpu": "1",
  "crawler_path": "/path/to/pasa-7b-crawler",
  "selector_path": "/path/to/pasa-7b-selector",
  "prompts_path": "pasa/agent_prompt.json"
}
```

### 3. 测试PASA搜索功能

```bash
# 使用curl测试搜索API
curl -X POST http://localhost:8001/pasa/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Papers about contrastive learning for representation learning",
    "expand_layers": 2,
    "search_queries": 5,
    "search_papers": 10,
    "expand_papers": 20,
    "threads_num": 0
  }'

# 预期输出 (JSON数组):
[
  {
    "title": "SimCLR: A Simple Framework for Contrastive Learning",
    "link": "2002.05709",
    "snippet": "This paper presents SimCLR: a simple framework for contrastive learning..."
  },
  ...
]
```

### 4. 在MCP工具中使用

**`tools/pasa_tool.py` 会自动被 DirectorAgent 调用**,无需手动操作:

```python
# DirectorAgent内部调用示例
results = await pasa_search(
    query="Papers on efficient transformers"
)
# pasa_tool.py透明地转发请求到pasa_server.py
```

---

## API文档 API Documentation

### 服务器端点 Server Endpoints

#### 1. `GET /` - 欢迎页面

**响应:**
```json
{
  "service": "PASA Server",
  "version": "1.0.0",
  "status": "running",
  "description": "Independent Flask service for PASA paper search",
  "endpoints": {...}
}
```

#### 2. `GET /health` - 健康检查

**响应:**
```json
{
  "status": "healthy" | "unhealthy",
  "models_loaded": true | false,
  "crawler_loaded": true | false,
  "selector_loaded": true | false,
  "error": null | "error message",
  "gpu": "0",
  "crawler_path": "/path/to/model",
  "selector_path": "/path/to/model",
  "prompts_path": "/path/to/prompts"
}
```

**状态码:**
- `200` - 服务健康且模型已加载
- `503` - 服务不健康或模型未加载

#### 3. `POST /pasa/search` - 论文搜索

**请求体:**
```json
{
  "query": "Natural language research query (REQUIRED)",
  "expand_layers": 2,           // Optional, default: 2
  "search_queries": 5,          // Optional, default: 5
  "search_papers": 10,          // Optional, default: 10
  "expand_papers": 20,          // Optional, default: 20
  "threads_num": 0              // Optional, default: 0
}
```

**响应体:**
```json
[
  {
    "title": "Paper Title",
    "link": "arxiv_id",
    "snippet": "Abstract text"
  }
]
```

**状态码:**
- `200` - 搜索成功
- `400` - 请求参数错误
- `503` - 模型未加载
- `500` - 内部服务器错误

---

## 常见问题 Troubleshooting

### 问题1: 模型加载失败

**症状:**
```
❌ Failed to load PASA models: FileNotFoundError
```

**解决方法:**
1. 检查`.pasa_env`中的模型路径是否存在:
   ```bash
   ls /path/to/pasa-7b-crawler
   ls /path/to/pasa-7b-selector
   ls <repo_root>/pasa/pasa/agent_prompt.json
   ```

2. 修正`.pasa_env`中的路径并重启服务器

### 问题2: GPU不可用

**症状:**
```
RuntimeError: CUDA out of memory
```

**解决方法:**
1. 检查GPU使用情况:
   ```bash
   nvidia-smi
   ```

2. 修改`.pasa_env`中的`PASA_GPU_ID`到空闲GPU:
   ```bash
   PASA_GPU_ID=1
   ```

3. 重启PASA服务器

### 问题3: 端口占用

**症状:**
```
OSError: [Errno 98] Address already in use
```

**解决方法:**
1. 查找占用端口的进程:
   ```bash
   lsof -i :8001
   ```

2. 停止该进程或修改`.pasa_env`中的端口:
   ```bash
   PASA_SERVER_PORT=8002
   ```

3. 同时更新主`.env`中的`PASA_SERVICE_URL`:
   ```bash
   PASA_SERVICE_URL=http://localhost:8002
   ```

### 问题4: pasa_tool.py连接失败

**症状:**
```
RuntimeError: PASA server at http://localhost:8001 is not healthy or not reachable
```

**解决方法:**
1. 确认PASA服务器正在运行:
   ```bash
   curl http://localhost:8001/health
   ```

2. 检查`.env`中的`PASA_SERVICE_URL`是否正确

3. 检查防火墙和网络连接

### 问题5: 代理导致arXiv无法访问

**症状:**
```
Failed to download arxiv paper: Connection error
```

**解决方法:**
1. 检查`.pasa_env`中的代理配置:
   ```bash
   http_proxy=http://127.0.0.1:58887
   https_proxy=http://127.0.0.1:58887
   ```

2. 确认代理服务器正在运行

3. 如果不需要代理,注释掉这两行:
   ```bash
   # http_proxy=
   # https_proxy=
   ```

---

## 性能对比 Performance Comparison

### 旧架构 (pasa_tool_heavy.py)

| 指标 | 数值 |
|------|------|
| 首次调用延迟 | 60-180秒 (包含模型加载) |
| 后续调用延迟 | 30-120秒 (搜索时间) |
| GPU占用时长 | 调用期间 |
| 并发能力 | 低 (受MCP进程限制) |

### 新架构 (解耦后)

| 指标 | 数值 |
|------|------|
| 服务器启动时间 | 60-180秒 (一次性) |
| 每次调用延迟 | 30-120秒 (仅搜索时间) |
| GPU占用时长 | 服务器运行期间 (常驻) |
| 并发能力 | 高 (Flask多线程) |

### 优势总结

✅ **启动时间:** 模型仅加载一次,后续调用无需等待
✅ **响应速度:** 首次调用速度提升 2-6倍
✅ **资源隔离:** GPU使用独立管理,不影响主进程
✅ **可扩展性:** 可部署到专用GPU服务器
✅ **易维护性:** 服务器独立重启,不影响主系统

---

## 架构优势 Architecture Benefits

### 1. 性能提升
- **模型预加载:** 启动时加载一次,后续请求无需重复加载
- **首次调用:** 延迟从60-180秒降至30-120秒
- **并发支持:** Flask多线程处理,支持多个并发请求

### 2. 资源隔离
- **独立进程:** PASA服务器运行在独立进程,不占用主进程资源
- **GPU管理:** GPU使用可独立监控和控制
- **故障隔离:** 服务器崩溃不影响主系统

### 3. 灵活部署
- **远程部署:** 可部署到专用GPU服务器,主系统无需GPU
- **负载均衡:** 可启动多个服务器实例实现负载均衡
- **独立扩展:** 可根据需要独立扩展PASA服务能力

### 4. 易于维护
- **配置分离:** `.pasa_env`独立管理PASA相关配置
- **独立重启:** 服务器可独立重启,不影响主系统
- **日志隔离:** 独立日志文件,便于调试

### 5. 架构一致性
- **统一模式:** 与`arxiv_tool.py`等工具保持一致的HTTP调用模式
- **标准接口:** REST API标准化,易于集成和测试

---

## 迁移指南 Migration Guide

### 从pasa_tool_heavy.py迁移到新架构

**步骤1: 配置环境**
```bash
# 1. 编辑.pasa_env配置GPU和模型路径
vim pasa/.pasa_env

# 2. 验证主.env中的PASA_SERVICE_URL
grep PASA_SERVICE_URL <project_root>/.env
```

**步骤2: 启动PASA服务器**
```bash
# 后台启动
cd <repo_root>/pasa
bash start_pasa_server.sh --background

# 验证启动成功
curl http://localhost:8001/health
```

**步骤3: 更新MCP工具配置**

如果使用自定义MCP工具管理器,将`pasa_tool_heavy.py`替换为`pasa_tool.py`:

```python
# 旧版本
from backend.tools.pasa_tool_heavy import mcp as pasa_mcp

# 新版本
from backend.tools.pasa_tool import mcp as pasa_mcp
```

**步骤4: 测试验证**
```bash
# 测试服务器API
curl -X POST http://localhost:8001/pasa/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test query"}'

# 在DirectorAgent中测试MCP工具调用
# (无需修改调用代码,透明切换)
```

---

## 开发者注意事项 Developer Notes

### 代码修改建议

#### 修改PASA服务器配置
如需修改默认参数,编辑`pasa_server.py`的`run_pasa_search`函数:

```python
def run_pasa_search(
    query: str,
    expand_layers: int = 2,        # 修改此处
    search_queries: int = 5,       # 修改此处
    search_papers: int = 10,       # 修改此处
    expand_papers: int = 20,       # 修改此处
    threads_num: int = 0,          # 修改此处
) -> List[Dict[str, str]]:
    ...
```

#### 修改MCP工具超时时间
如需修改HTTP请求超时,编辑`pasa_tool.py`:

```python
PASA_REQUEST_TIMEOUT = 300.0  # 修改为更长或更短的超时时间(秒)
```

### 日志调试

**PASA服务器日志级别:**
```python
# 在pasa_server.py中修改
logging.basicConfig(
    level=logging.DEBUG,  # 改为DEBUG获取详细日志
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

**pasa_tool.py日志:**
```python
# MCP工具日志通过FastMCP框架管理
# 查看日志需在运行时设置环境变量
export LOG_LEVEL=DEBUG
```

---

## 维护清单 Maintenance Checklist

### 日常维护
- [ ] 定期检查PASA服务器运行状态: `curl http://localhost:8001/health`
- [ ] 监控GPU使用情况: `nvidia-smi`
- [ ] 检查日志文件大小: `ls -lh /tmp/pasa_server.log`
- [ ] 验证磁盘空间充足 (模型缓存可能占用大量空间)

### 定期维护
- [ ] 每月重启PASA服务器,清理缓存
- [ ] 检查模型文件完整性
- [ ] 更新PASA库依赖: `pip install --upgrade pasa`
- [ ] 备份`.pasa_env`配置文件

### 故障响应
- [ ] 服务器崩溃时查看日志: `tail -100 /tmp/pasa_server.log`
- [ ] GPU错误时检查: `dmesg | grep -i nvidia`
- [ ] 网络问题时检查代理配置
- [ ] 记录错误信息用于debug

---

## 相关文档 Related Documentation

- **主项目文档:** `<project_root>/CLAUDE.md`
- **工具目录:** `<repo_root>/pasa/`
- **PASA官方文档:** (如有,添加链接)

---

## 更新日志 Changelog

### 2025-01-XX - v1.0.0 (解耦架构)
- ✅ 创建独立的Flask服务器 (`pasa_server.py`)
- ✅ 创建轻量级MCP工具 (`pasa_tool.py`)
- ✅ 配置文件分离 (`.pasa_env` 和 `.env`)
- ✅ 启动脚本自动化 (`start_pasa_server.sh`)
- ✅ 完整的API文档和故障排除指南
- 🔄 保留旧版本 (`pasa_tool_heavy.py`) 作为备份

---

## 反馈与支持 Feedback & Support

如遇到问题或有改进建议,请:
1. 检查本文档的"常见问题"章节
2. 查看PASA服务器日志: `/tmp/pasa_server.log`
3. 联系项目维护者

---

**最后更新:** 2025-01-XX
**作者:** Claude Code
**版本:** 1.0.0
