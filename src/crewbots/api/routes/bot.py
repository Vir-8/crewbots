"""The bot-token API: what a bot's crewbots MCP server calls, acting as its own task and nothing else."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from crewbots.api.context import Context
from crewbots.api.schemas import (
    AskBody,
    ChildMessage,
    Delegation,
    Escalation,
    Forget,
    GroupMember,
    HandoffBody,
    MergeProposal,
    MergeWithdrawal,
    NameBody,
    NextCheck,
    OpenGroup,
    PageEdit,
    PageWrite,
    PermissionAsk,
    PostBody,
    ProposedRoutine,
    Recall,
    Remember,
    RemindOn,
    ReviewBody,
    RoomOut,
    SearchHit,
    ShareBody,
    StateBody,
    ThreadBody,
)
from crewbots.crew_tools import CrewTools, ToolRefused


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    @v1.post("/bot/browser/{action}")
    async def bot_browser(request: Request, action: str, body: dict[str, Any]):
        task = request.state.task
        try:
            content = await CrewTools(runner, task.id, False).call("browser_" + action, body)
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None
        return {"content": content}

    @v1.post("/bot/delegate", status_code=201)
    async def bot_delegate(request: Request, body: Delegation):
        child = runner.delegate(request.state.task.id, body.bot, body.goal, project=body.project,
                                workspace=body.workspace, review_of=body.review_of)
        return {"id": child.id, "bot": child.bot, "title": child.title, "status": child.status}

    @v1.post("/bot/open_group", status_code=201, response_model=RoomOut)
    async def bot_open_group(request: Request, body: OpenGroup):
        return runner.open_group(request.state.task.id, body.title, body.members, body.lead)

    @v1.post("/bot/add_to_group", response_model=RoomOut)
    async def bot_add_to_group(request: Request, body: GroupMember):
        return runner.add_to_group(request.state.task.id, body.bot, body.room)

    @v1.post("/bot/ask", status_code=201)
    async def bot_ask(request: Request, body: AskBody):
        asked = runner.ask(request.state.task.id, body.bot, body.question)
        return {"id": asked.id, "bot": asked.bot, "status": asked.status}

    @v1.get("/bot/tasks")
    async def bot_tasks(request: Request):
        return runner.delegated_by(request.state.task.id)

    @v1.post("/bot/room_status")
    async def bot_room_status(request: Request, body: dict[str, str]):
        return runner.room_status(request.state.task.id, body.get("room") or None)

    @v1.post("/bot/sweep_worktrees")
    async def bot_sweep_worktrees(request: Request, body: dict[str, bool]):
        return await runner.bot_worktree_sweep(request.state.task.id, body.get("dry_run", True))

    @v1.post("/bot/message")
    async def bot_message(request: Request, body: ChildMessage):
        task = runner.message_child(request.state.task.id, body.task_id, body.text)
        return {"id": task.id, "status": task.status}

    @v1.post("/bot/handoff", status_code=201)
    async def bot_handoff(request: Request, body: HandoffBody):
        return runner.handoff(request.state.task.id, body.done, body.output, body.open, body.needs).to_json()

    @v1.post("/bot/review", status_code=201)
    async def bot_review(request: Request, body: ReviewBody):
        findings = [f.model_dump() for f in body.findings]
        return runner.review(request.state.task.id, body.verdict, body.summary, findings).to_json()

    @v1.post("/bot/propose_push")
    async def bot_propose_push(request: Request, body: dict[str, str]):
        from crewbots.push import proposal_message, propose
        proposal = await propose(runner, request.state.task.id, body.get("project", ""), body.get("summary", ""))
        return {"id": proposal, "message": proposal_message(runner, proposal)}

    @v1.post("/bot/propose_replace_main")
    async def bot_propose_replace_main(request: Request, body: dict[str, str]):
        from crewbots.replace_main import proposal_message, propose
        proposal = await propose(runner, request.state.task.id, body.get("project", ""), body.get("branch", ""),
                                 body.get("summary", ""))
        return {"id": proposal, "message": proposal_message(runner, proposal)}

    @v1.post("/bot/propose_merge")
    async def bot_propose_merge(request: Request, body: MergeProposal):
        task = await runner.propose_merge_off_loop(request.state.task.id, body.task_id, body.summary)
        event = runner.store.last_event(task.id, ("merged",)) if task.merge_state == "merged" else None
        data = event.data if event and event.data.get("commit") == task.merged_commit else {}
        return {"id": task.id, "merge_state": task.merge_state, "merged_commit": task.merged_commit,
                "leftover_files": data.get("leftover_files"), "leftover_check_error": data.get("leftover_check_error")}

    @v1.post("/bot/withdraw_merge")
    async def bot_withdraw_merge(request: Request, body: MergeWithdrawal):
        task = runner.withdraw_merge(body.task_id, body.reason, parent_id=request.state.task.id)
        event = runner.store.last_event(task.id, ("merge_withdrawn",))
        return {"id": task.id, "merge_state": task.merge_state, "notice": event.data.get("notice") if event else None}

    @v1.post("/bot/remember", status_code=201)
    async def bot_remember(request: Request, body: Remember):
        note = runner.remember(request.state.task.id, body.title, body.body, shared=body.shared)
        return {"title": note.title, "path": str(note.path)}

    @v1.post("/bot/next_check")
    async def bot_next_check(request: Request, body: NextCheck):
        return {"at": runner.next_check(request.state.task.id, body.minutes, body.why).isoformat()}

    @v1.post("/bot/set_name")
    async def bot_set_name(request: Request, body: NameBody):
        bot = runner.set_name(request.state.task.id, body.name)
        return {"name": bot.name, "display_name": bot.display_name}

    @v1.post("/bot/write_page", status_code=201)
    async def bot_write_page(request: Request, body: PageWrite):
        page = runner.write_page(request.state.task.id, body.page, body.text)
        return {"page": page.name, "path": str(page.path)}

    @v1.post("/bot/edit_page")
    async def bot_edit_page(request: Request, body: PageEdit):
        page = runner.write_page(request.state.task.id, body.page, body.replace, find=body.find)
        return {"page": page.name, "path": str(page.path)}

    @v1.post("/bot/forget")
    async def bot_forget(request: Request, body: Forget):
        note = runner.forget(request.state.task.id, body.name, shared=body.shared)
        return {"title": note.title}

    @v1.post("/bot/propose_routine", status_code=201)
    async def bot_propose_routine(request: Request, body: ProposedRoutine):
        path = runner.propose_routine(request.state.task.id, body.name, body.description, body.goal, body.schedule,
                                      body.on, body.repo)
        return {"path": str(path)}

    @v1.post("/bot/remind_on", status_code=201)
    async def bot_remind_on(request: Request, body: RemindOn):
        return runner.remind_on(request.state.task.id, body.event, body.match, body.then)

    @v1.post("/bot/working_state")
    async def bot_working_state(request: Request, body: dict[str, Any]):
        try:
            return {"result": await CrewTools(runner, request.state.task.id, False).call("working_state", body)}
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None

    @v1.post("/bot/update_state")
    async def bot_update_state(request: Request, body: StateBody):
        return {"path": str(runner.update_state(request.state.task.id, body.state))}

    @v1.post("/bot/recall", response_model=list[SearchHit])
    async def bot_recall(request: Request, body: Recall):
        return runner.recall(request.state.task.id, body.query)

    @v1.post("/bot/start_thread", status_code=201)
    async def bot_start_thread(request: Request, body: ThreadBody):
        thread = runner.start_thread(request.state.task.id, body.title, body.goal, body.project)
        return {"id": thread.id, "bot": thread.bot, "title": thread.title, "status": thread.status}

    @v1.post("/bot/add_bot", status_code=201)
    async def bot_add_bot(request: Request, body: dict[str, Any]):
        try:
            bot = runner.add_bot(request.state.task.id, body)
        except (KeyError, ValueError) as e:  # a missing field, or an access that isn't one
            raise HTTPException(422, f"add_bot: {e}") from None
        return {"name": bot.name, "label": bot.label, "title": bot.title}

    @v1.post("/bot/post", status_code=201)
    async def bot_post(request: Request, body: PostBody):
        return runner.post_from(request.state.task.id, body.text, body.room or None)

    @v1.post("/bot/share", status_code=201)
    async def bot_share(request: Request, body: ShareBody):
        return await runner.share(request.state.task.id, body.files, body.text, body.room or None)

    @v1.post("/bot/review/{tool}")
    async def bot_review_tool(request: Request, tool: str, body: dict[str, Any]):
        """A reviewer's tools, for one that reaches crewbots over MCP. It can only read and draft on its own pull
        request; nothing here posts to GitHub."""
        task = request.state.task
        if task.kind != "review" or not task.pr:
            raise HTTPException(403, "only a code review has these tools")
        try:
            text = await runner.review_tools(task).call(tool, {k: v for k, v in body.items() if v is not None})
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None
        return {"text": text}

    @v1.post("/bot/ask_permission", status_code=201)
    async def bot_ask_permission(request: Request, body: PermissionAsk):
        asked = runner.ask_permission(request.state.task.id, body.what, body.why, body.kind, body.folder)
        return {"id": asked["id"]}

    @v1.post("/bot/escalate")
    async def bot_escalate(request: Request, body: Escalation):
        runner.escalate(request.state.task.id, body.question, [c.model_dump(exclude_none=True) for c in body.choices],
                        body.multiple)
        return {"ok": True}

    return v1
