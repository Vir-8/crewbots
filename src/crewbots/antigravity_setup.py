"""Install and sign in to Google's official ACP runtime; never handle its account tokens."""

import asyncio
import hashlib
import io
import json
import platform
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from crewbots import paths
from crewbots.backends.antigravity_runtime import VERSION, connect, models, runtime

# The registry commit that lists VERSION: its main branch lists only the newest release.
REGISTRY = ("https://raw.githubusercontent.com/agentclientprotocol/registry/"
            "02b460362cfedf4d43b03df7aad6d79a353fe064/antigravity-acp/agent.json")


def install() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("The crewbots Antigravity workspace sandbox currently requires macOS")
    with urllib.request.urlopen(REGISTRY, timeout=30) as response:
        entry = json.load(response)
    if entry.get("id") != "antigravity-acp" or entry.get("authors") != ["Google LLC"]:
        raise RuntimeError("Unexpected publisher in the ACP registry")
    target = "darwin-aarch64" if platform.machine() == "arm64" else "darwin-x86_64"
    distribution = entry["distribution"]["binary"][target]
    url = distribution["archive"]
    if not url.startswith("https://dl.google.com/agy-extensions/releases/"):
        raise RuntimeError("The ACP archive must come from Google's distribution host")
    destination = runtime().parent
    if destination.exists():
        raise RuntimeError(f"Runtime folder already exists: {destination}; leaving it untouched")
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != distribution.get("sha256"):
        raise RuntimeError("Antigravity ACP archive checksum did not match the official registry")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in ("agy_acp_server.par", "localharness_external"):
                path = stage / name
                path.write_bytes(archive.read(name))
                path.chmod(0o755)
        stage.rename(destination)
    print(f"Installed Google Antigravity ACP {VERSION} at {destination}")


async def login() -> None:
    paths.antigravity().mkdir(parents=True, exist_ok=True)
    connection, _ = await connect(paths.antigravity(), login=True)
    try:
        print("Complete sign-in in the browser opened by Google's official ACP runtime.", flush=True)
        await connection.request("authenticate", {"methodId": "oauth-personal"}, timeout=300)
        print("Antigravity subscription sign-in complete. Credentials remain managed by Google's runtime.")
    finally:
        await connection.close()


def main(args) -> int:
    if args.action == "install":
        install()
    elif args.action == "login":
        asyncio.run(login())
    else:
        print(json.dumps(asyncio.run(models()), indent=2))
    return 0
