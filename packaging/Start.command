#!/bin/bash
set -u
archive_dir="$(cd "$(dirname "$0")" && pwd -P)" || exit 1
entry="$archive_dir/QuizLens.app/Contents/Resources/quiz-lens/launch.command"
if [[ ! -f "$entry" ]]; then
    echo "Помести Start.command рядом с QuizLens.app или распакуй ZIP заново."
    exit 1
fi
exec /bin/bash "$entry" "$@"
