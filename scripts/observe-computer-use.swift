import AppKit
import Foundation

// 只观察桌面元数据，不读取剪贴板内容，不注入鼠标键盘事件。
let start = Date()
let initialClipboard = NSPasteboard.general.changeCount
while Date().timeIntervalSince(start) < 90 {
    let mouse = NSEvent.mouseLocation
    let sample: [String: Any] = [
        "time": Date().timeIntervalSince1970,
        "frontmostApp": NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "unknown",
        "mouseX": mouse.x,
        "mouseY": mouse.y,
        "clipboardChangeCount": NSPasteboard.general.changeCount,
        "clipboardChanged": NSPasteboard.general.changeCount != initialClipboard
    ]
    let data = try JSONSerialization.data(withJSONObject: sample, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
    fflush(stdout)
    Thread.sleep(forTimeInterval: 0.05)
}
