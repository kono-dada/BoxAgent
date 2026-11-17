import AVFoundation
import Foundation
import Speech

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func normalizedSpeech(_ value: String) -> String {
    value.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
        .replacingOccurrences(of: "[^\\p{L}\\p{N}]+", with: " ", options: .regularExpression)
        .trimmingCharacters(in: .whitespaces)
}

struct WakePhrase {
    let name: String
    let normalized: String
    let usesWordBoundaries: Bool

    init(_ name: String) {
        self.name = name
        normalized = normalizedSpeech(name)
        usesWordBoundaries = normalized.range(of: "^[a-z0-9 ]+$", options: .regularExpression) != nil
    }

    func matches(_ transcript: String) -> Bool {
        guard !normalized.isEmpty else { return false }
        let text = normalizedSpeech(transcript)
        if usesWordBoundaries {
            return " \(text) ".contains(" \(normalized) ")
        }
        return text.replacingOccurrences(of: " ", with: "")
            .contains(normalized.replacingOccurrences(of: " ", with: ""))
    }
}

final class WakeListener {
    private let recognizer: SFSpeechRecognizer
    private let phrase: WakePhrase
    private let engine = AVAudioEngine()
    private var task: SFSpeechRecognitionTask?
    private var restart: Timer?
    private var generation = 0

    init(recognizer: SFSpeechRecognizer, phrase: WakePhrase) {
        self.recognizer = recognizer
        self.phrase = phrase
    }

    func listen() {
        restart?.invalidate()
        generation += 1
        let current = generation
        if task != nil {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
            task?.cancel()
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.contextualStrings = [phrase.name]
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 && format.channelCount > 0 else { fail("找不到可用的麦克风") }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                if let result {
                    let text = result.bestTranscription.formattedString
                    if self.phrase.matches(text) {
                        print("wake")
                        fflush(stdout)
                        exit(0)
                    }
                    if result.isFinal { self.scheduleRestart(after: 0.1) }
                } else if error != nil {
                    self.scheduleRestart(after: 2)
                }
            }
        }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            fail("无法启动麦克风")
        }
        scheduleRestart(after: 50)
    }

    private func scheduleRestart(after delay: TimeInterval) {
        restart?.invalidate()
        restart = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            self?.listen()
        }
    }
}

let args = Array(CommandLine.arguments.dropFirst())
let phrase = WakePhrase(args.first ?? "")
guard !phrase.normalized.isEmpty else { fail("角色名称不能为空") }
let locales = phrase.usesWordBoundaries ? ["en-US", "zh-CN"] : ["zh-CN"]
guard let recognizer = locales.lazy
    .compactMap({ SFSpeechRecognizer(locale: Locale(identifier: $0)) })
    .first(where: { $0.supportsOnDeviceRecognition }) else {
    fail("本机离线语音识别不可用，请检查 macOS 听写语言设置")
}
let listener = WakeListener(recognizer: recognizer, phrase: phrase)
SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized else { fail("请在系统设置中允许 BoxAgent 使用语音识别") }
    AVCaptureDevice.requestAccess(for: .audio) { granted in
        guard granted else { fail("请在系统设置中允许 BoxAgent 使用麦克风") }
        DispatchQueue.main.async { listener.listen() }
    }
}
RunLoop.main.run()
