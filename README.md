# Internal QA Bot

这是一个面向公司内部落地的知识问答机器人。系统先判断问题应该走普通对话、确定性规则、业务系统查询还是知识库证据检索，再把请求交给对应的处理链路。

知识库检索采用 Agentic RAG / Direct Corpus Interaction 风格：先搜索原始语料和精确线索，再融合 SQLite FTS5/BM25、TF-IDF、轻量向量召回和可选 Qdrant embedding 向量召回，最后读取原文上下文并基于证据回答。

## 功能

- 文档入库：支持粘贴 FAQ、制度说明、流程文档、操作手册等文本
- 文本切分：按段落优先，长文本使用滑动窗口
- 问题分层：先判断问题应走寒暄、确定性规则、业务系统查询还是知识库证据检索
- 普通聊天：寒暄、帮助和通用问题优先走 LLM 普通对话；模型不可用时回退到内置说明
- 直接语料检索：从用户问题抽取精确线索，在原始知识库片段中搜索命中项
- SQLite FTS5/BM25：文档入库时同步维护全文索引，作为关键词检索和弱排序通道
- 弱线索补充：使用 TF-IDF 作为辅助召回，不把系统限制在单次语义 top-k
- 向量辅助召回：可选接入 Qdrant + embedding 做真实向量检索；未配置时使用轻量 hash vector recall 兜底
- 上下文核验：命中后读取同一文档相邻片段，避免只看孤立 chunk
- Agentic RAG 问答：基于检索证据、原文上下文和会话历史构造回答
- 来源追踪：回答下方展示检索轨迹、命中文档、证据线索、相关度和上下文
- 会话记录：SQLite 保存多轮对话
- 模型适配：通过 OpenAI Python SDK 调用 OpenAI-compatible Chat Completions 接口；未配置 `OPENAI_API_KEY` 时返回检索摘要

## 快速开始

```bash
cd internal-qa-bot
python3 -m pip install -e .
python3 -m app.server
```

浏览器打开：

```text
http://127.0.0.1:8000
```

首次启动会自动导入 `data/knowledge/sample_faq.md` 作为示例知识库。

FastAPI 会自动提供接口文档：

```text
http://127.0.0.1:8000/docs
```

## 配置聊天模型

复制环境变量模板：

```bash
cp .env.example .env
```

根据你使用的模型服务设置：

```env
OPENAI_API_KEY=你的 API Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

启动时会自动读取项目根目录下的 `.env`。真实 `.env` 已被 `.gitignore` 忽略，不要提交到 GitHub；提交 `.env.example` 即可。系统通过 OpenAI Python SDK 发起请求。如果使用其他兼容 OpenAI Chat Completions 的服务，只需要修改 `OPENAI_BASE_URL` 和 `OPENAI_MODEL`。

DeepSeek 聊天模型示例：

```env
OPENAI_API_KEY=你的 DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

## 配置管理 Token

默认不配置 `ADMIN_API_TOKEN` 时，管理写接口不鉴权，方便本地开发。

如果配置：

```env
ADMIN_API_TOKEN=替换成足够随机的管理密钥
```

以下写接口会要求带管理 token：

- `POST /api/documents`
- `POST /api/documents/batch`
- `POST /api/vector-index/rebuild`

请求可以使用 Bearer token：

```http
Authorization: Bearer 替换成足够随机的管理密钥
```

也可以使用：

```http
X-Admin-Token: 替换成足够随机的管理密钥
```

前端“新增文档”面板里的“管理 Token”会随入库请求发送 `X-Admin-Token`。

## 配置 Qdrant 向量检索

Qdrant 是可选增强，不配置时系统仍会使用 SQLite FTS5/BM25、TF-IDF 和轻量 hash vector recall。配置后，Qdrant 负责 embedding 向量相似度检索，SQLite 仍然是文档和 chunk 原文的主存储。

启动本地 Qdrant：

```bash
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

如果使用远程 OpenAI-compatible embedding，在 `.env` 中配置：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=你的 embedding API Key
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536

QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=internal_qa_chunks
```

