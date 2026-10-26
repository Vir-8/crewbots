import type { Crew } from "../state";
import { Icon } from "./Icon";

/** The daemon itself, when it isn't simply running: restarting once the crew is idle, or being reconnected to. */
export function CrewBanner({ crew }: { crew: Crew }) {
  if (crew.status.state !== "connected") {
    const failed = crew.status.state === "error" || crew.status.state === "no-crew";
    return (
      <div className={`crew-banner ${failed ? "bad" : ""}`} role="status">
        {failed ? <Icon name="alert" size={13} /> : <span className="working-dot" />}
        <span>{crew.status.message?.split("\n")[0] ?? "Reconnecting to the crew…"}</span>
      </div>
    );
  }
  if (!crew.crewState?.restarting) return null;
  const running = crew.crewState.turns_running;
  return (
    <div className="crew-banner" role="status">
      <Icon name="repeat" size={13} />
      <span>
        Restarting when the crew is idle
        {running > 0 && <span className="faint"> · {running === 1 ? "1 turn" : `${running} turns`} still running</span>}
      </span>
      <button className="text-button" onClick={() => crew.restart(false)} title="Turns cut off carry on after the restart">
        Restart now
      </button>
      <button className="text-button" onClick={() => crew.cancelRestart()}>
        Cancel
      </button>
    </div>
  );
}
