//! What the menu bar and notifications know about the crew: every task's status, kept current from /v1/live, and the
//! transitions worth interrupting you for.

use std::collections::{HashMap, HashSet};

use serde_json::Value;

#[derive(Clone, Debug, Default, PartialEq)]
struct TaskLite {
    title: String,
    bot: String,
    status: String,
    kind: String, // chat, seat or thread
    parent_id: Option<String>,
    room_id: Option<String>,
    merge_state: Option<String>,
    result: Option<String>,
}

impl TaskLite {
    fn from(task: &Value) -> Option<(String, Self)> {
        let text = |k: &str| task.get(k).and_then(Value::as_str).map(String::from);
        Some((
            text("id")?,
            TaskLite {
                title: text("title").unwrap_or_default(),
                bot: text("bot").unwrap_or_default(),
                status: text("status").unwrap_or_default(),
                kind: text("kind").unwrap_or_else(|| "thread".into()),
                parent_id: text("parent_id"),
                room_id: text("room_id"),
                merge_state: text("merge_state"),
                result: text("result"),
            },
        ))
    }

    fn active(&self) -> bool {
        self.status == "queued" || self.status == "running"
    }

    fn top_level(&self) -> bool {
        self.parent_id.is_none()
    }

    /// Waiting on the user: a merge to approve, a task held on a budget, or a goal that asks, failed, or was cut off.
    fn needs_you(&self) -> bool {
        self.merge_state.as_deref() == Some("proposed")
            || self.status == "held"
            || (self.top_level() && matches!(self.status.as_str(), "needs_input" | "failed" | "interrupted"))
    }
}

#[derive(Debug, PartialEq)]
pub enum Alert {
    Question { bot: String, title: String },
    Merge { title: String },
    Failed { bot: String, title: String },
    Held { bot: String, title: String },
    Done { title: String },
    Replied { bot: String, text: String },
    Said { author: String, text: String },
    NeedsYou { bot: String, text: String },
}

impl Alert {
    pub fn text(&self) -> (String, String) {
        match self {
            Alert::Question { bot, title } => (format!("{bot} has a question"), title.clone()),
            Alert::Merge { title } => ("Ready to merge".into(), title.clone()),
            Alert::Failed { bot, title } => (format!("{bot} couldn't finish"), title.clone()),
            Alert::Held { bot, title } => (format!("{bot} is waiting on a budget or plan limit"), title.clone()),
            Alert::Done { title } => ("Done".into(), title.clone()),
            Alert::Replied { bot, text } => (bot.clone(), preview(text)),
            Alert::Said { author, text } => (format!("{author} in a group chat"), preview(text)),
            Alert::NeedsYou { bot, text } => (format!("{bot} needs you"), preview(text)),
        }
    }
}

fn preview(text: &str) -> String {
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() > 140 { format!("{}…", flat.chars().take(139).collect::<String>()) } else { flat }
}

#[derive(Debug, Default, PartialEq, Clone)]
pub struct Summary {
    pub running: usize,
    pub needs_you: usize,
    pub paused: bool,
}

impl Summary {
    /// The text beside the menu bar icon: short, and empty when there's nothing to say.
    pub fn title(&self) -> String {
        match (self.needs_you, self.running) {
            (0, 0) => String::new(),
            (0, r) => format!("{r}"),
            (n, _) => format!("{n}!"),
        }
    }

    /// The first line of the menu.
    pub fn line(&self) -> String {
        let mut parts = Vec::new();
        if self.running > 0 {
            parts.push(format!("{} working", self.running));
        }
        if self.needs_you > 0 {
            parts.push(format!("{} need{} you", self.needs_you, if self.needs_you == 1 { "s" } else { "" }));
        }
        let state = if parts.is_empty() { "Everyone is idle".to_string() } else { parts.join(" · ") };
        if self.paused { format!("Paused · {state}") } else { state }
    }
}

#[derive(Default)]
pub struct CrewView {
    tasks: HashMap<String, TaskLite>,
    paused: bool,
    muted: HashSet<String>, // group chats you muted: what's said there isn't announced
    muted_bots: HashSet<String>,
}

