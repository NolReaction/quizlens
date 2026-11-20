import Foundation
import Darwin
import CoreGraphics
import ImageIO
import Vision

// Each stdin frame is a 4-byte big-endian header length, UTF-8 JSON header, and raw image.
// Reply lines on stdout report progress or exactly one result per frame.
struct Region: Decodable {
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}
struct Frame {
    let image: Data
    let regions: [Region]
}
struct Fragment: Encodable {
    let text: String
    let x: CGFloat
    let y: CGFloat
    let w: CGFloat
    let h: CGFloat
    var readingWarning: ReadingWarning? = nil
}
struct Reply: Encodable {
    var stage: String?
    var texts: [String]?
    var parts: [[Fragment]]?
    var observations: [Fragment]?
    var aspect: CGFloat?
    var error: String?
}
struct ReadFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

func imageFromFrame(_ bytes: Data, limit: Int? = nil) throws -> (image: CGImage, aspect: CGFloat) {
    guard let source = CGImageSourceCreateWithData(bytes as CFData, nil),
          let info = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = info[kCGImagePropertyPixelWidth] as? NSNumber,
          let height = info[kCGImagePropertyPixelHeight] as? NSNumber,
          width.intValue > 0, height.intValue > 0,
          width.intValue <= 8192, height.intValue <= 8192,
          width.intValue * height.intValue <= 24_000_000 else {
        throw ReadFailure("Не удалось открыть кадр. Выберите окно или вкладку ещё раз.")
    }
    let image: CGImage?
    if let limit = limit, max(width.intValue, height.intValue) > limit {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: limit,
        ]
        image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    } else {
        image = CGImageSourceCreateImageAtIndex(source, 0, nil)
    }
    guard let decoded = image else { throw ReadFailure("Не удалось открыть изображение окна.") }
    return (decoded, CGFloat(width.intValue) / CGFloat(height.intValue))
}

func pixelRect(_ region: Region, image: CGImage) throws -> CGRect {
    guard [region.x, region.y, region.w, region.h].allSatisfy({ $0.isFinite }),
          region.x >= 0, region.y >= 0, region.w > 0, region.h > 0,
          region.x + region.w <= 1.00001, region.y + region.h <= 1.00001 else {
        throw ReadFailure("Область текста выходит за границы кадра.")
    }
    let width = CGFloat(image.width)
    let height = CGFloat(image.height)
    return CGRect(x: CGFloat(region.x) * width, y: CGFloat(region.y) * height,
                  width: CGFloat(region.w) * width, height: CGFloat(region.h) * height)
        .intersection(CGRect(x: 0, y: 0, width: width, height: height))
}

private func orderedParts(_ observations: [VNRecognizedTextObservation],
                          _ concerns: [ObjectIdentifier: VisionConcern]) -> [Fragment] {
    // Vision uses y=0 at the bottom. Group fragments of a line before ordering by x.
    var rows: [[VNRecognizedTextObservation]] = []
    for observation in observations.sorted(by: { $0.boundingBox.midY > $1.boundingBox.midY }) {
        if let first = rows.last?.first,
           abs(first.boundingBox.midY - observation.boundingBox.midY) <=
            0.45 * min(first.boundingBox.height, observation.boundingBox.height) {
            rows[rows.count - 1].append(observation)
        } else {
            rows.append([observation])
        }
    }
    return rows.flatMap { $0.sorted { $0.boundingBox.minX < $1.boundingBox.minX } }
        .compactMap { observation in
            guard let text = observation.topCandidates(1).first?.string,
                  !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
            let box = observation.boundingBox
            return Fragment(text: text, x: box.minX, y: 1 - box.maxY, w: box.width, h: box.height,
                            readingWarning: concerns[ObjectIdentifier(observation)]?.warning)
        }
}

