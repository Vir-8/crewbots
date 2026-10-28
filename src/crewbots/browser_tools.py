"""Browser tool dispatch shared by the in-process and HTTP MCP transports."""

from crewbots.browser_cdp import BrowserError


async def browser_call(runner, task_id, name, args):
    task = runner.store.get_task(task_id)
    bot = runner.bot_for(task.bot)
    network = True if any(g["kind"] == "network" for g in runner.store.grants(task_id)) else bot.network
    runner.store.log_agent("browser", task.bot, task_id=task_id, text=name)
    try:
        return await runner.browser.action(task_id, runner._cwd(task, bot), network, name.removeprefix("browser_"), args)
    except BrowserError as e:
        runner.store.log_agent("refused", task.bot, task_id=task_id, text=f"{name}: {e}")
        raise
