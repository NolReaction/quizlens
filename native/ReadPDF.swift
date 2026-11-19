import AppKit
import Foundation
import PDFKit
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func opticalText(_ page: PDFPage) throws -> String {
    let size = page.bounds(for: .cropBox).size
    guard size.width > 0, size.height > 0 else { return "" }
    let scale = 2000 / max(size.width, size.height)
    let snapshot = page.thumbnail(of: CGSize(width: size.width * scale, height: size.height * scale), for: .cropBox)
    guard let image = snapshot.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    let supported = try request.supportedRecognitionLanguages()
    let languages = ["ru", "en"].compactMap { code in
        supported.first { $0.lowercased().hasPrefix(code) }
    }
    if !languages.isEmpty {
        request.recognitionLanguages = languages
    }
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

guard CommandLine.arguments.count == 2 else { fail("Передайте путь к PDF.") }
let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let document = PDFDocument(url: inputURL) else { fail("PDFKit не смог открыть этот PDF.") }
guard !document.isLocked else { fail("PDF защищён паролем. Откройте и сохраните доступную копию.") }
var pages = [String]()
for index in 0..<document.pageCount {
    guard let page = document.page(at: index) else { pages.append(""); continue }
    // A page number or footer alone doesn't make an image slide searchable.
    do {
        let source = try autoreleasepool { () throws -> String in
            let embedded = page.string ?? ""
            if embedded.filter({ $0.isLetter || $0.isNumber }).count >= 45 { return embedded }
            let recognized = try opticalText(page)
            return recognized.count > embedded.count ? recognized : embedded
        }
        pages.append(source)
    } catch {
        let embedded = page.string ?? ""
        guard !embedded.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            fail("Не удалось распознать страницу \(index + 1). \(error.localizedDescription)")
        }
        pages.append(embedded)
    }
}
do {
    let data = try JSONEncoder().encode(pages)
    FileHandle.standardOutput.write(data)
} catch {
    fail("Не удалось вернуть распознанные страницы PDF.")
}
