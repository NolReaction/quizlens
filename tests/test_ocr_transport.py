"""Exercise the actual pipe and subprocess lifecycle, independent of Vision/SDK availability."""
import hashlib
import base64
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ocr


REGIONS = [dict(x=0, y=i / 5, w=1, h=.2) for i in range(5)]

# Small deliberately fragmented reads mimic Darwin pipe behavior. Special frames
# stall or exit after a progress message, letting the supervisor prove recovery.
HELPER = r'''
import hashlib, json, os, sys, time
reader = sys.stdin.buffer

def read_exact(size):
    data = bytearray()
    while len(data) < size:
        chunk = reader.read(min(1019, size - len(data)))
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)

while True:
    leading = reader.read(4)
    if not leading:
        break
    header = json.loads(read_exact(int.from_bytes(leading, 'big')))
    print('{"stage":"receive"}', flush=True)
    raw = read_exact(header['size'])
    print('{"stage":"vision"}', flush=True)
    if raw == b'exit': os._exit(7)
    if raw == b'slow': time.sleep(.9)
    if header.get('layout'):
        assert header['regions'] == []
        print(json.dumps({'aspect': 1.75, 'observations': [
            {'text': raw.decode(), 'x': .3, 'y': .4, 'w': .2, 'h': .1}]}), flush=True)
        continue
    values = [hashlib.sha256(raw).hexdigest(), str(len(raw)), str(os.getpid()), str(len(header['regions'])), 'Готово']
    reply = (json.dumps({'texts': values}, ensure_ascii=False) + '\n').encode()
    # Split the UTF-8 payload between normal OS-level writes as well.
    os.write(1, reply[:-4])
    time.sleep(.002)
    os.write(1, reply[-4:])
'''


@unittest.skipIf(os.name == 'nt', 'The Apple Vision worker uses POSIX executable scripts and pipes')
class FramedTransport(unittest.TestCase):
    def setUp(self):
        ocr._stop_worker()
        folder = tempfile.TemporaryDirectory(prefix='quiz lens путь ')
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.executable = self.directory / 'Vision helper с пробелом'
        self.executable.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(HELPER), encoding='utf-8')
        self.executable.chmod(0o700)
        pick = patch.object(ocr, '_native_binary', return_value=self.executable)
        pick.start()
        self.addCleanup(pick.stop)
        self.addCleanup(ocr._stop_worker)

    def test_multi_megabyte_payload_and_boundaries_without_eof(self):
        # Include newlines and every byte value. A line-based image reader fails here.
        image = bytes(range(256)) * 24576
        records = [ocr._vision(value, REGIONS) for value in (image, b'next\x00\nafter large frame')]
        self.assertEqual(records[0][:2], [hashlib.sha256(image).hexdigest(), str(len(image))])
        self.assertEqual(records[1][0], hashlib.sha256(b'next\x00\nafter large frame').hexdigest())
        self.assertEqual(records[0][2], records[1][2])
        self.assertEqual(records[0][3:], ['5', 'Готово'])

    @patch.object(ocr, '_COLD_TIMEOUT', 3)
    @patch.object(ocr, '_WARM_TIMEOUT', .30)
    def test_cold_then_warm_timeout_reaps_worker_and_restores_cold_budget(self):
        first = ocr._vision(b'slow', REGIONS)
        old_process = ocr._worker
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, r'0\.3 с.+обработка изображения Apple Vision'):
            ocr._vision(b'slow', REGIONS)
        self.assertLess(time.monotonic() - start, 2)
        self.assertIsNotNone(old_process.poll())
        self.assertIsNone(ocr._worker)
        after = ocr._vision(b'slow', REGIONS)
        self.assertNotEqual(first[2], after[2])
        self.assertEqual(set(self.directory.iterdir()), {self.executable})

    def test_unexpected_child_exit_reports_stage_and_next_frame_succeeds(self):
        with self.assertRaisesRegex(RuntimeError, 'завершилась.+Apple Vision'):
            ocr._vision(b'exit', REGIONS)
        self.assertEqual(ocr._vision(b'retry', REGIONS)[-1], 'Готово')

    def test_switch_between_full_frame_and_regions_on_one_worker(self):
        first = ocr._vision(b'quiz A', REGIONS)
        for source in (b'quiz B', b'quiz C'):
            parts, aspect = ocr._vision(source, None)
            self.assertEqual(aspect, 1.75)
            self.assertEqual(parts, [dict(text=source.decode(), x=.3, y=.4, w=.2, h=.1)])
        self.assertEqual(ocr._vision(b'regions again', REGIONS)[2], first[2])


    def mac_startup(self):
        for target, name, value in [(sys, 'platform', 'darwin'), (ocr, '_closing', False),
                                    (ocr, '_warmup_thread', None), (ocr, '_warmup_error', '')]:
            change = patch.object(target, name, value)
            change.start()
            self.addCleanup(change.stop)

    def test_startup_is_async_and_first_foreground_waits_for_same_process(self):
        self.mac_startup()
        entered = threading.Event()
        real_exchange = ocr._exchange_frame
        pids = []

        def traced(process, frame, regions, timeout):
            pids.append(process.pid)
            if frame.startswith(b'\x89PNG'):
                entered.set()
                return real_exchange(process, b'slow', regions, timeout)
            return real_exchange(process, frame, regions, timeout)

        with patch.object(ocr, '_exchange_frame', side_effect=traced):
            ocr.start_prewarm()
            self.assertTrue(entered.wait(2))
            self.assertEqual(ocr.warmup_status()['state'], 'preparing')
            ocr.start_prewarm()
            self.assertEqual(ocr._vision(b'actual question', REGIONS)[-1], 'Готово')
        self.assertEqual(len(pids), 2)
        self.assertEqual(pids[0], pids[1])
        self.assertEqual(ocr.warmup_status()['state'], 'ready')

    def test_failed_prewarm_is_recoverable(self):
        self.mac_startup()
        with patch.object(ocr, '_exchange_frame', side_effect=RuntimeError('warmup failed')):
            ocr.start_prewarm()
            ocr._warmup_thread.join(2)
        self.assertEqual(ocr.warmup_status(), {'state': 'failed', 'detail': 'warmup failed'})
        ocr._vision(b'retry', REGIONS)
        self.assertEqual(ocr.warmup_status()['state'], 'ready')

    def test_shutdown_while_warming_reaps_child_and_never_restarts(self):
        self.mac_startup()
        entered = threading.Event()
        real_exchange = ocr._exchange_frame

        def slow(process, image, regions, timeout):
            entered.set()
            return real_exchange(process, b'slow', regions, timeout)

        with patch.object(ocr, '_exchange_frame', side_effect=slow):
            ocr.start_prewarm()
            self.assertTrue(entered.wait(2))
            old = ocr._worker
            ocr.shutdown()
            ocr._warmup_thread.join(2)
        self.assertIsNotNone(old.poll())
        self.assertIsNone(ocr._worker)
        ocr.start_prewarm()
        with self.assertRaisesRegex(RuntimeError, 'завершает работу'):
            ocr._vision(b'late', REGIONS)
        self.assertIsNone(ocr._worker)

    def test_prewarm_skips_linux(self):
        with patch.object(sys, 'platform', 'linux'), patch.object(ocr, '_warmup_thread', None):
            ocr.start_prewarm()
            self.assertIsNone(ocr._warmup_thread)
            self.assertEqual(ocr.warmup_status()['state'], 'idle')


