import AppKit
import Foundation

guard CommandLine.arguments.count == 2 else {
    fatalError("Usage: render-dmg-background.swift OUTPUT_DIRECTORY")
}
let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

func text(_ value: String, y: CGFloat, height: CGFloat, size: CGFloat,
          weight: NSFont.Weight, color: NSColor) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    NSAttributedString(string: value, attributes: [
        .font: NSFont.systemFont(ofSize: size, weight: weight),
        .foregroundColor: color, .paragraphStyle: paragraph,
    ]).draw(in: NSRect(x: 24, y: y, width: 552, height: height))
}

for scale in [1, 2] {
    guard let image = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 600 * scale,
        pixelsHigh: 400 * scale, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
        isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let context = NSGraphicsContext(bitmapImageRep: image) else { fatalError("Cannot create canvas") }
    image.size = NSSize(width: 600, height: 400)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(scale), y: CGFloat(scale))
    NSColor(calibratedRed: 0.96, green: 0.97, blue: 0.98, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: 600, height: 400).fill()
    text("安装 BoxAgent", y: 318, height: 44, size: 27, weight: .medium,
         color: NSColor(calibratedWhite: 0.12, alpha: 1))
    text("将左侧应用拖入右侧 Applications", y: 286, height: 25, size: 14, weight: .regular,
         color: NSColor(calibratedWhite: 0.36, alpha: 1))
    let arrow = NSBezierPath()
    arrow.move(to: NSPoint(x: 271, y: 210))
    arrow.line(to: NSPoint(x: 329, y: 210))
    arrow.move(to: NSPoint(x: 317, y: 222))
    arrow.line(to: NSPoint(x: 329, y: 210))
    arrow.line(to: NSPoint(x: 317, y: 198))
    arrow.lineWidth = 2.5
    arrow.lineCapStyle = .round
    arrow.lineJoinStyle = .round
    NSColor(calibratedRed: 0.25, green: 0.43, blue: 0.72, alpha: 1).setStroke()
    arrow.stroke()
    text("复制完成后，从「应用程序」打开 BoxAgent", y: 61, height: 25,
         size: 13, weight: .regular, color: NSColor(calibratedWhite: 0.27, alpha: 1))
    text("首次打开会引导你完成配置", y: 35, height: 23,
         size: 12, weight: .regular, color: NSColor(calibratedWhite: 0.40, alpha: 1))
    NSGraphicsContext.restoreGraphicsState()
    let name = scale == 1 ? "dmg-background.png" : "dmg-background@2x.png"
    guard let png = image.representation(using: .png, properties: [:]) else { fatalError("Cannot encode PNG") }
    try png.write(to: output.appendingPathComponent(name))
}