private func croppedText(_ image: CGImage, _ rect: CGRect, _ request: VNRecognizeTextRequest,
                         budget: inout Int) throws -> [Fragment] {
    guard let cropped = image.cropping(to: rect.integral) else {
        throw ReadFailure("Не удалось вырезать область текста.")
    }
    try VNImageRequestHandler(cgImage: cropped, orientation: .up, options: [:]).perform([request])
    let observations = request.results ?? []
    let concerns = verifyCriticalReadings(observations, image: cropped,
                                         languages: request.recognitionLanguages, budget: &budget)
    return orderedParts(observations, concerns)
}

private func isPackedStrip(_ rects: [CGRect], width: CGFloat, height: CGFloat) -> Bool {
    // Both browser modes pack left-aligned bands from top to bottom. Keep
    // fractional coordinates until cropping so abutting edges remain shared.
    guard let first = rects.first, let last = rects.last,
          first.minY <= 1, last.maxY >= height - 1,
          rects.allSatisfy({ !$0.isEmpty && $0.minX <= 1 }),
          rects.contains(where: { $0.maxX >= width - 1 }) else { return false }
    for index in rects.indices.dropFirst() where rects[index].minY < rects[index - 1].maxY - 0.000001 {
        return false
    }
    return true
}

private func packedTexts(_ image: CGImage, _ rects: [CGRect], _ request: VNRecognizeTextRequest,
                         budget: inout Int) throws -> [[Fragment]?] {
    let width = CGFloat(image.width), height = CGFloat(image.height)
    // minimumTextHeight is relative to the input image. Preserve the smallest
    // text that was eligible in any one of the individual strips.
    let previousMinimum = request.minimumTextHeight
    defer { request.minimumTextHeight = previousMinimum }
    request.minimumTextHeight = previousMinimum * Float((rects.map { $0.height }.min() ?? height) / height)
    try VNImageRequestHandler(cgImage: image, orientation: .up, options: [:]).perform([request])
    var groups = Array(repeating: [VNRecognizedTextObservation](), count: rects.count)
    var ambiguous = Set<Int>()
    for observation in request.results ?? [] {
        let b = observation.boundingBox
        let box = CGRect(x: b.minX * width, y: (1 - b.maxY) * height,
                         width: b.width * width, height: b.height * height)
        let touched = rects.indices.filter { index in
            let overlap = rects[index].intersection(box)
            return !overlap.isNull && overlap.width > 0.000001 && overlap.height > 0.000001
        }
        if touched.count == 1, let index = touched.first,
           rects[index].insetBy(dx: -1, dy: -1).contains(box) {
            groups[index].append(observation)
        } else {
            // A single OCR box may span two answers. Re-read both from their
            // own crops instead of guessing ownership or duplicating text.
            ambiguous.formUnion(touched)
        }
    }
    let concerns = verifyCriticalReadings(request.results ?? [], image: image,
                                         languages: request.recognitionLanguages, budget: &budget)
    return rects.indices.map { index -> [Fragment]? in
        guard !ambiguous.contains(index) else { return nil }
        let parts = orderedParts(groups[index], concerns)
        return parts.isEmpty ? nil : parts
    }
}

func recognize(_ frame: Frame, with request: VNRecognizeTextRequest) throws -> [[Fragment]] {
    guard (3...9).contains(frame.regions.count) else { throw ReadFailure("Нужны области вопроса и 2–8 вариантов.") }
    let image = try imageFromFrame(frame.image).image
    var budget = 2
    let rects = try frame.regions.map { try pixelRect($0, image: image) }
    if isPackedStrip(rects, width: CGFloat(image.width), height: CGFloat(image.height)),
       let texts = try? packedTexts(image, rects, request, budget: &budget) {
        return try rects.indices.map { index in
            if let text = texts[index] { return text }
            return try croppedText(image, rects[index], request, budget: &budget)
        }
    }
    return try rects.map { try croppedText(image, $0, request, budget: &budget) }
}

private struct FrameHeader: Decodable {
    let size: Int
    let regions: [Region]
    let layout: Bool?
}