如果使用本地 sentence-transformers embedding，例如 `BAAI/bge-m3`：

```bash
python3 -m pip install -e ".[local-embedding]"
```

```env
OPENAI_API_KEY=你的 DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024

QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=internal_qa_chunks
```

`BAAI/bge-m3` 首次启动会下载模型；如果之前用 1536 维 embedding 建过 Qdrant collection，需要删除后用 1024 维重建：

```bash
curl -X DELETE http://127.0.0.1:6333/collections/internal_qa_chunks
```

SQLite 继续保存文档、chunk 原文和聊天记录；Qdrant 只保存 chunk embedding、`chunk_id`、`document_id`、标题和位置等 metadata。新增文档时，系统会先写 SQLite，再尽力同步 Qdrant；如果 Qdrant 不可用，文档入库不会失败，只会在响应中标记向量索引未完成。

### 验证 Qdrant

确认服务在线：

```bash
curl http://127.0.0.1:6333/collections
```

新增文档后，接口响应中应该出现：

```json
{
  "vector_indexed": true,
  "vector_index_status": "ok"
}
```

查看 Qdrant collection：

```bash
curl http://127.0.0.1:6333/collections/internal_qa_chunks
```

查看写入的 points：

```bash
curl -s -X POST http://127.0.0.1:6333/collections/internal_qa_chunks/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit":5,"with_payload":true,"with_vectors":false}'
```

问答时，如果 Qdrant 参与召回，`retrieval_trace` 会包含：

```json
{
  "name": "qdrant",
  "label": "Qdrant embedding 向量召回",
  "matched_sources": 1
}
```

`sources[].evidence` 也会出现 `Qdrant 向量召回`。

注意：只有配置 Qdrant 后新增的文档会自动写入 Qdrant。已有 SQLite 文档可以通过 `POST /api/vector-index/rebuild` 批量同步。

## API

### 健康检查

```http
GET /api/health
```

### 新增知识库文档

```http
POST /api/documents
Content-Type: application/json
X-Admin-Token: 可选，配置 ADMIN_API_TOKEN 后必填

{
  "title": "差旅报销制度",
  "content": "员工完成出差后，需要在 10 个工作日内提交差旅报销申请..."
}
```

### 查看文档

```http
GET /api/documents
```

### 批量导入知识库文档

前端支持一次选择多个 `.md` 或 `.txt` 文件，浏览器会读取文件内容并调用批量入库接口。文件名会作为文档标题，去掉 `.md`、`.markdown` 或 `.txt` 后缀。

接口也可以直接接收 JSON：

```http
POST /api/documents/batch
Content-Type: application/json
X-Admin-Token: 可选，配置 ADMIN_API_TOKEN 后必填

{
  "documents": [
    {
      "title": "VPN 申请流程",
      "content": "VPN 申请需要填写权限申请单..."
    },
    {
      "title": "会议室预约制度",
      "content": "会议室预约需要提前 1 个工作日..."
    }
  ]
}
```

响应会返回每个文档的入库和向量同步状态：

```json
{
  "documents_total": 2,
  "documents_succeeded": 2,
  "documents_failed": 0,
  "results": [
    {
      "index": 0,
      "status": "ok",
      "id": 1,
      "title": "VPN 申请流程",
      "chunk_count": 1,
      "vector_indexed": false,
      "vector_index_status": "disabled"
    }
  ]
}
```

### 重建向量索引

已有 SQLite 文档需要补写 Qdrant 时，可以触发批量重建：

```http
POST /api/vector-index/rebuild
X-Admin-Token: 可选，配置 ADMIN_API_TOKEN 后必填
```

响应会包含每个文档的同步状态：

```json
{
  "vector_indexed": true,
  "vector_index_status": "ok",
  "documents_total": 2,
  "documents_succeeded": 2,
  "documents_failed": 0,
  "results": [
    {
      "document_id": 2,
      "title": "VPN 申请流程",
      "vector_indexed": true,
      "vector_index_status": "ok"
    }
  ]
}
```

