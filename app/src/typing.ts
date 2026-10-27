// What the message boxes understand as you type: the app's own / commands, and @mentions. Kept apart from the
// components so it can be tested on its own.

/** Commands run in the app itself, from the message box: nothing is sent to a bot. Each shows what it will do first
 * (a panel above the box), and runs only when you say so. `takes`: what it needs after its name. A box offers the
 * ones it can do, by name (`commandsFor`'s `can`). */
export type Command = { name: string; icon: "chart" | "settings" | "handoff" | "users" | "check" | "pause" | "play" | "x"; description: string; takes?: string };

export const COMMANDS: Command[] = [
  { name: "new", icon: "handoff", description: "Start a thread for this bot, beside the chat", takes: "what it's for" },
  { name: "group", icon: "users", description: "Start a group chat with this bot", takes: "its name" },
  { name: "remember", icon: "check", description: "Give this bot a note to keep", takes: "the note" },
  { name: "stop", icon: "x", description: "Stop what's running here now" },
  { name: "pause", icon: "pause", description: "Pause the crew: no new turns until you resume" },
  { name: "resume", icon: "play", description: "Let the crew start turns again" },
  { name: "status", icon: "settings", description: "This chat's model, session and context, and its plan's limits" },
  { name: "usage", icon: "chart", description: "What's left on your Claude and Codex plans" },
];

/** The commands offered for what's in the box: "/" and the start of a command's name; or, for one that takes
 * something, its whole name and what follows. */
export function commandsFor(text: string, can: (name: string) => boolean): Command[] {
  const typed = /^\/(\S*)(?:\s+([\s\S]*))?$/.exec(text);
  if (!typed) return [];
  const [, name, rest] = typed;
  if (rest === undefined) return COMMANDS.filter((c) => c.name.startsWith(name.toLowerCase()) && can(c.name));
  const command = COMMANDS.find((c) => c.name === name.toLowerCase() && c.takes);
  return command && can(command.name) ? [command] : [];
}

/** What follows a command's name in the box. */
export function argumentOf(text: string): string {
  return /^\/\S*\s+([\s\S]*)$/.exec(text)?.[1].trim() ?? "";
}

/** In a group chat's box: the name being typed after an @ at the end of the text, if one is. */
export function mentionTyped(text: string): string | undefined {
  return /(?:^|\s)@([\w-]*)$/.exec(text)?.[1];
}

/** That name, finished: `@na` becomes `@name `. */
export function finishMention(text: string, name: string): string {
  return text.replace(/@([\w-]*)$/, `@${name} `);
}

/** In a review comment: the @mention the caret is in, as where its @ is and what's typed after it. Not one inside
 * a word, an email address or `code`. */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const typed = /(^|[^\w`@])@([\w-]*)$/.exec(text.slice(0, caret));
  return typed ? { start: caret - typed[2].length - 1, query: typed[2] } : null;
}

/** Puts `login` in place of the mention at `at`, with a space after it; where the caret goes then. */
export function insertMention(value: string, at: { start: number; query: string }, login: string): { value: string; caret: number } {
  return {
    value: `${value.slice(0, at.start)}@${login} ${value.slice(at.start + 1 + at.query.length)}`,
    caret: at.start + login.length + 2,
  };
}
