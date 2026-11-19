#!/bin/bash
set -eu
cd "$(dirname "$0")"
if [[ "$(/usr/bin/uname -s)" != Darwin ]]; then
    echo "Этот запуск предназначен для macOS 14 или новее."
    exit 1
fi
case "$(/usr/bin/sw_vers -productVersion)" in
  10.*|11.*|12.*|13.*) echo "QuizLens требует macOS 14 или новее."; exit 1 ;;
esac

# A python.org or Homebrew install does not require the Xcode developer tools.
candidates=(/opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3)
if /usr/bin/xcode-select -p >/dev/null 2>&1; then candidates+=(/usr/bin/python3); fi
python_exec=""
for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]] && "$candidate" -I -B -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
        python_exec="$candidate"
        break
    fi
done
if [[ -z "$python_exec" ]]; then
    echo "Установи Python 3.9 или новее для macOS с https://www.python.org/downloads/macos/ и снова открой QuizLens."
    exit 1
fi

if [[ -f packaging/PRECOMPILED ]]; then
    helpers="$(cd ../.. && pwd -P)/Helpers"
    for name in RegionOCR ReadPDF WindowReader; do
        if [[ ! -x "$helpers/$name" ]]; then echo "В комплекте отсутствует $name. Распакуй приложение заново."; exit 1; fi
    done
    export QUIZ_LENS_REGION_OCR="$helpers/RegionOCR"
    export QUIZLENS_PDF_READER="$helpers/ReadPDF"
    export QUIZLENS_WINDOW_READER="$helpers/WindowReader"
    exec "$python_exec" -E -s -B -u app.py "$@"
fi
if ! /usr/bin/xcrun -f swiftc >/dev/null 2>&1; then
    echo "Для сборки из исходников установи Apple Command Line Tools командой xcode-select --install и повтори запуск."
    exit 1
fi
echo "QuizLens · оставь это окно открытым. Для выхода нажми Ctrl+C."
exec "$python_exec" -E -s -B -u packaging/bootstrap.py "$@"
