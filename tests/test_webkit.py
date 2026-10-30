"""webkit_check: the daemon runs WebKit for a bot. A fake helper and a fake swiftc stand in; no test starts WebKit."""

import asyncio
import os

import httpx
import pytest
from fakes import FakeBackend, bot, make_crew

from crewbots import webkit
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused, tools_for
from crewbots.mcp_server import build_server
from crewbots.runner import Runner
from crewbots.store import Store
from crewbots.webkit import WebKit, WebKitError

TITLE = "#!/bin/sh\necho 'crewbots'\n"


def fake_helper(service: WebKit, script: str) -> None:
    service.root.mkdir(parents=True, exist_ok=True)
    service.helper().write_text(script)
    service.helper().chmod(0o755)


@pytest.fixture
def service(tmp_path):
    return WebKit(tmp_path / "webkit", swiftc=str(tmp_path / "no-swiftc"))


@pytest.mark.parametrize("url", ["https://example.com/", "http://localhost@example.com/", "http://me:pw@localhost/",
                                 "http://localhost.example.com/", "http://127.0.0.2/", "http://[::1]:5173/",
                                 "file:///etc/passwd", "javascript:alert(1)", "localhost:5173", ""])
async def test_only_local_pages_are_opened(service, tmp_path, url):
    fake_helper(service, f"#!/bin/sh\ntouch {tmp_path}/ran\n")

    with pytest.raises(WebKitError, match="Only local pages"):
        await service.check(url)
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("url", ["http://127.0.0.1:5173/", "http://localhost:5173/chat?x=1", "https://LOCALHOST/"])
async def test_localhost_and_127_0_0_1_are(service, url):
    fake_helper(service, TITLE)

    assert await service.check(url) == "crewbots"


@pytest.mark.parametrize("options", [{"wait": 11}, {"wait": -1}, {"wait": True}, {"width": 100}, {"height": "860"},
                                     {"click": 5}, {"js": ["return 1"]}])
async def test_options_out_of_range_are_refused(service, tmp_path, options):
    fake_helper(service, f"#!/bin/sh\ntouch {tmp_path}/ran\n")

    with pytest.raises(WebKitError):
        await service.check("http://127.0.0.1:5173/", **options)
    assert not (tmp_path / "ran").exists()


async def test_options_reach_the_helper_and_everything_it_writes_stays_in_the_temp_folder(service, tmp_path,
                                                                                          monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("FAKE_OUT", str(out))
    fake_helper(service, '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_OUT"\necho "tmp=$TMPDIR" >> "$FAKE_OUT"\n'
                         'cat "$TMPDIR/js.js" >> "$FAKE_OUT"\ntouch "$TMPDIR/webkit-cache"\necho \'{"rows": 3}\'\n')

    result = await service.check("http://127.0.0.1:5173/", click="[title='Send']", setup="await 1", js="return 3",
                                 wait=0.5, width=800, height=600)

    assert result == '{"rows": 3}'
    args, rest = out.read_text().split("tmp=")
    assert args.splitlines()[:9] == ["http://127.0.0.1:5173/", "--local-only", "--size", "800x600", "--wait", "0.5",
                                     "--timeout", "30", "--setup"]
    assert "--click\n[title='Send']" in args and "--js\n" in args
    run = rest.splitlines()[0]
    assert run.startswith(str(service.root)) and rest.splitlines()[1] == "return 3"
    # The run's folder, with its scripts and whatever WebKit left there, is gone; only the helper stays
    assert list(service.root.iterdir()) == [service.helper()]


async def test_one_check_runs_at_a_time(service, tmp_path):
    fake_helper(service, "#!/bin/sh\nsleep 0.5\necho first\n")

    first = asyncio.create_task(service.check("http://127.0.0.1:5173/"))
    while not service.lock.locked():
        await asyncio.sleep(0.01)
    with pytest.raises(WebKitError, match="one runs at a time"):
        await service.check("http://127.0.0.1:5173/")
    assert await first == "first"
    fake_helper(service, TITLE)
    assert await service.check("http://127.0.0.1:5173/") == "crewbots"  # free again once it's done


async def test_a_check_that_runs_too_long_is_stopped(service, tmp_path, monkeypatch):
    monkeypatch.setattr(webkit, "TIMEOUT_S", 2)  # room for macOS to look over a new executable before its first run
    monkeypatch.setattr(webkit, "GRACE_S", 0)
    monkeypatch.setenv("FAKE_PID", str(tmp_path / "pid"))
    fake_helper(service, '#!/bin/sh\necho $$ > "$FAKE_PID"\nexec sleep 30\n')

    with pytest.raises(WebKitError, match="didn't finish in 2 seconds"):
        await service.check("http://127.0.0.1:5173/", wait=0)
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)
    assert not service.lock.locked()


