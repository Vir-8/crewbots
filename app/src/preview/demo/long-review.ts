// A narrow-panel stress case: open with ?task=t_longreview and choose Review.
import { chatIds } from "./chats";
import { diffs, done, message, reviews, say, work } from "./make";

const path = "src/crewbots/browser/very-long-unbroken-directory-name/another-very-long-unbroken-directory-name/browser_session_manager.py";
const flag = "--proxy-bypass-list=<-loopback>";
const t = work("t_longreview", "engineer", "Inspect a long browser review in the thread panel", chatIds.chief);
message(t.id, "chief", `Inspect ${path} and ${flag}.`);
say(t.id, `The implementation is in \`${path}\`.\n\n\`\`\`sh\nchrome ${flag} --user-data-dir=/a/very-long-unbroken-directory-name/another-very-long-unbroken-directory-name/profile\n\`\`\``);
done(t.id);
reviews.set(t.id, {
  work_id: t.id, version: 1, round: 1, max_rounds: 3, approved: false, failed: true, out_of_rounds: false,
  reviews: ["reviewer", "critic"].map((reviewer, index) => ({
    task_id: `t_longrev${index}`, reviewer, status: "idle", verdict: "fail",
    summary: `The browser setup needs changes. Check ${flag} and the long paths before continuing.`,
    findings: Array.from({ length: 6 }, (_, i) => ({
      location: `${path}:${100 + i}`,
      problem: `Finding ${i + 1}: the configuration containing ${flag} needs to remain readable when the panel is narrow.`,
      scenario: `the path /a/very-long-unbroken-directory-name/another-very-long-unbroken-directory-name/profile is used`,
    })),
  })),
});
diffs.set(t.id, `diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n@@ -1 +1 @@\n-old\n+chrome ${flag} --user-data-dir=/a/very-long-unbroken-directory-name/another-very-long-unbroken-directory-name/profile\n`);
