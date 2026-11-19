// Build on macOS with xcrun swiftc -O -parse-as-library WindowReader.swift LayoutFragments.swift -o WindowReader
import Cocoa
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers
import Vision

private struct ReaderError: LocalizedError {
    var errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

private struct WindowInfo: Encodable {
    let id: UInt32
    let title: String
    let app: String
}

private struct Region {
    let x: CGFloat
    let y: CGFloat
    let w: CGFloat
    let h: CGFloat

    static let full = Region(x: 0, y: 0, w: 1, h: 1)
}

private struct TextLine: Encodable {
    let region: Int
    let text: String
    let x: CGFloat
    let y: CGFloat
    let w: CGFloat
    let h: CGFloat
}

private struct OCRFrame: Encodable {
    let type = "frame"
    let aspect: CGFloat
    let lines: [TextLine]
}

private func emitJSON<T: Encodable>(_ value: T) throws {
    var data = try JSONEncoder().encode(value)
    data.append(0x0a)
    try FileHandle.standardOutput.write(contentsOf: data)
}

private func parseRegions(_ value: String) throws -> [Region] {
    let coordinates = try JSONDecoder().decode([[Double]].self, from: Data(value.utf8))
    guard coordinates.count == 1 || (3...9).contains(coordinates.count) else {
        throw ReaderError("Выбери область окна либо разметь вопрос и 2–8 вариантов.")
    }
    return try coordinates.map { box in
        guard box.count == 4, box.allSatisfy({ $0.isFinite }), box[0] >= 0, box[1] >= 0,
              box[2] > 0, box[3] > 0, box[0] + box[2] <= 1.000001, box[1] + box[3] <= 1.000001 else {
            throw ReaderError("Область находится за границей окна.")
        }
        return Region(x: CGFloat(box[0]), y: CGFloat(box[1]), w: CGFloat(box[2]), h: CGFloat(box[3]))
    }
}

@available(macOS 14.0, *)
private func captureWindows() async throws -> [SCWindow] {
    guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
        throw ReaderError("Разрешите запись экрана для приложения или Terminal в Системных настройках → Конфиденциальность и безопасность → Запись экрана. Затем закройте и заново откройте приложение.")
    }
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    return content.windows.filter { window in
        window.windowLayer == 0 && window.frame.width >= 100 && window.frame.height >= 70 &&
        window.owningApplication != nil
    }
}

@available(macOS 14.0, *)
private struct SelectedWindow {
    let filter: SCContentFilter
    let configuration: SCStreamConfiguration

    init(_ window: SCWindow, maxDimension: CGFloat) {
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let size = window.frame.size
        // Keep Retina text while bounding the OCR workload on very large monitors.
        let scale = min(max(CGFloat(filter.pointPixelScale), 1), maxDimension / max(size.width, size.height))
        let config = SCStreamConfiguration()
        config.width = max(2, Int((size.width * scale).rounded()))
        config.height = max(2, Int((size.height * scale).rounded()))
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        config.scalesToFit = true
        self.filter = filter
        self.configuration = config
    }

    func image() async throws -> CGImage {
        try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
    }
}

private func supportedLanguages() throws -> [String] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    let supported = try request.supportedRecognitionLanguages()
    let languages = ["ru", "en"].compactMap { prefix in
        supported.first { $0.lowercased().hasPrefix(prefix) }
    }
    guard languages.contains(where: { $0.lowercased().hasPrefix("ru") }) else {
        throw ReaderError("На этой версии macOS Vision не поддерживает русский OCR. Обновите macOS.")
    }
    return languages
}

