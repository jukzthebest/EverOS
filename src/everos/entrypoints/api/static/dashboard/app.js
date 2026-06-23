const state = {
  activeFile: null,
  fileDirty: false,
  previewMode: false,
  fileTree: null,
  fileMode: "docs",
  fileFilter: "",
  searchDefaultsApplied: false,
};

let DEFAULT_USER_ID = "user";
let DEFAULT_AGENT_ID = "codex";
let DEFAULT_APP_ID = "codex";
const AUTO_PROJECT_VALUES = new Set(["", "auto", "all", "*"]);

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
    applySearchDefaults(data);
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
  const method = $("#searchMethod").value;
  const literalTerms = requiredLiteralTerms(query);
  if (method === "vector" && literalTerms.length) {
    $("#searchResults").textContent =
      `Vector search is semantic-only. Exact terms detected: ${literalTerms.join(", ")}. Use keyword or hybrid.`;
    $("#searchPreview").textContent = "No result selected.";
    return;
  }
  const appId = $("#searchAppId").value.trim() || DEFAULT_APP_ID;
  const results = $("#searchResults");
  results.textContent = "Resolving projects...";
  $("#searchPreview").textContent = "";
  const projectIds = await resolveSearchProjects(appId);
  const basePayload = {
    query,
    app_id: appId,
    method,
    top_k: 20,
  };

  results.textContent =
    projectIds.length > 1 ? `Searching ${projectIds.length} projects...` : "Searching...";

  try {
    const response = await runSearchRequests(basePayload, ownerType, projectIds);
    renderSearchResults(response, query);
  } catch (error) {
    results.textContent = error.message;
  }
}

async function runSearchRequests(basePayload, ownerType, projectIds) {
  const ownerId = $("#searchOwnerId").value.trim();
  const requests = [];
  for (const projectId of projectIds) {
    if (ownerType === "user" || ownerType === "both") {
      requests.push(
        request("/api/v1/memory/search", {
          method: "POST",
          body: JSON.stringify({
            ...basePayload,
            project_id: projectId,
            include_profile: true,
            user_id: ownerId || DEFAULT_USER_ID,
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
            project_id: projectId,
            include_profile: false,
            agent_id:
              ownerType === "agent" ? ownerId || DEFAULT_AGENT_ID : DEFAULT_AGENT_ID,
          }),
        }),
      );
    }
  }
  const responses = await Promise.all(requests);
  return mergeSearchData(responses.map((response) => response.data));
}

function mergeSearchData(chunks) {
  return {
    episodes: dedupeSearchItems(chunks.flatMap((item) => item.episodes || [])),
    profiles: dedupeSearchItems(chunks.flatMap((item) => item.profiles || [])),
    agent_cases: dedupeSearchItems(chunks.flatMap((item) => item.agent_cases || [])),
    agent_skills: dedupeSearchItems(chunks.flatMap((item) => item.agent_skills || [])),
    unprocessed_messages: dedupeSearchItems(
      chunks.flatMap((item) => item.unprocessed_messages || []),
    ),
  };
}

