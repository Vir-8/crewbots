// What the code review page's parts share.

import type { Pull } from "../../types";

export type Selected = { repo: string; number: number };

export const area = () => document.querySelector<HTMLElement>(".review-area"); // the grid both edges resize

/** Who a comment might @mention, the author first: from GitHub, or just the author for a pull request kept from before. */
export const people = (pull: Pull) => (pull.people?.length ? pull.people : [pull.author]);