// Compare only the selected text boxes. A moving timer outside them should not trigger OCR.
private func pixels(_ image: CGImage) -> Data? {
    let width = image.width
    let height = image.height
    var data = Data(count: width * height)
    let didDraw = data.withUnsafeMutableBytes { (bytes: UnsafeMutableRawBufferPointer) -> Bool in
        guard let address = bytes.baseAddress,
              let context = CGContext(data: address, width: width, height: height,
                                      bitsPerComponent: 8, bytesPerRow: width,
                                      space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
        context.draw(image, in: CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        return true
    }
    return didDraw ? data : nil
}

private struct RegionCache {
    var pixels: Data?
    var frame: CGRect = .null
    var lines: [TextLine] = []
}

private func recognize(_ image: CGImage, regions: [Region], languages: [String],
                       caches: inout [RegionCache]) throws -> [TextLine] {
    let width = CGFloat(image.width)
    let height = CGFloat(image.height)
    let bounds = CGRect(x: 0, y: 0, width: width, height: height)
    var result: [TextLine] = []
    for (index, region) in regions.enumerated() {
        let proposed = CGRect(x: region.x * width, y: region.y * height,
                              width: region.w * width, height: region.h * height)
        let rect = bounds.intersection(proposed.integral)
        guard !rect.isEmpty, let crop = image.cropping(to: rect) else { continue }
        let current = pixels(crop)
        if current != nil, caches[index].frame == rect, current == caches[index].pixels {
            result.append(contentsOf: caches[index].lines)
            continue
        }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = languages
        request.usesLanguageCorrection = false
        let wholeWindow = regions.count == 1
        request.minimumTextHeight = wholeWindow ? 0.003 : 0.005
        let handler = VNImageRequestHandler(cgImage: crop, options: [:])
        try handler.perform([request])
        let observations = request.results ?? []
        let fragments: [(text: String, box: CGRect)]
        if wholeWindow {
            fragments = layoutFragments(observations, aspect: width / height)
        } else {
            fragments = observations.compactMap { observation in
                guard let candidate = observation.topCandidates(1).first else { return nil }
                let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
                return text.isEmpty ? nil : (text: text, box: observation.boundingBox)
            }
        }
        let lines = fragments.map { fragment in
            let box = fragment.box
            // Vision uses bottom-left coordinates. The client draws from the top left.
            return TextLine(region: index, text: fragment.text,
                            x: (rect.minX + box.minX * rect.width) / width,
                            y: (rect.minY + (1 - box.maxY) * rect.height) / height,
                            w: box.width * rect.width / width,
                            h: box.height * rect.height / height)
        }
        // Group neighboring fragments before sorting by x, including mixed Russian/Latin text.
        var rows: [[TextLine]] = []
        for line in lines.sorted(by: { $0.y + $0.h/2 < $1.y + $1.h/2 }) {
            if let first = rows.last?.first,
               abs(first.y + first.h/2 - line.y - line.h/2) < min(first.h, line.h) * 0.45 {
                rows[rows.count - 1].append(line)
            } else {
                rows.append([line])
            }
        }
        let sorted = rows.flatMap { $0.sorted { $0.x < $1.x } }
        caches[index] = RegionCache(pixels: current, frame: rect, lines: sorted)
        result.append(contentsOf: sorted)
    }
    return result
}

@available(macOS 14.0, *)
private func watch(_ window: SelectedWindow, regions: [Region]) async throws {
    let languages = try supportedLanguages()
    var caches = Array(repeating: RegionCache(), count: regions.count)
    // Re-emit stable frames. This lets the client debounce even a completely static page.
    while !Task.isCancelled {
        let start = ProcessInfo.processInfo.systemUptime
        let image = try await window.image()
        let lines = try autoreleasepool {
            try recognize(image, regions: regions, languages: languages, caches: &caches)
        }
        try emitJSON(OCRFrame(aspect: CGFloat(image.width) / CGFloat(image.height), lines: lines))
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        let interval = regions.count == 1 ? 0.65 : 0.35
        let delay = max(0.025, interval - elapsed)
        try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
    }
}

private func writePNG(_ image: CGImage) throws {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
        throw ReaderError("Не удалось создать изображение окна.")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw ReaderError("Не удалось сохранить изображение окна.")
    }
    try FileHandle.standardOutput.write(contentsOf: data as Data)
}

@main
private struct WindowReader {
    static func main() async {
        let arguments = Array(CommandLine.arguments.dropFirst())
        do {
            guard #available(macOS 14.0, *) else {
                throw ReaderError("Для захвата окна нужна macOS 14 или новее.")
            }
            if arguments == ["--list"] {
                let windows = try await captureWindows()
                let result = windows.map { window in
                    WindowInfo(id: window.windowID, title: window.title ?? "",
                               app: window.owningApplication?.applicationName ?? "")
                }
                try emitJSON(result)
                return
            }
            guard arguments.count >= 2, ["--watch", "--preview"].contains(arguments[0]),
                  let id = UInt32(arguments[1]) else {
                throw ReaderError("WindowReader --list | --preview ID | --watch ID [--regions JSON]")
            }
            let regions: [Region]
            if arguments.count == 4, arguments[0] == "--watch", arguments[2] == "--regions" {
                regions = try parseRegions(arguments[3])
            } else if arguments.count == 2 {
                regions = [.full]
            } else {
                throw ReaderError("Неизвестные параметры захвата.")
            }
            let windows = try await captureWindows()
            guard let target = windows.first(where: { $0.windowID == id }) else {
                throw ReaderError("Выбранное окно закрыто или свёрнуто. Откройте его и выберите заново.")
            }
            let autoCapture = arguments[0] == "--watch" && regions.count == 1
            let window = SelectedWindow(target, maxDimension: autoCapture ? 2000 : 3200)
            if arguments[0] == "--preview" {
                let image = try await window.image()
                try writePNG(image)
            } else {
                try await watch(window, regions: regions)
            }
        } catch {
            let notice = error.localizedDescription
            if arguments.first == "--watch" {
                try? emitJSON(["type": "error", "message": notice])
            }
            FileHandle.standardError.write(Data((notice + "\n").utf8))
            exit(EXIT_FAILURE)
        }
    }
}
