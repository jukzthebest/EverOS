const state = {
  activeFile: null,
  fileDirty: false,
};

const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function setServerStatus(ok, label) {
  const dot = $("#serverStatus");
  dot.classList.toggle("ok", ok);
  dot.classList.toggle("bad", !ok);
  $("#serverStatusText").textContent = label;
}

function switchView(name) {
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === name);
  });
}

async function loadOverview() {
  try {
    const data = await request("/api/v1/dashboard/health");
    setServerStatus(true, "Running");
    $("#metricMarkdown").textContent = data.markdown_files;
    $("#metricScopes").textContent = data.app_project_count;
    $("#metricPending").textContent = data.queue.pending ?? "-";
    const failed =
      (data.queue.failed_retryable ?? 0) + (data.queue.failed_permanent ?? 0);
    $("#metricFailed").textContent =
      data.queue.failed_retryable == null ? "-" : String(failed);
    $("#memoryRoot").textContent = data.memory_root;
    $("#sqliteStatus").textContent = data.sqlite_exists ? "present" : "missing";
    $("#lancedbStatus").textContent = data.lancedb_exists ? "present" : "missing";
    $("#providerChain").textContent = data.provider_chain.join(" -> ");
    $("#modelDetails").textContent = [
      data.grok_model && `grok=${data.grok_model}`,
      data.codex_model && `codex=${data.codex_model}`,
      data.openai_model && `openai=${data.openai_model}`,
      `default=${data.model}`,
    ]
      .filter(Boolean)
      .join(", ");
    $("#embeddingModel").textContent = data.embedding_model
      ? `${data.embedding_provider}:${data.embedding_model}`
      : data.embedding_provider;
  } catch (error) {
    setServerStatus(false, "Error");
    $("#memoryRoot").textContent = error.message;
  }
}

async function runSearch(event) {
  event.preventDefault();
  const query = $("#searchQuery").value.trim();
  if (!query) return;

  const ownerType = $("#searchOwnerType").value;
  const basePayload = {
    query,
    app_id: "codex",
    project_id: $("#searchProjectId").value.trim() || "default",
    method: $("#searchMethod").value,
    top_k: 20,
  };

  const results = $("#searchResults");
  results.textContent = "Searching...";
  $("#searchPreview").textContent = "";

  try {
    const response = await runSearchRequests(basePayload, ownerType);
    renderSearchResults(response, query);
  } catch (error) {
    results.textContent = error.message;
  }
}

async function runSearchRequests(basePayload, ownerType) {
  const ownerId = $("#searchOwnerId").value.trim();
  const requests = [];
  if (ownerType === "user" || ownerType === "both") {
    requests.push(
      request("/api/v1/memory/search", {
        method: "POST",
        body: JSON.stringify({
          ...basePayload,
          include_profile: true,
          user_id: ownerId || "lengxiaochu",
        }),
      }),
    );
  }
  if (ownerType === "agent" || ownerType === "both") {
    requests.push(
      request("/api/v1/memory/search", {
        method: "POST",
        body: JSON.stringify({
          ...basePayload,
          include_profile: false,
          agent_id: ownerType === "agent" ? ownerId || "codex" : "codex",
        }),
      }),
    );
  }
  const responses = await Promise.all(requests);
  return mergeSearchData(responses.map((response) => response.data));
}

function mergeSearchData(chunks) {
  return {
    episodes: chunks.flatMap((item) => item.episodes || []),
    profiles: chunks.flatMap((item) => item.profiles || []),
    agent_cases: chunks.flatMap((item) => item.agent_cases || []),
    agent_skills: chunks.flatMap((item) => item.agent_skills || []),
    unprocessed_messages: chunks.flatMap((item) => item.unprocessed_messages || []),
  };
}

