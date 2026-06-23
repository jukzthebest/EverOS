#!/usr/bin/env node
// Codex UserPromptSubmit hook: inject one useful EverOS hit before the model runs.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const LOG = path.join(os.homedir(), ".codex", "log", "everos-memory-prompt-hook.log");
const COMMAND_TIMEOUT_MS = 800;
const MAX_PROMPT_CHARS = Number(process.env.EVEROS_MEMORY_MAX_PROMPT_CHARS || 1200);
const MAX_CONTEXT_CHARS = 1800;
const MIN_STABLE_HIT_SCORE = Number(process.env.EVEROS_MEMORY_MIN_STABLE_HIT_SCORE || 4);
const MIN_EPISODE_HIT_SCORE = Number(process.env.EVEROS_MEMORY_MIN_EPISODE_HIT_SCORE || 12);
const DEFAULT_PROJECT = process.env.EVEROS_MEMORY_PROJECT || "auto";
const PROJECT_RULES = parseProjectRules(process.env.EVEROS_CODEX_PROJECT_RULES || "");
const STABLE_KINDS = new Set(["agent_skill", "skill", "playbook", "agent_playbook"]);

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
    ["search", query, "--project", project, "--limit", "5", "--json"],
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

  const hit = Array.isArray(hits) ? selectUsefulHit(hits, prompt) : null;
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
  return typeof preferred === "string" ? normalizePrompt(preferred) : "";
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

function normalizePrompt(prompt) {
  const trimmed = prompt.trim();
  const markers = [
    "## My request for Codex:",
    "## My request for Codex",
    "My request for Codex:",
  ];
  for (const marker of markers) {
    const index = trimmed.lastIndexOf(marker);
    if (index >= 0) return trimmed.slice(index + marker.length).trim();
  }
  return trimmed;
}

function shouldRecall(prompt) {
  if (!prompt || prompt.length > MAX_PROMPT_CHARS) return false;
  if (isInternalGeneratedPrompt(prompt)) return false;
  if (isTrivialPrompt(prompt)) return false;
  return true;
}

function isInternalGeneratedPrompt(prompt) {
  const lowered = prompt.trim().toLowerCase();
  return (
    lowered.startsWith("# overview\n\ngenerate 0 to 3 hyperpersonalized suggestions") ||
    lowered.startsWith("you are an expert at upholding safety and compliance standards") ||
    lowered.startsWith("## memory writing agent:")
  );
}

function isTrivialPrompt(prompt) {
  const compact = prompt.replace(/\s+/g, " ").trim().toLowerCase();
  if (compact.length <= 2) return true;
  return /^(ok|okay|yes|no|thanks|thank you|done|go on|continue|可以|好的|好|嗯|继续|接着|不了|谢谢|可以，继续|可以,继续)$/.test(
    compact
  );
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
  if (score < minScoreForKind(hit.kind)) return false;
  const text = `${hit.title || ""}\n${hit.body || ""}`.toLowerCase();
  const identifiers = explicitIdentifiers(prompt);
  if (identifiers.length && !identifiers.some((identifier) => text.includes(identifier))) {
    return false;
  }
  if (score >= 8) return true;
  const tokens = prompt
    .toLowerCase()
    .split(/\s+|，|。|、|:|：|\?|？/)
    .filter((token) => token.length >= 4);
  const overlaps = tokens.filter((token) => text.includes(token)).length;
  return score >= 4 && overlaps >= 1;
}

function selectUsefulHit(hits, prompt) {
  const useful = hits.filter((hit) => isUsefulHit(hit, prompt));
  return useful.find((hit) => isStableKind(hit.kind)) || useful[0] || null;
}

function isStableKind(kind) {
  return STABLE_KINDS.has(String(kind || "").toLowerCase());
}

function minScoreForKind(kind) {
  return isStableKind(kind) ? MIN_STABLE_HIT_SCORE : MIN_EPISODE_HIT_SCORE;
}

function explicitIdentifiers(prompt) {
  const seen = new Set();
  return (prompt.toLowerCase().match(/[a-z0-9][a-z0-9_.:-]{1,}/g) || [])
    .filter((token) => token.length >= 3 || /\d/.test(token) || token.includes("_"))
    .filter((token) => {
      if (seen.has(token)) return false;
      seen.add(token);
      return true;
    });
}

function formatContext(project, query, hit) {
  const body = clip(extractAnswerContext(String(hit.body || "")), MAX_CONTEXT_CHARS);
  return [
    "Silent local context:",
    `source=everos project=${project} confidence=${confidence(hit.score)}`,
    `query=${query}`,
    `hit=[${hit.kind || "memory"}] ${hit.title || hit.item_id || ""}`,
    hit.score == null ? "" : `score=${Number(hit.score).toFixed(3)}`,
    hit.session_id ? `session=${hit.session_id}` : "",
    "",
    body,
    "",
    "Rule: use this silently. Do not mention EverOS, hooks, memory search, skill loading, or retrieval steps. If it directly answers the request, answer directly; otherwise verify against current evidence. Add at most one short source/confidence line when useful.",
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