@unittest.skipUnless(sys.platform == 'darwin', 'Requires Apple Vision and Command Line Tools')
class SystemVision(unittest.TestCase):
    def test_real_native_helper_recognizes_russian_strips_twice(self):
        # CoreGraphics fixture at a fixed pixel size, including on Retina Macs.
        # The macOS app needs Command Line Tools and can run this without Pillow.
        drawing = r"""
import AppKit
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 900, pixelsHigh: 350,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, colorSpaceName: .deviceRGB,
    bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor.white.setFill()
NSBezierPath(rect: NSRect(x: 0, y: 0, width: 900, height: 350)).fill()
let style: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 34), .foregroundColor: NSColor.black]
for (index, value) in ["Выберите формат", "Строка", "Таблица", "Модель", "Число"].enumerated() {
    (value as NSString).draw(at: NSPoint(x: 24, y: CGFloat(350 - (index + 1) * 70 + 14)), withAttributes: style)
}
NSGraphicsContext.restoreGraphicsState()
FileHandle.standardOutput.write(bitmap.representation(using: .png, properties: [:])!)
"""
        frame = subprocess.run(['/usr/bin/xcrun', 'swift', '-'], input=drawing.encode(),
                               stdout=subprocess.PIPE, check=True, timeout=150).stdout
        self.assertTrue(frame.startswith(b'\x89PNG'))
        ocr._stop_worker()
        self.addCleanup(ocr._stop_worker)
        for _ in range(2):
            self.assertEqual(ocr._vision(frame, REGIONS), ['Выберите формат', 'Строка', 'Таблица', 'Модель', 'Число'])
        boxes, aspect = ocr._vision(frame, None)
        self.assertAlmostEqual(aspect, 900 / 350)
        self.assertIn('Таблица', [box['text'] for box in boxes])
        self.assertLess(next(box['y'] for box in boxes if box['text'] == 'Строка'),
                        next(box['y'] for box in boxes if box['text'] == 'Число'))


class LayoutOCR(unittest.TestCase):
    def test_linux_full_frame_coordinates_and_empty_image(self):
        if sys.platform == 'darwin' or not ocr.shutil.which('tesseract'):
            self.skipTest('Requires Tesseract')
        try:
            from PIL import Image, ImageDraw, ImageFont
            font = ImageFont.truetype('DejaVuSans.ttf', 36)
        except (ImportError, OSError):
            self.skipTest('Requires Pillow and DejaVu Sans')
        image = Image.new('L', (2200, 800), 'white')

        def read():
            encoded = io.BytesIO()
            image.save(encoded, format='PNG')
            return ocr.layout_observations('data:image/png;base64,' + base64.b64encode(encoded.getvalue()).decode())

        self.assertEqual(read(), ([], 2.75))
        draw = ImageDraw.Draw(image)
        for word, x, y in [('Alpha', 120, 80), ('Bravo', 1300, 80),
                           ('Delta', 120, 500), ('Echo', 1300, 500)]:
            draw.text((x, y), word, font=font, fill='black')
        parts, aspect = read()
        self.assertEqual(aspect, 2.75)
        lookup = {p['text']: p for p in parts}
        for word, x, y in [('Alpha', .055, .11), ('Bravo', .591, .11),
                           ('Delta', .055, .634), ('Echo', .591, .634)]:
            self.assertAlmostEqual(lookup[word]['x'], x, delta=.01)
            self.assertAlmostEqual(lookup[word]['y'], y, delta=.02)
            self.assertGreater(lookup[word]['w'], 0)
            self.assertGreater(lookup[word]['h'], 0)


if __name__ == '__main__':
    unittest.main()
