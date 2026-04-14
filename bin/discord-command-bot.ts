import { spawn } from "node:child_process";
import { promises as fs, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

type Lane = "architect" | "executor" | "reviewer";
type ParsedTextCommand =
  | { type: "help" }
  | { type: "projects" }
  | { type: "status"; lane?: Lane }
  | { type: "lanes_up" }
  | { type: "lane_up"; lane: Lane }
  | { type: "heartbeat"; lane?: Lane }
  | { type: "architect_followup" }
  | { type: "handoff"; fromLane: Lane; toLane: Lane; summary: string }
  | { type: "lane_prompt"; lane: Lane; prompt: string }
  | { type: "map_channel"; projectKey: string };

type SlashCommand =
  | { type: "help" }
  | { type: "projects" }
  | { type: "status"; projectKey?: string; lane?: Lane }
  | { type: "lanes_up"; projectKey?: string }
  | { type: "lane_up"; projectKey?: string; lane: Lane }
  | { type: "heartbeat"; projectKey?: string; lane?: Lane }
  | { type: "architect_followup"; projectKey?: string }
  | { type: "handoff"; projectKey?: string; fromLane: Lane; toLane: Lane; summary: string }
  | { type: "lane_prompt"; projectKey?: string; lane: Lane; prompt: string }
  | { type: "map_channel"; projectKey: string };

type ProjectRecord = {
  key: string;
  name?: string;
  root: string;
  github_repo?: string;
  command_channel_id?: string;
};

type ProjectRegistry = { projects: ProjectRecord[] };

type GatewayPayload = { op: number; d: unknown; s: number | null; t: string | null };
type DiscordUser = { id: string; bot?: boolean; username?: string };
type DiscordMessage = {
  id: string;
  channel_id: string;
  guild_id?: string;
  content?: string;
  author?: DiscordUser;
  webhook_id?: string | null;
};
type InteractionOption = { name: string; type: number; value?: string | number | boolean; options?: InteractionOption[] };
type DiscordInteraction = {
  id: string;
  token: string;
  application_id: string;
  type: number;
  channel_id?: string;
  guild_id?: string;
  data?: { name?: string; options?: InteractionOption[] };
  member?: { user?: DiscordUser };
  user?: DiscordUser;
};

const CONFIG_DIR = path.join(os.homedir(), ".config", "clawhip");
const PROJECTS_PATH = process.env.CLAWHIP_PROJECTS_PATH || path.join(CONFIG_DIR, "projects.json");
const PROJECTCTL = path.join(CONFIG_DIR, "bin", "projectctl.py");
const DISCORD_API_BASE = "https://discord.com/api/v10";
const MAX_LEN = 1800;
const GUILDS_INTENT = 1 << 0;
const GUILD_MESSAGES_INTENT = 1 << 9;
const DIRECT_MESSAGES_INTENT = 1 << 12;
const MESSAGE_CONTENT_INTENT = 1 << 15;
const COMMAND_INTENTS = GUILDS_INTENT | GUILD_MESSAGES_INTENT | DIRECT_MESSAGES_INTENT | MESSAGE_CONTENT_INTENT;
const INTERACTION_PING = 1;
const INTERACTION_APPLICATION_COMMAND = 2;
const RESPONSE_CHANNEL_MESSAGE = 4;
const RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5;
const EPHEMERAL_FLAG = 1 << 6;

const LANE_MAP: Record<string, Lane> = {
  arch: "architect",
  architect: "architect",
  exec: "executor",
  executor: "executor",
  review: "reviewer",
  reviewer: "reviewer",
};

function normalizeLane(raw?: string): Lane | undefined {
  if (!raw) return undefined;
  return LANE_MAP[raw.trim().toLowerCase()];
}

function parseTextCommand(content: string): ParsedTextCommand | null {
  const trimmed = content.trim();
  if (!trimmed) return null;
  if (/^help$/i.test(trimmed)) return { type: "help" };
  if (/^projects$/i.test(trimmed)) return { type: "projects" };
  const statusMatch = trimmed.match(/^status(?:\s+(arch|architect|exec|executor|review|reviewer))?(?::.*)?$/i);
  if (statusMatch) return { type: "status", lane: normalizeLane(statusMatch[1]) };
  if (/^lanes\s+up$/i.test(trimmed)) return { type: "lanes_up" };
  const laneUpMatch = trimmed.match(/^(arch|architect|exec|executor|review|reviewer)\s+up$/i);
  if (laneUpMatch) {
    const lane = normalizeLane(laneUpMatch[1]);
    return lane ? { type: "lane_up", lane } : null;
  }
  const heartbeatMatch = trimmed.match(/^heartbeat(?:\s+(all|arch|architect|exec|executor|review|reviewer))?$/i);
  if (heartbeatMatch) {
    const raw = heartbeatMatch[1]?.toLowerCase();
    if (!raw || raw === "all") return { type: "heartbeat" };
    const lane = normalizeLane(raw);
    return lane ? { type: "heartbeat", lane } : null;
  }
  if (/^arch(?:itect)?\s+followup(?::.*)?$/i.test(trimmed)) return { type: "architect_followup" };
  const handoffMatch = trimmed.match(/^handoff\s+(arch|architect|exec|executor|review|reviewer)\s*->\s*(arch|architect|exec|executor|review|reviewer)\s*:\s*(.+)$/i);
  if (handoffMatch) {
    const fromLane = normalizeLane(handoffMatch[1]);
    const toLane = normalizeLane(handoffMatch[2]);
    const summary = handoffMatch[3]?.trim();
    if (!fromLane || !toLane || !summary) return null;
    return { type: "handoff", fromLane, toLane, summary };
  }
  const mapMatch = trimmed.match(/^map-channel\s+([a-zA-Z0-9._-]+)$/i);
  if (mapMatch) return { type: "map_channel", projectKey: mapMatch[1]!.trim() };
  const lanePromptMatch = trimmed.match(/^(arch|architect|exec|executor|review|reviewer)\s*:\s*(.+)$/i);
  if (lanePromptMatch) {
    const lane = normalizeLane(lanePromptMatch[1]);
    const prompt = lanePromptMatch[2]?.trim();
    return lane && prompt ? { type: "lane_prompt", lane, prompt } : null;
  }
  return null;
}

function splitCsv(value?: string): Set<string> {
  if (!value) return new Set();
  return new Set(value.split(",").map((v) => v.trim()).filter(Boolean));
}

function loadRegistry(): ProjectRegistry {
  if (!readFileSync) throw new Error("fs unavailable");
  return JSON.parse(readFileSync(PROJECTS_PATH, "utf8"));
}

async function saveRegistry(registry: ProjectRegistry): Promise<void> {
  await fs.writeFile(PROJECTS_PATH, `${JSON.stringify(registry, null, 2)}\n`, "utf8");
}

function truncate(text: string, limit = MAX_LEN): string {
  return text.length <= limit ? text : `${text.slice(0, limit - 15)}\n…[truncated]`;
}

function codeBlock(text: string): string {
  const safe = text.replace(/```/g, "'''");
  return `\`\`\`\n${truncate(safe, MAX_LEN - 8)}\n\`\`\``;
}

function redactSecrets(text: string): string {
  return text.replace(/https:\/\/discord\.com\/api\/webhooks\/\d+\/[^\s)\]]+/g, "https://discord.com/api/webhooks/<redacted>");
}

