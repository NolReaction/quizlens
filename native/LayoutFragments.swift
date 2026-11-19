import CoreGraphics
import Foundation
import Vision

// RegionOCR and WindowReader share this path. Vision boxes have their origin at bottom-left.
// Usually Vision returns one line per observation; on grids it can join two answer buttons.
func layoutFragments(_ observations: [VNRecognizedTextObservation], aspect: CGFloat)
    -> [(text: String, box: CGRect)] {
    guard aspect.isFinite, aspect > 0, observations.count <= 900 else { return [] }
    var result: [(text: String, box: CGRect)] = []
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
            result.append(contentsOf: clusters)
        } else {
            result.append((value, bounds))
        }
        if result.count > 900 { return [] }
    }
    return result
}