function dedupeSearchItems(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.id || item.message_id || item.session_id || JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderSearchResults(data, query) {
  const items = [
    ...data.episodes.map((item) => ({
      kind: "episode",
      title: item.subject || item.summary || item.id,
      score: asNumber(item.score),
      meta: "",
      body: item.episode,
      project: item.project_id,
      raw: item,
    })),
    ...data.profiles.map((item) => ({
      kind: "profile",
      title: item.id,
      score: null,
      meta: "profile",
      body: JSON.stringify(item.profile_data, null, 2),
      project: item.project_id,
      raw: item,
    })),
    ...data.agent_cases.map((item) => ({
      kind: "case",
      title: item.task_intent || item.id,
      score: asNumber(item.score),
      meta: "",
      body: [item.approach, item.key_insight].filter(Boolean).join("\n\n"),
      project: item.project_id,
      raw: item,
    })),
    ...data.agent_skills.map((item) => ({
      kind: "skill",
      title: item.description || item.name || item.id,
      score: asNumber(item.score),
      body: item.content || item.description,
      project: item.project_id,
      raw: item,
    })),
    ...data.unprocessed_messages.map((item) => ({
      kind: "buffer",
      title: `${item.role} · ${item.sender_id}`,
      score: null,
      meta: `buffer · ${item.session_id}`,
      body: typeof item.content === "string" ? item.content : JSON.stringify(item.content),
      project: item.project_id,
      raw: item,
    })),
  ]
    .filter((item) => item.kind !== "profile" && item.kind !== "buffer");

  const rankedItems = filterAndRankItems(query, items);
  const visibleItems = rankedItems.slice(0, 10);

  const results = $("#searchResults");
  results.innerHTML = "";
  if (visibleItems.length === 0) {
    const terms = requiredLiteralTerms(query);
    results.textContent = terms.length
      ? `No exact matches for: ${terms.join(", ")}.`
      : "No results.";
    $("#searchPreview").textContent = "No result selected.";
    return;
  }
  for (const item of visibleItems) {
    const button = document.createElement("button");
    button.className = "result-item";
    button.innerHTML = `<strong></strong><span></span><small></small>`;
    button.querySelector("strong").textContent = item.title;
    button.querySelector("span").textContent = itemMeta(item, query);
    const excerpt = matchExcerpt(item.body, query);
    const excerptElement = button.querySelector("small");
    excerptElement.textContent = excerpt;
    excerptElement.hidden = !excerpt;
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
  if (item.project) parts.push(item.project);
  if (item.raw?.session_id) parts.push(item.raw.session_id);
  const rank = boostedScore(item, query);
  if (rank != null) parts.push(`score ${score(rank)}`);
  if (item.score != null && rank !== item.score) parts.push(`raw ${score(item.score)}`);
  return parts.join(" · ");
}

function filterAndRankItems(query, items) {
  const terms = requiredLiteralTerms(query);
  const filtered = terms.length
    ? items.filter((item) =>
        terms.every((term) => searchableItemText(item).includes(term)),
      )
    : items;
  return filtered.sort(
    (left, right) => (boostedScore(right, query) ?? 0) - (boostedScore(left, query) ?? 0),
  );
}

function requiredLiteralTerms(query) {
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
  for (const match of query.matchAll(/[A-Za-z0-9][A-Za-z0-9_./:-]{2,}/g)) {
    const term = match[0].toLowerCase();
    if (!stopwords.has(term) && isLiteralAnchor(term) && !terms.includes(term)) {
      terms.push(term);
    }
  }
  return terms;
}

function isLiteralAnchor(term) {
  return /\d/.test(term) || /[_./:-]/.test(term);
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
  return base + boost;
}

function queryTokens(query) {
  const tokens = [];
  for (const match of query.matchAll(/[A-Za-z0-9_./:-]{2,}|[\u4e00-\u9fff]+/g)) {
    const token = match[0].toLowerCase();
    if (/^[\u4e00-\u9fff]+$/.test(token)) {
      if (token.length <= 4) tokens.push(token);
      if (token.length > 2) {
        for (let index = 0; index < token.length - 1; index += 1) {
          tokens.push(token.slice(index, index + 2));
        }
      }
      continue;
    }
    tokens.push(token);
  }
  return [...new Set(tokens)];
}

function matchExcerpt(body, query) {
  const text = String(body || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  const tokens = queryTokens(query).sort((left, right) => right.length - left.length);
  const index = tokens.map((token) => lower.indexOf(token)).find((value) => value >= 0);
  if (index == null || index < 0) return truncateText(text, 180);
  const start = Math.max(0, index - 70);
  const end = Math.min(text.length, index + 140);
  return `${start > 0 ? "..." : ""}${text.slice(start, end)}${
    end < text.length ? "..." : ""
  }`;
}

function truncateText(text, maxLength) {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3)}...`;
}

function syncOwnerId() {
  const ownerType = $("#searchOwnerType").value;
  if (ownerType === "agent") {
    $("#searchOwnerId").value = DEFAULT_AGENT_ID;
  } else if (ownerType === "user") {
    $("#searchOwnerId").value = DEFAULT_USER_ID;
  }
}

function applySearchDefaults(data) {
  DEFAULT_USER_ID =
    data.default_user_id || inferUserIdFromMemoryRoot(data.memory_root) || DEFAULT_USER_ID;
  DEFAULT_AGENT_ID = data.default_agent_id || DEFAULT_AGENT_ID;
  DEFAULT_APP_ID = data.default_app_id || DEFAULT_APP_ID;
  if (state.searchDefaultsApplied) return;

  const ownerType = $("#searchOwnerType").value;
  $("#searchOwnerId").value =
    ownerType === "agent" ? DEFAULT_AGENT_ID : DEFAULT_USER_ID;
  $("#searchAppId").value = DEFAULT_APP_ID;
  const exampleProject =
    (data.default_projects || []).find((project) => project && project !== "default") ||
    "project-id";
  $("#searchProjectId").placeholder = `Project: auto/all/${exampleProject}`;
  $("#searchProjectId").value = "";
  state.searchDefaultsApplied = true;
}

function inferUserIdFromMemoryRoot(path) {
  const match = String(path || "").match(/^\/(?:Users|home)\/([^/]+)/);
  return match?.[1] || "";
}

async function resolveSearchProjects(appId) {
  const raw = $("#searchProjectId").value.trim();
  const discovered = await discoverSearchProjects(appId);
  if (AUTO_PROJECT_VALUES.has(raw.toLowerCase())) {
    return discovered.length ? discovered : ["default"];
  }
  return parseProjectIds(raw, discovered);
}

async function discoverSearchProjects(appId) {
  try {
    const root = state.fileTree || (await loadSearchTree());
    const projectsNode = findTreeNode(root, (node) => {
      const path = normalizeTreePath(node.path).toLowerCase();
      return node.type === "dir" && path === `${appId}/projects`.toLowerCase();
    });
    return (projectsNode?.children || [])
      .filter((child) => child.type === "dir")
      .map((child) => child.name)
      .sort((left, right) => left.localeCompare(right));
  } catch (error) {
    return [];
  }
}

async function loadSearchTree() {
  const data = await request("/api/v1/dashboard/tree?max_depth=4&include_hidden=false");
  if (!state.fileTree) state.fileTree = data;
  return data;
}

function parseProjectIds(raw, discovered) {
  const canonical = new Map(discovered.map((project) => [project.toLowerCase(), project]));
  const projects = raw
    .split(",")
    .flatMap((value) => {
      const project = value.trim();
      if (AUTO_PROJECT_VALUES.has(project.toLowerCase())) return discovered;
      return project ? [canonical.get(project.toLowerCase()) || project] : [];
    });
  const uniqueProjects = [...new Set(projects)];
  return uniqueProjects.length ? uniqueProjects : ["default"];
}

function findTreeNode(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) {
    const match = findTreeNode(child, predicate);
    if (match) return match;
  }
  return null;
}

function normalizeTreePath(path) {
  return String(path || "")
    .replace(/^\.\//, "")
    .replace(/\/+$/, "");
}

async function loadTree() {
  $("#fileList").textContent = "Loading...";
  $("#fileTree").textContent = "";
  try {
    const data = await request("/api/v1/dashboard/tree?max_depth=10&include_hidden=true");
    state.fileTree = data;
    renderFileBrowser();
  } catch (error) {
    $("#fileList").textContent = error.message;
  }
}

function renderFileBrowser() {
  const list = $("#fileList");
  const tree = $("#fileTree");
  document.querySelectorAll("[data-file-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.fileMode === state.fileMode);
  });
  list.hidden = state.fileMode !== "docs";
  tree.hidden = state.fileMode !== "tree";
  if (!state.fileTree) return;
  if (state.fileMode === "tree") {
    tree.innerHTML = "";
    tree.append(renderTree(state.fileTree));
    markActiveFile();
    return;
  }
  renderDocumentList(state.fileTree);
}

function renderDocumentList(root) {
  const list = $("#fileList");
  const query = state.fileFilter.trim().toLowerCase();
  const documents = flattenDocuments(root).filter((item) => {
    if (!query) return true;
    return `${item.name}\n${item.path}`.toLowerCase().includes(query);
  });
  list.innerHTML = "";
  if (!documents.length) {
    const empty = document.createElement("p");
    empty.className = "empty-list";
    empty.textContent = "No documents.";
    list.append(empty);
    return;
  }
  for (const item of documents) {
    const button = document.createElement("button");
    button.className = "doc-row";
    button.title = item.path;
    button.dataset.path = item.path;

    const title = document.createElement("strong");
    title.textContent = compactDocumentName(item.name);
    const pathLine = document.createElement("span");
    pathLine.textContent = documentPathLabel(item.path);

    button.append(title, pathLine);
    button.addEventListener("click", () => loadFile(item.path));
    list.append(button);
  }
  markActiveFile();
}

function flattenDocuments(node, items = []) {
  if (node.type === "file") {
    if (isDocumentFile(node.name)) {
      items.push({
        name: node.name,
        path: node.path,
        modifiedAt: node.modified_at || 0,
      });
    }
    return items;
  }
  for (const child of node.children || []) {
    flattenDocuments(child, items);
  }
  return items.sort((left, right) => {
    if (right.modifiedAt !== left.modifiedAt) return right.modifiedAt - left.modifiedAt;
    return left.path.localeCompare(right.path);
  });
}

function isDocumentFile(name) {
  return /\.(md|markdown|toml|txt)$/i.test(name);
}

function compactDocumentName(name) {
  return name.replace(/\.(md|markdown|toml|txt)$/i, "");
}

function documentPathLabel(path) {
  const parts = path.split("/").filter(Boolean);
  const folders = parts.slice(0, -1);
  if (folders.length <= 3) return folders.join(" / ") || "root";
  return `... / ${folders.slice(-3).join(" / ")}`;
}

function setFileMode(mode) {
  state.fileMode = mode;
  renderFileBrowser();
}

function updateFileFilter(value) {
  state.fileFilter = value;
  if (state.fileMode !== "docs") state.fileMode = "docs";
  renderFileBrowser();
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
    button.dataset.path = node.path;
    button.textContent = node.name;
    button.addEventListener("click", () => loadFile(node.path));
    li.append(button);
    return li;
  }

  const details = document.createElement("details");
  details.open = depth < 1;
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
    state.previewMode = false;
    $("#activeFile").textContent = data.path;
    $("#fileEditor").value = data.content;
    $("#saveFile").disabled = !data.editable;
    updatePreviewState();
    markActiveFile();
  } catch (error) {
    $("#activeFile").textContent = error.message;
    $("#fileEditor").value = "";
    $("#saveFile").disabled = true;
    state.activeFile = null;
    state.previewMode = false;
    updatePreviewState();
    markActiveFile();
  }
}

function markActiveFile() {
  document.querySelectorAll("[data-path]").forEach((button) => {
    button.classList.toggle("active", button.dataset.path === state.activeFile);
  });
}

function updatePreviewState() {
  const canPreview = Boolean(state.activeFile?.toLowerCase().endsWith(".md"));
  if (!canPreview) state.previewMode = false;
  $("#togglePreview").disabled = !canPreview;
  $("#togglePreview").textContent = state.previewMode ? "Edit" : "Preview";
  $("#fileEditor").hidden = state.previewMode;
  $("#markdownPreview").hidden = !state.previewMode;
  if (state.previewMode) {
    $("#markdownPreview").innerHTML = renderMarkdown($("#fileEditor").value);
  } else {
    $("#markdownPreview").textContent = "";
  }
}

function togglePreview() {
  if ($("#togglePreview").disabled) return;
  state.previewMode = !state.previewMode;
  updatePreviewState();
}

function renderMarkdown(markdown) {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let inCode = false;
  let codeLines = [];
  let listType = null;
  let paragraph = [];

  const closeParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(formatInline).join("<br>")}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };
  const closeBlocks = () => {
    closeParagraph();
    closeList();
  };

  for (const line of lines) {
    const fence = line.match(/^```/);
    if (fence) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeBlocks();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      closeBlocks();
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeBlocks();
      const level = heading[1].length;
      html.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    if (unordered) {
      closeParagraph();
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${formatInline(unordered[1])}</li>`);
      continue;
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) {
      closeParagraph();
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${formatInline(ordered[1])}</li>`);
      continue;
    }

    closeList();
    paragraph.push(line.trim());
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  closeBlocks();
  return html.join("\n");
}

function formatInline(text) {
  const parts = String(text).split(/(`[^`]+`)/g);
  return parts
    .map((part) => {
      if (part.startsWith("`") && part.endsWith("`")) {
        return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
      }
      return escapeHtml(part)
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    })
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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
$("#fileFilter").addEventListener("input", (event) => updateFileFilter(event.target.value));
document.querySelectorAll("[data-file-mode]").forEach((button) => {
  button.addEventListener("click", () => setFileMode(button.dataset.fileMode));
});
$("#saveFile").addEventListener("click", saveFile);
$("#togglePreview").addEventListener("click", togglePreview);
$("#fileEditor").addEventListener("input", () => {
  state.fileDirty = true;
  if (state.previewMode) updatePreviewState();
});
$("#runCascadeSync").addEventListener("click", () => runCascade("sync"));
$("#runCascadeFix").addEventListener("click", () => runCascade("fix"));

loadOverview();
loadTree();