async def test_what_the_page_got_wrong_comes_back(service):
    fake_helper(service, "#!/bin/sh\necho 'verify-webkit: nothing matches --click #nope' >&2\nexit 1\n")

    with pytest.raises(WebKitError, match="nothing matches --click #nope"):
        await service.check("http://127.0.0.1:5173/", click="#nope")


async def test_the_helper_is_built_once_into_the_temp_folder(tmp_path):
    calls = tmp_path / "calls"
    swiftc = tmp_path / "swiftc"
    # A fake swiftc: records its arguments, and writes a helper that prints a title where -o says
    swiftc.write_text(f'#!/bin/sh\necho "$@" >> {calls}\nwhile [ "$1" != "-o" ]; do shift; done\n'
                      f'printf "#!/bin/sh\\necho built\\n" > "$2"\nchmod +x "$2"\n')
    swiftc.chmod(0o755)
    service = WebKit(tmp_path / "webkit", swiftc=str(swiftc))
    service.root.mkdir()
    (service.root / "verify-webkit-0123456789abcdef").write_text("an older build")

    assert await service.check("http://127.0.0.1:5173/") == "built"
    assert await service.check("http://127.0.0.1:5173/") == "built"

    built = calls.read_text().splitlines()
    assert len(built) == 1 and f"-module-cache-path {service.root}/module-cache" in built[0]
    assert sorted(p.name for p in service.root.iterdir()) == [service.helper().name]  # the older one is gone


async def test_a_failed_build_says_why_and_leaves_no_helper(tmp_path):
    swiftc = tmp_path / "swiftc"
    swiftc.write_text("#!/bin/sh\necho 'error: cannot find WKWebView in scope' >&2\nexit 1\n")
    swiftc.chmod(0o755)
    service = WebKit(tmp_path / "webkit", swiftc=str(swiftc))

    with pytest.raises(WebKitError, match="couldn't build the WebKit helper: error: cannot find WKWebView"):
        await service.check("http://127.0.0.1:5173/")
    assert not service.helper().exists()
    with pytest.raises(WebKitError, match="swiftc isn't installed"):
        await WebKit(tmp_path / "webkit", swiftc=str(tmp_path / "missing")).check("http://127.0.0.1:5173/")


async def test_the_tool_through_crew_tools_the_bot_api_and_mcp(service, tmp_path):
    fake_helper(service, TITLE)
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("engineer", workspace=tmp_path)), {"fake": FakeBackend()})
    runner.webkit = service
    task = store.create_task("engineer", "webkit")
    runner._tokens["run-token"] = task.id
    tools = CrewTools(runner, task.id, False)

    assert await tools.call("webkit_check", {"url": "http://127.0.0.1:5173/"}) == "crewbots"
    with pytest.raises(ToolRefused, match="Only local pages"):
        await tools.call("webkit_check", {"url": "https://example.com/"})
    assert [(e["kind"], e["text"]) for e in store.agent_log()][:3] == [
        ("refused", "webkit_check: Only local pages: an http(s) URL on localhost or 127.0.0.1"),
        ("webkit", "https://example.com/"), ("webkit", "http://127.0.0.1:5173/")]

    app = create_app(runner, "admin", 8765)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
        bot_token = {"Authorization": "Bearer run-token"}
        assert (await client.post("/bot/webkit", json={"url": "http://localhost:5173/"})).status_code == 403
        ok = await client.post("/bot/webkit", json={"url": "http://localhost:5173/"}, headers=bot_token)
        assert ok.status_code == 200 and ok.json() == {"text": "crewbots"}
        refused = await client.post("/bot/webkit", json={"url": "https://example.com/"}, headers=bot_token)
        assert refused.status_code == 422 and "Only local pages" in refused.json()["detail"]

    sent = []

    def daemon(request):
        sent.append((request.url.path, request.read(), request.extensions["timeout"]))
        return httpx.Response(200, json={"text": "crewbots"})

    with httpx.Client(transport=httpx.MockTransport(daemon), base_url="http://daemon/v1", timeout=60) as client:
        result = await build_server(client, False).call_tool("webkit_check", {"url": "http://127.0.0.1:5173/",
                                                                              "js": "return 1"})
    assert result.content[0].text == "crewbots"
    path, body, timeout = sent[0]
    assert path == "/v1/bot/webkit" and body == b'{"url":"http://127.0.0.1:5173/","js":"return 1"}'
    assert timeout["read"] > webkit.BUILD_TIMEOUT_S  # the first check builds the helper, past the client's 60s
    assert all("webkit_check" in {t.name for t in tools_for(False, kind=kind)} for kind in ("chat", "seat", "thread"))
    await runner.shutdown()
    store.close()
