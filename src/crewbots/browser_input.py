"""Validate the limited viewer input protocol before passing it to Chrome."""

from crewbots.browser_cdp import BrowserError


def input_event(event, viewport=None):
    if not isinstance(event, dict):
        raise BrowserError("Invalid browser input")
    kind = event.get("type")
    if not isinstance(kind, str):
        raise BrowserError("Invalid browser input")
    if kind == "text" and set(event) == {"type", "text"} and isinstance(event["text"], str) and len(event["text"]) <= 4096:
        return "Input.insertText", {"text": event["text"]}
    if kind in {"keyDown", "rawKeyDown", "keyUp"}:
        if (set(event) <= {"type", "key", "code", "windowsVirtualKeyCode", "modifiers"}
                and all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 64 for k in ("key", "code"))
                and all(type(event.get(k, 0)) is int and 0 <= event.get(k, 0) <= limit
                        for k, limit in (("windowsVirtualKeyCode", 255), ("modifiers", 15)))):
            return "Input.dispatchKeyEvent", dict(event)
    if kind in {"mouseMoved", "mousePressed", "mouseReleased", "mouseWheel"}:
        fields = {"type", "x", "y", "button", "buttons", "clickCount", "modifiers", "deltaX", "deltaY"}
        width, height = viewport or (3841, 3841)
        if (set(event) <= fields
                and all(type(event.get(k)) in (int, float) and 0 <= event[k] < limit
                        for k, limit in (("x", width), ("y", height)))
                and isinstance(event.get("button", "none"), str)
                and event.get("button", "none") in {"none", "left", "right", "middle", "back", "forward"}
                and (kind not in {"mousePressed", "mouseReleased"} or event.get("button") not in {None, "none"})
                and all(type(event.get(k, 0)) is int and 0 <= event.get(k, 0) <= limit
                        for k, limit in (("buttons", 31), ("clickCount", 3), ("modifiers", 15)))
                and all(type(event.get(k, 0)) in (int, float) and abs(event.get(k, 0)) <= 10000
                        for k in ("deltaX", "deltaY"))):
            params = dict(event)
            if kind == "mouseWheel":
                params.setdefault("deltaX", 0)
                params.setdefault("deltaY", 0)
            return "Input.dispatchMouseEvent", params
    raise BrowserError("Invalid browser input")