async function apiFetch(token: string, route: string, init?: RequestInit): Promise<Response> {
  return await fetch(`${DISCORD_API_BASE}${route}`, {
    ...init,
    headers: {
      Authorization: `Bot ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
}

async function sendMessage(token: string, channelId: string, content: string, replyTo?: string): Promise<void> {
  const body: Record<string, unknown> = { content: truncate(content), allowed_mentions: { parse: [] } };
  if (replyTo) body.message_reference = { message_id: replyTo };
  const res = await apiFetch(token, `/channels/${channelId}/messages`, { method: "POST", body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`Discord send failed (${res.status}): ${await res.text()}`);
}

async function execCapture(args: string[], cwd?: string): Promise<{ code: number; out: string }> {
  return await new Promise((resolve, reject) => {
    const child = spawn(args[0]!, args.slice(1), { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (c) => (out += c.toString()));
    child.stderr.on("data", (c) => (out += c.toString()));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? 1, out }));
  });
}

function findProjectByKey(registry: ProjectRegistry, key?: string): ProjectRecord | undefined {
  if (!key) return undefined;
  return registry.projects.find((p) => p.key === key);
}

function findProjectByChannel(registry: ProjectRegistry, channelId?: string): ProjectRecord | undefined {
  if (!channelId) return undefined;
  return registry.projects.find((p) => p.command_channel_id === channelId);
}

function resolveTextProject(content: string, channelId: string, registry: ProjectRegistry, defaultProject?: string): { project: ProjectRecord; content: string } | null {
  const prefixed = content.match(/^project\s+([a-zA-Z0-9._-]+)\s+(.+)$/i);
  if (prefixed) {
    const project = findProjectByKey(registry, prefixed[1]!);
    return project ? { project, content: prefixed[2]!.trim() } : null;
  }
  const byChannel = findProjectByChannel(registry, channelId);
  if (byChannel) return { project: byChannel, content };
  const project = findProjectByKey(registry, defaultProject);
  return project ? { project, content } : null;
}

function getNestedOptions(options?: InteractionOption[]): InteractionOption[] {
  return options ?? [];
}

function getOption(options: InteractionOption[] | undefined, name: string): InteractionOption | undefined {
  return getNestedOptions(options).find((opt) => opt.name === name);
}

function getStringOption(options: InteractionOption[] | undefined, name: string): string | undefined {
  const option = getOption(options, name);
  return typeof option?.value === "string" ? option.value : undefined;
}

function parseSlashCommand(interaction: DiscordInteraction): SlashCommand | null {
  const name = interaction.data?.name;
  const options = interaction.data?.options;
  switch (name) {
    case "help":
      return { type: "help" };
    case "projects":
      return { type: "projects" };
    case "status":
      return { type: "status", projectKey: getStringOption(options, "project"), lane: normalizeLane(getStringOption(options, "lane")) };
    case "lanes-up":
      return { type: "lanes_up", projectKey: getStringOption(options, "project") };
    case "lane-up": {
      const lane = normalizeLane(getStringOption(options, "lane"));
      return lane ? { type: "lane_up", projectKey: getStringOption(options, "project"), lane } : null;
    }
    case "heartbeat":
      return { type: "heartbeat", projectKey: getStringOption(options, "project"), lane: normalizeLane(getStringOption(options, "lane")) };
    case "architect-followup":
      return { type: "architect_followup", projectKey: getStringOption(options, "project") };
    case "handoff": {
      const fromLane = normalizeLane(getStringOption(options, "from_lane"));
      const toLane = normalizeLane(getStringOption(options, "to_lane"));
      const summary = getStringOption(options, "summary");
      if (!fromLane || !toLane || !summary) return null;
      return { type: "handoff", projectKey: getStringOption(options, "project"), fromLane, toLane, summary };
    }
    case "prompt": {
      const lane = normalizeLane(getStringOption(options, "lane"));
      const prompt = getStringOption(options, "prompt");
      if (!lane || !prompt) return null;
      return { type: "lane_prompt", projectKey: getStringOption(options, "project"), lane, prompt };
    }
    case "map-channel": {
      const projectKey = getStringOption(options, "project");
      return projectKey ? { type: "map_channel", projectKey } : null;
    }
    default:
      return null;
  }
}

async function resolveSlashProject(registry: ProjectRegistry, interaction: DiscordInteraction, projectKey?: string, defaultProject?: string): Promise<ProjectRecord | null> {
  if (projectKey) {
    const project = findProjectByKey(registry, projectKey);
    if (!project) return null;
    if (interaction.channel_id) {
      const existing = findProjectByChannel(registry, interaction.channel_id);
      if (!existing || existing.key === project.key) {
        if (project.command_channel_id !== interaction.channel_id) {
          project.command_channel_id = interaction.channel_id;
          await saveRegistry(registry);
        }
      }
    }
    return project;
  }
  const byChannel = findProjectByChannel(registry, interaction.channel_id);
  if (byChannel) return byChannel;
  return findProjectByKey(registry, defaultProject) ?? null;
}

async function executeForProject(project: ProjectRecord, command: Exclude<SlashCommand, { type: "map_channel" | "projects" | "help" }> | ParsedTextCommand | { type: "projects" } | { type: "help" }): Promise<string> {
  const root = project.root;
  switch (command.type) {
    case "help":
      return [
        `Project: ${project.key}`,
        "Text: HELP / STATUS / LANES UP / ARCH FOLLOWUP / HANDOFF ARCH -> EXEC: summary / ARCH: prompt",
        "Slash: /help /projects /status /lanes-up /lane-up /heartbeat /architect-followup /handoff /prompt /map-channel",
        "Multi-project text prefix: PROJECT <key> STATUS",
      ].join("\n");
    case "projects":
      throw new Error("projects command should be handled without a specific project context");
    case "status": {
      const args = ["python3", PROJECTCTL, "status", project.key];
      if (command.lane) args.push(command.lane);
      const res = await execCapture(args, root);
      return res.code === 0 ? codeBlock(redactSecrets(res.out)) : `❌ STATUS failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "lanes_up": {
      const res = await execCapture(["python3", PROJECTCTL, "lanes-up", project.key], root);
      return res.code === 0 ? `✅ LANES UP (${project.key})\n${truncate(redactSecrets(res.out), 1200)}` : `❌ LANES UP failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "lane_up": {
      const res = await execCapture(["python3", PROJECTCTL, "lane-up", project.key, command.lane], root);
      return res.code === 0 ? `✅ ${command.lane.toUpperCase()} UP (${project.key})\n${truncate(redactSecrets(res.out), 1200)}` : `❌ lane up failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "heartbeat": {
      const args = ["python3", PROJECTCTL, "heartbeat", project.key];
      if (command.lane) args.push(command.lane);
      const res = await execCapture(args, root);
      return res.code === 0 ? `💓 heartbeat sent for ${project.key}${command.lane ? `:${command.lane}` : ""}` : `❌ heartbeat failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "architect_followup": {
      const res = await execCapture(["python3", PROJECTCTL, "followup", project.key], root);
      return res.code === 0 ? `🏗️ architect follow-up dispatched for ${project.key}` : `❌ architect follow-up failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "handoff": {
      const res = await execCapture(["python3", PROJECTCTL, "handoff", project.key, command.fromLane, command.toLane, command.summary], root);
      return res.code === 0 ? `🔁 HANDOFF ${command.fromLane} -> ${command.toLane} (${project.key})` : `❌ handoff failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "lane_prompt": {
      const temp = path.join(os.tmpdir(), `clawhip-discord-${project.key}-${Date.now()}.txt`);
      await fs.writeFile(temp, `${command.prompt}\n`, "utf8");
      const res = await execCapture(["python3", PROJECTCTL, "keepalive", project.key, command.lane, "--prompt-file", temp, "--timeout", "5"], root);
      await fs.unlink(temp).catch(() => undefined);
      return res.code === 0 ? `📨 ${command.lane.toUpperCase()} prompt sent for ${project.key}` : `❌ lane prompt failed\n${codeBlock(redactSecrets(res.out))}`;
    }
    case "map_channel":
      throw new Error("map-channel should be handled outside project execution");
  }
}

async function listProjects(registry: ProjectRegistry): Promise<string> {
  if (registry.projects.length === 0) return "No registered projects.";
  return registry.projects
    .map((p) => `• ${p.key} — ${p.root}${p.command_channel_id ? ` — channel ${p.command_channel_id}` : ""}`)
    .join("\n");
}

async function mapChannel(registry: ProjectRegistry, projectKey: string, channelId?: string): Promise<string> {
  if (!channelId) throw new Error("No channel_id on interaction/message");
  const project = findProjectByKey(registry, projectKey);
  if (!project) throw new Error(`Unknown project key: ${projectKey}`);
  const existing = findProjectByChannel(registry, channelId);
  if (existing && existing.key !== project.key) throw new Error(`Channel already mapped to project ${existing.key}`);
  project.command_channel_id = channelId;
  await saveRegistry(registry);
  return `✅ Mapped channel ${channelId} to project ${project.key}`;
}

async function getChannelGuildId(token: string, channelId: string): Promise<string | null> {
  const res = await apiFetch(token, `/channels/${channelId}`);
  if (!res.ok) return null;
  const data = (await res.json()) as { guild_id?: string };
  return data.guild_id ?? null;
}

function slashCommandDefinitions() {
  const laneChoices = [
    { name: "architect", value: "architect" },
    { name: "executor", value: "executor" },
    { name: "reviewer", value: "reviewer" },
  ];
  const projectOption = {
    type: 3,
    name: "project",
    description: "Registered project key",
    required: false,
  };
  return [
    { name: "help", description: "Show orchestration help", type: 1 },
    { name: "projects", description: "List registered projects", type: 1 },
    { name: "status", description: "Show project or lane status", type: 1, options: [projectOption, { type: 3, name: "lane", description: "Lane", required: false, choices: laneChoices }] },
    { name: "lanes-up", description: "Start all lanes for a project", type: 1, options: [projectOption] },
    { name: "lane-up", description: "Start one lane for a project", type: 1, options: [{ type: 3, name: "lane", description: "Lane", required: true, choices: laneChoices }, { ...projectOption }] },
    { name: "heartbeat", description: "Send lane heartbeat(s)", type: 1, options: [{ type: 3, name: "lane", description: "Lane", required: false, choices: laneChoices }, projectOption] },
    { name: "architect-followup", description: "Send a GitHub-aware follow-up to the architect lane", type: 1, options: [projectOption] },
    { name: "handoff", description: "Hand work from one lane to another", type: 1, options: [{ type: 3, name: "from_lane", description: "From lane", required: true, choices: laneChoices }, { type: 3, name: "to_lane", description: "To lane", required: true, choices: laneChoices }, { type: 3, name: "summary", description: "Handoff summary", required: true }, { ...projectOption }] },
    { name: "prompt", description: "Send a direct prompt to one lane", type: 1, options: [{ type: 3, name: "lane", description: "Lane", required: true, choices: laneChoices }, { type: 3, name: "prompt", description: "Prompt text", required: true }, { ...projectOption }] },
    { name: "map-channel", description: "Map this channel to a registered project", type: 1, options: [{ type: 3, name: "project", description: "Registered project key", required: true }] },
  ];
}

const token = process.env.DISCORD_BOT_TOKEN?.trim();
if (!token) throw new Error("DISCORD_BOT_TOKEN is required");
const allowedChannels = splitCsv(process.env.DISCORD_ALLOWED_CHANNELS);
const allowedUsers = splitCsv(process.env.DISCORD_ALLOWED_USERS);
const defaultProject = process.env.DISCORD_DEFAULT_PROJECT?.trim();
const gatewayUrl = process.env.DISCORD_GATEWAY_URL?.trim() || "wss://gateway.discord.gg/?v=10&encoding=json";
const applicationIdFromEnv = process.env.DISCORD_APPLICATION_ID?.trim();

class Bot {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private resumeGatewayUrl: string | null = null;
  private seq: number | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private heartbeatAcked = true;
  private selfUserId: string | null = null;
  private stopped = false;
  private registeredGuilds = new Set<string>();

  start() { void this.connect(false); }
  stop() { this.stopped = true; if (this.heartbeatTimer) clearInterval(this.heartbeatTimer); this.ws?.close(); }

  private connect(resume: boolean) {
    const url = resume && this.resumeGatewayUrl ? this.resumeGatewayUrl : gatewayUrl;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.addEventListener("open", () => console.log(`[clawhip-bot] gateway open (${resume ? "resume" : "identify"})`));
    ws.addEventListener("message", (event) => { void this.onPayload(JSON.parse(String(event.data)) as GatewayPayload); });
    ws.addEventListener("close", () => {
      if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
      if (!this.stopped) setTimeout(() => this.connect(Boolean(this.sessionId && this.seq !== null)), 2000);
    });
    ws.addEventListener("error", (event) => console.error("[clawhip-bot] gateway error", event));
  }

  private send(obj: object) { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj)); }
  private startHeartbeat(interval: number) {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (!this.heartbeatAcked) { this.ws?.close(); return; }
      this.heartbeatAcked = false;
      this.send({ op: 1, d: this.seq });
    }, interval);
  }

  private identify() {
    this.send({ op: 2, d: { token, intents: COMMAND_INTENTS, properties: { os: process.platform, browser: "clawhip-machine-bot", device: "clawhip-machine-bot" } } });
  }

  private resume() {
    if (!this.sessionId) return this.identify();
    this.send({ op: 6, d: { token, session_id: this.sessionId, seq: this.seq } });
  }

  private async ensureSlashCommands(): Promise<void> {
    const registry = loadRegistry();
    const channelIds = new Set<string>();
    for (const project of registry.projects) {
      if (project.command_channel_id) channelIds.add(project.command_channel_id);
    }
    for (const channelId of allowedChannels) channelIds.add(channelId);
    const guildIds = new Set<string>();
    for (const channelId of channelIds) {
      const guildId = await getChannelGuildId(token, channelId);
      if (guildId) guildIds.add(guildId);
    }
    const appId = applicationIdFromEnv || this.selfUserId;
    if (!appId) return;
    const body = JSON.stringify(slashCommandDefinitions());
    for (const guildId of guildIds) {
      if (this.registeredGuilds.has(guildId)) continue;
      const res = await apiFetch(token, `/applications/${appId}/guilds/${guildId}/commands`, { method: "PUT", body });
      if (!res.ok) {
        console.error(`[clawhip-bot] slash command registration failed for guild ${guildId}: ${await res.text()}`);
        continue;
      }
      this.registeredGuilds.add(guildId);
      console.log(`[clawhip-bot] slash commands registered for guild ${guildId}`);
    }
  }

  private async respondInteractionDeferred(interaction: DiscordInteraction): Promise<void> {
    const body = { type: RESPONSE_DEFERRED_CHANNEL_MESSAGE, data: { flags: EPHEMERAL_FLAG } };
    const res = await apiFetch(token, `/interactions/${interaction.id}/${interaction.token}/callback`, { method: "POST", body: JSON.stringify(body) });
    if (!res.ok) throw new Error(`interaction defer failed (${res.status}): ${await res.text()}`);
  }

  private async respondInteractionImmediate(interaction: DiscordInteraction, content: string): Promise<void> {
    const body = { type: RESPONSE_CHANNEL_MESSAGE, data: { content: truncate(content), flags: EPHEMERAL_FLAG, allowed_mentions: { parse: [] } } };
    const res = await apiFetch(token, `/interactions/${interaction.id}/${interaction.token}/callback`, { method: "POST", body: JSON.stringify(body) });
    if (!res.ok) throw new Error(`interaction response failed (${res.status}): ${await res.text()}`);
  }

  private async editInteractionResponse(interaction: DiscordInteraction, content: string): Promise<void> {
    const appId = interaction.application_id || applicationIdFromEnv || this.selfUserId;
    if (!appId) throw new Error("missing application id for interaction response");
    const res = await apiFetch(token, `/webhooks/${appId}/${interaction.token}/messages/@original`, { method: "PATCH", body: JSON.stringify({ content: truncate(content), allowed_mentions: { parse: [] } }) });
    if (!res.ok) throw new Error(`interaction edit failed (${res.status}): ${await res.text()}`);
  }

  private async onPayload(payload: GatewayPayload) {
    if (typeof payload.s === "number") this.seq = payload.s;
    switch (payload.op) {
      case 10:
        this.startHeartbeat((payload.d as { heartbeat_interval: number }).heartbeat_interval);
        if (this.sessionId && this.seq !== null && this.resumeGatewayUrl) this.resume(); else this.identify();
        return;
      case 11:
        this.heartbeatAcked = true;
        return;
      case 7:
        this.ws?.close();
        return;
      case 9:
        this.sessionId = null; this.seq = null; setTimeout(() => this.connect(false), 2000); return;
      case 0:
        break;
      default:
        return;
    }
    if (payload.t === "READY") {
      const d = payload.d as { session_id: string; resume_gateway_url: string; user?: { id: string; username?: string } };
      this.sessionId = d.session_id;
      this.resumeGatewayUrl = `${d.resume_gateway_url}?v=10&encoding=json`;
      this.selfUserId = d.user?.id ?? null;
      console.log(`[clawhip-bot] ready as ${d.user?.username ?? "bot"}`);
      void this.ensureSlashCommands();
      return;
    }
    if (payload.t === "INTERACTION_CREATE") {
      void this.handleInteraction(payload.d as DiscordInteraction);
      return;
    }
    if (payload.t !== "MESSAGE_CREATE") return;
    const msg = payload.d as DiscordMessage;
    await this.handleMessage(msg);
  }

  private async handleMessage(msg: DiscordMessage): Promise<void> {
    if (!msg.author || msg.author.bot || msg.webhook_id) return;
    const registry = loadRegistry();
    const mapped = findProjectByChannel(registry, msg.channel_id);
    if (allowedChannels.size && !allowedChannels.has(msg.channel_id) && !mapped) return;
    if (allowedUsers.size && !allowedUsers.has(msg.author.id)) return;
    let content = (msg.content || "").trim();
    if (!content) return;
    if (this.selfUserId) content = content.replace(new RegExp(`^<@!?${this.selfUserId}>\\s*`, "i"), "").trim();
    const resolved = resolveTextProject(content, msg.channel_id, registry, defaultProject);
    if (!resolved) return;
    const parsed = parseTextCommand(resolved.content);
    if (!parsed) return;
    try {
      const reply = await this.executeParsedCommand(registry, resolved.project, parsed, msg.channel_id);
      await sendMessage(token, msg.channel_id, reply, msg.id);
    } catch (err) {
      await sendMessage(token, msg.channel_id, `❌ Command failed\n${codeBlock(String(err))}`, msg.id);
    }
  }

  private async executeParsedCommand(registry: ProjectRegistry, project: ProjectRecord | null, command: ParsedTextCommand | SlashCommand, channelId?: string): Promise<string> {
    if (command.type === "projects") {
      return listProjects(registry);
    }
    if (command.type === "map_channel") {
      return await mapChannel(registry, command.projectKey, channelId);
    }
    if (!project) throw new Error("No project resolved for this command");
    return await executeForProject(project, command as Exclude<ParsedTextCommand | SlashCommand, { type: "projects" | "map_channel" }>);
  }

  private async handleInteraction(interaction: DiscordInteraction): Promise<void> {
    const actorId = interaction.member?.user?.id || interaction.user?.id;
    if (allowedUsers.size && actorId && !allowedUsers.has(actorId)) {
      await this.respondInteractionImmediate(interaction, "❌ You are not allowed to use this bot here.");
      return;
    }
    if (interaction.type === INTERACTION_PING) {
      await this.respondInteractionImmediate(interaction, "pong");
      return;
    }
    if (interaction.type !== INTERACTION_APPLICATION_COMMAND) return;
    const registry = loadRegistry();
    const parsed = parseSlashCommand(interaction);
    if (!parsed) {
      await this.respondInteractionImmediate(interaction, "❌ Unknown slash command.");
      return;
    }
    const explicitProjectKey = "projectKey" in parsed ? parsed.projectKey : undefined;
    const project = await resolveSlashProject(registry, interaction, explicitProjectKey, defaultProject);
    try {
      await this.respondInteractionDeferred(interaction);
      const reply = await this.executeParsedCommand(registry, project, parsed, interaction.channel_id);
      await this.editInteractionResponse(interaction, reply);
    } catch (err) {
      try {
        await this.editInteractionResponse(interaction, `❌ Command failed\n${codeBlock(String(err))}`);
      } catch (inner) {
        console.error("[clawhip-bot] interaction failure", err, inner);
      }
    }
  }
}

const bot = new Bot();
process.on("SIGINT", () => bot.stop());
process.on("SIGTERM", () => bot.stop());
bot.start();
