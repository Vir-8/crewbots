// Opens a page in WebKit (the engine the crewbots app runs in), optionally clicks an element with real mouse events,
// runs a script in the page, and prints what it returns as JSON. For what only WebKit does: hover kept on a moved
// element until the pointer moves, scroll clamped by a layout in the middle of an update. Compiled and run by
// `scripts/verify webkit`.
//
//   verify-webkit URL [--size 1400x860] [--wait SECONDS] [--setup FILE] [--click SELECTOR] [--js FILE]
//
// --setup and --js are the bodies of async functions run in the page (top-level await works); --setup runs before the
// click, --js after it, and what --js returns is printed. The window shows on screen while it runs, since WebKit doesn't
// draw frames for a hidden one.
import AppKit
import WebKit

var options: [String: String] = [:]
var given: URL?
var args = CommandLine.arguments.dropFirst()
while let arg = args.popFirst() {
    if arg.hasPrefix("--") { options[arg] = args.popFirst() ?? "" } else { given = URL(string: arg) }
}
guard let given else {
    FileHandle.standardError.write("usage: verify-webkit URL [--size WxH] [--wait S] [--setup FILE] [--click SELECTOR] [--js FILE]\n".data(using: .utf8)!)
    exit(2)
}
let url: URL = given
let size = (options["--size"] ?? "1400x860").split(separator: "x").compactMap { Double($0) }
let (width, height) = (size.count == 2 ? size[0] : 1400, size.count == 2 ? size[1] : 860)
let wait = Double(options["--wait"] ?? "2") ?? 2

func script(_ option: String) -> String? {
    guard let path = options[option] else { return nil }
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { fail("can't read \(path)") }
    return text
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("verify-webkit: \(message)\n".data(using: .utf8)!)
    exit(1)
}

final class Harness: NSObject, WKNavigationDelegate {
    let window = NSWindow(contentRect: NSRect(x: 80, y: 80, width: width, height: height), styleMask: [.titled],
                          backing: .buffered, defer: false)
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height))

    func start() {
        window.contentView = web
        web.navigationDelegate = self
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { self.setup() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { fail("\(error)") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("couldn't load \(url): \(error.localizedDescription)")
    }

    func run(_ body: String, _ done: @escaping (Any?) -> Void) {
        web.callAsyncJavaScript(body, arguments: [:], in: nil, in: .page) { result in
            switch result {
            case .success(let value): done(value)
            case .failure(let error): fail("script failed: \(error)")
            }
        }
    }

    func setup() {
        guard let body = script("--setup") else { return click() }
        run(body) { _ in self.click() }
    }

    func click() {
        guard let selector = options["--click"] else { return after() }
        let find = "const r = document.querySelector(\(String(reflecting: selector)))?.getBoundingClientRect(); " +
                   "return r ? [r.x + r.width / 2, r.y + r.height / 2] : null;"
        run(find) { value in
            guard let point = value as? [Double], point.count == 2 else { fail("nothing matches --click \(selector)") }
            let at = NSPoint(x: point[0], y: height - point[1])  // a window counts from the bottom
            self.mouse(.mouseMoved, at)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                self.mouse(.leftMouseDown, at)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) {
                    self.mouse(.leftMouseUp, at)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.after() }
                }
            }
        }
    }

    func mouse(_ type: NSEvent.EventType, _ at: NSPoint) {
        let event = NSEvent.mouseEvent(with: type, location: at, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1,
                                       pressure: type == .leftMouseDown ? 1 : 0)!
        window.sendEvent(event)
    }

    func after() {
        let body = script("--js") ?? "return document.title;"
        run(body) { value in
            let out = value.map { v -> Any in v is NSNull ? NSNull() : v } ?? NSNull()
            if JSONSerialization.isValidJSONObject(out),
               let data = try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys]) {
                print(String(data: data, encoding: .utf8)!)
            } else {
                print(out)
            }
            exit(0)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let harness = Harness()
harness.start()
DispatchQueue.main.asyncAfter(deadline: .now() + 90) { fail("timed out after 90s") }
app.run()
