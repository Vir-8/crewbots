// What the message boxes understand as you type: the app's own / commands, and @mentions. Kept apart from the
// components so it can be tested on its own.

/** Commands run in the app itself, from the message box: nothing is sent to a bot. `task`: only where there's a chat
 * or thread to report on. */
export const COMMANDS: { name: string; icon: "chart" | "settings"; description: string; task?: boolean }[] = [
  { name: "status", icon: "settings", description: "This chat's model, session and context, and its plan's limits", task: true },
  { name: "usage", icon: "chart", description: "What's left on your Claude and Codex plans" },
];

/** The commands offered for what's in the box: "/" and the start of a command's name, and nothing else. */
export function commandsFor(text: string, hasTask: boolean): typeof COMMANDS {
  const slash = /^\/(\S*)$/.exec(text)?.[1];
  if (slash === undefined) return [];
  return COMMANDS.filter((c) => c.name.startsWith(slash.toLowerCase()) && (!c.task || hasTask));
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