### 问答

```http
POST /api/chat
Content-Type: application/json

{
  "question": "差旅报销需要哪些材料？",
  "session_id": "可选，会话 ID"
}
```

响应会包含：

```json
{
  "answer": "基于证据生成的回答",
  "route": {
    "layer": "knowledge_evidence",
    "handler": "dci_retrieval"
  },
  "retrieval_trace": [
    {"name": "keyword", "label": "原文关键词命中", "matched_sources": 1},
    {"name": "bm25", "label": "SQLite FTS5/BM25 全文召回", "matched_sources": 1},
    {"name": "qdrant", "label": "Qdrant embedding 向量召回", "matched_sources": 1},
    {"name": "context", "label": "相邻 chunk 上下文核验", "matched_sources": 1}
  ],
  "sources": [
    {
      "title": "会议室预约制度",
      "evidence": ["原文命中 `会议室预约`", "Qdrant 向量召回"]
    }
  ]
}
```

## 项目结构

```text
internal-qa-bot/
  app/
    chatbot.py      # Chatbot fallback：轻量向量召回内置说明
    chunker.py      # 文本切分
    problem_layers.py # 问题分层：chatbot / rule_engine / tool_call / dci_retrieval
    retriever.py    # 证据检索：DCI 精确线索 + FTS/BM25 + TF-IDF + vector recall + 上下文读取
    vector_retriever.py # 零依赖 hash vector recall，可替换为 embedding/向量库
    embedding.py    # OpenAI-compatible / local sentence-transformers embedding
    qdrant_index.py # Qdrant 向量索引适配
    llm.py          # 模型调用和 fallback
    rag.py          # RAG 编排
    storage.py      # SQLite 持久化
    server.py       # FastAPI HTTP API 和静态资源服务
  data/knowledge/   # 示例知识库
  static/           # 前端页面
  tests/            # 单元测试
```

## 测试

```bash
python3 -m unittest discover -s tests
```

## 工作流程

1. 管理员上传知识库文档，系统将文本切分为 chunk。
2. 用户提问时，系统先做问题分层：普通聊天和帮助问题走 LLM，高风险或负责人介入诉求走规则，审批/工单/报销/考勤等状态问题走业务工具，明确命中制度/流程/FAQ/操作手册等内部知识问题才走知识库检索。
3. 普通聊天层优先使用模型回答；模型不可用时，使用轻量向量召回匹配能力说明、使用方式和证据优先原则。
4. 进入知识库证据层后，系统从问题中抽取关键词、短语和数字等精确线索。
5. 检索器直接扫描原始知识库 chunk，找出命中线索的文档片段，相当于在应用内提供 `grep/read_context` 能力。
6. 系统再融合 SQLite FTS5/BM25、TF-IDF 弱线索、Qdrant embedding 向量召回和轻量向量语义召回，补足口语化、同义表达和措辞差异。
7. 命中片段后读取同一文档的相邻 chunk，检查原文上下文。
8. 模型只基于检索证据和上下文回答，并返回来源、证据线索和相关度。
9. 会话 ID 绑定历史消息，支持多轮上下文扩展。

## 存储分工

SQLite 是主数据源：

```text
documents   原始文档
chunks      切分后的文本片段
chunks_fts  SQLite FTS5 全文索引，用于 BM25
messages    多轮会话记录
```

Qdrant 是可选向量索引：

```text
vector      chunk embedding
payload     chunk_id / document_id / title / position
```

检索时，Qdrant 返回 `chunk_id`，系统再回 SQLite 读取 chunk 原文和相邻上下文。这样 Qdrant 不承担主数据库职责，只负责语义相似度召回。

## 检索设计

- 系统优先保证可控召回和证据链，而不是只依赖 embedding + 向量数据库。
- 向量检索适合语义近似召回，但内部制度和流程经常需要精确条款、数字、角色、系统名称、有效期和例外条件。
- 单次 top-k 相似度召回如果漏掉关键条款，后面的模型很难补救；所以这里采用直接语料交互，让系统能先搜原文、再读上下文、最后回答。
- embedding + 向量数据库适合作为辅助召回通道，但不作为唯一检索接口。
- 当前模型调用走 OpenAI-compatible 协议，方便切换不同供应商。

