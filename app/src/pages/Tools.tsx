import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import { Avatar, labelOf } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { Field, Page, Segmented, Sheet } from "../components/ui";
import type { Crew } from "../state";
import type { InstalledMcp, McpCheckResult, McpServerInfo, RegistryServer, Skill } from "../types";

/** What the bots can reach beyond their own CLI: the crew's MCP servers, which a bot gets only when its card names
 * one, and the skills every bot can read. */
export function Tools({ crew }: { crew: Crew }) {
  const [servers, setServers] = useState<McpServerInfo[] | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [adding, setAdding] = useState(false);
  const [checks, setChecks] = useState<Record<string, McpCheckResult | "checking">>({});
  const [error, setError] = useState<string | null>(null);
  const load = () => api.mcpServers().then(setServers, (e) => setError(String(e).replace(/^Error: /, "")));
  useEffect(() => {
    load();
    api.skills().then(setSkills, () => setSkills([]));
  }, [crew.bots.length]);

  const check = async (name: string) => {
    setChecks((c) => ({ ...c, [name]: "checking" }));
    const result = await api.checkMcpServer(name).catch((e) => ({ ok: false, tools: [], error: String(e) }));
    setChecks((c) => ({ ...c, [name]: result }));
  };
  const remove = async (s: McpServerInfo) => {
    const users = s.bots.length ? ` ${s.bots.map(labelOf).join(", ")} will lose it too.` : "";
    if (!window.confirm(`Remove ${s.name} from the crew?${users}`)) return;
    await api.removeMcpServer(s.name).catch((e) => setError(String(e)));
    load();
  };
  const groups = [...new Set(skills.map((s) => s.source))];

  return (
    <Page title="Tools">
      <section className="page-section">
        <div className="section-head">
          <h2>MCP servers</h2>
          <span className="grow" />
          <button className="button small primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={13} />
            Add a server
          </button>
        </div>
        <p className="faint small">A bot uses a server only when its card names it: choose them in each bot's editor. Your own Claude and Codex servers never reach a bot otherwise.</p>
        {error && <p className="error-text small">{error}</p>}
        {servers?.length === 0 && <p className="faint">No servers yet. Add the ones your other apps have, or find one in the registry.</p>}
        <div className="list">
          {servers?.map((s) => {
            const checked = checks[s.name];
            return (
              <div key={s.name} className="list-row mcp-row">
                <span className="list-text">
                  <span className="list-title">
                    {s.name} <span className="tag">{s.transport === "stdio" ? "Program" : s.transport === "sse" ? "URL · SSE" : "URL"}</span>
                  </span>
                  <span className="faint mono small ellipsis" title={s.url ?? [s.command, ...s.args].join(" ")}>
                    {s.url ?? [s.command, ...s.args].join(" ")}
                  </span>
                  {checked && checked !== "checking" && (
                    <span className={`small ${checked.ok ? "faint" : "error-text"}`} title={checked.ok ? checked.tools.join(", ") : undefined}>
                      {checked.ok ? `${checked.tools.length} tools: ${checked.tools.slice(0, 6).join(", ")}${checked.tools.length > 6 ? "…" : ""}` : checked.error}
                    </span>
                  )}
                </span>
                <span className="mcp-users" title={s.bots.length ? `Used by ${s.bots.map(labelOf).join(", ")}` : "No bot uses it yet"}>
                  {s.bots.map((b) => (
                    <Avatar key={b} name={b} size={20} />
                  ))}
                </span>
                <button className="button small" disabled={checked === "checking"} onClick={() => check(s.name)} title="Start it and list its tools">
                  {checked === "checking" ? "Checking…" : "Check"}
                </button>
                <button className="icon-button" title="Remove" onClick={() => remove(s)}>
                  <Icon name="x" size={14} />
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section className="page-section">
        <h2>Skills</h2>
        <p className="faint small">Every bot sees these and reads one when a task matches it, unless its card names only some. The crew's own come first; your global ones are the same skills Claude Code and Codex use.</p>
        {groups.map((source) => (
          <div key={source} className="skill-group">
            <h3>{source === "crew" ? "The crew's" : source}</h3>
            <div className="list">
              {skills
                .filter((s) => s.source === source)
                .map((s) => (
                  <button key={s.name} className="list-row skill-row" onClick={() => bridge.reveal(s.path)} title={s.path}>
                    <span className="list-text">
                      <span className="list-title">{s.name}</span>
                      <span className="faint small ellipsis">{s.description}</span>
                    </span>
                  </button>
                ))}
            </div>
          </div>
        ))}
      </section>

      {adding && (
        <AddServer
          taken={new Set(servers?.map((s) => s.name))}
          onClose={() => setAdding(false)}
          onAdded={() => {
            load();
          }}
        />
      )}
    </Page>
  );
}

type Way = "apps" | "registry" | "custom";

/** Three ways to add one: copy it from your other apps, find it in the MCP registry, or write it out. */
function AddServer({ taken, onClose, onAdded }: { taken: Set<string>; onClose: () => void; onAdded: () => void }) {
  const [way, setWay] = useState<Way>("apps");
  return (
    <Sheet title="Add an MCP server" onClose={onClose} footer={<button className="button" onClick={onClose}>Done</button>}>
      <Segmented value={way} options={[["apps", "Your apps"], ["registry", "Browse"], ["custom", "Custom"]]} onChange={setWay} />
      <div className="add-server-body">
        {way === "apps" ? <FromApps taken={taken} onAdded={onAdded} /> : way === "registry" ? <FromRegistry taken={taken} onAdded={onAdded} /> : <Custom taken={taken} onAdded={onAdded} />}
      </div>
    </Sheet>
  );
}

function FromApps({ taken, onAdded }: { taken: Set<string>; onAdded: () => void }) {
  const [found, setFound] = useState<InstalledMcp[] | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.installedMcp().then(setFound, (e) => setError(String(e)));
  }, []);
  if (error) return <p className="error-text small">{error}</p>;
  if (!found) return <p className="faint small">Looking in Claude Code, Claude's desktop app and Codex…</p>;
  if (found.length === 0) return <p className="faint small">None of your apps has an MCP server set up.</p>;
  return (
    <>
      <p className="faint small">Copied as they are set up there, env and all.</p>
      <div className="list">
        {found.map((s) => {
          const there = s.in_crew || taken.has(s.name) || added.has(s.id);
          return (
            <div key={s.id} className="list-row mcp-row">
              <span className="list-text">
                <span className="list-title">
                  {s.name} <span className="faint small">{s.source}</span>
                </span>
                <span className="faint mono small ellipsis">{s.url ?? [s.command, ...s.args].join(" ")}</span>
              </span>
              <button
                className="button small"
                disabled={there}
                onClick={async () => {
                  try {
                    await api.importMcp(s.id);
                    setAdded(new Set([...added, s.id]));
                    onAdded();
                  } catch (e) {
                    setError(String(e).replace(/^Error: /, ""));
                  }
                }}
              >
                {there ? "Added" : "Add"}
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}

function FromRegistry({ taken, onAdded }: { taken: Set<string>; onAdded: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RegistryServer[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setResults(await api.searchMcp(query.trim()));
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setSearching(false);
    }
  };
  return (
    <>
      <form
        className="registry-search"
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the MCP registry: blender, github, figma…" autoFocus />
        <button className="button small" disabled={searching || !query.trim()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </form>
      <p className="faint small">The official registry (registry.modelcontextprotocol.io). Its search matches names, so try the app or company's name.</p>
      {error && <p className="error-text small">{error}</p>}
      {results?.length === 0 && <p className="faint small">Nothing by that name.</p>}
      <div className="list">
        {results?.map((r) => (
          <div key={r.name} className="registry-entry">
            <button className="list-row registry-head" onClick={() => setOpen(open === r.name ? null : r.name)}>
              <span className="list-text">
                <span className="list-title">
                  {r.title ?? r.name} <span className="faint small">{r.version}</span>
                </span>
                <span className="faint small">{r.description}</span>
                <span className="faint mono small">{r.name}</span>
              </span>
              <Icon name={open === r.name ? "chevronDown" : "chevronRight"} size={13} />
            </button>
            {open === r.name && <RegistryForm entry={r} taken={taken} onAdded={onAdded} />}
          </div>
        ))}
      </div>
    </>
  );
}

function RegistryForm({ entry, taken, onAdded }: { entry: RegistryServer; taken: Set<string>; onAdded: () => void }) {
  const [option, setOption] = useState(entry.options[0]?.id ?? "");
  const [name, setName] = useState(defaultName(entry.name));
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "adding" | "added">("idle");
  const [error, setError] = useState<string | null>(null);
  const chosen = entry.options.find((o) => o.id === option);
  if (entry.options.length === 0) return <p className="faint small pad-x">It doesn't say how to run it. Add it under Custom, from its own instructions.</p>;
  return (
    <div className="registry-form">
      {entry.options.length > 1 && (
        <Field label="Run it as">
          <select value={option} onChange={(e) => setOption(e.target.value)}>
            {entry.options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Name" hint="What the crew and its role cards call it.">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      {chosen?.fields.map((f) => (
        <Field key={`${f.kind}-${f.key}`} label={`${f.key}${f.required ? "" : " (optional)"}`} hint={f.description || undefined}>
          <input type={f.secret ? "password" : "text"} value={values[f.key] ?? ""} placeholder={f.default ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
        </Field>
      ))}
      {error && <p className="error-text small">{error}</p>}
      <div className="form-actions">
        {entry.website && (
          <button className="text-button" onClick={() => bridge.openUrl(entry.website!)}>
            Its page
          </button>
        )}
        <span className="grow" />
        <button
          className="button small primary"
          disabled={state !== "idle" || !name || taken.has(name)}
          onClick={async () => {
            setState("adding");
            setError(null);
            try {
              await api.addFromRegistry({ server: entry.name, version: entry.version, option, name, values });
              setState("added");
              onAdded();
            } catch (e) {
              setError(String(e).replace(/^Error: /, ""));
              setState("idle");
            }
          }}
        >
          {state === "added" ? "Added" : taken.has(name) ? "Name taken" : state === "adding" ? "Adding…" : "Add"}
        </button>
      </div>
    </div>
  );
}

function Custom({ taken, onAdded }: { taken: Set<string>; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"stdio" | "http">("stdio");
  const [command, setCommand] = useState("");
  const [env, setEnv] = useState("");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState("");
  const [state, setState] = useState<"idle" | "adding" | "added">("idle");
  const [error, setError] = useState<string | null>(null);
  const pairs = (text: string, sep: string) =>
    Object.fromEntries(
      text
        .split("\n")
        .map((line) => line.split(sep))
        .filter(([k, ...v]) => k.trim() && v.length)
        .map(([k, ...v]) => [k.trim(), v.join(sep).trim()]),
    );
  const add = async () => {
    setState("adding");
    setError(null);
    const words = command.trim().split(/\s+/);
    try {
      await api.saveMcpServer(name, kind === "stdio" ? { transport: "stdio", command: words[0], args: words.slice(1), env: pairs(env, "=") } : { transport: "http", url: url.trim(), headers: pairs(headers, ":") });
      setState("added");
      onAdded();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
      setState("idle");
    }
  };
  return (
    <div className="registry-form">
      <Field label="Name" hint="Letters, digits, - and _.">
        <input value={name} onChange={(e) => setName(e.target.value.trim())} placeholder="blender" />
      </Field>
      <Segmented value={kind} options={[["stdio", "A program to start"], ["http", "A URL"]]} onChange={setKind} />
      {kind === "stdio" ? (
        <>
          <Field label="Command" hint="The program and its arguments, as you'd type them.">
            <input className="mono" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="uvx blender-mcp" />
          </Field>
          <Field label="Environment (optional)" hint="One KEY=value a line.">
            <textarea className="mono" rows={3} value={env} onChange={(e) => setEnv(e.target.value)} placeholder="BLENDER_PORT=9876" />
          </Field>
        </>
      ) : (
        <>
          <Field label="URL" hint="Its streamable HTTP address.">
            <input className="mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:8080/mcp" />
          </Field>
          <Field label="Headers (optional)" hint="One Name: value a line, such as an Authorization header.">
            <textarea className="mono" rows={2} value={headers} onChange={(e) => setHeaders(e.target.value)} />
          </Field>
        </>
      )}
      {error && <p className="error-text small">{error}</p>}
      <div className="form-actions">
        <span className="grow" />
        <button className="button small primary" disabled={state !== "idle" || !name || taken.has(name) || (kind === "stdio" ? !command.trim() : !url.trim())} onClick={add}>
          {state === "added" ? "Added" : taken.has(name) ? "Name taken" : state === "adding" ? "Adding…" : "Add"}
        </button>
      </div>
    </div>
  );
}

/** "io.github.someone/blender-mcp" → "blender-mcp". */
function defaultName(registryName: string): string {
  return (registryName.split("/").at(-1) ?? registryName).replace(/[^A-Za-z0-9_-]+/g, "-");
}
