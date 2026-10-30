"""Workspace tools for ACP. Commands run in our sandbox, never in the account-owning agent process."""

import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import signal
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent

from crewbots import paths
from crewbots.backends.antigravity_runtime import profile
from crewbots.backends.base import Access, package_caches, temp_env
from crewbots.backends.credentials import without_credentials
from crewbots.backends.policy import Policy

OUTPUT_LIMIT = 100_000


class Workspace:
    def __init__(self, config: dict):
        self.cwd = Path(config["cwd"])
        self.temp = Path(config["temp"])
        self.policy = Policy(Access(config["access"]), self.cwd, set(), network=config["network"],
                             write_dirs=config["write_dirs"], deny_read=[Path(p) for p in config["deny_read"]])

    def read(self, given: str) -> Path:
        path = Path(given).expanduser()
        path = (path if path.is_absolute() else self.cwd / path).resolve()
        if reason := self.policy.check_read({"file_path": str(path)}):
            raise ToolError(reason)
        if any(path.is_relative_to(p.resolve()) for p in (profile(), self.temp)):
            raise ToolError("This path contains private agent configuration")
        return path

    def write(self, given: str) -> Path:
        path = self.read(given)
        if reason := self.policy.check_write(str(path)):
            raise ToolError(reason)
        return path

    def sandbox(self) -> str:
        def rule(op: str, folder: Path) -> str:
            return f'({op} (subpath {json.dumps(str(folder.resolve()))}))'

        writable = [self.temp / "work"]
        if self.policy.access is not Access.READ_ONLY:
            writable += [self.cwd, paths.caches()]
        writable += list(self.policy.write_dirs)
        lines = ["(version 1)", "(allow default)", "(deny file-write*)", "(deny network*)",
                 '(allow file-write* (literal "/dev/null"))',
                 '(deny mach-lookup (global-name-regex ".*security.*"))']
        lines += [rule("allow file-write*", p) for p in writable]
        if self.policy.network is True:
            lines.append("(allow network*)")
        private = [*self.policy.private, profile(), self.temp / "tools.json"]
        lines += [rule("deny file-read* file-write*", p) for p in private]
        # A command must not rewrite the rules governing its next command.
        lines.append(rule("deny file-write*", self.temp))
        lines.append(rule("allow file-write*", self.temp / "work"))
        return "\n".join(lines)

    async def command(self, command: str, timeout: float = 120) -> str:
        if reason := self.policy.check_command(command, sandboxed=True):
            raise ToolError(reason)
        work = self.temp / "work"
        work.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "USER")}
        env |= without_credentials() | package_caches() | temp_env(work)
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec", "-p", self.sandbox(), "/bin/zsh", "-f", "-c", command,
            cwd=self.cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)

        async def collect() -> bytes:
            output = b""
            while chunk := await process.stdout.read(65536):
                output = (output + chunk)[-OUTPUT_LIMIT:]
            await process.wait()
            return output

        try:
            output = await asyncio.wait_for(collect(), min(max(timeout, 1), 300))
            return f"Exit code: {process.returncode}\n{output.decode(errors='replace')[-OUTPUT_LIMIT:]}"
        except TimeoutError:
            raise ToolError("Command timed out") from None
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


def build(config: dict) -> MCPServer:
    workspace = Workspace(config)
    server = MCPServer("crewbots_workspace")

    @server.tool()
    def read_file(path: str, start_line: int = 1, lines: int = 200) -> str:
        """Read a text file, with line numbers. Paths may be relative to the task folder."""
        target = workspace.read(path)
        if target.stat().st_size > 10_000_000:
            raise ToolError("File is too large; use a focused search")
        text = target.read_text().splitlines()
        start = max(start_line - 1, 0)
        return "\n".join(f"{n + 1}: {line}" for n, line in enumerate(text[start:start + min(lines, 1000)], start))

    @server.tool()
    def view_image(path: str) -> list[ImageContent]:
        """View a PNG, JPEG, GIF or WebP image in the task's readable files."""
        target = workspace.read(path)
        mime = mimetypes.guess_type(target.name)[0]
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp") or target.stat().st_size > 10_000_000:
            raise ToolError("Use a PNG, JPEG, GIF or WebP image under 10 MB")
        return [ImageContent(type="image", mimeType=mime, data=base64.b64encode(target.read_bytes()).decode())]

    @server.tool()
    def write_file(path: str, content: str) -> str:
        """Write a file in the task's allowed folders."""
        target = workspace.write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {path}"

    @server.tool()
    def edit_file(path: str, old: str, new: str) -> str:
        """Replace exactly one matching piece of text in a file; ambiguous edits fail."""
        target = workspace.write(path)
        text = target.read_text()
        if not old or text.count(old) != 1:
            raise ToolError("The old text must match exactly once")
        target.write_text(text.replace(old, new, 1))
        return f"Edited {path}"

    @server.tool()
    async def run_command(command: str, timeout: float = 120) -> str:
        """Run a foreground shell command in the task folder, inside its filesystem and network sandbox.

        Use rg for searching, git for inspecting changes, and project commands for tests and builds.
        """
        return await workspace.command(command, timeout)

    return server


if __name__ == "__main__":
    build(json.loads(Path(sys.argv[1]).read_text())).run()
