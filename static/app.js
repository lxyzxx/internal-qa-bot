const chat = document.querySelector("#chat");
const chatForm = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const statusEl = document.querySelector("#status");
const documentsEl = document.querySelector("#documents");
const docForm = document.querySelector("#doc-form");
const adminToken = document.querySelector("#admin-token");
const docTitle = document.querySelector("#doc-title");
const docContent = document.querySelector("#doc-content");
const batchForm = document.querySelector("#batch-form");
const batchFiles = document.querySelector("#batch-files");
const batchStatus = document.querySelector("#batch-status");

let sessionId = localStorage.getItem("internal-qa-bot-session-id") || "";
adminToken.value = localStorage.getItem("internal-qa-bot-admin-token") || "";

function addMessage(
  role,
  content,
  sources = [],
  route = null,
  chatbotKnowledge = [],
  retrievalTrace = [],
  feedbackPayload = null,
) {
  const item = document.createElement("article");
  item.className = `message ${role}`;
  item.textContent = content;

  if (route) {
    const routeItem = document.createElement("div");
    routeItem.className = "route";
    routeItem.textContent = `分层：${route.layer} / ${route.handler}。${route.reason}`;
    item.appendChild(routeItem);
  }

  if (retrievalTrace.length > 0) {
    const traceItem = document.createElement("div");
    traceItem.className = "trace";
    traceItem.textContent = `检索轨迹：${retrievalTrace
      .map((step) => `${step.label}(${step.matched_sources})`)
      .join(" -> ")}`;
    item.appendChild(traceItem);
  }

  if (sources.length > 0) {
    const sourceList = document.createElement("div");
    sourceList.className = "sources";
    sources.forEach((source, index) => {
      const sourceItem = document.createElement("div");
      sourceItem.className = "source";
      const sourceText = source.context || source.content;
      const text = sourceText.length > 140 ? `${sourceText.slice(0, 140)}...` : sourceText;
      const evidence =
        source.evidence && source.evidence.length > 0
          ? source.evidence.join("；")
          : "相关片段召回";
      sourceItem.textContent =
        `来源 ${index + 1}：${source.title}，证据：${evidence}，` +
        `相关度 ${source.score}。${text}`;
      sourceList.appendChild(sourceItem);
    });
    item.appendChild(sourceList);
  }

  if (chatbotKnowledge.length > 0) {
    const knowledgeList = document.createElement("div");
    knowledgeList.className = "sources";
    chatbotKnowledge.forEach((knowledge) => {
      const knowledgeItem = document.createElement("div");
      knowledgeItem.className = "source";
      knowledgeItem.textContent =
        `聊天知识：${knowledge.title}，证据：${knowledge.evidence}，` +
        `相关度 ${knowledge.score}。${knowledge.content}`;
      knowledgeList.appendChild(knowledgeItem);
    });
    item.appendChild(knowledgeList);
  }

  if (feedbackPayload) {
    const actions = document.createElement("div");
    actions.className = "feedback-actions";
    [
      ["up", "有用"],
      ["down", "有问题"],
    ].forEach(([rating, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary-button";
      button.textContent = label;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await requestJson("/api/feedback", {
            method: "POST",
            body: JSON.stringify({ ...feedbackPayload, rating }),
          });
          actions.textContent = "反馈已记录";
        } catch (error) {
          button.disabled = false;
          alert(`反馈失败：${error.message}`);
        }
      });
      actions.appendChild(button);
    });
    item.appendChild(actions);
  }

  chat.appendChild(item);
  chat.scrollTop = chat.scrollHeight;
}

async function requestJson(url, options = {}) {
  const { admin = false, headers = {}, ...requestOptions } = options;
  const response = await fetch(url, {
    ...requestOptions,
    headers: {
      "Content-Type": "application/json",
      ...headers,
      ...(admin ? adminHeaders() : {}),
    },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || data.detail || "请求失败");
  }
  return data;
}

function adminHeaders() {
  const token = adminToken.value.trim();
  if (!token) return {};
  localStorage.setItem("internal-qa-bot-admin-token", token);
  return { "X-Admin-Token": token };
}

async function loadDocuments() {
  const data = await requestJson("/api/documents");
  documentsEl.innerHTML = "";

  if (data.documents.length === 0) {
    documentsEl.innerHTML = '<div class="empty">暂无文档</div>';
    return;
  }

  data.documents.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "doc-item";
    item.innerHTML = `
      <div class="doc-title"></div>
      <div class="doc-meta">${doc.chunk_count} 个片段 · ${doc.created_at}</div>
    `;
    item.querySelector(".doc-title").textContent = doc.title;
    documentsEl.appendChild(item);
  });
}

async function checkHealth() {
  await requestJson("/api/health");
  statusEl.textContent = "已连接";
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  questionInput.value = "";
  addMessage("user", question);
  addMessage("assistant", "正在检索知识库...");

  try {
    const data = await requestJson("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, session_id: sessionId || undefined }),
    });
    sessionId = data.session_id;
    localStorage.setItem("internal-qa-bot-session-id", sessionId);
    chat.lastElementChild.remove();
    const chatbotKnowledge = data.chatbot_knowledge || [];
    addMessage(
      "assistant",
      data.answer,
      data.sources,
      data.route,
      chatbotKnowledge,
      data.retrieval_trace || [],
      {
        session_id: sessionId,
        question,
        answer: data.answer,
      },
    );
  } catch (error) {
    chat.lastElementChild.remove();
    addMessage("assistant", `请求失败：${error.message}`);
  }
});

docForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = docTitle.value.trim();
  const content = docContent.value.trim();
  if (!title || !content) return;

  try {
    await requestJson("/api/documents", {
      method: "POST",
      admin: true,
      body: JSON.stringify({ title, content }),
    });
    docTitle.value = "";
    docContent.value = "";
    await loadDocuments();
  } catch (error) {
    alert(`入库失败：${error.message}`);
  }
});

function titleFromFileName(name) {
  return name.replace(/\.(md|markdown|txt)$/i, "").trim() || name;
}

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = Array.from(batchFiles.files || []);
  if (files.length === 0) return;

  batchStatus.textContent = "正在读取文件...";
  try {
    const documents = await Promise.all(
      files.map(async (file) => ({
        title: titleFromFileName(file.name),
        content: await file.text(),
      })),
    );
    batchStatus.textContent = "正在导入...";
    const data = await requestJson("/api/documents/batch", {
      method: "POST",
      admin: true,
      body: JSON.stringify({ documents }),
    });
    batchFiles.value = "";
    batchStatus.textContent =
      `导入完成：成功 ${data.documents_succeeded} 个，` +
      `失败 ${data.documents_failed} 个。`;
    await loadDocuments();
  } catch (error) {
    batchStatus.textContent = `导入失败：${error.message}`;
  }
});

checkHealth().catch(() => {
  statusEl.textContent = "未连接";
});
loadDocuments().catch(() => {
  documentsEl.innerHTML = '<div class="empty">加载失败</div>';
});
addMessage("assistant", "你好，我会先检索知识库原文并核验上下文，再回答内部问题。");
