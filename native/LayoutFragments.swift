import CoreGraphics
import Foundation
import Vision
import ImageIO
import UniformTypeIdentifiers

// RegionOCR and WindowReader share this path. Vision boxes have their origin at bottom-left.
// Usually Vision returns one line per observation; on grids it can join two answer buttons.
func layoutFragments(_ observations: [VNRecognizedTextObservation], aspect: CGFloat)
    -> [(text: String, box: CGRect, source: VNRecognizedTextObservation)] {
    guard aspect.isFinite, aspect > 0, observations.count <= 900 else { return [] }
    var result: [(text: String, box: CGRect, source: VNRecognizedTextObservation)] = []
    let frame = CGRect(x: 0, y: 0, width: 1, height: 1)
    for observation in observations {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let value = candidate.string
        let words = value.split(whereSeparator: { $0.isWhitespace })
        guard !words.isEmpty else { continue }
        let bounds = observation.boundingBox.intersection(frame)
        guard !bounds.isNull, !bounds.isEmpty else { continue }
        var clusters: [(text: String, box: CGRect)] = []
        if words.count > 1 && words.count < 120 {
            for word in words {
                guard let rectangle = try? candidate.boundingBox(for: word.startIndex..<word.endIndex) else {
                    clusters.removeAll()
                    break
                }
                let box = rectangle.boundingBox.intersection(frame)
                guard !box.isEmpty, !box.isNull else {
                    clusters.removeAll()
                    break
                }
                if let last = clusters.last {
                    let gap = box.minX - last.box.maxX
                    let textHeight = min(last.box.height, box.height) / aspect
                    let label = last.text.trimmingCharacters(in: CharacterSet(charactersIn: "()[].:")).uppercased()
                    // Keep the small leading badge box when Vision resolves it
                    // independently, even with only a normal inter-word gap.
                    let isBadge = clusters.count == 1 && label.count == 1 &&
                        "12345678①②③④⑤⑥⑦⑧ABCDEFGHАБВГДЕЖЗ".contains(label)
                    if gap >= max(0.012, textHeight * 1.4) || (isBadge && gap > textHeight * 0.15) {
                        clusters.append((String(word), box))
                        continue
                    }
                    let i = clusters.count - 1
                    clusters[i].text += " " + String(word)
                    clusters[i].box = last.box.union(box)
                } else {
                    clusters.append((String(word), box))
                }
            }
        }
        if clusters.count > 1 {
            result.append(contentsOf: clusters.map { ($0.text, $0.box, observation) })
        } else {
            result.append((value, bounds, observation))
        }
        if result.count > 900 { return [] }
    }
    return result
}

// Concerns travel with this frame only. A bounded second Vision pass inspects
// the actual pixels behind a critical number, sign or negation.
struct ReadingWarning: Encodable {
    let readings: [String]
    let image: String?
}

struct VisionConcern {
    let box: CGRect
    let warning: ReadingWarning
}