// read(2) can return a partial frame on a pipe. Read exactly what the header
// promises so the next request starts at a known byte, even for multi-MiB JPEGs.
private func readBytes(_ count: Int, cleanEOF: Bool = false) throws -> Data? {
    var data = Data(count: count)
    let bytesRead = try data.withUnsafeMutableBytes { (buffer: UnsafeMutableRawBufferPointer) -> Int in
        guard let start = buffer.baseAddress else { return 0 }
        var position = 0
        while position < count {
            let readSize = Darwin.read(STDIN_FILENO, start.advanced(by: position), count - position)
            if readSize > 0 { position += readSize; continue }
            if readSize == 0 { break }
            if errno == EINTR { continue }
            throw ReadFailure("Сбой при чтении кадра из локального приложения.")
        }
        return position
    }
    if cleanEOF && bytesRead == 0 { return nil }
    guard bytesRead == count else { throw ReadFailure("Приложение прервало передачу кадра.") }
    return data
}

private func emit(_ reply: Reply) throws {
    var data = try JSONEncoder().encode(reply)
    data.append(10)
    try FileHandle.standardOutput.write(contentsOf: data)
}

private func makeRequest() throws -> VNRecognizeTextRequest {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.005
    let supported = try request.supportedRecognitionLanguages()
    let languages = ["ru", "en"].compactMap { code in
        supported.first { $0.lowercased() == code || $0.lowercased().hasPrefix(code + "-") }
    }
    guard languages.count == 2 else {
        throw ReadFailure("Apple Vision этой версии macOS не предоставил русский и английский языки для OCR.")
    }
    request.recognitionLanguages = languages
    return request
}

private func runWorker() throws {
    var vision: VNRecognizeTextRequest?
    while let sizeBytes = try readBytes(4, cleanEOF: true) {
        try autoreleasepool {
            let headerSize = sizeBytes.reduce(0) { ($0 << 8) | Int($1) }
            guard (1...4096).contains(headerSize), let bytes = try readBytes(headerSize) else {
                throw ReadFailure("Служба OCR получила неверный заголовок изображения.")
            }
            let header = try JSONDecoder().decode(FrameHeader.self, from: bytes)
            let fullFrame = header.layout == true
            guard (1...8 * 1024 * 1024).contains(header.size),
                  (fullFrame ? header.regions.isEmpty : (3...9).contains(header.regions.count)) else {
                throw ReadFailure("Неверный размер кадра или число областей изображения.")
            }
            try emit(Reply(stage: "receive"))
            guard let image = try readBytes(header.size) else { throw ReadFailure("Кадр пуст.") }
            if vision == nil {
                try emit(Reply(stage: "configure"))
                vision = try makeRequest()
            }
            guard let request = vision else { throw ReadFailure("Не удалось запустить Apple Vision.") }
            try emit(Reply(stage: "vision"))
            if fullFrame {
                let frame = try imageFromFrame(image, limit: 2000)
                // A short answer marker can occupy a much smaller fraction of
                // an entire window than of one manually selected text region.
                let previousMinimum = request.minimumTextHeight
                request.minimumTextHeight = 0.003
                defer { request.minimumTextHeight = previousMinimum }
                try VNImageRequestHandler(cgImage: frame.image, orientation: .up, options: [:]).perform([request])
                let observations = request.results ?? []
                var budget = 2
                let concerns = verifyCriticalReadings(observations, image: frame.image,
                                                     languages: request.recognitionLanguages, budget: &budget)
                let boxes = layoutFragments(observations, aspect: frame.aspect).map { part in
                    Fragment(text: part.text, x: part.box.minX, y: 1 - part.box.maxY,
                             w: part.box.width, h: part.box.height,
                             readingWarning: warningInside(concerns[ObjectIdentifier(part.source)], fragment: part.box))
                }
                try emit(Reply(observations: boxes, aspect: frame.aspect))
            } else {
                let frame = Frame(image: image, regions: header.regions)
                let parts = try recognize(frame, with: request)
                let texts = parts.map { row in row.map { $0.text }.joined(separator: " ") }
                try emit(Reply(texts: texts, parts: parts))
            }
        }
    }
}

@main
struct RegionOCR {
    static func main() {
        do { try runWorker() } catch { try? emit(Reply(error: error.localizedDescription)) }
    }
}
