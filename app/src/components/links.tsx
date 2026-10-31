import { createContext, useContext, useState, type MouseEvent, type ReactNode } from "react";
import { bridge } from "../bridge";

type From = { files: string[]; folder: string | null };

/** Where a relative path in a message's links is: a file shown under the message whose path ends with it, else the
 * folder the message's task works in, when the app knows it. */
const LinkFrom = createContext<From>({ files: [], folder: null });

/** Sets where the relative links drawn in `children` point: the `files` shown with them (real paths, as the crew
 * found them), and `folder`, or the folder set further out when it isn't given. */
export function LinksFrom({ files = [], folder, children }: { files?: { path: string }[]; folder?: string | null; children: ReactNode }) {
  const outer = useContext(LinkFrom);
  return <LinkFrom.Provider value={{ files: files.map((f) => f.path), folder: folder === undefined ? outer.folder : folder }}>{children}</LinkFrom.Provider>;
}

/** A [label](target) link a bot wrote, drawn as `children`. A web address opens in the browser; a path (absolute, from
 * ~, a file:// address, or relative to where the message is, maybe with a :line after it) is shown in Finder, never
 * opened: a bot wrote it, and it could be something that runs. One Finder can't find greys, and says so. Anything
 * else, or a relative path with nowhere to be from, is only its label. */
export function TextLink({ target, children }: { target: string; children: ReactNode }) {
  const from = useContext(LinkFrom);
  const [missing, setMissing] = useState(false);
  const web = /^https?:\/\//i.test(target);
  const path = web ? "" : local(target, from);
  if (!web && !path) return <>{children}</>;
  const open = (e: MouseEvent) => {
    e.preventDefault();
    if (web) void bridge.openUrl(target).catch(() => undefined);
    else reveal(path).then(() => setMissing(false), () => setMissing(true));
  };
  return (
    <a className={missing ? "text-link missing" : "text-link"} onClick={open} title={missing ? `${path} wasn't found` : web ? target : path}>
      {children}
    </a>
  );
}

/** The file a path link names, or "" if it can't be known. */
function local(target: string, from: From): string {
  let path = target;
  if (/^file:\/\//i.test(path)) {
    try {
      path = decodeURIComponent(path.slice(7));
    } catch {
      return "";
    }
  }
  if (/^[\w+-]+:|^#/.test(path)) return ""; // another scheme, a host:port, or a place in the page
  path = path.replace(/(:\d+)+$/, "").replace(/^\.\//, "");
  if (!path || path.split("/").includes("..")) return "";
  if (path.startsWith("/") || path.startsWith("~/")) return path;
  const shown = from.files.find((f) => f.endsWith(`/${path}`));
  return shown ?? (from.folder ? `${from.folder.replace(/\/$/, "")}/${path}` : "");
}

async function reveal(path: string) {
  await bridge.reveal(path.startsWith("~/") ? `${await bridge.home()}${path.slice(1)}` : path);
}