private let numberText = try! NSRegularExpression(pattern: #"[+−–-]?\d+(?:[.,]\d+)?\s*[%‰]?"#)
private let criticalText = try! NSRegularExpression(
    pattern: #"[+−–-]?\d+(?:[.,]\d+)?\s*[%‰]?|[<>≤≥≠=]+|(?<!\p{L})(?:не|без|not|except)(?!\p{L})"#,
    options: .caseInsensitive)
private func normalizedReading(_ text: String) -> String {
    text.lowercased().replacingOccurrences(of: #"\s+"#, with: "", options: .regularExpression)
        .replacingOccurrences(of: ",", with: ".").replacingOccurrences(of: "−", with: "-")
        .replacingOccurrences(of: "–", with: "-").replacingOccurrences(of: "<=", with: "≤")
        .replacingOccurrences(of: ">=", with: "≥")
}
private func criticalValues(_ text: String) -> [String] {
    let value = text as NSString
    return criticalText.matches(in: text, range: NSRange(location: 0, length: value.length))
        .map { normalizedReading(value.substring(with: $0.range)) }
}
private func simpleNumber(_ text: String) -> Bool {
    let entire = NSRange(text.startIndex..<text.endIndex, in: text)
    return numberText.firstMatch(in: text, range: entire)?.range == entire
}

private struct Scrutiny {
    let observation: VNRecognizedTextObservation
    let text: String
    let box: CGRect
    let alternative: String?
    let numeric: Bool
}

private func draftCheck(_ observation: VNRecognizedTextObservation, imageHeight: CGFloat) -> Scrutiny? {
    let candidates = observation.topCandidates(3)
    guard let best = candidates.first else { return nil }
    let text = best.string.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, text.count <= 140 else { return nil }
    let tokens = criticalValues(text)
    // An inserted or omitted negation also matters when the first candidate
    // contains no critical tokens at all.
    let alternate = candidates.dropFirst().first { other in
        other.confidence >= 0.35 && best.confidence - other.confidence <= 0.15 &&
        !other.string.isEmpty && other.string.count <= 140 && criticalValues(other.string) != tokens
    }
    if let alternate = alternate {
        let top = text.split(whereSeparator: { $0.isWhitespace })
        let alt = alternate.string.split(whereSeparator: { $0.isWhitespace })
        if top.count == alt.count {
            let diff = top.indices.filter { normalizedReading(String(top[$0])) != normalizedReading(String(alt[$0])) }
            let source = best.string
            let originalWords = source.split(whereSeparator: { $0.isWhitespace })
            if diff.count == 1, let index = diff.first, originalWords.indices.contains(index) {
                let word = originalWords[index]
                if let rectangle = try? best.boundingBox(for: word.startIndex..<word.endIndex) {
                    return Scrutiny(observation: observation, text: String(word), box: rectangle.boundingBox,
                                    alternative: String(alt[index]), numeric: simpleNumber(String(word)))
                }
            }
        }
        return Scrutiny(observation: observation, text: text, box: observation.boundingBox,
                        alternative: alternate.string, numeric: simpleNumber(text))
    }
    // Decimal separators and leading signs can vanish in an otherwise
    // high-confidence read. Skip lane numbers and whole lines of ordinary text.
    let small = observation.boundingBox.height * imageHeight < 18
    if simpleNumber(text), text.count >= 2,
       (best.confidence < 0.74 || text.contains(where: { ".,−–-+".contains($0) })
        || (small && text.contains(where: { "%‰".contains($0) }))) {
        return Scrutiny(observation: observation, text: text, box: observation.boundingBox,
                        alternative: nil, numeric: true)
    }
    if best.confidence < 0.76, tokens.count == 1,
       ["не", "без", "not", "except", "<", ">", "<=", ">=", "≤", "≥", "≠"].contains(text.lowercased()) {
        return Scrutiny(observation: observation, text: text, box: observation.boundingBox,
                        alternative: nil, numeric: false)
    }
    return nil
}

private func enlargedGrayCrop(_ image: CGImage) -> CGImage? {
    let scale = min(3.0, 1152.0 / CGFloat(image.width), 96.0 / CGFloat(image.height))
    let width = max(1, Int((CGFloat(image.width) * scale).rounded()))
    let height = max(1, Int((CGFloat(image.height) * scale).rounded()))
    let pad = 5
    guard let context = CGContext(data: nil, width: width + pad*2, height: height + pad*2,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
    else { return nil }
    // Use the corner luminance for padding, including dark-themed quizzes.
    var pixel: UInt8 = 255
    if let corner = image.cropping(to: CGRect(x: 0, y: 0, width: 1, height: 1)) {
        withUnsafeMutablePointer(to: &pixel) { pointer in
            if let sampler = CGContext(data: pointer, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 1,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) {
                sampler.draw(corner, in: CGRect(x: 0, y: 0, width: 1, height: 1))
            }
        }
    }
    context.setFillColor(gray: CGFloat(pixel) / 255, alpha: 1)
    context.fill(CGRect(x: 0, y: 0, width: width + pad*2, height: height + pad*2))
    context.interpolationQuality = .high
    context.draw(image, in: CGRect(x: pad, y: pad, width: width, height: height))
    return context.makeImage()
}

private func smallPNG(_ image: CGImage) -> String? {
    let scale = min(1, 560 / CGFloat(image.width), 64 / CGFloat(image.height))
    let w = max(1, Int(CGFloat(image.width) * scale)), h = max(1, Int(CGFloat(image.height) * scale))
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
    else { return nil }
    ctx.interpolationQuality = .high
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    let data = NSMutableData()
    guard let preview = ctx.makeImage(),
          let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)
    else { return nil }
    CGImageDestinationAddImage(destination, preview, nil)
    guard CGImageDestinationFinalize(destination), data.length <= 32 * 1024 else { return nil }
    return "data:image/png;base64," + (data as Data).base64EncodedString()
}

func verifyCriticalReadings(_ observations: [VNRecognizedTextObservation], image: CGImage,
                            languages: [String], budget: inout Int) -> [ObjectIdentifier: VisionConcern] {
    guard budget > 0, observations.count <= 900 else { return [:] }
    let w = CGFloat(image.width), h = CGFloat(image.height)
    var found: [ObjectIdentifier: VisionConcern] = [:]
    let checks = observations.compactMap { draftCheck($0, imageHeight: h) }
    // Give genuine ambiguities priority over preventive decimal checks.
    let ordered = checks.filter { $0.alternative != nil } + checks.filter { $0.alternative == nil }
    for check in ordered {
        if budget == 0 { break }
        let box = check.box
        let rect = CGRect(x: box.minX*w, y: (1-box.maxY)*h, width: box.width*w, height: box.height*h)
        let pad = max(1, min(6, rect.height*0.11))
        let area = rect.insetBy(dx: -pad, dy: -pad).integral.intersection(CGRect(x: 0, y: 0, width: w, height: h))
        guard !area.isNull, area.height >= 5, area.width >= 3, area.width <= 1600, area.height <= 180,
              let crop = image.cropping(to: area), let sample = enlargedGrayCrop(crop) else { continue }
        budget -= 1
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = languages
        request.usesLanguageCorrection = false
        request.minimumTextHeight = 0.10
        try? VNImageRequestHandler(cgImage: sample, orientation: .up, options: [:]).perform([request])
        let readings = (request.results ?? []).compactMap { $0.topCandidates(1).first }
        var alternative = check.alternative
        if readings.count == 1, let reread = readings.first, reread.confidence >= 0.45 {
            let revised = reread.string.trimmingCharacters(in: .whitespacesAndNewlines)
            let updated = criticalValues(revised)
            if normalizedReading(check.text) == normalizedReading(revised) { continue }
            if updated != criticalValues(check.text), !revised.isEmpty, revised.count <= 140,
               (!check.numeric || simpleNumber(revised)) {
                alternative = revised
            }
        }
        if let other = alternative {
            found[ObjectIdentifier(check.observation)] = VisionConcern(box: box, warning: ReadingWarning(
                readings: [check.text, other], image: smallPNG(crop)))
        }
    }
    return found
}

func warningInside(_ concern: VisionConcern?, fragment: CGRect) -> ReadingWarning? {
    guard let concern = concern else { return nil }
    let overlap = fragment.intersection(concern.box)
    guard !overlap.isNull, overlap.width * overlap.height >= 0.80 * concern.box.width * concern.box.height
    else { return nil }
    return concern.warning
}
