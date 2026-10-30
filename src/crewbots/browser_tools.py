"""Browser tool dispatch shared by the in-process and HTTP MCP transports."""

from crewbots import attachments
from crewbots.browser_cdp import BrowserError
from crewbots.runner.common import effective_network
from crewbots.webkit import WebKitError


async def browser_call(runner, task_id, name, args):
    task = runner.store.get_task(task_id)
    bot = runner.bot_for(task.bot)
    network = effective_network(bot, runner.store.grants(task_id))
    runner.store.log_agent("browser", task.bot, task_id=task_id, text=name)
    # Screenshots go in the crew's attachments, not where the bot works: a worktree's commit would take them into the
    # repo. They stay readable to the bot there, and `share` can show them.
    shots = runner.crew.home / attachments.FOLDER / "browser" / task_id
    try:
        return await runner.browser.action(task_id, shots, network, name.removeprefix("browser_"), args)
    except BrowserError as e:
        runner.store.log_agent("refused", task.bot, task_id=task_id, text=f"{name}: {e}")
        raise


async def webkit_call(runner, task_id, args):
    """webkit_check: local pages only, whatever the bot's network setting, since it's for the app's own pages."""
    bot = runner.store.get_task(task_id).bot
    runner.store.log_agent("webkit", bot, task_id=task_id, text=str(args.get("url", "")))
    options = {k: args[k] for k in ("click", "setup", "js", "wait", "width", "height") if args.get(k) is not None}
    try:
        return await runner.webkit.check(args["url"], **options)
    except WebKitError as e:
        runner.store.log_agent("refused", bot, task_id=task_id, text=f"webkit_check: {e}")
        raise
