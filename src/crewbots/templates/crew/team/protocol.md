How work moves between bots.

1. **Every task has exactly one owner.** The owner works on it until it hands off or escalates. No other bot works on it unless asked. Only the chief assigns or reassigns tasks.
2. **Every handoff has the same shape:**

   ```
   HANDOFF
   Task:   what was asked
   From:   this bot → To: the next bot
   Done:   what was completed
   Output: file paths, branches
   Open:   anything unfinished or uncertain
   Needs:  what the next bot must do
   ```

   The next bot works from the handoff and the files it points to, nothing else.
3. **Questions go to the owner, decisions go up.** If you need information, ask the bot that owns it. If you need a decision, escalate to the chief. The chief escalates to the user only when the decision really is theirs.
