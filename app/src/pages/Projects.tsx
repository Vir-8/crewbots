import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { ProjectOut } from "../api-types";
import { labelOf } from "../components/Avatar";
import { BringIn } from "../components/BringIn";
import { Icon } from "../components/Icon";
import { Field, Page, Segmented, Sheet } from "../components/ui";
import type { Crew } from "../state";

type Draft = { name: string; folder: string; about: string; existing: boolean; isolation?: ProjectOut["isolation"] };

const WHERE: Record<ProjectOut["isolation"], string> = { in_place: "The folder itself", worktree: "A worktree each" };
const HOW: Record<ProjectOut["isolation"], string> = {
  in_place:
    "Tasks work side by side in this folder, on the branch it's on, as you do: the lead gives each engineer different files. A Unity project's one Editor sees all of it, and one task drives it at a time.",
  worktree:
    "Tasks run side by side, each in its own checkout of the project, which takes the disk space of its tracked files. In Unity, each worktree needs an Editor opened on it (its first open imports everything), and then engineers check their work at the same time.",
};

/** Your projects: the folders the crew works in. A bot's chat isn't any one project's; work on a project runs in its
 * folder (in a worktree of its own when a bot writes in a git repository), and every bot sees the list, with what
 * each project is. You add and remove them; the bots can't. */
export function Projects(props: { crew: Crew; onChat: (bot: string) => void }) {
  const [projects, setProjects] = useState<ProjectOut[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Draft | null>(null);
  const [bringing, setBringing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chief = props.crew.bots.find((b) => b.can_delegate) ?? props.crew.bots[0];

  const load = () =>
    api.projects().then(
      (p) => {
        setProjects(p.projects);
        setErrors(p.errors);
      },
      (e) => setError(message(e)),
    );
  useEffect(() => {
    void load();
  }, []);
  const add = async () => {
    const folder = await bridge.pickFolder();
    if (folder) setDraft({ name: nameFor(folder), folder, about: "", existing: false });
  };
  const save = async () => {
    if (!draft) return;
    try {
      await api.saveProject(draft.name.trim(), draft.folder, draft.about, draft.isolation);
      setDraft(null);
      setError(null);
      await load();
    } catch (e) {
      setError(message(e));
    }
  };
  const remove = async (name: string) => {
    try {
      await api.removeProject(name);
      setDraft(null);
      await load();
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <Page
      title="Projects"
      actions={
        <>
          <button className="button small" onClick={() => setBringing(true)} title="The folders you've worked in lately with Claude Code or Codex, and their memories">
            Look in Claude Code and Codex
          </button>
          <button className="button small" onClick={add}>
            <Icon name="plus" size={13} />
            Add a project
          </button>
        </>
      }
    >
      <p className="page-lede">
        The folders your crew works in. A bot starts or hands off work in a project and does it in its folder: in a worktree of its own when it
        writes in a git repository, or, for a Unity project unless you change it, in the folder itself, beside you. Every bot sees this list, with
        what each project is.
      </p>
      {error && <p className="bad small">{error}</p>}
      {Object.entries(errors).map(([file, why]) => (
        <div key={file} className="crew-problem">
          <Icon name="alert" size={14} />
          <div>
            <div>projects/{file} couldn't be read</div>
            <div className="crew-problem-why">{why}</div>
          </div>
        </div>
      ))}
      <div className="list">
        {projects?.map((p) => (
          <div key={p.name} className="list-row" role="button" tabIndex={0} onClick={() => setDraft({ name: p.name, folder: p.folder, about: p.about, existing: true, isolation: p.isolation })}>
            <Icon name="folder" size={18} />
            <span className="list-text">
              <span className="list-title">
                {p.name}
                {p.missing && <span className="pill bad">Folder missing</span>}
              </span>
              <span className="faint">
                {tilde(p.folder)} · {WHERE[p.isolation]}
              </span>
              {p.about && <span className="project-about">{p.about.split("\n")[0]}</span>}
            </span>
          </div>
        ))}
        {projects?.length === 0 && (
          <p className="faint list-empty">No projects yet. Add the folders you work in, or look for the ones you've used with Claude Code and Codex.</p>
        )}
      </div>

      {draft && (
        <Sheet
          title={draft.existing ? draft.name : "New project"}
          onClose={() => setDraft(null)}
          footer={
            <>
              {draft.existing && (
                <button className="button danger" onClick={() => remove(draft.name)} title="Its folder stays as it is">
                  Remove
                </button>
              )}
              <span className="grow" />
              <button className="button" onClick={() => setDraft(null)}>
                Cancel
              </button>
              <button className="button primary" onClick={save} disabled={!draft.name.trim()}>
                {draft.existing ? "Save" : "Add"}
              </button>
            </>
          }
        >
          {!draft.existing && (
            <Field label="Name" hint="How you and the bots call it: lowercase letters, digits and dashes.">
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
          )}
          <Field label="Folder">
            <div className="folder-field">
              <span className="faint">{tilde(draft.folder)}</span>
              <button className="button small" onClick={async () => { const f = await bridge.pickFolder(); if (f) setDraft({ ...draft, folder: f }); }}>
                Choose…
              </button>
            </div>
          </Field>
          <Field label="What it is" hint="A line or two, so the bots know which work belongs here. How to build and test it belongs in the project's own AGENTS.md or CLAUDE.md.">
            <textarea rows={4} value={draft.about} onChange={(e) => setDraft({ ...draft, about: e.target.value })} autoFocus={draft.existing} />
          </Field>
          {draft.isolation && (
            <Field label="Where tasks work" hint={HOW[draft.isolation]}>
              <Segmented value={draft.isolation} options={Object.entries(WHERE) as [ProjectOut["isolation"], string][]} onChange={(isolation) => setDraft({ ...draft, isolation })} />
            </Field>
          )}
        </Sheet>
      )}

      {bringing && chief && (
        <BringIn
          chief={labelOf(chief.name)}
          onClose={() => setBringing(false)}
          onDone={(brought) => {
            setBringing(false);
            void load();
            if (brought.task_id) props.onChat(chief.name); // to see what it learned
          }}
        />
      )}
    </Page>
  );
}

function nameFor(folder: string): string {
  const base = folder.split("/").filter(Boolean).at(-1) ?? "";
  return base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "project";
}

function tilde(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, "~");
}

function message(e: unknown): string {
  return String(e).replace(/^Error: /, "");
}
