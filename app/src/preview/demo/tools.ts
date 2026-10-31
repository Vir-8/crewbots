// The crew's MCP servers and routines, for the preview's bridge: kept while the preview is open, as the daemon keeps them.

import type { McpServerInfo } from "../../types";
import { mcpServers, routines } from "../fixtures";

type Answer = { ok: boolean; status: number; data: unknown };
type Server = Omit<McpServerInfo, "bots">;
const ok = (data: unknown) => ({ ok: true, status: 200, data });
const missing = (what: string) => ({ ok: false, status: 404, data: { detail: `no ${what}` } });

/** The servers your other apps have set up. */
const installed: { id: string; source: string; server: Server }[] = [
  { id: "a", source: "Claude Code", server: { name: "blender", transport: "stdio", command: "uvx", args: ["blender-mcp"], url: null, env_keys: ["BLENDER_PORT"], header_keys: [] } },
  { id: "b", source: "Claude Code · Glider", server: { name: "UnityMCP", transport: "http", command: null, args: [], url: "http://127.0.0.1:8080/mcp", env_keys: [], header_keys: [] } },
  { id: "c", source: "Codex", server: { name: "postgres", transport: "stdio", command: "npx", args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/acme"], url: null, env_keys: [], header_keys: [] } },
];
const registry = [{ name: "io.github.someone/figma-mcp", title: "Figma", description: "Read Figma files, frames and components.", version: "1.4.0", website: "https://github.com/someone/figma-mcp", options: [{ id: "package:0", label: "npm · figma-mcp 1.4.0", fields: [{ kind: "env", key: "FIGMA_TOKEN", description: "A personal access token", required: true, secret: true, default: null }] }, { id: "remote:0", label: "Remote · https://mcp.figma.example/mcp", fields: [] }] }];

let kept: ReturnType<typeof routines> | undefined;
const routineList = () => (kept ??= routines());

/** Adds a server to the crew's, or replaces the one of that name, keeping the bots that use it. */
function save(server: Server) {
  const at = mcpServers.findIndex((s) => s.name === server.name);
  const saved = { ...server, bots: mcpServers[at]?.bots ?? [] };
  if (at < 0) mcpServers.push(saved);
  else mcpServers[at] = saved;
  return ok(saved);
}

/** `run` starts a routine by hand, which confirming a scheduled one does as its test. */
export async function toolsRequest(method: string, pathname: string, parts: string[], body: any, run: (name: string) => Promise<Answer>) {
  if (parts[0] === "mcp") return mcpRequest(method, pathname, parts, body);
  if (parts[0] !== "routines") return null;
  const list = routineList();
  if (method === "GET" && pathname === "/routines") return ok(structuredClone(list)); // copies, as over the wire
  const at = list.routines.findIndex((r) => r.name === parts[1]);
  const routine = list.routines[at];
  if (method === "DELETE" && parts.length === 2) {
    if (!routine) return missing(`routine named ${parts[1]}`);
    list.routines.splice(at, 1);
    return ok({ ok: true });
  }
  if (method === "POST" && parts[2] === "confirm") {
    if (!routine) return missing(`routine named ${parts[1]}`);
    Object.assign(routine, { proposed_by: null, enabled: true });
    return routine.schedule ? run(routine.name) : ok(null);
  }
  if (parts[2] === "file" && routine) {
    if (method === "PUT") Object.assign(routine, body);
    return ok({ goal: "Write the standup for what the crew did since {{since}}.\n\n{{activity}}", ...routine });
  }
  return null;
}

function mcpRequest(method: string, pathname: string, parts: string[], body: any) {
  if (method === "GET" && pathname === "/mcp/servers") return ok(structuredClone(mcpServers));
  if (method === "GET" && pathname === "/mcp/installed") {
    return ok(installed.map(({ id, source, server }) => ({ id, source, ...server, in_crew: mcpServers.some((s) => s.name === server.name) })));
  }
  if (method === "GET" && pathname === "/mcp/registry") return ok(registry);
  if (method === "POST" && pathname === "/mcp/import") {
    const found = installed.find((s) => s.id === body.id);
    return found ? save({ ...found.server, name: body.name ?? found.server.name }) : missing("such server in your apps' configs");
  }
  if (method === "POST" && pathname === "/mcp/registry") {
    const keys = Object.keys(body.values);
    const remote = String(body.option).startsWith("remote");
    return save(remote
      ? { name: body.name, transport: "http", command: null, args: [], url: "https://mcp.figma.example/mcp", env_keys: [], header_keys: keys }
      : { name: body.name, transport: "stdio", command: "npx", args: ["-y", `figma-mcp@${body.version}`], url: null, env_keys: keys, header_keys: [] });
  }
  if (parts[1] !== "servers" || !parts[2]) return null;
  const name = decodeURIComponent(parts[2]);
  if (method === "PUT") {
    const stdio = body.transport === "stdio";
    return save({ name, transport: body.transport, command: stdio ? body.command : null, args: stdio ? body.args : [], url: stdio ? null : body.url, env_keys: Object.keys(body.env ?? {}), header_keys: Object.keys(body.headers ?? {}) });
  }
  if (method === "DELETE") {
    const at = mcpServers.findIndex((s) => s.name === name);
    return ok({ removed: name, bots: at < 0 ? [] : mcpServers.splice(at, 1)[0].bots });
  }
  if (method === "POST" && parts[3] === "check") return ok({ ok: true, tools: ["get_scene_info", "create_object", "modify_object", "render", "execute_blender_code"], error: null });
  return null;
}
