"""The repo's CI steps, run locally on the exact commit before a Push or Replace card is made.

Commands come from `.github/workflows/*.yml`. Supported, and nothing else:
- jobs triggered on `push` (`on: push`, a list, or a map; `branches:` and `branches-ignore:` are matched as globs
  against the branch), run in file order and then job order. `needs:` is not used for ordering.
- steps with `run:`, in a `bash -e` shell, with `working-directory` (step, or the job's `defaults.run`) and literal
  `env` (workflow, job, step). `CI=true` is set, and HOME is a fresh empty folder (uv and npm keep the real caches).
- `actions/setup-python` with a `python-version` sets UV_PYTHON, so uv uses that Python. `actions/setup-node`'s
  `node-version` (a major such as 22, or 22.x) must be installed: the node on PATH if its major matches, else one
  of the usual install places (Homebrew node@N, nvm, fnm, volta), put first on PATH for that job's steps. If there
  is none the check fails. The version used is recorded.
- only setup steps with no effect on the result are skipped: actions/checkout, setup-python, setup-node,
  astral-sh/setup-uv, actions/cache, actions/upload-artifact. Any other `uses:` step, and any step or job with an
  `if:` other than `always()` or `success()`, makes the check fail ("can't reproduce CI step"): a run never passes
  having skipped a real check. A matrix, `${{ }}` expressions or a `shell` other than bash fail it too.
With no workflow job for the branch, a project's own `ci:` list of commands is run. With neither, the check fails:
a push needs CI to have run.

The commit is checked out into a shared clone under the daemon's temp folder, so the user's folder is never touched.
"""

import fnmatch
import glob
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crewbots import paths
from crewbots.runner.common import NotAllowed

log = logging.getLogger(__name__)

STEP_TIMEOUT = 20 * 60
SETUP_ACTIONS = ("actions/checkout", "actions/setup-python", "actions/setup-node", "astral-sh/setup-uv",
                 "actions/cache", "actions/upload-artifact")
TAIL_LINES = 40


class CIFailed(NotAllowed):
    pass


@dataclass
class Step:
    name: str
    run: str
    directory: str = ""
    env: dict[str, str] = field(default_factory=dict)
    node: str = ""  # the Node major this step runs under, from the job's setup-node


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    node: str = ""  # the node-version the workflow asks for


def _on_push(trigger: Any, branch: str) -> bool:
    if trigger == "push":
        return True
    if isinstance(trigger, list):
        return "push" in trigger
    if not isinstance(trigger, dict) or "push" not in trigger:
        return False
    push = trigger["push"] or {}
    if "branches" in push:
        return any(fnmatch.fnmatchcase(branch, str(p)) for p in push["branches"] or [])
    if "branches-ignore" in push:
        return not any(fnmatch.fnmatchcase(branch, str(p)) for p in push["branches-ignore"] or [])
    return True


def _always(condition: Any) -> bool:
    return str(condition).strip().removeprefix("${{").removesuffix("}}").strip() in ("always()", "success()")


def _major(version: Any, where: str) -> str:
    match = re.fullmatch(r"v?(\d+)(\.[x\d]+)*", str(version).strip())
    if not match:
        raise CIFailed(f"can't reproduce CI step {where} locally (node-version {version})")
    return match.group(1)


def _env(*maps: Any) -> dict[str, str]:
    env: dict[str, str] = {}
    for m in maps:
        for key, value in (m or {}).items():
            if "${{" in str(value):
                raise CIFailed(f"env {key} uses an expression, which isn't supported")
            env[str(key)] = str(value)
    return env