impl CrewView {
    pub fn load(&mut self, tasks: &[Value], paused: bool) {
        self.tasks = tasks.iter().filter_map(TaskLite::from).collect();
        self.paused = paused;
    }

    pub fn set_paused(&mut self, paused: bool) {
        self.paused = paused;
    }

    /// Takes in group chats, all of them or one that changed, and remembers which are muted.
    pub fn set_rooms(&mut self, rooms: &[Value]) {
        for room in rooms {
            let Some(id) = room["id"].as_str() else { continue };
            if room["muted"].as_bool().unwrap_or(false) {
                self.muted.insert(id.to_string());
            } else {
                self.muted.remove(id);
            }
        }
    }

    pub fn set_chat_mute(&mut self, bot: &str, muted: bool) {
        if muted { self.muted_bots.insert(bot.to_string()); } else { self.muted_bots.remove(bot); }
    }

    pub fn set_chats(&mut self, response: &Value) {
        if response["ok"].as_bool() != Some(true) { return; }
        let Some(chats) = response["data"].as_array() else { return };
        self.muted_bots.clear();
        for chat in chats {
            if let Some(bot) = chat["bot"].as_str() {
                self.set_chat_mute(bot, chat["muted"].as_bool().unwrap_or(false));
            }
        }
    }

    fn muted_task(&self, task: &TaskLite) -> bool {
        (task.kind == "chat" && self.muted_bots.contains(&task.bot))
            || (task.kind == "seat" && task.room_id.as_ref().is_some_and(|room| self.muted.contains(room)))
    }

    /// Takes in one task update and says whether it's worth a notification.
    pub fn update(&mut self, task: &Value) -> Option<Alert> {
        let (id, now) = TaskLite::from(task)?;
        let before = self.tasks.insert(id.clone(), now.clone());
        let before = before?; // a task just created: you made it, or a bot delegated it; nothing to announce
        if self.muted_task(&now) { return None; }
        if before.merge_state.as_deref() != Some("proposed") && now.merge_state.as_deref() == Some("proposed") {
            return Some(Alert::Merge { title: now.title });
        }
        if before.status == now.status || now.kind == "seat" {
            return None; // a bot in a group chat is heard through what it says there
        }
        if now.status == "held" {
            return Some(Alert::Held { bot: now.bot, title: now.title }); // at any depth: it holds up its goal
        }
        if !now.top_level() {
            return None;
        }
        match now.status.as_str() {
            "needs_input" => Some(Alert::Question { bot: now.bot, title: now.title }),
            // A bot's chat going quiet means it replied; the reply is the notification.
            "idle" if now.kind == "chat" && before.active() && !self.busy_below(&id) => {
                Some(Alert::Replied { bot: now.bot, text: now.result.unwrap_or_default() })
            }
            "failed" => Some(Alert::Failed { bot: now.bot, title: now.title }),
            // A goal is done when its bot goes idle with nothing it delegated still going. The chief going idle after
            // handing out work isn't it; its last reply, once the work is back, is.
            "idle" if before.active() && !self.busy_below(&id) => Some(Alert::Done { title: now.title }),
            _ => None,
        }
    }

    /// Something a bot raised on its own, as "needs you": the crew already kept it to a few a day, outside quiet hours.
    pub fn raised(&self, event: &Value) -> Option<Alert> {
        if event["kind"].as_str()? != "text" || event["data"]["level"].as_str()? != "needs_you" {
            return None;
        }
        let task = self.tasks.get(event["task_id"].as_str()?);
        if task.is_some_and(|t| self.muted_task(t)) { return None; }
        let bot = task.map(|t| t.bot.clone()).unwrap_or_default();
        Some(Alert::NeedsYou { bot, text: event["data"]["text"].as_str().unwrap_or_default().to_string() })
    }

    /// A message in a group chat: one from a bot is worth a notification, unless you muted the chat.
    pub fn said(&self, message: &Value) -> Option<Alert> {
        let author = message["author"].as_str()?;
        if author == "user" || message["room_id"].as_str().is_some_and(|room| self.muted.contains(room)) {
            return None;
        }
        let mut text = message["text"].as_str().unwrap_or_default().to_string();
        if text.is_empty() {
            let files = message["attachments"].as_array().into_iter().flatten().filter_map(|f| f["name"].as_str());
            text = files.collect::<Vec<_>>().join(", "); // it shared files and said nothing
        }
        Some(Alert::Said { author: author.to_string(), text })
    }

