// crewbots-dictate: listens on the microphone and writes what it hears, as it firms up, using macOS's own speech
// recognizer, on this Mac when it can. The app runs it while you dictate and stops it with "stop" on stdin.
//
// Out, one JSON object a line: {"text": "everything heard so far", "final": false}, ending with "final": true, or
// {"error": "why"} if it can't listen.

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

let engine = AVAudioEngine()
let request = SFSpeechAudioBufferRecognitionRequest()
request.shouldReportPartialResults = true
request.addsPunctuation = true
var task: SFSpeechRecognitionTask?
var heard = ""
var finished = false

func finish() {
    if finished { return }
    finished = true
    say(["text": heard, "final": true])
    exit(0)
}

func start() {
    guard let recognizer = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          recognizer.isAvailable else {
        fail("Speech recognition isn't available. Check that Siri is on in System Settings.")
    }
    if recognizer.supportsOnDeviceRecognition {
        request.requiresOnDeviceRecognition = true  // it stays on this Mac
    }
    let input = engine.inputNode
    input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
        request.append(buffer)
    }
    engine.prepare()
    do {
        try engine.start()
    } catch {
        fail("Couldn't start the microphone: \(error.localizedDescription)")
    }
    task = recognizer.recognitionTask(with: request) { result, error in
        if let result {
            heard = result.bestTranscription.formattedString
            say(["text": heard, "final": result.isFinal])
            if result.isFinal { finish() }
        }
        if error != nil { finish() }  // the end of speech, a pause too long, or the one-minute limit: keep what was heard
    }
}

// "stop" (or the app going away) ends listening; the recognizer then gives its final text.
Thread {
    while let line = readLine() {
        if line.trimmingCharacters(in: .whitespaces) == "stop" { break }
    }
    DispatchQueue.main.async {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request.endAudio()
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
