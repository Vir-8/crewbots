import { beforeEach, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

// Drive the components' event props with persistent hook state, without a browser.
const hooks = vi.hoisted(() => ({ values: [] as any[], index: 0, crew: {} as any }));
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
    useEffect: () => {},
  };
});
vi.mock("./state", async (original) => ({ ...await original<typeof import("./state")>(), useCrew: () => hooks.crew }));
vi.mock("./components/Resizer", () => ({ useWidth: () => [null, () => {}], Resizer: () => null, RailEdge: () => null }));
import { App } from "./App";
import { Composer } from "./components/Composer";
import { DirectChat, GroupChat } from "./components/ChatView";
import { Panel } from "./components/Panel";
import { Profile, RoomProfile } from "./components/Profile";
import { Sidebar } from "./components/Sidebar";
import { MergeButton } from "./components/WorkCard";

function find(node: any, type: any): ReactElement<any> | undefined {
  if (!node || typeof node !== "object") return undefined;
  if (Array.isArray(node)) return node.map((n) => find(n, type)).find(Boolean);
  if (node.type === type) return node;
  return find(node.props?.children, type);
}
function render(component: () => ReactElement) { hooks.index = 0; return component(); }
beforeEach(() => { hooks.values = []; localStorage.clear(); });

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
  const actions = find(tree, "form")!.props.children[3];
  actions.props.children[0].props.onClick();
  tree = draw();
  expect(find(tree, "form")).toBeUndefined();
  expect(withdrawMerge).toHaveBeenCalledTimes(1);
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
