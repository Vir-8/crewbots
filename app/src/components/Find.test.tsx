import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "./bits";
import { Finding, Marked, count, shown } from "./Find";

const marks = (html: string) => html.match(/<mark class="find-hit">/g)?.length ?? 0;

describe("finding in a conversation", () => {
  it("counts in what a message says as many matches as it marks once it's drawn", () => {
    const said = [
      "Fixed the **export**: `export_rows` now exports filtered rows.\n\n- export to CSV\n- 2. export again\n\n```\nexport()\n```",
      "## Export plan\n\nSee t_0123456789 and the EXPORT button.",
      "Nothing to see here.",
    ];
    for (const text of said) {
      const html = renderToStaticMarkup(
        <Finding.Provider value="export">
          <Markdown text={text} onSelect={() => undefined} />
        </Finding.Provider>,
      );
      expect(marks(html)).toBe(count(shown(text), "export"));
    }
    expect(count(shown(said[0]), "export")).toBe(6);
  });

  it("marks nothing when nothing is being looked for, and keeps the text as it was", () => {
    expect(renderToStaticMarkup(<Marked text="an export" />)).toBe("an export");
    const html = renderToStaticMarkup(
      <Finding.Provider value="ab">
        <Marked text="Abba abba" />
      </Finding.Provider>,
    );
    expect(html).toBe('<mark class="find-hit">Ab</mark>ba <mark class="find-hit">ab</mark>ba');
  });
});
