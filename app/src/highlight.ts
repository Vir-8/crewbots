// Light colouring for a text or code file a bot shared: comments, strings, numbers and keywords, by a few rules that
// fit most languages. Not a parser, and no dependency: enough to read by.

export type Token = { text: string; kind?: "comment" | "string" | "number" | "keyword" };

const KEYWORDS = new Set(
  ("and as async await break case catch class const continue def default defer del do elif else enum except export " +
    "extends false final finally fn for from func function go if impl import in interface is lambda let loop match " +
    "mod module mut new nil none not null or package pass private protected pub public raise return self static " +
    "struct super switch this throw throws trait true try type typeof undefined use var void where while with yield " +
    "None True False SELECT FROM WHERE JOIN INSERT UPDATE DELETE CREATE TABLE INTO VALUES AND OR NOT NULL ORDER BY " +
    "GROUP LIMIT select from where join insert update delete create table into values order by group limit").split(" "),
);

// How each kind of file writes a comment.
const HASH = new Set(["py", "sh", "bash", "zsh", "rb", "pl", "r", "yaml", "yml", "toml", "ini", "cfg", "conf", "gd", "jl", "nim", "ex", "exs", "cmake", "dockerfile", "makefile", "mk"]);
const SLASH = new Set(["js", "jsx", "mjs", "cjs", "ts", "tsx", "rs", "go", "java", "kt", "kts", "swift", "c", "h", "cc", "cpp", "hpp", "cs", "php", "scala", "dart", "zig", "glsl", "hlsl", "shader", "css", "scss", "less", "json5", "jsonc", "vue", "svelte", "proto", "gradle"]);
const DASH = new Set(["sql", "lua", "hs", "elm"]);
const TAGS = new Set(["html", "htm", "xml", "svg", "plist", "xaml", "vue", "svelte"]);
const PLAIN = new Set(["txt", "md", "markdown", "csv", "tsv", "log", "rst", "text"]);
/** What counts as text or code, by extension (a .ts file is TypeScript, whatever its MIME type says). */
export const TEXT = new Set([...HASH, ...SLASH, ...DASH, ...TAGS, ...PLAIN, "json", "env", "diff", "patch", "tex", "srt", "vtt", "gitignore"]);

const STRING = String.raw`"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|` + "`(?:\\\\.|[^`\\\\])*`";
const NUMBER = String.raw`\b(?:0x[\da-fA-F]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b`;
const WORD = String.raw`\b[A-Za-z_]\w*\b`;

const patterns = new Map<string, RegExp>();

function pattern(ext: string): RegExp | null {
  if (PLAIN.has(ext)) return null;
  const comments = [
    HASH.has(ext) && "#.*",
    SLASH.has(ext) && String.raw`//.*|/\*[\s\S]*?\*/`,
    DASH.has(ext) && "--.*",
    TAGS.has(ext) && "<!--[\\s\\S]*?-->",
  ].filter(Boolean);
  const key = ext || "?";
  if (!patterns.has(key)) {
    const comment = comments.length ? comments.join("|") : "(?!)";
    patterns.set(key, new RegExp(`(${comment})|(${STRING})|(${NUMBER})|(${WORD})`, "g"));
  }
  return patterns.get(key)!;
}

export function highlight(code: string, ext: string): Token[] {
  const re = pattern(ext.toLowerCase());
  if (!re) return [{ text: code }];
  const out: Token[] = [];
  let last = 0;
  re.lastIndex = 0;
  for (let m = re.exec(code); m; m = re.exec(code)) {
    const kind = m[1] ? "comment" : m[2] ? "string" : m[3] ? "number" : KEYWORDS.has(m[4]) ? "keyword" : undefined;
    if (!kind) continue; // a plain word stays in the text around it
    if (m.index > last) out.push({ text: code.slice(last, m.index) });
    out.push({ text: m[0], kind });
    last = m.index + m[0].length;
  }
  if (last < code.length) out.push({ text: code.slice(last) });
  return out;
}
