import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { task } from "../preview/demo/make";
import { AskCard } from "./ThreadParts";

it("renders the complete question and answer as Markdown under separate labels", () => {
  const answer = "**Checked**\n\n- First case\n- Second case\n\n" + "Long answer ".repeat(50);
  const result = task("t_ask_test01", "critic", "Test", { kind: "ask", result: answer });
  const html = renderToStaticMarkup(<AskCard bot="critic" question="A **question**?" task={result} onOpen={vi.fn()} />);
  expect(html).toContain("Asked critic</button>");
  expect(html).toContain("Answer</div>");
  expect(html).toContain("<strong>question</strong>");
  expect(html).toContain("<strong>Checked</strong>");
  expect(html).toContain('<li><span class="pre">First case</span></li>');
  expect(html.match(/Long answer/g)).toHaveLength(50);
  expect(html.match(/ask-card-markdown clamped/g)).toHaveLength(2);
});

it("keeps the pending state visible without an actionable card", () => {
  const html = renderToStaticMarkup(<AskCard bot="reviewer" question="Ready?" onOpen={vi.fn()} />);
  expect(html).toContain("Answering…");
  expect(html).not.toContain('role="button"');
});
