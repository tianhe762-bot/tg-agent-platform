#!/usr/bin/env python3
"""Cross-platform release builder.

Creates dist/tg-agent-v<version>.tar.gz (+ .sha256) with all text files
normalized to LF, per project convention (no CRLF in release packages).
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {
    ".py", ".sh", ".md", ".txt", ".service", ".conf", ".example",
    ".yml", ".yaml", ".json", ".ini", ".dockerignore", ".gitignore",
}
TEXT_NAMES = {"requirements.txt", "VERSION", "Dockerfile", "pytest.ini"}

INCLUDE = [
    "src",
    "configs",
    "scripts",
    "systemd",
    "docker",
    "tests",
    ".github",
    "install.sh",
    "docs",
    "requirements.txt",
    "VERSION",
    "README.md",
    "CHANGELOG.md",
    "pytest.ini",
]

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "data", "logs"}
EXCLUDE_FILES = {"configs/.env"}


def is_text(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES


def normalize(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def build(out_dir: Path) -> Path:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"tg-agent-v{version}.tar.gz"

    crlf_files: list[str] = []
    with tarfile.open(archive, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for rel in INCLUDE:
            src = ROOT / rel
            if not src.exists():
                print(f"skip missing: {rel}")
                continue
            if src.is_file():
                if src.relative_to(ROOT).as_posix() in EXCLUDE_FILES:
                    continue
                _add_file(tar, src, rel, crlf_files)
                continue
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in EXCLUDE_DIRS for part in path.relative_to(src).parts):
                    continue
                if path.relative_to(ROOT).as_posix() in EXCLUDE_FILES:
                    continue
                archive_rel = f"{rel}/{path.relative_to(src).as_posix()}"
                _add_file(tar, path, archive_rel, crlf_files)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out_dir / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    print(f"✅ 发布包: {archive} ({archive.stat().st_size / 1024:.1f} KB)")
    print(f"✅ SHA256: {digest}")
    if crlf_files:
        print(f"⚠️ 发现 CRLF 已自动转 LF: {len(crlf_files)} 个文件")
    else:
        print("✅ 无 CRLF 文件")
    return archive


def _add_file(tar: tarfile.TarFile, path: Path, archive_rel: str, crlf_files: list[str]) -> None:
    raw = path.read_bytes()
    if is_text(path):
        if b"\r" in raw:
            crlf_files.append(archive_rel)
        raw = normalize(raw)
    info = tarfile.TarInfo(archive_rel)
    info.size = len(raw)
    info.mtime = int(path.stat().st_mtime)
    if archive_rel.endswith(".sh") or archive_rel == "install.sh":
        info.mode = 0o755
    else:
        info.mode = 0o644
    tar.addfile(info, io.BytesIO(raw))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    build(out)
