// crewbots-dictate: listens on the microphone and writes what it hears, as it firms up, using macOS's own speech
// recognizer, on this Mac when it can. The app runs it while you dictate and stops it with "stop" on stdin.
//
// Out, one JSON object a line: {"text": "everything heard so far", "final": false}, ending with "final": true, or
// {"error": "why"} if it can't listen.
//
// The recognizer works one utterance at a time: after a pause it starts over and reports only the new words, and a
// request ends after about a minute. So what's been heard is kept here, and each new utterance is added after it.

import AVFoundation
import Foundation
import Speech

setvbuf(stdout, nil, _IOLBF, 0)

func say(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object), let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

func fail(_ message: String) -> Never {
    say(["error": message])
    exit(1)
}

/// Everything heard: the utterances already finished, and the one going on now.
struct Heard {
    var finished = ""
    var current = ""

    var text: String { joined(finished, current) }

    /// The recognizer's latest guess at the current utterance. A guess that doesn't carry on from the last one means
    /// it started a new utterance after a pause, so the last one is kept as finished.
    mutating func update(_ guess: String) {
        if startsOver(guess) {
            finished = joined(finished, current)
        }
        current = guess
    }

    /// The current utterance is over (the request ended): keep it.
    mutating func settle() {
        finished = joined(finished, current)
        current = ""
    }

    private func startsOver(_ guess: String) -> Bool {
        guard !current.isEmpty, !guess.isEmpty else { return false }
        if guess.count * 2 < current.count { return true }  // much shorter: not a revision of the same words
        let first = { (s: String) in s.split(separator: " ").first.map { $0.lowercased().filter(\.isLetter) } ?? "" }
        return guess.count < current.count && first(guess) != first(current)
    }
}

func joined(_ a: String, _ b: String) -> String {
    if a.isEmpty { return b }
    if b.isEmpty { return a }
    return a + " " + b
}

#if TESTING
// swiftc -DTESTING dictation/main.swift -o /tmp/t && /tmp/t
var h = Heard()
for guess in ["First", "First sentence", "First sentence add a CSV export.", "Second", "Second sentence and filters."] {
    h.update(guess)
}
assert(h.text == "First sentence add a CSV export. Second sentence and filters.", h.text)
h.settle()
h.update("Third")
assert(h.text == "First sentence add a CSV export. Second sentence and filters. Third", h.text)
var revised = Heard()
for guess in ["Fur", "First thing", "First thing to do"] { revised.update(guess) }
assert(revised.text == "First thing to do", revised.text)
print("ok")
exit(0)
#endif

let engine = AVAudioEngine()
var recognizer: SFSpeechRecognizer!
var request: SFSpeechAudioBufferRecognitionRequest?
var task: SFSpeechRecognitionTask?
var heard = Heard()
var stopping = false
var finished = false

func finish() {
    if finished { return }
    finished = true
    heard.settle()
    say(["text": heard.text, "final": true])
    exit(0)
}

/// One recognition request, until it ends; while still listening, the next one carries on after it.
func listen() {
    let next = SFSpeechAudioBufferRecognitionRequest()
    next.shouldReportPartialResults = true
    next.addsPunctuation = true
    if recognizer.supportsOnDeviceRecognition {
        next.requiresOnDeviceRecognition = true  // it stays on this Mac
    }
    request = next
    task = recognizer.recognitionTask(with: next) { result, error in
        if let result {
            heard.update(result.bestTranscription.formattedString)
            say(["text": heard.text, "final": false])
        }
        if error != nil || result?.isFinal == true {
            heard.settle()
            if stopping { finish() } else { listen() }  // a pause too long, or the one-minute limit: keep going
        }
    }
}

func start() {
    guard let found = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          found.isAvailable else {
        fail("Speech recognition isn't available. Check that Siri is on in System Settings.")
    }
    recognizer = found
    let input = engine.inputNode
    input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
        request?.append(buffer)
    }
    engine.prepare()
    do {
        try engine.start()
    } catch {
        fail("Couldn't start the microphone: \(error.localizedDescription)")
    }
    listen()
}

// "stop" (or the app going away) ends listening; the recognizer then gives its final text.
Thread {
    while let line = readLine() {
        if line.trimmingCharacters(in: .whitespaces) == "stop" { break }
    }
    DispatchQueue.main.async {
        stopping = true
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { finish() }
    }
}.start()

SFSpeechRecognizer.requestAuthorization { status in
    DispatchQueue.main.async {
        switch status {
        case .authorized:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async {
                    if granted { start() } else { fail("crewbots isn't allowed to use the microphone. Allow it in System Settings → Privacy & Security → Microphone.") }
                }
            }
        case .denied, .restricted:
            fail("crewbots isn't allowed to use speech recognition. Allow it in System Settings → Privacy & Security → Speech Recognition.")
        default:
            fail("Speech recognition hasn't been allowed yet.")
        }
    }
}

RunLoop.main.run()
