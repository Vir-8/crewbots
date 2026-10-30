// The Settings page's merge switch and review checkouts, for the preview's bridge: kept while the preview is open.

const ok = (data: unknown) => ({ ok: true, status: 200, data });

let autoMerge = false;
const repos: Record<string, string> = { "acme/web": "~/code/acme-web" };

export function settingsRequest(method: string, pathname: string, parts: string[], body: any) {
  if (pathname === "/crew/merge-settings") {
    if (method === "PUT") autoMerge = Boolean(body.auto_merge);
    return ok({ auto_merge: autoMerge });
  }
  if (method === "GET" && pathname === "/reviews/repos") return ok({ ...repos });
  if (method === "PUT" && parts[0] === "reviews" && parts[1] === "repos" && parts.length === 4) {
    const repo = `${parts[2]}/${parts[3]}`;
    if (body.folder) repos[repo] = body.folder;
    else delete repos[repo];
    return ok({ ...repos });
  }
  return null;
}
