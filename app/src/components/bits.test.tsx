import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bridge } from "../bridge";
import { Markdown, MentionText, MessageMeta, TextLink, dayAndTime, stamp, timeBreaks } from "./bits";
import { setCrew } from "./Avatar";
import { Finding, count } from "./Find";
import { bots } from "../preview/demo/make";

const html = (text: string) => renderToStaticMarkup(<Markdown text={text} onSelect={() => undefined} />);

describe("Markdown", () => {
  it("styles crew handles and display names, but not emails, unknown words, or code", () => {
    setCrew(bots);
    const out = html("@engineer and **@Roger**; pat@engineer.games @engineer-extra @unknown `@engineer`\n\n```\n@Roger\n```");
    expect(out.match(/class="mention"/g)).toHaveLength(2);
    expect(out).toContain('<span class="mention">@engineer</span>');
    expect(out).toContain('<span class="mention">@Roger</span>');
    expect(out).toContain('pat@engineer.games @engineer-extra @unknown <code>@engineer</code>');
    expect(out).toContain('<pre>@Roger</pre>');
  });

  it("preserves find hits across mention boundaries and skips code in plain messages", () => {
    setCrew(bots);
    const text = "Ask @engineer now. `@engineer`\n```\n@Roger\n```";
    const out = renderToStaticMarkup(<Finding.Provider value="ask @engineer now"><MentionText text={text} plain /></Finding.Provider>);
    expect(out).toContain('<mark class="find-hit">Ask <span class="mention">@engineer</span> now</mark>');
    expect(out.match(/class="mention"/g)).toHaveLength(1);
    expect(out.match(/class="find-hit"/g)).toHaveLength(count(text, "ask @engineer now"));
    const markdown = renderToStaticMarkup(<Finding.Provider value="ask @engineer now"><Markdown text="Ask @engineer now" onSelect={() => {}} /></Finding.Provider>);
    expect(markdown).toContain('<mark class="find-hit">Ask <span class="mention">@engineer</span> now</mark>');
    const group = renderToStaticMarkup(<Markdown text="@everyone @unknown" everyone onSelect={() => {}} />);
    expect(group).toContain('<span class="mention">@everyone</span> @unknown');
  });
  it("reads Windows and old Mac line endings like Unix ones", () => {
    const unix = html("Intro\n\n- one\n- two\n\nAfter");
    expect(html("Intro\r\n\r\n- one\r\n- two\r\n\r\nAfter")).toBe(unix);
    expect(html("Intro\r\rn- one\r- two")).not.toBe(""); // a lone \r is a line ending too
    expect(html("Intro  - one - two  After")).toBe(unix);
  });

  it("nests a list under the item it's indented under", () => {
    const out = html("- fruit\n  - apple\n  - pear\n- veg");
    expect(out).toContain("<ul><li><span class=\"pre\">fruit</span><ul><li><span class=\"pre\">apple</span></li>");
    expect(out.match(/<ul>/g)).toHaveLength(2);
    expect(out).toContain("<li><span class=\"pre\">veg</span></li></ul>");
  });

  it("numbers a list from where it starts, and keeps going past a blank line", () => {
    expect(html("3. three\n4. four")).toContain('<ol start="3">');
    expect(html("1. one\n2. two")).toContain("<ol>");
    expect(html("1. one\n\n2. two").match(/<ol/g)).toHaveLength(1);
  });

  it("keeps a fence's lines exactly as written", () => {
    const out = html("Before\n```ts\n**not bold**\n- not a list\n```\nAfter");
    expect(out).toContain("<pre>**not bold**\n- not a list</pre>");
    expect(out).toContain("After");
    expect(html("```\nnever closed\n- still code")).toContain("<pre>never closed\n- still code</pre>");
  });

  it("makes headings, bold and code, and never HTML", () => {
    const out = html("## Plan\nUse `uv run` and **ship it** <img src=x onerror=alert(1)>");
    expect(out).toContain("<h3>");
    expect(out).toContain("<code>uv run</code>");
    expect(out).toContain("<strong>");
    expect(out).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });

  it("turns task ids into links", () => {
    expect(html("See t_0123456789 for it")).toContain('<a class="task-link">t_0123456789</a>');
  });

  it("turns [label](target) into a link titled with its target, but not in code or a page link", () => {
    setCrew(bots);
    const shot = "/Users/pat/.crewbots/crew/attachments/919bcfd31991/browser-dark-c0.png";
    const out = html(`[A — two columns](${shot}) for @Roger, t_0123456789 and **bold**; [the PR](https://github.com/o/r/pull/7)\n\n` +
      "`[x](y)` and [[Plan]] and [not a link] (here)");
    expect(out).toContain(`<a class="text-link" title="${shot}">A — two columns</a> for <span class="mention">@Roger</span>`);
    expect(out).toContain('<a class="task-link">t_0123456789</a> and <strong>bold</strong>');
    expect(out).toContain('<a class="text-link" title="https://github.com/o/r/pull/7">the PR</a>');
    expect(out).toContain("<code>[x](y)</code>");
    expect(out).toContain('class="page-link off">Plan</span> and [not a link] (here)');
    expect(out.match(/text-link/g)).toHaveLength(2);
    expect(html("```\n[x](y)\n```")).not.toContain("text-link");
  });

  it("draws links in bold, with parentheses or spaces in their target, and code as their label", () => {
    expect(html("**see [A](/x/a.png) now**")).toContain('<strong>see <a class="text-link" title="/x/a.png">A</a> now</strong>');
    expect(html("[Foo](https://en.wikipedia.org/wiki/Foo_(bar)) after"))
      .toContain('<a class="text-link" title="https://en.wikipedia.org/wiki/Foo_(bar)">Foo</a> after');
    expect(html("[A](</Users/x/My Shot.png>)")).toContain('<a class="text-link" title="/Users/x/My Shot.png">A</a>');
    expect(html("[`run.sh`](/x/run.sh)")).toContain('<a class="text-link" title="/x/run.sh"><code>run.sh</code></a>');
    expect(html("[the plan](docs/plan.md) and [x](javascript:alert(1))")).toBe(
      '<div class="md"><p><span class="pre">the plan and x</span></p></div>'); // nowhere to show them from
  });

  it("finishes every document quickly, whatever it's made of", () => {
    // Fragments that start, end or confuse blocks, joined at random: 5,000 documents, each with a time budget.
    const parts = ["- ", "* ", "+ ", "1. ", "10) ", "  ", "\t", "    - ", "```", "``", "`", "# ", "###### ", "**", "*",
      "\r\n", "\n", "\r", " ", "", " ", "text", "t_0123456789", "@name", "<b>", "&amp;", "\\", "\u0000", "🙂", "[", "](", ")", "[[", "]]"];
    let seed = 7;
    const random = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
    let slowest = 0;
    for (let n = 0; n < 5000; n++) {
      const text = Array.from({ length: 1 + Math.floor(random() * 60) }, () => parts[Math.floor(random() * parts.length)]).join("");
      const started = performance.now();
      html(text);
      slowest = Math.max(slowest, performance.now() - started);
    }
    expect(slowest).toBeLessThan(100);
  });
});

