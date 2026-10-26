import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "./bits";

const html = (text: string) => renderToStaticMarkup(<Markdown text={text} onSelect={() => undefined} />);

describe("Markdown", () => {
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

  it("finishes every document quickly, whatever it's made of", () => {
    // Fragments that start, end or confuse blocks, joined at random: 5,000 documents, each with a time budget.
    const parts = ["- ", "* ", "+ ", "1. ", "10) ", "  ", "\t", "    - ", "```", "``", "`", "# ", "###### ", "**", "*",
      "\r\n", "\n", "\r", " ", "", " ", "text", "t_0123456789", "@name", "<b>", "&amp;", "\\", "\u0000", "🙂"];
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
