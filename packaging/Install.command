#!/bin/bash
# Build and install a locally signed Finder launcher in this user's Applications.
set -euo pipefail
archive_dir="$(cd "$(dirname "$0")" && pwd -P)"
source_app="$archive_dir/QuizLens.app"
if [[ "$(uname -s)" != Darwin || ! -d "$source_app" ]]; then
    echo "Запусти Install.command на Mac из папки, в которой лежит QuizLens.app."
    exit 1
fi
if [[ ! -f "$source_app/Contents/Resources/quiz-lens/packaging/PRECOMPILED" ]] && ! /usr/bin/xcrun -f clang >/dev/null 2>&1; then
    echo "Подтверди установку инструментов Apple, дождись её завершения и снова открой Install.command."
    /usr/bin/xcode-select --install || true
    exit 1
fi

install_dir="$HOME/Applications"
installed_app="$install_dir/QuizLens.app"
mkdir -p "$install_dir"
stage="$(mktemp -d "$install_dir/.quizlens-install.XXXXXXXX")"
trap 'rm -rf "$stage"' EXIT
stage_app="$stage/QuizLens.app"
# Keep the previous installation until compilation and local signing finish.
/usr/bin/ditto "$source_app" "$stage_app"
contents="$stage_app/Contents"
binary="$contents/MacOS/QuizLens"
if [[ ! -f "$contents/Resources/quiz-lens/packaging/PRECOMPILED" ]]; then
    /usr/bin/xcrun --sdk macosx clang -fobjc-arc -O2 -mmacosx-version-min=14.0 "$contents/Resources/quiz-lens/packaging/FinderLauncher.m" \
        -framework AppKit -o "$binary"
    /usr/bin/codesign --force --sign - "$stage_app"
fi
/usr/bin/codesign --verify "$stage_app"

if [[ -e "$installed_app" || -L "$installed_app" ]]; then
    identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$installed_app/Contents/Info.plist" 2>/dev/null || true)"
    if [[ "$identifier" != local.quizlens.desktop || -L "$installed_app" ]]; then
        echo "В ~/Applications уже есть другой QuizLens.app. Переименуй его и снова запусти установку."
        exit 1
    fi
    mv "$installed_app" "$stage/previous.app"
fi
if ! mv "$stage_app" "$installed_app"; then
    if [[ -d "$stage/previous.app" ]]; then
        mv "$stage/previous.app" "$installed_app"
    fi
    exit 1
fi
echo "Готово. Приложение находится в ~/Applications/QuizLens.app"
/usr/bin/open "$installed_app"