function renderSearchResults(data, query) {
  const items = [
    ...data.episodes.map((item) => ({
      kind: "episode",
      title: item.subject || item.summary || item.id,
      score: asNumber(item.score),
      meta: "",
      body: item.episode,
      raw: item,
    })),
    ...data.profiles.map((item) => ({
      kind: "profile",
      title: item.id,
      score: null,
      meta: "profile",
      body: JSON.stringify(item.profile_data, null, 2),
      raw: item,
    })),
    ...data.agent_cases.map((item) => ({
      kind: "case",
      title: item.task_intent || item.id,
      score: asNumber(item.score),
      meta: "",
      body: [item.approach, item.key_insight].filter(Boolean).join("\n\n"),
      raw: item,
    })),
    ...data.agent_skills.map((item) => ({
      kind: "skill",
      title: item.description || item.name || item.id,
      score: asNumber(item.score),
      body: item.content || item.description,
      raw: item,
    })),
    ...data.unprocessed_messages.map((item) => ({
      kind: "buffer",
      title: `${item.role} · ${item.sender_id}`,
      score: null,
      meta: `buffer · ${item.session_id}`,
      body: typeof item.content === "string" ? item.content : JSON.stringify(item.content),
      raw: item,
    })),
  ]
    .filter((item) => item.kind !== "profile" && item.kind !== "buffer");

  const visibleItems = filterAndRankItems(query, items).slice(0, 10);

  const results = $("#searchResults");
  results.innerHTML = "";
  if (visibleItems.length === 0) {
    results.textContent = "No results.";
    return;
  }
  for (const item of visibleItems) {
    const button = document.createElement("button");
    button.className = "result-item";
    button.innerHTML = `<strong></strong><span></span>`;
    button.querySelector("strong").textContent = item.title;
    button.querySelector("span").textContent = itemMeta(item, query);
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".result-item")
        .forEach((entry) => entry.classList.remove("active"));
      button.classList.add("active");
      $("#searchPreview").textContent = `${item.body}\n\n${JSON.stringify(
        item.raw,
        null,
        2,
      )}`;
    });
    results.append(button);
  }
  results.querySelector(".result-item")?.click();
}

function score(value) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function itemMeta(item, query) {
  const parts = [item.kind];
  if (item.raw?.session_id) parts.push(item.raw.session_id);
  const rank = boostedScore(item, query);
  if (rank != null) parts.push(`score ${score(rank)}`);
  if (item.score != null && rank !== item.score) parts.push(`raw ${score(item.score)}`);
  return parts.join(" · ");
}

function filterAndRankItems(query, items) {
  const terms = requiredAsciiTerms(query);
  const filtered = terms.length
    ? items.filter((item) =>
        terms.every((term) => searchableItemText(item).includes(term)),
      )
    : items;
  return filtered.sort(
    (left, right) => (boostedScore(right, query) ?? 0) - (boostedScore(left, query) ?? 0),
  );
}

function requiredAsciiTerms(query) {
  const stopwords = new Set([
    "and",
    "for",
    "from",
    "how",
    "into",
    "please",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "why",
    "with",
  ]);
  const terms = [];
  for (const match of query.matchAll(/[A-Za-z][A-Za-z0-9_./:-]{2,}/g)) {
    const term = match[0].toLowerCase();
    if (!stopwords.has(term) && !terms.includes(term)) terms.push(term);
  }
  return terms;
}

function searchableItemText(item) {
  return `${item.title || ""}\n${item.body || ""}\n${item.raw?.id || ""}`.toLowerCase();
}

function boostedScore(item, query) {
  const base = item.score ?? 0;
  const tokens = queryTokens(query);
  let boost = 0;
  const title = String(item.title || "").toLowerCase();
  const body = String(item.body || "").toLowerCase();
  for (const token of tokens) {
    if (title.includes(token)) boost += 5;
    if (body.includes(token)) boost += 0.5;
  }
  if (tokens.some((token) => ["连接", "登录", "命令", "mysql"].includes(token))) {
    if (item.kind === "episode") boost += 1;
    if (body.includes("mysql ")) boost += 2;
  }
  return base + boost;
}