describe("a link a bot wrote", () => {
  afterEach(() => vi.restoreAllMocks());
  const click = (target: string) => {
    const preventDefault = vi.fn();
    TextLink({ label: "it", target }).props.onClick({ preventDefault });
    expect(preventDefault).toHaveBeenCalled(); // the window never goes there itself
  };

  it("opens a web address in the browser", () => {
    const openUrl = vi.spyOn(bridge, "openUrl").mockResolvedValue(undefined);
    const reveal = vi.spyOn(bridge, "reveal").mockResolvedValue(undefined);
    click("https://example.com/a?b=1");
    expect(openUrl).toHaveBeenCalledWith("https://example.com/a?b=1");
    expect(reveal).not.toHaveBeenCalled();
  });

  it("shows a path in Finder, never opening it", async () => {
    const openUrl = vi.spyOn(bridge, "openUrl").mockResolvedValue(undefined);
    const reveal = vi.spyOn(bridge, "reveal").mockRejectedValue(new Error("gone"));
    const openFile = vi.spyOn(bridge, "openFile");
    vi.spyOn(bridge, "home").mockResolvedValue("/Users/pat");
    for (const target of ["/Users/pat/work/run.sh", "file:///Users/pat/My%20Shot.png", "/Users/pat/a.ts:12:3", "~/notes.md"]) click(target);
    await vi.waitFor(() => expect(reveal).toHaveBeenCalledTimes(4));
    expect(reveal.mock.calls).toEqual([["/Users/pat/work/run.sh"], ["/Users/pat/My Shot.png"], ["/Users/pat/a.ts"], ["/Users/pat/notes.md"]]);
    expect(openUrl).not.toHaveBeenCalled();
    expect(openFile).not.toHaveBeenCalled();
  });
});