def _plan_workflow(path: Path, branch: str, plan: Plan) -> None:
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError) as e:
        raise CIFailed(f"{path.name} can't be read: {e}") from None
    if not isinstance(doc, dict):
        return
    trigger = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` as True
    if not _on_push(trigger, branch):
        return
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict) or not job.get("steps"):
            raise CIFailed(f"can't reproduce CI job {job_id} locally (reusable workflow or no steps)")
        if "if" in job and not _always(job["if"]):
            raise CIFailed(f"can't reproduce CI job {job_id} locally (if: {job['if']})")
        if "matrix" in (job.get("strategy") or {}):
            raise CIFailed(f"job {job_id} uses a matrix, which isn't supported")
        default = ((job.get("defaults") or {}).get("run") or {}).get("working-directory", "")
        env = _env(doc.get("env"), job.get("env"))
        python = node = ""
        for step in job["steps"]:
            label = step.get("name") or step.get("run", step.get("uses", "?")).strip().splitlines()[0]
            if "if" in step and not _always(step["if"]):
                raise CIFailed(f"can't reproduce CI step {job_id}: {label} locally (if: {step['if']})")
            if "run" in step:
                if "${{" in step["run"] or "${{" in str(step.get("working-directory", "")):
                    raise CIFailed(f"{job_id}: {label} uses an expression, which isn't supported")
                if step.get("shell", "bash") != "bash":
                    raise CIFailed(f"{job_id}: {label} uses shell {step['shell']}, which isn't supported")
                plan.steps.append(Step(f"{job_id}: {label}", step["run"], str(step.get("working-directory", default)),
                                       {**env, **({"UV_PYTHON": python} if python else {}), **_env(step.get("env"))}, node))
            else:
                uses, args = str(step.get("uses", "")), step.get("with") or {}
                action = uses.split("@", 1)[0]
                if action == "actions/setup-python" and args.get("python-version"):
                    python = str(args["python-version"])
                elif action == "actions/setup-node" and args.get("node-version"):
                    node = _major(args["node-version"], f"{job_id}: {uses}")
                    plan.node = node
                if action not in SETUP_ACTIONS:
                    raise CIFailed(f"can't reproduce CI step {job_id}: {label} locally (uses {uses})")
                plan.skipped.append(f"{job_id}: {uses}")


def plan_for(checkout: Path, branch: str, project_commands: tuple[str, ...] = ()) -> Plan:
    plan = Plan()
    workflows = sorted((checkout / ".github" / "workflows").glob("*.y*ml"))
    for path in workflows:
        _plan_workflow(path, branch, plan)
    if not plan.steps:
        plan.skipped = []
        plan.steps = [Step(c.strip().splitlines()[0], c) for c in project_commands]
    return plan


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def _node_at(directory: str | None) -> str:
    """The version of the node in `directory` (the one on PATH for None), or "" if there's none."""
    exe = str(Path(directory) / "node") if directory else "node"
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_node(major: str) -> tuple[str | None, str]:
    """(folder to put first on PATH, or None for the node already there; the version) for a node of this major."""
    version = _node_at(None)
    if version.startswith(f"v{major}."):
        return None, version
    home = Path.home()
    for pattern in (f"/opt/homebrew/opt/node@{major}/bin", f"/usr/local/opt/node@{major}/bin",
                    f"{home}/.nvm/versions/node/v{major}.*/bin", f"{home}/.fnm/node-versions/v{major}.*/installation/bin",
                    f"{home}/.volta/tools/image/node/{major}.*/bin"):
        for found in sorted(glob.glob(pattern), reverse=True):
            version = _node_at(found)
            if version.startswith(f"v{major}."):
                return found, version
    raise CIFailed(f"Node {major} is required by CI but isn't installed")


def _caches() -> dict[str, str]:
    """The real uv and npm caches and uv's Pythons, so a fresh HOME doesn't make every run download everything."""
    home = Path.home()
    env = {"UV_PYTHON_INSTALL_DIR": os.environ.get("UV_PYTHON_INSTALL_DIR", str(home / ".local" / "share" / "uv" / "python")),
           "npm_config_cache": os.environ.get("npm_config_cache", str(home / ".npm"))}
    if "UV_CACHE_DIR" not in os.environ:
        try:
            env["UV_CACHE_DIR"] = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True,
                                                 timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return {k: v for k, v in env.items() if v}


def _run_step(step: Step, checkout: Path, timeout: int, base_env: dict[str, str]) -> tuple[int, str]:
    directory = (checkout / step.directory).resolve()
    if not directory.is_relative_to(checkout.resolve()) or not directory.is_dir():
        return 1, f"working-directory {step.directory!r} isn't a folder in the checkout"
    env = base_env | step.env
    with tempfile.TemporaryFile("w+") as out:
        try:
            process = subprocess.Popen(["bash", "-e", "-c", step.run], cwd=directory, env=env, stdout=out,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            return 1, f"can't run: {e}"
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = 1
            out.write(f"\ntimed out after {timeout} seconds")
        out.seek(0)
        return code, out.read()


def check(folder: Path, sha: str, branch: str, project_commands: tuple[str, ...] = (),
          timeout: int = STEP_TIMEOUT) -> dict[str, Any]:
    """Run the CI steps on `sha` in a clean checkout. Returns what to record on the card, or raises CIFailed with the
    failing step and the end of its output. Blocking: call it off the event loop."""
    paths.temp().mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ci-", dir=paths.temp()))
    try:
        result = _run(folder, sha, branch, project_commands, timeout, work)
    except CIFailed as e:
        leftover = _remove(work)
        raise CIFailed(f"{e}\n{leftover}" if leftover else str(e)) from None
    except BaseException:
        _remove(work)
        raise
    leftover = _remove(work)
    if leftover:
        result["cleanup"] = leftover
        result["note"] += f" ({leftover})"
    return result


def _remove(work: Path) -> str:
    shutil.rmtree(work, ignore_errors=True)
    if work.exists():
        log.warning("ci: temporary checkout left at %s", work)
        return f"temporary checkout left at {work}"
    return ""


def _run(folder: Path, sha: str, branch: str, project_commands: tuple[str, ...], timeout: int,
         work: Path) -> dict[str, Any]:
    checkout = work / "checkout"
    try:
        for args in (["clone", "-q", "--shared", "--no-checkout", str(folder), str(checkout)],
                     ["-C", str(checkout), "checkout", "-q", "--detach", sha]):
            subprocess.run(["git", *args], check=True, capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError) as e:
        raise CIFailed(f"CI could not check out {sha[:12]}: {getattr(e, 'stderr', None) or e}") from None
    plan = plan_for(checkout, branch, project_commands)
    if not plan.steps:
        raise CIFailed(f"no CI configured for {branch}; add a ci: list to the project")
    nodes = {major: find_node(major) for major in dict.fromkeys(s.node for s in plan.steps if s.node)}
    home = work / "home"
    home.mkdir()
    base = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    base |= {**_caches(), "HOME": str(home), "CI": "true"}
    for step in plan.steps:
        env = dict(base)
        if step.node and nodes[step.node][0]:
            env["PATH"] = f"{nodes[step.node][0]}{os.pathsep}{env.get('PATH', '')}"
        code, output = _run_step(step, checkout, timeout, env)
        if code:
            raise CIFailed(f"CI failed on {sha[:12]} at step '{step.name}' (exit {code}):\n{_tail(output)}")
    return {"status": "passed", "sha": sha, "steps": [s.name for s in plan.steps], "skipped": plan.skipped,
            "node": sorted(v for _, v in nodes.values()), "note": f"CI passed locally on {sha[:12]}"
            + (f", Node {', '.join(sorted(v for _, v in nodes.values()))}" if nodes else "")}
