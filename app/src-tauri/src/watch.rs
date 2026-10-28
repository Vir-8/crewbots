//! What the menu bar and notifications know about the crew: every task's status, kept current from /v1/live, and the
//! things worth interrupting you for: only what waits on you (a question, a permission, a push, a replaced main, a merge
//! to approve or one that failed, a held budget, something a bot raised as needing you).

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
    Permission { bot: String, text: String },
    Push { title: String },
    ReplaceMain { title: String },
    Merge { title: String },
    MergeFailed { title: String, error: String },
    Held { bot: String, title: String },
    NeedsYou { bot: String, text: String },
}

impl Alert {
    pub fn text(&self) -> (String, String) {
        match self {
            Alert::Question { bot, title } => (format!("{bot} has a question"), title.clone()),
            Alert::Permission { bot, text } => (format!("{bot} needs your permission"), preview(text)),
            Alert::Push { title } => ("Ready to push".into(), title.clone()),
            Alert::ReplaceMain { title } => ("Ready to replace main".into(), title.clone()),
            Alert::Merge { title } => ("Ready to merge".into(), title.clone()),
            Alert::MergeFailed { title, error } => (format!("Merge failed: {title}"), preview(error)),
            Alert::Held { bot, title } => (format!("{bot} is waiting on a budget or plan limit"), title.clone()),
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
    names: HashMap<String, String>, // bot handle -> what the app calls it
    asked: HashSet<String>, // tasks that asked permission: their wait is announced as that, not as a question
}

impl CrewView {
    pub fn load(&mut self, tasks: &[Value], paused: bool) {
        self.tasks = tasks.iter().filter_map(TaskLite::from).collect();
        self.asked.clear(); // a reconnect: what was asked before is answered or gone
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

    /// Takes in the bots, to call each by its display name rather than its handle.
    pub fn set_bots(&mut self, bots: &Value) {
        let Some(bots) = bots["data"].as_array() else { return };
        self.names = bots
            .iter()
            .filter_map(|b| Some((b["name"].as_str()?.to_string(), b["display_name"].as_str().filter(|n| !n.is_empty())?.to_string())))
            .collect();
    }

    fn name(&self, handle: &str) -> String {
        self.names.get(handle).cloned().unwrap_or_else(|| handle.to_string())
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
        if matches!(now.status.as_str(), "idle" | "failed" | "cancelled" | "interrupted") {
            self.asked.remove(&id); // its turn ended without waiting on the permission it asked
        }
        if self.muted_task(&now) { return None; }
        if before.status == now.status {
            return None;
        }
        let bot = self.name(&now.bot);
        if now.status == "held" {
            return Some(Alert::Held { bot, title: now.title }); // at any depth, a group chat's seat too
        }
        if now.kind == "seat" {
            return None; // a bot in a group chat is heard through what it says there
        }
        if now.status == "needs_input" && self.asked.remove(&id) {
            return None; // it asked permission, which was announced as that
        }
        if now.top_level() && now.status == "needs_input" {
            return Some(Alert::Question { bot, title: now.title });
        }
        None
    }

    /// What an event says that waits on you: a bot's own "needs you", a permission to give, a push or a replaced main
    /// to press, a merge to approve (unless it merges by itself) or one that failed.
    pub fn raised(&mut self, event: &Value) -> Option<Alert> {
        let kind = event["kind"].as_str()?;
        let data = &event["data"];
        let task_id = event["task_id"].as_str()?;
        if kind == "permission_answer" {
            self.asked.remove(task_id); // before any mute check: muting never keeps a marker alive
            return None;
        }
        let task = self.tasks.get(task_id);
        // A seat's question is judged by its room's mute below, not by the main chat it is shown in.
        if kind != "seat_escalation" && task.is_some_and(|t| self.muted_task(t)) { return None; }
        let title = task.map(|t| t.title.clone()).unwrap_or_default();
        let text = |k: &str| data[k].as_str().unwrap_or_default().to_string();
        match kind {
            "text" if data["level"].as_str() == Some("needs_you") => {
                Some(Alert::NeedsYou { bot: self.name(&task.map(|t| t.bot.clone()).unwrap_or_default()), text: text("text") })
            }
            "seat_escalation" => {
                let seat = data["task"].as_str().and_then(|id| self.tasks.get(id));
                if seat.is_some_and(|t| t.room_id.as_ref().is_some_and(|room| self.muted.contains(room))) { return None; }
                let bot = self.name(&task.map(|t| t.bot.clone()).unwrap_or_default());
                Some(Alert::Question { bot, title: format!("{}: {}", text("room"), text("question")) })
            }
            "permission" if data["task"].as_str() == Some(task_id) => {
                self.asked.insert(task_id.to_string());
                Some(Alert::Permission { bot: self.name(&text("bot")), text: text("what") })
            }
            "push_proposed" if data["error"].is_null() => Some(Alert::Push { title: text("summary") }),
            "replace_main_proposed" if data["error"].is_null() => Some(Alert::ReplaceMain { title: text("summary") }),
            "merge_proposed" if data["auto"].as_bool() != Some(true) => Some(Alert::Merge { title }),
            "merge_failed" => Some(Alert::MergeFailed { title, error: text("error") }),
            _ => None,
        }
    }

    pub fn summary(&self) -> Summary {
        Summary {
            running: self.tasks.values().filter(|t| t.active()).count(),
            needs_you: self.tasks.values().filter(|t| t.needs_you()).count(),
            paused: self.paused,
        }
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

    fn event(task: &str, kind: &str, data: Value) -> Value {
        json!({"task_id": task, "kind": kind, "data": data})
    }

    #[test]
    fn a_merge_to_approve_is_announced_unless_it_merges_by_itself() {
        let mut view = CrewView::default();
        view.load(&[task("c", "idle", Some("g"))], false);
        let proposed = |auto: bool| event("c", "merge_proposed", json!({"summary": "s", "auto": auto}));
        assert!(matches!(view.raised(&proposed(false)), Some(Alert::Merge { .. })));
        assert_eq!(view.raised(&proposed(true)), None);
        // the status change that comes with a proposal is not an announcement of its own
        let mut now = task("c", "idle", Some("g"));
        now["merge_state"] = json!("proposed");
        assert_eq!(view.update(&now), None);
    }

    #[test]
    fn a_failed_merge_is_announced() {
        let mut view = CrewView::default();
        view.load(&[task("c", "idle", Some("g"))], false);
        let failed = event("c", "merge_failed", json!({"error": "conflict in a.txt", "automatic": true}));
        assert_eq!(view.raised(&failed).map(|a| a.text()), Some(("Merge failed: title c".into(), "conflict in a.txt".into())));
    }

    #[test]
    fn permissions_pushes_and_replaced_mains_are_announced() {
        let mut view = CrewView::default();
        view.load(&[task("g", "running", None)], false);
        let ask = event("g", "permission", json!({"task": "g", "bot": "chief", "what": "write in ~/x"}));
        assert!(matches!(view.raised(&ask), Some(Alert::Permission { .. })));
        // its wait on the answer is not also announced as a question
        assert_eq!(view.update(&task("g", "needs_input", None)), None);
        assert!(matches!(view.raised(&event("g", "push_proposed", json!({"summary": "ship it"}))), Some(Alert::Push { .. })));
        assert_eq!(view.raised(&event("g", "push_proposed", json!({"summary": "x", "error": "no upstream"}))), None);
        assert!(matches!(view.raised(&event("g", "replace_main_proposed", json!({"summary": "s"}))),
                         Some(Alert::ReplaceMain { .. })));
    }

    #[test]
    fn a_room_seats_escalation_in_the_main_chat_is_announced_unless_muted() {
        let mut view = CrewView::default();
        let mut chat = task("c", "idle", None);
        chat["kind"] = json!("chat");
        let mut seat = task("s", "running", None);
        seat["kind"] = json!("seat");
        seat["room_id"] = json!("r1");
        view.load(&[chat, seat], false);
        let escalation = event("c", "seat_escalation", json!({"task": "s", "room": "Eng", "question": "Ship?"}));
        assert_eq!(view.raised(&escalation).unwrap().text(), ("chief has a question".into(), "Eng: Ship?".into()));
        view.set_rooms(&[json!({"id": "r1", "muted": true})]);
        assert_eq!(view.raised(&escalation), None);
    }

    #[test]
    fn muting_the_main_chat_does_not_silence_a_room_seats_escalation() {
        let mut view = CrewView::default();
        let mut chat = task("c", "idle", None);
        chat["kind"] = json!("chat");
        let mut seat = task("s", "running", None);
        seat["kind"] = json!("seat");
        seat["room_id"] = json!("r1");
        view.load(&[chat, seat], false);
        view.set_chat_mute("chief", true);
        view.set_rooms(&[json!({"id": "r1", "muted": false})]);
        let escalation = event("c", "seat_escalation", json!({"task": "s", "room": "Eng", "question": "Ship?"}));
        assert!(matches!(view.raised(&escalation), Some(Alert::Question { .. })));
    }

    #[test]
    fn muting_does_not_keep_a_permission_marker_alive() {
        let mut view = CrewView::default();
        let mut chat = task("c", "running", None);
        chat["kind"] = json!("chat");
        view.load(&[chat.clone()], false);
        view.raised(&event("c", "permission", json!({"task": "c", "bot": "chief", "what": "w"})));
        view.set_chat_mute("chief", true);
        view.raised(&event("c", "permission_answer", json!({"permission": 1, "allowed": true})));
        view.set_chat_mute("chief", false);
        chat["status"] = json!("needs_input");
        assert!(matches!(view.update(&chat), Some(Alert::Question { .. })));
        // the same for a turn that ends while muted
        chat["status"] = json!("running");
        view.update(&chat);
        view.raised(&event("c", "permission", json!({"task": "c", "bot": "chief", "what": "w"})));
        view.set_chat_mute("chief", true);
        chat["status"] = json!("failed");
        view.update(&chat);
        view.set_chat_mute("chief", false);
        chat["status"] = json!("running");
        view.update(&chat);
        chat["status"] = json!("needs_input");
        assert!(matches!(view.update(&chat), Some(Alert::Question { .. })));
    }

    #[test]
    fn a_seat_that_becomes_held_is_announced() {
        let mut view = CrewView::default();
        let mut seat = task("s", "running", None);
        seat["kind"] = json!("seat");
        view.load(&[seat.clone()], false);
        seat["status"] = json!("held");
        assert!(matches!(view.update(&seat), Some(Alert::Held { .. })));
        seat["status"] = json!("needs_input");
        assert_eq!(view.update(&seat), None);
    }

    #[test]
    fn a_permission_marker_does_not_outlive_its_permission() {
        let ask = |id: &str| event(id, "permission", json!({"task": id, "bot": "chief", "what": "w"}));
        let mut view = CrewView::default();
        view.load(&[task("g", "running", None)], false);
        view.raised(&ask("g"));
        view.update(&task("g", "failed", None)); // the turn failed with the permission unanswered
        view.update(&task("g", "running", None));
        assert!(matches!(view.update(&task("g", "needs_input", None)), Some(Alert::Question { .. })));
        view.raised(&ask("g"));
        view.raised(&event("g", "permission_answer", json!({"permission": 1, "allowed": false})));
        view.update(&task("g", "running", None));
        assert!(matches!(view.update(&task("g", "needs_input", None)), Some(Alert::Question { .. })));
        view.raised(&ask("g"));
        view.load(&[task("g", "running", None)], false); // reconnect
        assert!(matches!(view.update(&task("g", "needs_input", None)), Some(Alert::Question { .. })));
    }

    #[test]
    fn a_reply_a_finished_goal_and_a_failure_are_not_announced() {
        let mut view = CrewView::default();
        let mut chat = task("c", "running", None);
        chat["kind"] = json!("chat");
        view.load(&[chat.clone(), task("g", "running", None)], false);
        chat["status"] = json!("idle");
        chat["result"] = json!("NO_REPLY");
        assert_eq!(view.update(&chat), None);
        assert_eq!(view.update(&task("g", "idle", None)), None);
        assert_eq!(view.update(&task("g", "failed", None)), None);
    }

    #[test]
    fn titles_use_display_names_not_handles() {
        let mut view = CrewView::default();
        view.load(&[task("g", "running", None), task("h", "running", Some("g"))], false);
        view.set_bots(&json!({"ok": true, "data": [{"name": "chief", "display_name": "Roger"},
                                                    {"name": "eng-lead", "display_name": null}]}));
        assert_eq!(view.update(&task("g", "needs_input", None)).unwrap().text().0, "Roger has a question");
        assert_eq!(view.update(&task("h", "held", Some("g"))).unwrap().text().0, "Roger is waiting on a budget or plan limit");
        let raised = json!({"task_id": "g", "kind": "text", "data": {"text": "CI is red", "level": "needs_you"}});
        assert_eq!(view.raised(&raised).unwrap().text().0, "Roger needs you");
        let ask = event("g", "permission", json!({"task": "g", "bot": "chief", "what": "w"}));
        assert_eq!(view.raised(&ask).unwrap().text().0, "Roger needs your permission");
        assert_eq!(view.name("eng-lead"), "eng-lead"); // no display name: the handle
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
        view.set_chat_mute("chief", false);
        assert!(view.raised(&raised).is_some());
        chat["status"] = json!("running");
        view.update(&chat);
        chat["status"] = json!("needs_input");
        assert!(matches!(view.update(&chat), Some(Alert::Question { .. })));
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
