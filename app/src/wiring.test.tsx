import { beforeEach, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

// Drive the components' event props with persistent hook state, without a browser.
const hooks = vi.hoisted(() => ({ values: [] as any[], index: 0, crew: {} as any, effects: [] as (() => any)[] }));
vi.mock("react", async (original) => {
  const actual = await original<typeof import("react")>();
  return {
    ...actual,
    useState: (initial: any) => {
      const i = hooks.index++;
      if (!(i in hooks.values)) hooks.values[i] = typeof initial === "function" ? initial() : initial;
      return [hooks.values[i], (next: any) => { hooks.values[i] = typeof next === "function" ? next(hooks.values[i]) : next; }];
    },
    useRef: (initial: any) => {
      const i = hooks.index++;
      return hooks.values[i] ??= { current: initial };
    },
    useEffect: (effect: () => any) => { hooks.effects.push(effect); },
    useSyncExternalStore: (_subscribe: any, getSnapshot: () => any) => getSnapshot(),
  };
});
vi.mock("./state", async (original) => ({ ...await original<typeof import("./state")>(), useCrew: () => hooks.crew }));
vi.mock("./components/Resizer", () => ({ useWidth: () => [null, () => {}], Resizer: () => null, RailEdge: () => null }));
import { App } from "./App";
import { Composer } from "./components/Composer";
import { DirectChat, GroupChat } from "./components/ChatView";
import { Panel } from "./components/Panel";
import { Profile, RoomProfile } from "./components/Profile";
import { ChatRow, RowMenu, Sidebar } from "./components/Sidebar";
import { MergeButton, WorkCard } from "./components/WorkCard";
import { Question } from "./components/Question";
import { UpdatesDialog } from "./components/UpdatesDialog";

function find(node: any, type: any): ReactElement<any> | undefined {
  if (!node || typeof node !== "object") return undefined;
  if (Array.isArray(node)) return node.map((n) => find(n, type)).find(Boolean);
  if (node.type === type) return node;
  return find(node.props?.children, type);
}
function render(component: () => ReactElement<any>) { hooks.index = 0; return component(); }
beforeEach(() => { hooks.values = []; hooks.effects = []; localStorage.clear(); });

it("toggles mute from the existing chat menu", () => {
  const onMute = vi.fn();
  const onClose = vi.fn();
  const props = { at: { x: 0, y: 0 }, place: null, last: -1, onPin: vi.fn(), onMute, onClose };
  const mute = find(render(() => RowMenu({ ...props, muted: false })), "button")!;
  expect(mute.props.children).toBe("Mute");
  mute.props.onClick();
  expect(onMute).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
  expect(find(render(() => RowMenu({ ...props, muted: true })), "button")!.props.children).toBe("Unmute");
});

it("opens the row menu with a right click or keyboard and mutes the selected chat", () => {
  const chat = { key: "bot:chief", kind: "direct", bot: "chief", title: "Chief", muted: false, unread: 0, working: false, updated_at: "" } as any;
  const muteChat = vi.fn().mockResolvedValue(undefined);
  const props = { crew: { chats: [chat], inbox: [], bots: [], muteChat } as any, current: null, rail: false,
    onChat: vi.fn(), onOpen: vi.fn(), onNewGroup: vi.fn(), onExpand: vi.fn() };
  const draw = () => render(() => Sidebar(props));
  find(draw(), ChatRow)!.props.extra.onContextMenu({ preventDefault: vi.fn(), clientX: 20, clientY: 30 });
  const menu = find(draw(), RowMenu)!;
  expect(menu.props.at).toMatchObject({ key: chat.key, x: 20, y: 30 });
  menu.props.onMute();
  expect(muteChat).toHaveBeenCalledWith(chat, true);
  menu.props.onClose();
  find(draw(), ChatRow)!.props.extra.onKeyDown({ key: "F10", shiftKey: true, preventDefault: vi.fn(),
    currentTarget: { getBoundingClientRect: () => ({ left: 12, bottom: 45 }) } });
  expect(find(draw(), RowMenu)!.props.at).toMatchObject({ x: 12, y: 45 });
});

it("opens and sends an Other answer from a question", () => {
  const send = vi.fn().mockResolvedValue(undefined);
  const block = { kind: "ask", taskId: "question", question: "Which?", choices: [{ label: "First" }], multiple: false } as any;
  const draw = () => render(() => Question({ crew: { send } as any, block, onOpen: () => {} }));
  let tree = draw();
  const choices = (tree.props.children as any[])[2];
  choices.props.children[1].props.onClick();
  tree = draw();
  const input = find(tree, "input")!;
  expect(input.props.autoFocus).toBe(true);
  input.props.onChange({ target: { value: "My answer" } });
  tree = draw();
  const other = (tree.props.children as any[])[2].props.children[2];
  const button = other.props.children[1];
  expect(button.props.className).toBe("button small");
  expect(button.props.disabled).toBe(false);
  button.props.onClick();
  expect(send).toHaveBeenCalledWith("question", "My answer");
});

it("sends a free-text room question to its seat", () => {
  const send = vi.fn().mockResolvedValue(undefined);
  const block = { kind: "ask", taskId: "seat", room: "Engineering", question: "Which?", questionId: 42 } as any;
  const draw = () => render(() => Question({ crew: { send } as any, block, onOpen: () => {} }));
  (draw().props.children as any[])[2].props.children[1].props.onClick();
  find(draw(), "input")!.props.onChange({ target: { value: "Use the existing design" } });
  find(draw(), "input")!.props.onKeyDown({ key: "Enter", nativeEvent: { isComposing: false } });
  expect(send).toHaveBeenCalledWith("seat", "Use the existing design", [], 42);
});

it("shows the stale question error on the card", async () => {
  const send = vi.fn().mockRejectedValue(new Error("This question is closed or has been replaced."));
  const block = { kind: "ask", taskId: "seat", room: "Engineering", question: "Which?", questionId: 42,
    choices: [{ label: "No" }] } as any;
  const draw = () => render(() => Question({ crew: { send } as any, block, onOpen: () => {} }));
  (draw().props.children as any[])[2].props.children[0][0].props.onClick();
  await Promise.resolve();
  const message = (draw().props.children as any[])[1].props.children[3];
  expect(message.props.role).toBe("alert");
  expect(message.props.children).toBe("This question is closed or has been replaced.");
});

it("keeps the Other answer open while Escape is composing", () => {
  const block = { kind: "ask", taskId: "question", question: "Which?", choices: [{ label: "First" }], multiple: false } as any;
  const draw = () => render(() => Question({ crew: {} as any, block, onOpen: () => {} }));
  let tree = draw();
  (tree.props.children as any[])[2].props.children[1].props.onClick();
  find(draw(), "input")!.props.onChange({ target: { value: "Draft" } });
  find(draw(), "input")!.props.onKeyDown({ key: "Escape", nativeEvent: { isComposing: true } });
  expect(find(draw(), "input")!.props.value).toBe("Draft");
  find(draw(), "input")!.props.onKeyDown({ key: "Escape", nativeEvent: { isComposing: false } });
  expect(find(draw(), "input")).toBeUndefined();
});

it("ignores Escape during composition in the update dialog", () => {
  const onClose = vi.fn();
  const add = vi.fn();
  const remove = vi.fn();
  vi.stubGlobal("addEventListener", add);
  vi.stubGlobal("removeEventListener", remove);
  render(() => UpdatesDialog({ onClose }));
  const cleanup = hooks.effects.at(-1)!();
  const keydown = add.mock.calls[0][1];
  keydown({ key: "Escape", isComposing: true });
  expect(onClose).not.toHaveBeenCalled();
  keydown({ key: "Escape", isComposing: false });
  expect(onClose).toHaveBeenCalledOnce();
  cleanup();
  vi.unstubAllGlobals();
});

it("offers no room reply limit by default and lets a room opt in", () => {
  const changeRoom = vi.fn().mockResolvedValue(undefined);
  const room = { id: "room", title: "Room", lead: "chief", members: ["chief"], max_depth: null } as any;
  const crew = { tasks: new Map(), bots: [{ name: "chief" }], changeRoom } as any;
  const draw = () => render(() => RoomProfile({ crew, room, onOpen: () => {}, onChat: () => {}, onClose: () => {}, onGone: () => {} }));
  const select = find(draw(), "select")!;
  expect(select.props.value).toBe("");
  expect(select.props.children[0].props.children).toBe("No limit");
  select.props.onChange({ target: { value: "50" } });
  expect(changeRoom).toHaveBeenCalledWith("room", { max_depth: 50 });
  select.props.onChange({ target: { value: "" } });
  expect(changeRoom).toHaveBeenCalledWith("room", { max_depth: null });
});

it("keeps the withdraw dialog open and shows why the withdrawal failed", async () => {
  const withdrawMerge = vi.fn().mockRejectedValue(new Error("Worktree maintenance is running; try again when it finishes."));
  const task = { id: "work", status: "idle", merge_state: "proposed" } as any;
  const draw = () => render(() => MergeButton({ crew: { withdrawMerge } as any, task })!);
  find(draw(), "button")!.props.onClick();
  find(draw(), "input")!.props.onChange({ target: { value: "Superseded" } });
  await find(draw(), "form")!.props.onSubmit({ preventDefault: () => {} });
  const tree = draw();
  expect(find(tree, "form")).toBeDefined();
  expect(find(tree, "input")!.props.value).toBe("Superseded");
  const alert = find(tree, "p")!;
  expect(alert.props.role).toBe("alert");
  expect(alert.props.children).toBe("Worktree maintenance is running; try again when it finishes.");
});

const buttons = (node: any): any[] => !node || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(buttons) : [...(node.type === "button" ? [node] : []), ...buttons(node.props?.children)];

it("marks a proposal already on main and offers only Withdraw, and follows main as it moves", async () => {
  const { api } = await import("./api");
  const status = vi.spyOn(api, "mergeStatus").mockResolvedValue({ already_on: "main" });
  const task = { id: "work", status: "idle", merge_state: "proposed" } as any;
  const events = new Map<string, unknown[]>();
  const draw = () => render(() => MergeButton({ crew: { events } as any, task })!);
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
  const focus = vi.fn();
  vi.stubGlobal("addEventListener", focus);
  vi.stubGlobal("removeEventListener", vi.fn());
  draw();
  hooks.effects[0]!();
  await settle();
  expect(JSON.stringify(draw())).toContain("Already on ");
  expect(buttons(draw()).map((b) => b.props.children)).toEqual(["Withdraw"]);
  // The task gets an event after main moved on without the change: Merge is back.
  status.mockResolvedValue({ already_on: null });
  events.set("work", [{ id: 1 }]);
  hooks.effects = [];
  draw();
  hooks.effects[0]!();
  await settle();
  expect(JSON.stringify(draw())).not.toContain("Already on ");
  expect(buttons(draw()).map((b) => b.props.children).length).toBe(2);
  // The window regains focus after the change landed on main.
  status.mockResolvedValue({ already_on: "main" });
  const onFocus = focus.mock.calls.find(([name]) => name === "focus")![1];
  onFocus();
  await settle();
  expect(JSON.stringify(draw())).toContain("Already on ");
  // And a later event brings it back to not-on-main and back to already-on, by events.
  status.mockResolvedValue({ already_on: null });
  events.set("work", [{ id: 1 }, { id: 2 }]);
  hooks.effects = [];
  draw();
  hooks.effects[0]!();
  await settle();
  expect(JSON.stringify(draw())).not.toContain("Already on ");
  status.mockResolvedValue({ already_on: "main" });
  events.set("work", [{ id: 1 }, { id: 2 }, { id: 3 }]);
  hooks.effects = [];
  draw();
  hooks.effects[0]!();
  await settle();
  expect(JSON.stringify(draw())).toContain("Already on ");
  expect(status).toHaveBeenCalledTimes(5);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("moves a second proposal to Already on main when the first one merges, without a focus event", async () => {
  const { api } = await import("./api");
  const status = vi.spyOn(api, "mergeStatus").mockResolvedValue({ already_on: null });
  const first = { id: "first", status: "idle", merge_state: "proposed" } as any;
  const second = { id: "second", status: "idle", merge_state: "proposed" } as any;
  const tasks = new Map<string, any>([["first", first], ["second", second]]);
  vi.stubGlobal("addEventListener", vi.fn());
  vi.stubGlobal("removeEventListener", vi.fn());
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
  const draw = () => render(() => MergeButton({ crew: { tasks } as any, task: second })!);
  draw();
  hooks.effects[0]!();
  await settle();
  expect(JSON.stringify(draw())).not.toContain("Already on ");
  status.mockResolvedValue({ already_on: "main" });
  tasks.set("first", { ...first, merge_state: "merged" }); // the first card's task merged
  hooks.effects = [];
  draw();
  hooks.effects[0]!();
  await settle();
  expect(status).toHaveBeenLastCalledWith("second");
  expect(JSON.stringify(draw())).toContain("Already on ");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("ignores a merge-status answer that arrives after a newer question was asked", async () => {
  const { api } = await import("./api");
  const answers: ((v: { already_on: string | null }) => void)[] = [];
  vi.spyOn(api, "mergeStatus").mockImplementation(() => new Promise((resolve) => { answers.push(resolve); }));
  const task = { id: "work", status: "idle", merge_state: "proposed" } as any;
  vi.stubGlobal("addEventListener", vi.fn());
  vi.stubGlobal("removeEventListener", vi.fn());
  const events = new Map<string, unknown[]>();
  const draw = () => render(() => MergeButton({ crew: { events } as any, task })!);
  const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
  draw();
  hooks.effects[0]!(); // question 1
  events.set("work", [{ id: 1 }]);
  hooks.effects = [];
  draw();
  hooks.effects[0]!(); // question 2
  answers[1]!({ already_on: null }); // the newer answer comes first
  await settle();
  answers[0]!({ already_on: "main" }); // the older one is late
  await settle();
  expect(JSON.stringify(draw())).not.toContain("Already on ");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("withdraws through the in-app dialog and allows cancellation", async () => {
  const withdrawMerge = vi.fn().mockResolvedValue(undefined);
  const task = { id: "work", status: "idle", merge_state: "proposed" } as any;
  const draw = () => render(() => MergeButton({ crew: { withdrawMerge } as any, task })!);
  let tree = draw();
  find(tree, "button")!.props.onClick();
  tree = draw();
  expect(find(tree, "form")!.props.role).toBe("dialog");
  expect(find(tree, "input")!.props.value).toBe("");
  find(tree, "input")!.props.onChange({ target: { value: "  Superseded  " } });
  tree = draw();
  await find(tree, "form")!.props.onSubmit({ preventDefault: () => {} });
  await Promise.resolve();
  expect(withdrawMerge).toHaveBeenCalledWith("work", "Superseded");
  tree = draw();
  expect(find(tree, "form")).toBeUndefined();
  find(tree, "button")!.props.onClick();
  tree = draw();
  const actions = find(tree, "form")!.props.children[4];
  actions.props.children[0].props.onClick();
  tree = draw();
  expect(find(tree, "form")).toBeUndefined();
  expect(withdrawMerge).toHaveBeenCalledTimes(1);
  find(tree, "button")!.props.onClick();
  tree = draw();
  find(tree, "input")!.props.onChange({ target: { value: "Draft" } });
  tree = draw();
  const add = vi.fn();
  const remove = vi.fn();
  vi.stubGlobal("addEventListener", add);
  vi.stubGlobal("removeEventListener", remove);
  const cleanup = hooks.effects.at(-1)!();
  expect(add).toHaveBeenCalledWith("keydown", expect.any(Function));
  const keydown = add.mock.calls[0][1];
  expect(find(draw(), "form")).toBeDefined();
  keydown({ key: "Escape", isComposing: true });
  expect(find(draw(), "form")).toBeDefined();
  expect(find(draw(), "input")!.props.value).toBe("Draft");
  keydown({ key: "Escape", isComposing: false });
  expect(find(draw(), "form")).toBeUndefined();
  cleanup();
  expect(remove).toHaveBeenCalledWith("keydown", add.mock.calls[0][1]);
  vi.unstubAllGlobals();
  tree = draw();
  expect(find(tree, "form")).toBeUndefined();
  find(tree, "button")!.props.onClick();
  expect(find(draw(), "input")!.props.value).toBe("");
});

it("shows the review cap received for the work", () => {
  const task = { id: "work", parent_id: "parent", status: "idle", title: "Work", bot: "engineer" } as any;
  const review = { id: "review", review_of: task.id, review_version: 1, bot: "reviewer" };
  const crew = { family: () => [], children: new Map([["parent", [review]]]), verdicts: new Map(), tasks: new Map(), latest: new Map(), asks: new Map(), events: new Map(), ensureEvents: vi.fn() } as any;
  hooks.values[0] = { task: "work", max: 5 };
  const tree = render(() => WorkCard({ crew, task, onOpen: () => {} }));
  const foot = (tree.props.children as any[]).find((n) => n?.props?.className === "work-foot");
  expect(foot.props.children[1].props.children).toEqual(["round ", 1, " of 5"]);
});

it("lists the files in a branch merge proposal", () => {
  const task = { id: "work", parent_id: "parent", status: "idle", title: "Work", bot: "engineer", merge_state: "proposed" } as any;
  const crew = { family: () => [], children: new Map(), verdicts: new Map(), tasks: new Map(), latest: new Map(), asks: new Map(), ensureEvents: vi.fn(),
    events: new Map([["work", [{ kind: "merge_proposed", data: { files: ["Assets/Vir.png", "Assets/new.png"] } }]]]) } as any;
  const tree = render(() => WorkCard({ crew, task, onOpen: () => {} }));
  const files = (tree.props.children as any[]).find((n) => n?.props?.className === "work-files");
  expect(files.props.children[1].props.children.map((item: any) => item.props.children.props.children)).toEqual(["Assets/Vir.png", "Assets/new.png"]);
});

it("shows saves left uncommitted after a branch merge", () => {
  const task = { id: "work", parent_id: "parent", status: "idle", title: "Work", bot: "engineer", merge_state: "merged" } as any;
  const ensureEvents = vi.fn();
  const crew = { family: () => [], children: new Map(), verdicts: new Map(), tasks: new Map(), latest: new Map(), asks: new Map(), ensureEvents,
    events: new Map([["work", [{ kind: "merged", data: { leftover_files: ["Assets/Vir.png", "new file"] } }]]]) } as any;
  const tree = render(() => WorkCard({ crew, task, onOpen: () => {} }));
  const files = (tree.props.children as any[]).find((n) => n?.props?.className === "work-files");
  expect(files.props.children[0].props.children).toBe("Saved after the merge started, still uncommitted:");
  expect(files.props.children[1].props.children.map((item: any) => item.props.children.props.children)).toEqual(["Assets/Vir.png", "new file"]);
  hooks.effects[0]();
  expect(ensureEvents).toHaveBeenCalledWith("work");
});

it("composer ignores IME keys and resets the highlighted mention as typing narrows results", () => {
  const draw = () => render(() => Composer({ placeholder: "Message", onSend: async () => {}, mentions: ["alice", "albert", "alfred", "alfie"] }));
  let tree = draw();
  find(tree, "textarea")!.props.onChange({ target: { value: "@al" } });
  tree = draw();
  const key = (name: string, composing = false) => {
    let prevented = false;
    find(tree, "textarea")!.props.onKeyDown({ key: name, shiftKey: false, nativeEvent: { isComposing: composing }, preventDefault: () => { prevented = true; } });
    return prevented;
  };
  expect(key("ArrowDown")).toBe(true);
  tree = draw();
  expect(key("Enter", true)).toBe(false);
  expect(key("Tab", true)).toBe(false);
  expect(find(tree, "textarea")!.props.value).toBe("@al");
  find(tree, "textarea")!.props.onChange({ target: { value: "@alf" } });
  tree = draw();
  expect(key("Tab")).toBe(true);
  tree = draw();
  expect(find(tree, "textarea")!.props.value).toBe("@alfred ");
});

for (const kind of ["direct", "group"] as const) {
  it(`${kind} profile toggle replaces an open thread and works again after closing it`, () => {
    Object.assign(globalThis, { location: { search: "" } });
    const root = { id: "root", kind: kind === "direct" ? "chat" : "seat", bot: "chief", room_id: "room" };
    const task = { id: "work", kind: "thread", parent_id: "root", bot: "chief" };
    const chat = kind === "direct" ? { key: "bot:chief", kind, bot: "chief" } : { key: "room:room", kind, room: { id: "room" } };
    hooks.crew = { status: { state: "connected" }, chats: [chat], tasks: new Map<string, any>([["root", root], ["work", task]]) };
    const draw = () => render(App);
    let tree = draw();
    find(tree, Sidebar)!.props.onChat(chat.key);
    tree = draw();
    find(tree, Sidebar)!.props.onOpen("work");
    tree = draw();
    expect(find(tree, Panel)).toBeTruthy();
    const chatComponent = kind === "direct" ? DirectChat : GroupChat;
    const toggle = (t: ReactElement) => find(t, chatComponent)!.props[kind === "direct" ? "onProfile" : "onPanel"]();
    toggle(tree);
    tree = draw();
    expect(find(tree, Panel)).toBeUndefined();
    expect(find(tree, kind === "direct" ? Profile : RoomProfile)).toBeTruthy();
    find(tree, Sidebar)!.props.onOpen("work");
    tree = draw();
    find(tree, Panel)!.props.onClose();
    tree = draw();
    expect(find(tree, kind === "direct" ? Profile : RoomProfile)).toBeTruthy();
    toggle(tree);
    tree = draw();
    expect(find(tree, kind === "direct" ? Profile : RoomProfile)).toBeUndefined();
  });
}
