import AppKit
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

final class FrameOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private var lastImage: Data?
    private let context = CIContext(options: [.cacheIntermediates: false])

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("Game capture disconnected: \(error)\n", stderr)
        exit(4)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .screen, sampleBuffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false)
                as? [[SCStreamFrameInfo: Any]],
              let rawStatus = attachments.first?[.status] as? Int,
              let status = SCFrameStatus(rawValue: rawStatus),
              status == .complete || status == .idle else { return }
        autoreleasepool {
            if status == .complete {
                guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
                let image = CIImage(cvPixelBuffer: pixels)
                guard let cgImage = context.createCGImage(image, from: image.extent) else { return }
                let bytes = NSMutableData()
                guard let encoder = CGImageDestinationCreateWithData(bytes, UTType.jpeg.identifier as CFString, 1, nil) else { return }
                CGImageDestinationAddImage(encoder, cgImage, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
                guard CGImageDestinationFinalize(encoder) else { return }
                lastImage = bytes as Data
            }
            guard let bytes = lastImage else { return }
            let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds
            let age = CMClockGetTime(CMClockGetHostTimeClock()).seconds - pts
            guard age.isFinite, age >= -0.1, age < 1.5 else { return }
            let captured = ProcessInfo.processInfo.systemUptime - max(0, age)
            let header = "{\"size\":\(bytes.count),\"captured_at\":\(captured)}\n"
            do {
                try FileHandle.standardOutput.write(contentsOf: Data(header.utf8))
                try FileHandle.standardOutput.write(contentsOf: bytes)
            } catch {
                exit(0)
            }
        }
    }
}

@main enum GameWindowStream {
    @MainActor static func main() async {
        NSApplication.shared.setActivationPolicy(.prohibited)
        guard CommandLine.arguments.count == 2,
              let identifier = UInt32(CommandLine.arguments[1]),
              CGPreflightScreenCaptureAccess() else {
            fputs("Game capture permission or window ID unavailable\n", stderr)
            exit(2)
        }
        do {
            let available = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
            guard let window = available.windows.first(where: { $0.windowID == identifier }),
                  window.owningApplication?.bundleIdentifier == "com.kurogame.mingchao" else {
                fputs("Requested game window unavailable\n", stderr)
                exit(3)
            }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = 1280
            config.height = max(2, Int(640 * window.frame.height / window.frame.width) * 2)
            config.minimumFrameInterval = CMTime(value: 1, timescale: 5)
            config.queueDepth = 3
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.capturesAudio = false
            config.showsCursor = false
            let output = FrameOutput()
            let stream = SCStream(filter: filter, configuration: config, delegate: output)
            try stream.addStreamOutput(output, type: .screen,
                                       sampleHandlerQueue: DispatchQueue(label: "boxagent.game.frames"))
            try await stream.startCapture()
            // Closing the host's input pipe ends capture and releases native resources.
            let input = Task.detached { try? FileHandle.standardInput.readToEnd() }
            _ = await input.value
            try? await stream.stopCapture()
        } catch {
            fputs("Game capture failed: \(error)\n", stderr)
            exit(4)
        }
    }
}
