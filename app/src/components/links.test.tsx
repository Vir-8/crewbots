import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { bridge } from "../bridge";

// Click a link and draw it again, with its state kept between draws, without a browser.
const hooks = vi.hoisted(() => ({ values: [] as any[], index: 0, from: { files: [] as string[], folder: null as string | null } }));
vi.mock("react", async (original) => {
  const actual = await original<typeof import("react")>();
  return {
    ...actual,
    useState: (initial: any) => {
      const i = hooks.index++;
      if (!(i in hooks.values)) hooks.values[i] = initial;
      return [hooks.values[i], (next: any) => { hooks.values[i] = next; }];
    },
    useContext: () => hooks.from,
  };
});
import { TextLink } from "./links";

const draw = (target: string) => {
  hooks.index = 0;
  return TextLink({ target, children: "it" }) as ReactElement<any>;
};
const click = (target: string) => {
  const preventDefault = vi.fn();
  draw(target).props.onClick({ preventDefault });
  expect(preventDefault).toHaveBeenCalled(); // the window never goes there itself
};

beforeEach(() => {
  hooks.values = [];
  hooks.from = { files: [], folder: null };
  vi.spyOn(bridge, "home").mockResolvedValue("/Users/pat");
});
afterEach(() => vi.restoreAllMocks());

it("opens a web address in the browser", () => {
  const openUrl = vi.spyOn(bridge, "openUrl").mockResolvedValue(undefined);
  const reveal = vi.spyOn(bridge, "reveal").mockResolvedValue(undefined);
  click("https://example.com/a?b=1");
  expect(openUrl).toHaveBeenCalledWith("https://example.com/a?b=1");
  expect(reveal).not.toHaveBeenCalled();
});

it("shows a path in Finder, never opening it", async () => {
  const openUrl = vi.spyOn(bridge, "openUrl").mockResolvedValue(undefined);
  const reveal = vi.spyOn(bridge, "reveal").mockResolvedValue(undefined);
  const openFile = vi.spyOn(bridge, "openFile");
  hooks.from = { files: ["/Users/pat/studio/renders/hero.png"], folder: "~/code/acme-web" };
  for (const target of ["/Users/pat/work/run.sh", "file:///Users/pat/My%20Shot.png", "/Users/pat/a.ts:12:3",
    "renders/hero.png", "src/app.py:42", "~/notes.md"]) click(target);
  await vi.waitFor(() => expect(reveal).toHaveBeenCalledTimes(6));
  expect(reveal.mock.calls).toEqual([["/Users/pat/work/run.sh"], ["/Users/pat/My Shot.png"], ["/Users/pat/a.ts"],
    ["/Users/pat/studio/renders/hero.png"], ["/Users/pat/code/acme-web/src/app.py"], ["/Users/pat/notes.md"]]);
  expect(openUrl).not.toHaveBeenCalled();
  expect(openFile).not.toHaveBeenCalled();
});

it("greys a path Finder can't find, and says so, until it's there again", async () => {
  hooks.from = { files: [], folder: "/Users/pat/code/acme-web" };
  const reveal = vi.spyOn(bridge, "reveal").mockRejectedValue(new Error("No such file or directory"));
  expect(draw("docs/gone.md").props).toMatchObject({ className: "text-link", title: "/Users/pat/code/acme-web/docs/gone.md" });
  click("docs/gone.md");
  await vi.waitFor(() => expect(draw("docs/gone.md").props.className).toBe("text-link missing"));
  expect(draw("docs/gone.md").props.title).toBe("/Users/pat/code/acme-web/docs/gone.md wasn't found");
  reveal.mockResolvedValue(undefined);
  click("docs/gone.md");
  await vi.waitFor(() => expect(draw("docs/gone.md").props.className).toBe("text-link"));
});
