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
  const payload = {
    query,
    app_id: "codex",
    project_id: $("#searchProjectId").value.trim() || "default",
    method: $("#searchMethod").value,
    top_k: 10,
    include_profile: ownerType === "user",
  };
  if (ownerType === "user") {
    payload.user_id = $("#searchOwnerId").value.trim() || "lengxiaochu";
  } else {
    payload.agent_id = $("#searchOwnerId").value.trim() || "codex";
  }

  const results = $("#searchResults");
  results.textContent = "Searching...";
  $("#searchPreview").textContent = "";

  try {
    const response = await request("/api/v1/memory/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderSearchResults(response.data);
  } catch (error) {
    results.textContent = error.message;
  }
}

function renderSearchResults(data) {
  const items = [
    ...data.episodes.map((item) => ({
      title: item.subject || item.summary || item.id,
      meta: `episode · ${item.session_id} · score ${score(item.score)}`,
      body: item.episode,
      raw: item,
    })),
    ...data.profiles.map((item) => ({
      title: item.id,
      meta: "profile",
      body: JSON.stringify(item.profile_data, null, 2),
      raw: item,
    })),
    ...data.agent_cases.map((item) => ({
      title: item.task_intent || item.id,
      meta: `case · ${item.session_id} · score ${score(item.score)}`,
      body: [item.approach, item.key_insight].filter(Boolean).join("\n\n"),
      raw: item,
    })),
    ...data.agent_skills.map((item) => ({
      title: item.name || item.id,
      meta: `skill · confidence ${score(item.confidence)}`,
      body: item.content || item.description,
      raw: item,
    })),
    ...data.unprocessed_messages.map((item) => ({
      title: `${item.role} · ${item.sender_id}`,
      meta: `buffer · ${item.session_id}`,
      body: typeof item.content === "string" ? item.content : JSON.stringify(item.content),
      raw: item,
    })),
  ];

  const results = $("#searchResults");
  results.innerHTML = "";
  if (items.length === 0) {
    results.textContent = "No results.";
    return;
  }
  for (const item of items) {
    const button = document.createElement("button");
    button.className = "result-item";
    button.innerHTML = `<strong></strong><span></span>`;
    button.querySelector("strong").textContent = item.title;
    button.querySelector("span").textContent = item.meta;
    button.addEventListener("click", () => {
      $("#searchPreview").textContent = `${item.body}\n\n${JSON.stringify(
        item.raw,
        null,
        2,
      )}`;
    });
    results.append(button);
  }
}

function score(value) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

async function loadTree() {
  const tree = $("#fileTree");
  tree.textContent = "Loading...";
  try {
    const data = await request("/api/v1/dashboard/tree?max_depth=6&include_hidden=true");
    tree.innerHTML = "";
    tree.append(renderTree(data));
  } catch (error) {
    tree.textContent = error.message;
  }
}

function renderTree(node) {
  const ul = document.createElement("ul");
  const li = document.createElement("li");
  const button = document.createElement("button");
  button.textContent = node.type === "dir" ? `${node.name}/` : node.name;
  if (node.type === "file") {
    button.addEventListener("click", () => loadFile(node.path));
  } else {
    button.disabled = true;
  }
  li.append(button);
  if (node.children?.length) {
    const childList = document.createElement("ul");
    for (const child of node.children) {
      childList.append(...renderTree(child).children);
    }
    li.append(childList);
  }
  ul.append(li);
  return ul;
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