describe("when a message was sent", () => {
  const now = new Date(2026, 9, 27, 18, 0); // a Tuesday evening
  const at = (...date: [number, number, number, number, number]) => new Date(...date).toISOString();
  const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

  it("says the time today, and the day as well before", () => {
    const today = at(2026, 9, 27, 0, 5);
    expect(stamp(today, now)).toBe(time(today));
    expect(stamp(at(2026, 9, 26, 23, 50), now)).toBe(`Yesterday ${time(at(2026, 9, 26, 23, 50))}`);
    const monday = at(2026, 9, 21, 15, 29); // under a week ago: the weekday
    expect(stamp(monday, now)).toBe(`${new Date(monday).toLocaleDateString(undefined, { weekday: "long" })} ${time(monday)}`);
  });

  it("gives a date further back, with the year once it isn't this one", () => {
    const october = at(2026, 9, 20, 15, 29); // a week ago to the day: no longer "Tuesday"
    expect(stamp(october, now)).toContain(time(october));
    expect(stamp(october, now)).not.toMatch(/day/);
    const last = stamp(at(2025, 9, 22, 9, 0), now);
    expect(last).toContain("2025");
    expect(last).not.toContain(time(at(2025, 9, 22, 9, 0)));
  });

  it("breaks a conversation where an hour has passed or the day has changed", () => {
    const messages = [
      { at: at(2026, 9, 26, 15, 29) }, // the first: a break
      { at: at(2026, 9, 26, 15, 50) },
      {}, // no time (a step, a card): passed over
      { at: at(2026, 9, 26, 16, 49) }, // 59 minutes on
      { at: at(2026, 9, 26, 17, 49) }, // an hour on: a break
      { at: at(2026, 9, 26, 23, 59) }, // six hours on: a break
      { at: at(2026, 9, 27, 0, 1) }, // two minutes on, but the next day: a break
    ];
    expect([...timeBreaks(messages)]).toEqual([0, 4, 5, 6]);
    expect(dayAndTime(at(2026, 9, 26, 15, 29), now)).toEqual(["Yesterday", time(at(2026, 9, 26, 15, 29))]);
    expect(dayAndTime(at(2026, 9, 27, 9, 0), now)[0]).toBe("Today");
  });

  it("shows the whole date on hover, and copies only when there's something to copy", () => {
    const sent = at(2026, 9, 22, 17, 55);
    const html = renderToStaticMarkup(<MessageMeta at={sent} text="Fixed it" />);
    expect(html.toLowerCase()).toContain(`datetime="${sent.toLowerCase()}"`);
    expect(html).toContain("2026"); // in the title
    expect(html).toContain('aria-label="Copy"');
    expect(renderToStaticMarkup(<MessageMeta at={sent} text="" />)).not.toContain("Copy");
    expect(renderToStaticMarkup(<MessageMeta text="no time" />)).toBe("");
  });
});