    pub fn summary(&self) -> Summary {
        Summary {
            running: self.tasks.values().filter(|t| t.active()).count(),
            needs_you: self.tasks.values().filter(|t| t.needs_you()).count(),
            paused: self.paused,
        }
    }

    fn busy_below(&self, id: &str) -> bool {
        self.tasks.iter().any(|(child, t)| t.parent_id.as_deref() == Some(id) && (t.active() || self.busy_below(child)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn task(id: &str, status: &str, parent: Option<&str>) -> Value {
        json!({"id": id, "title": format!("title {id}"), "bot": "chief", "status": status, "parent_id": parent,
               "merge_state": null})
    }

    #[test]
    fn new_tasks_are_not_announced() {
        let mut view = CrewView::default();
        assert_eq!(view.update(&task("g", "running", None)), None);
    }

    #[test]
    fn a_question_on_a_goal_is_announced_once() {
        let mut view = CrewView::default();
        view.load(&[task("g", "running", None)], false);
        assert!(matches!(view.update(&task("g", "needs_input", None)), Some(Alert::Question { .. })));
        assert_eq!(view.update(&task("g", "needs_input", None)), None);
    }

    #[test]
    fn a_goal_is_done_only_when_its_delegated_work_is_done_too() {
        let mut view = CrewView::default();
        view.load(&[task("g", "running", None), task("c", "running", Some("g"))], false);
        assert_eq!(view.update(&task("g", "idle", None)), None); // the chief handed out work and went idle
        assert_eq!(view.update(&task("c", "idle", Some("g"))), None); // the work came back; the chief wakes
        view.update(&task("g", "running", None));
        assert!(matches!(view.update(&task("g", "idle", None)), Some(Alert::Done { .. }))); // its last reply
    }

    #[test]
    fn a_proposed_merge_is_announced_whoever_owns_it() {
        let mut view = CrewView::default();
        view.load(&[task("c", "idle", Some("g"))], false);
        let mut proposed = task("c", "idle", Some("g"));
        proposed["merge_state"] = json!("proposed");
        assert!(matches!(view.update(&proposed), Some(Alert::Merge { .. })));
    }

    #[test]
    fn a_reply_in_a_chat_is_announced_with_what_it_said() {
        let mut view = CrewView::default();
        let mut chat = task("c", "running", None);
        chat["kind"] = json!("chat");
        view.load(&[chat.clone()], false);
        chat["status"] = json!("idle");
        chat["result"] = json!("Merged.   The CSV export is with the engineer.");
        assert_eq!(view.update(&chat), Some(Alert::Replied { bot: "chief".into(), text: "Merged.   The CSV export is with the engineer.".into() }));
        assert_eq!(Alert::Replied { bot: "chief".into(), text: "a  b".into() }.text().1, "a b");
    }

    #[test]
    fn bots_speaking_in_group_chats_are_announced_but_not_you() {
        let view = CrewView::default();
        assert!(matches!(view.said(&json!({"author": "engineer", "text": "done"})), Some(Alert::Said { .. })));
        assert_eq!(view.said(&json!({"author": "user", "text": "hi"})), None);
    }

    #[test]
    fn what_a_bot_raises_as_needs_you_is_announced_and_nothing_less() {
        let mut view = CrewView::default();
        view.load(&[json!({"id": "c", "title": "Chat", "bot": "engineer", "status": "idle", "parent_id": null,
                           "merge_state": null, "kind": "chat"})], false);
        let raised = |level: &str| json!({"task_id": "c", "kind": "text", "data": {"text": "CI is red", "level": level}});
        assert_eq!(view.raised(&raised("needs_you")), Some(Alert::NeedsYou { bot: "engineer".into(), text: "CI is red".into() }));
        assert_eq!(view.raised(&raised("question")), None);
        assert_eq!(view.raised(&json!({"task_id": "c", "kind": "text", "data": {"text": "hi"}})), None);
    }

    #[test]
    fn a_muted_group_chat_is_not_announced_until_unmuted() {
        let mut view = CrewView::default();
        let said = json!({"room_id": "r1", "author": "engineer", "text": "done"});
        view.set_rooms(&[json!({"id": "r1", "muted": true}), json!({"id": "r2", "muted": false})]);
        assert_eq!(view.said(&said), None);
        assert!(view.said(&json!({"room_id": "r2", "author": "engineer", "text": "done"})).is_some());
        let shared = json!({"room_id": "r2", "author": "engineer", "text": "", "attachments": [{"name": "cut.mp4"}]});
        assert!(matches!(view.said(&shared), Some(Alert::Said { text, .. }) if text == "cut.mp4"));
        view.set_rooms(&[json!({"id": "r1", "muted": false})]);
        assert!(view.said(&said).is_some());
    }

    #[test]
    fn a_muted_direct_chat_suppresses_all_its_alerts_until_unmuted() {
        let mut view = CrewView::default();
        let mut chat = task("c", "running", None);
        chat["kind"] = json!("chat");
        view.load(&[chat.clone()], false);
        view.set_chats(&json!({"ok": true, "data": [{"bot": "chief", "muted": true}]}));
        chat["status"] = json!("needs_input");
        assert_eq!(view.update(&chat), None);
        let raised = json!({"task_id": "c", "kind": "text", "data": {"level": "needs_you", "text": "hi"}});
        assert_eq!(view.raised(&raised), None);
        chat["status"] = json!("running");
        view.update(&chat);
        chat["status"] = json!("idle");
        assert_eq!(view.update(&chat), None);
        view.set_chat_mute("chief", false);
        assert!(view.raised(&raised).is_some());
        chat["status"] = json!("running");
        view.update(&chat);
        chat["status"] = json!("idle");
        assert!(matches!(view.update(&chat), Some(Alert::Replied { .. })));
    }

    #[test]
    fn failed_or_invalid_chat_load_keeps_mutes_but_a_valid_empty_list_clears_them() {
        let mut view = CrewView::default();
        let mut chat = task("c", "running", None);
        chat["kind"] = json!("chat");
        view.load(&[chat], false);
        view.set_chat_mute("chief", true);
        let raised = json!({"task_id": "c", "kind": "text", "data": {"level": "needs_you", "text": "hi"}});
        for response in [json!({"ok": false, "data": {"detail": "offline"}}),
                         json!({"ok": false, "data": []}), json!({"ok": true, "data": null})] {
            view.set_chats(&response);
            assert_eq!(view.raised(&raised), None);
        }
        view.set_chats(&json!({"ok": true, "data": []}));
        assert!(view.raised(&raised).is_some());
    }

    #[test]
    fn a_muted_group_seat_does_not_announce_a_hold() {
        let mut view = CrewView::default();
        let mut seat = task("s", "running", None);
        seat["kind"] = json!("seat");
        seat["room_id"] = json!("r1");
        view.load(&[seat.clone()], false);
        view.set_rooms(&[json!({"id": "r1", "muted": true})]);
        seat["status"] = json!("held");
        assert_eq!(view.update(&seat), None);
    }

    #[test]
    fn a_held_task_is_announced_and_needs_you_at_any_depth() {
        let mut view = CrewView::default();
        view.load(&[task("g", "idle", None), task("c", "queued", Some("g"))], false);
        assert!(matches!(view.update(&task("c", "held", Some("g"))), Some(Alert::Held { .. })));
        assert_eq!(view.summary().needs_you, 1);
        assert_eq!(view.update(&task("c", "running", Some("g"))), None);
        assert_eq!(view.summary().needs_you, 0);
    }

    #[test]
    fn the_summary_counts_work_and_what_needs_you() {
        let mut view = CrewView::default();
        view.load(&[task("a", "running", None), task("b", "queued", Some("a")), task("c", "failed", None)], true);
        let summary = view.summary();
        assert_eq!(summary, Summary { running: 2, needs_you: 1, paused: true });
        assert_eq!(summary.title(), "1!");
        assert_eq!(summary.line(), "Paused · 2 working · 1 needs you");
        assert_eq!(Summary::default().line(), "Everyone is idle");
    }
}
