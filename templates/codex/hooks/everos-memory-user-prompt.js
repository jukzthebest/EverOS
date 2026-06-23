#!/usr/bin/env node
// Codex UserPromptSubmit hook: inject one useful EverOS hit before the model runs.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const LOG = path.join(os.homedir(), ".codex", "log", "everos-memory-prompt-hook.log");
const COMMAND_TIMEOUT_MS = 800;
const MAX_CONTEXT_CHARS = 1800;
const DEFAULT_PROJECT = process.env.EVEROS_MEMORY_PROJECT || "auto";
const PROJECT_RULES = parseProjectRules(process.env.EVEROS_CODEX_PROJECT_RULES || "");
const RECALL_TERMS = splitTerms(
  process.env.EVEROS_MEMORY_RECALL_TERMS ||
    "previous,last,time,before,memory,remember,command,config,path,file,template,how,why"
);

let chunks = [];
let done = false;

process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", run);
setTimeout(run, 250);

function run() {
  if (done) return;
  done = true;

  const payload = readPayload();
  const prompt = extractPrompt(payload);
  const cwd = extractCwd(payload);
  const project = inferProject(prompt, cwd);
  if (!shouldRecall(prompt)) {
    return quiet("skip", { project, prompt: clip(prompt, 160) });
  }

  const query = buildQuery(prompt, project);
  const result = spawnSync(
    "everos-memory",
    ["search", query, "--project", project, "--limit", "1", "--json"],
    {
      encoding: "utf8",
      timeout: COMMAND_TIMEOUT_MS,
      env: {
        ...process.env,
        EVEROS_MEMORY_HOOK: "1",
      },
    }
  );

  if (result.error || result.status !== 0 || !result.stdout.trim()) {
    return quiet("search_failed", {
      project,
      status: result.status,
      error: result.error && result.error.message,
      stderr: clip(result.stderr || "", 300),
    });
  }

  let hits;
  try {
    hits = JSON.parse(result.stdout);
  } catch (error) {
    return quiet("json_failed", { project, error: error.message });
  }

  const hit = Array.isArray(hits) ? hits[0] : null;
  if (!hit || !isUsefulHit(hit, prompt)) {
    return quiet("no_useful_hit", {
      project,
      score: hit && hit.score,
      title: hit && hit.title,
    });
  }

  const context = formatContext(project, query, hit);
  log("inject", { project, score: hit.score, kind: hit.kind, title: hit.title });
  output({
    continue: true,
    suppressOutput: true,
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: context,
    },
    metadata: {
      source: "everos-memory",
      project,
      score: hit.score,
      item_id: hit.item_id,
      session_id: hit.session_id,
    },
  });
}

function readPayload() {
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return {};
  }
}

function extractPrompt(value) {
  const preferred = findByKeys(value, [
    "prompt",
    "user_prompt",
    "userPrompt",
    "message",
    "text",
    "input",
  ]);
  return typeof preferred === "string" ? preferred.trim() : "";
}

function extractCwd(value) {
  const preferred = findByKeys(value, [
    "cwd",
    "current_dir",
    "currentDirectory",
    "workspace",
  ]);
  return typeof preferred === "string" ? preferred : "";
}

function findByKeys(value, keys, depth = 0) {
  if (!value || depth > 4) return "";
  if (typeof value === "string") return "";
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findByKeys(item, keys, depth + 1);
      if (found) return found;
    }
    return "";
  }
  if (typeof value !== "object") return "";
  for (const key of keys) {
    if (typeof value[key] === "string" && value[key].trim()) return value[key];
  }
  for (const nested of Object.values(value)) {
    const found = findByKeys(nested, keys, depth + 1);
    if (found) return found;
  }
  return "";
}

function inferProject(prompt, cwd) {
  const haystack = `${prompt}\n${cwd}`.toLowerCase();
  for (const [pattern, project] of PROJECT_RULES) {
    if (pattern.test(haystack)) return project;
  }
  return DEFAULT_PROJECT;
}

function shouldRecall(prompt) {
  if (!prompt || prompt.length > 500) return false;
  const lowered = prompt.toLowerCase();
  return RECALL_TERMS.some((term) => lowered.includes(term.toLowerCase()));
}

function buildQuery(prompt, project) {
  const compact = prompt.replace(/\s+/g, " ").trim();
  return compact.slice(0, 180);
}

function parseProjectRules(value) {
  return value
    .split(",")
    .map((rule) => rule.trim())
    .filter(Boolean)
    .map((rule) => {
      const [pattern, project] = rule.split("=", 2).map((part) => part.trim());
      if (!pattern || !project) return null;
      try {
        return [new RegExp(pattern, "i"), project];
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function splitTerms(value) {
  return value
    .split(",")
    .map((term) => term.trim())
    .filter(Boolean);
}

function isUsefulHit(hit, prompt) {
  const score = Number(hit.score || 0);
  const text = `${hit.title || ""}\n${hit.body || ""}`.toLowerCase();
  if (score >= 8) return true;
  const tokens = prompt
    .toLowerCase()
    .split(/\s+|，|。|、|:|：|\?|？/)
    .filter((token) => token.length >= 4);
  const overlaps = tokens.filter((token) => text.includes(token)).length;
  return score >= 4 && overlaps >= 1;
}

function formatContext(project, query, hit) {
  const body = clip(extractAnswerContext(String(hit.body || "")), MAX_CONTEXT_CHARS);
  return [
    "EverOS pre-recall:",
    `source=everos project=${project} confidence=${confidence(hit.score)}`,
    `query=${query}`,
    `hit=[${hit.kind || "memory"}] ${hit.title || hit.item_id || ""}`,
    hit.score == null ? "" : `score=${Number(hit.score).toFixed(3)}`,
    hit.session_id ? `session=${hit.session_id}` : "",
    "",
    body,
    "",
    "Rule: use this as local memory context. If it directly answers the request, answer directly; otherwise verify against current evidence.",
  ]
    .filter(Boolean)
    .join("\n");
}

function extractAnswerContext(body) {
  const conclusion = body.match(/## 结论\s+([\s\S]*?)(?=\n## |$)/);
  if (conclusion && conclusion[1].trim()) return conclusion[1].trim();

  const summary = body.match(/## 摘要\s+([\s\S]*?)(?=\n## |$)/);
  if (summary && summary[1].trim()) return summary[1].trim();

  return body
    .replace(/## 关键轮次[\s\S]*$/m, "")
    .replace(/# .+?\n/m, "")
    .trim();
}

function confidence(score) {
  const value = Number(score || 0);
  if (value >= 8) return "high";
  if (value >= 4) return "medium";
  return "low";
}

function quiet(reason, data = {}) {
  log(reason, data);
  output({ continue: true, suppressOutput: true });
}

function output(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
  process.exit(0);
}

function log(event, data) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    fs.appendFileSync(
      LOG,
      `${JSON.stringify({ ts: new Date().toISOString(), event, ...data })}\n`
    );
  } catch {}
}

function clip(text, limit) {
  const compact = text
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, limit - 1).trimEnd()}…`;
}