当前混合检索通道：

```text
keyword       原文精确线索命中
bm25          SQLite FTS5/BM25 全文召回
tfidf         Python TF-IDF 弱线索召回
qdrant        embedding 向量召回，可选
vector        轻量 hash vector fallback
context       同文档相邻 chunk 上下文核验
```

## Embedding Provider

项目支持两种 embedding provider：

```text
openai  调用 OpenAI-compatible /v1/embeddings 接口
local   使用 sentence-transformers 本地模型
```

`openai` 适合使用 OpenAI、兼容 OpenAI embeddings 的云服务。`local` 适合 DeepSeek 只做聊天、本地用 `BAAI/bge-m3` 做向量。

本地 embedding 默认不安装，避免普通安装拉取 PyTorch 等大依赖。需要时执行：

```bash
python3 -m pip install -e ".[local-embedding]"
```

`BAAI/bge-m3` dense embedding 维度是 1024，所以 `.env` 中要设置：

```env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
```

如果切换过不同 embedding 维度，需要删除旧 Qdrant collection 后重新入库：

```bash
curl -X DELETE http://127.0.0.1:6333/collections/internal_qa_chunks
```

## 问题分层设计

这个项目不把所有内部问题都塞进 RAG，而是先做路由：

```text
普通寒暄、能力说明、帮助、外部通用问题
-> llm_chat + chatbot fallback

数据泄露、账号安全、合规风险、金额异常、明确要求负责人介入
-> rule_engine

查询审批、工单、报销、假期余额、考勤、权限申请状态
-> tool_call

明确命中制度说明、流程规范、系统操作手册、FAQ、报销材料、请假制度、权限/VPN 申请
-> dci_retrieval + sqlite fts/bm25 + tfidf + qdrant vector + lightweight vector recall

其他未命中问题
-> llm_chat
```

这对应真实内部问答系统的边界：确定性高风险问题走规则，实时状态走业务 API，泛化帮助和普通聊天走 LLM，证据查找走 DCI + 向量辅助召回，最后再交给模型组织语言。

## 为什么不是纯向量检索

这个项目的核心观点不是“向量检索没用”，而是“复杂问答机器人不应该只有一个固定相似度 top-k 接口”。

传统向量 RAG 的链路通常是：

```text
用户问题 -> embedding -> 向量库 top-k -> LLM 回答
```

这个链路很快，也适合语义近似问题。但内部知识问答里，很多问题更像证据定位：

- 差旅报销需要哪些材料
- 生产系统权限是否需要填写有效期
- 病假是否可以返岗后补交证明
- 某个系统是否必须先开通 VPN
- 会议室预约是否有人数或提前时间要求

这些问题需要精确搜索、组合多个稀疏线索、查看原文上下文和保留证据链。Direct Corpus Interaction 的做法是让系统直接和原始知识库交互：

```text
用户问题
-> 抽取精确线索
-> 搜索原始知识库
-> 用 SQLite FTS5/BM25 和 TF-IDF 弱线索补充
-> 融合 Qdrant embedding 向量召回和轻量向量语义召回
-> 读取命中片段的上下文
-> 基于证据回答并展示来源
```

因此，本项目的定位是可审计的内部知识问答机器人。向量召回已经作为辅助通道接入 chatbot 和知识库证据层，但它是工具之一，不是架构中心。

## 后续路线

- 增加用户权限和管理端接口
- 增加多轮检索计划：先查精确词，再查别名和同义词，再读上下文
- 增加检索轨迹面板，展示系统如何搜索、命中和核验证据
- 文档解析支持 PDF、Word 和网页导入
- 接入 OA、ITSM、HR、财务等内部系统工具
- 增加管理端：知识库版本、命中率、无答案问题、回答反馈和质检
