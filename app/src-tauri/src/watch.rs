//! What the menu bar and notifications know about the crew: every task's status, kept current from /v1/live, and the
//! transitions worth interrupting you for.

use std::collections::HashMap;

use serde_json::Value;

#[derive(Clone, Debug, Default, PartialEq)]
struct TaskLite {
    title: String,
    bot: String,
    status: String,
    parent_id: Option<String>,
    merge_state: Option<String>,
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
                parent_id: text("parent_id"),
                merge_state: text("merge_state"),
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
}

impl Alert {
    pub fn text(&self) -> (String, String) {
        match self {
            Alert::Question { bot, title } => (format!("{bot} has a question"), title.clone()),
            Alert::Merge { title } => ("Ready to merge".into(), title.clone()),
            Alert::Failed { bot, title } => (format!("{bot} couldn't finish"), title.clone()),
            Alert::Held { bot, title } => (format!("{bot} is waiting on a budget or plan limit"), title.clone()),
            Alert::Done { title } => ("Done".into(), title.clone()),
        }
    }
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
}

impl CrewView {
    pub fn load(&mut self, tasks: &[Value], paused: bool) {
        self.tasks = tasks.iter().filter_map(TaskLite::from).collect();
        self.paused = paused;
    }

    pub fn set_paused(&mut self, paused: bool) {
        self.paused = paused;
    }

    /// Takes in one task update and says whether it's worth a notification.
    pub fn update(&mut self, task: &Value) -> Option<Alert> {
        let (id, now) = TaskLite::from(task)?;
        let before = self.tasks.insert(id.clone(), now.clone());
        let before = before?; // a task just created: you made it, or a bot delegated it; nothing to announce
        if before.merge_state.as_deref() != Some("proposed") && now.merge_state.as_deref() == Some("proposed") {
            return Some(Alert::Merge { title: now.title });
        }
        if before.status == now.status {
            return None;
        }
        if now.status == "held" {
            return Some(Alert::Held { bot: now.bot, title: now.title }); // at any depth: it holds up its goal
        }
        if !now.top_level() {
            return None;
        }
        match now.status.as_str() {
            "needs_input" => Some(Alert::Question { bot: now.bot, title: now.title }),
            "failed" => Some(Alert::Failed { bot: now.bot, title: now.title }),
            // A goal is done when its bot goes idle with nothing it delegated still going. The chief going idle after
            // handing out work isn't it; its last reply, once the work is back, is.
            "idle" if before.active() && !self.busy_below(&id) => Some(Alert::Done { title: now.title }),
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
