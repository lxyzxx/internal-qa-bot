# Internal QA Bot 任务清单

更新时间：2026-05-19

这个文件用于记录项目当前完成度和后续待办。以后继续开发时，先读这里，再按需看相关代码，不需要每次重新遍历整个仓库。

## 当前结论

项目已经达到“可本地演示的 MVP”状态：内部知识问答主链路、问题分层、混合检索、来源追踪、基础前端和可选 Qdrant 向量检索已经落地。

项目还没有达到“公司内部生产上线”状态：权限、管理端、批量导入、向量索引重建、真实业务系统工具调用、审计和运营能力仍未完成。

粗略完成度：

- 内部知识问答 MVP：65%-75%
- 生产可上线系统：35%-45%

## 已完成

- [x] FastAPI 后端服务
- [x] 静态前端页面
- [x] 最小管理鉴权：配置 `ADMIN_API_TOKEN` 后保护写接口
- [x] 健康检查接口：`GET /api/health`
- [x] 文档列表接口：`GET /api/documents`
- [x] 新增知识库文档接口：`POST /api/documents`
- [x] 批量导入接口：`POST /api/documents/batch`
- [x] 问答接口：`POST /api/chat`
- [x] SQLite 持久化文档、chunk 和聊天记录
- [x] 示例知识库首次启动自动导入
- [x] 文档切分：段落优先，长文本滑动窗口
- [x] 问题分层：普通聊天、确定性规则、业务工具、知识库证据检索
- [x] 普通聊天优先走 LLM，模型不可用时 fallback
- [x] OpenAI-compatible Chat Completions 适配
- [x] 未配置 `OPENAI_API_KEY` 时返回检索摘要 fallback
- [x] 原文关键词直接检索
- [x] SQLite FTS5/BM25 全文召回
- [x] TF-IDF 弱线索召回
- [x] 轻量 hash vector recall fallback
- [x] Qdrant 向量索引适配层
- [x] OpenAI-compatible embedding provider
- [x] 本地 sentence-transformers embedding provider
- [x] 新增文档时尽力同步 Qdrant
- [x] 向量索引重建接口：已有 SQLite 文档批量重算 embedding 并同步 Qdrant
- [x] Qdrant 不可用时文档入库不失败，只标记向量索引未完成
- [x] 命中 chunk 后读取同文档相邻上下文
- [x] 回答返回来源、证据线索、相关度和上下文
- [x] 前端展示分层结果、检索轨迹和来源
- [x] 前端支持多选 Markdown/TXT 文件批量导入
- [x] README 已更新当前架构、配置、API、检索设计和后续路线
- [x] 单元测试覆盖 config、embedding、llm、problem_layers、qdrant_index、rag、retriever、server、storage、vector_retriever

## 未完成

- [ ] 登录页面和用户会话
- [ ] 用户权限模型
- [ ] 管理端接口
- [ ] Qdrant collection 维度变更后的自动处理或清晰错误提示
- [ ] 多轮检索计划：先查精确词，再查别名/同义词，再读上下文
- [ ] 检索轨迹独立面板和更细粒度 trace
- [ ] PDF 导入
- [ ] Word 导入
- [ ] 网页导入
- [ ] PDF、Word、网页等复杂文档解析
- [ ] OA 工具调用
- [ ] ITSM 工具调用
- [ ] HR 工具调用
- [ ] 财务/报销系统工具调用
- [ ] 管理端知识库版本管理
- [ ] 无答案问题收集
- [ ] 用户反馈和质检
- [ ] 命中率、召回质量、回答质量统计
- [ ] 生产部署配置
- [ ] 审计日志
- [ ] 敏感信息脱敏和安全策略

## 当前验证状态

最近一次验证命令：

```bash
.venv/bin/python -m unittest discover -s tests
```

结果：

- 51 个测试被发现
- 51 个测试通过

备注：

- 系统 Python 被 PEP 668 保护，不能直接执行 `python3 -m pip install -e .`。
- 已在项目内 `.venv` 安装依赖并通过测试。
- `.venv/` 已在 `.gitignore` 中，不会提交。

下次验证建议：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests
```

## 下次优先级

1. 增加管理端基础能力
   - 文档删除
   - 文档重新入库
   - 查看 chunk
   - 查看无答案问题
   - 查看反馈

2. 接入真实业务工具
   - 先定义工具调用接口和 mock adapter。
   - 再接 OA、ITSM、HR、财务等真实 API。

## 关键文件索引

- `app/server.py`：FastAPI 应用、API 路由、静态资源服务、示例数据导入
- `app/rag.py`：问题分层后的问答编排
- `app/retriever.py`：混合检索、证据融合、上下文读取
- `app/storage.py`：SQLite schema、文档、chunk、FTS5、消息记录
- `app/qdrant_index.py`：Qdrant 索引写入、删除、检索
- `app/embedding.py`：远程和本地 embedding provider
- `app/llm.py`：LLM 调用、prompt、fallback
- `app/problem_layers.py`：问题分层规则
- `static/app.js`：前端交互、聊天、文档入库、来源展示
- `README.md`：使用说明、架构说明、API 和路线

## 注意事项

- Qdrant 是可选增强，不配置时系统仍能用 SQLite FTS5/BM25、TF-IDF 和轻量向量 fallback 工作。
- 只有配置 Qdrant 后新增的文档会自动写入 Qdrant；历史文档可以通过 `POST /api/vector-index/rebuild` 批量同步。
- `ADMIN_API_TOKEN` 为空时写接口不鉴权；配置后，单文档入库、批量入库和向量重建需要 `Authorization: Bearer ...` 或 `X-Admin-Token`。
- 当前业务工具层只是路由和占位回答，还没有调用真实内部系统。
- 当前前端是基础操作台，不是完整管理端。
