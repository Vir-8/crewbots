import { Component, type ErrorInfo, type ReactNode } from "react";
import { api } from "../api";
import { Icon } from "./Icon";

/** Keeps one part's failure to itself: the rest of the window carries on, this part says so and can be shown again,
 * and the error goes to the daemon's log. `as` and `className` give the fallback the part's place in the layout. */
export class Boundary extends Component<
  { where: string; children: ReactNode; as?: "div" | "aside" | "section"; className?: string },
  { error: Error | null; tries: number }
> {
  state = { error: null as Error | null, tries: 0 };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    report(this.props.where, error, info.componentStack ?? undefined);
  }

  render() {
    if (!this.state.error) return <Shown key={this.state.tries}>{this.props.children}</Shown>;
    const Tag = this.props.as ?? "div";
    return (
      <Tag className={`${this.props.className ?? ""} failed-part`} role="alert">
        <span className="failed-part-line">
          <Icon name="alert" size={13} />
          This part failed to show.
          <button className="text-button" onClick={() => this.setState((s) => ({ error: null, tries: s.tries + 1 }))}>
            Reload it
          </button>
        </span>
      </Tag>
    );
  }
}

function Shown({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

const sent: number[] = []; // when errors were reported, so a part failing over and over doesn't flood the log
const PER_MINUTE = 20;

/** An error in the window, into the daemon's log (it can't reach a terminal from here). */
export function report(where: string, error: unknown, stack?: string) {
  const now = Date.now();
  while (sent.length && now - sent[0] > 60_000) sent.shift();
  if (sent.length >= PER_MINUTE) return;
  sent.push(now);
  const e = error instanceof Error ? error : new Error(String(error));
  api.clientError({ where, message: e.message, stack: [e.stack, stack].filter(Boolean).join("\n") || null }).catch(() => undefined);
}