function queryTokens(query) {
  const tokens = [];
  for (const match of query.matchAll(/[A-Za-z0-9_./:-]{2,}|[\u4e00-\u9fff]{2,}/g)) {
    const token = match[0].toLowerCase();
    tokens.push(token);
    if (token.includes("数据库")) tokens.push("mysql");
    if (token.includes("登录") || token.includes("连接")) tokens.push("连接", "mysql");
    if (token.includes("命令")) tokens.push("命令");
  }
  return [...new Set(tokens)];
}

function syncOwnerId() {
  const ownerType = $("#searchOwnerType").value;
  if (ownerType === "agent") {
    $("#searchOwnerId").value = "codex";
  } else if (ownerType === "user") {
    $("#searchOwnerId").value = "lengxiaochu";
  }
}

async function loadTree() {
  const tree = $("#fileTree");
  tree.textContent = "Loading...";
  try {
    const data = await request("/api/v1/dashboard/tree?max_depth=10&include_hidden=true");
    tree.innerHTML = "";
    tree.append(renderTree(data));
  } catch (error) {
    tree.textContent = error.message;
  }
}

function renderTree(node, depth = 0) {
  const ul = document.createElement("ul");
  ul.append(renderTreeItem(node, depth));
  return ul;
}

function renderTreeItem(node, depth) {
  const li = document.createElement("li");

  if (node.type === "file") {
    const button = document.createElement("button");
    button.className = "file-row";
    button.textContent = node.name;
    button.addEventListener("click", () => loadFile(node.path));
    li.append(button);
    return li;
  }

  const details = document.createElement("details");
  details.open = depth < 2;
  const summary = document.createElement("summary");
  summary.textContent = `${node.name}/`;
  details.append(summary);

  if (node.children?.length) {
    const childList = document.createElement("ul");
    for (const child of node.children) {
      childList.append(renderTreeItem(child, depth + 1));
    }
    details.append(childList);
  } else {
    const empty = document.createElement("small");
    empty.className = "empty-dir";
    empty.textContent = "Empty";
    details.append(empty);
  }

  li.append(details);
  return li;
}

async function loadFile(path) {
  try {
    const data = await request(`/api/v1/dashboard/file?path=${encodeURIComponent(path)}`);
    state.activeFile = data.path;
    state.fileDirty = false;
    $("#activeFile").textContent = data.path;
    $("#fileEditor").value = data.content;
    $("#saveFile").disabled = !data.editable;
  } catch (error) {
    $("#activeFile").textContent = error.message;
    $("#fileEditor").value = "";
    $("#saveFile").disabled = true;
  }
}

async function saveFile() {
  if (!state.activeFile) return;
  $("#saveFile").disabled = true;
  try {
    await request("/api/v1/dashboard/file", {
      method: "PUT",
      body: JSON.stringify({
        path: state.activeFile,
        content: $("#fileEditor").value,
      }),
    });
    state.fileDirty = false;
    $("#saveFile").disabled = false;
  } catch (error) {
    $("#activeFile").textContent = error.message;
    $("#saveFile").disabled = false;
  }
}

async function runCascade(action) {
  const output = $("#syncOutput");
  output.textContent = "Running...";
  try {
    const data = await request(`/api/v1/dashboard/cascade/${action}`, {
      method: "POST",
      body: "{}",
    });
    output.textContent = JSON.stringify(data, null, 2);
    await loadOverview();
  } catch (error) {
    output.textContent = error.message;
  }
}

document.querySelectorAll(".nav-button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

$("#refreshOverview").addEventListener("click", loadOverview);
$("#searchOwnerType").addEventListener("change", syncOwnerId);
$("#searchForm").addEventListener("submit", runSearch);
$("#refreshTree").addEventListener("click", loadTree);
$("#saveFile").addEventListener("click", saveFile);
$("#fileEditor").addEventListener("input", () => {
  state.fileDirty = true;
});
$("#runCascadeSync").addEventListener("click", () => runCascade("sync"));
$("#runCascadeFix").addEventListener("click", () => runCascade("fix"));

loadOverview();
loadTree();
