#!/usr/bin/env bash
# Build the LF-normalized release tarball (cross-platform Python builder).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 scripts/build_release.py "${1:-dist}"

echo
echo "发布包内 CRLF 校验："
tar -tzf dist/tg-agent-v"$(cat VERSION)".tar.gz | while read -r f; do
    case "$f" in
        *.sh|*.py|*.md|*.txt|*.service|*.conf|*.yml|*.yaml|*.example|*.ini|requirements.txt|VERSION|Dockerfile)
            tar -xOzf dist/tg-agent-v"$(cat VERSION)".tar.gz "$f" | grep -q $'\r' && echo "CRLF: $f" || true
            ;;
    esac
done
echo "✅ 校验完成"
