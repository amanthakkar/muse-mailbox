#!/usr/bin/env python3
"""Split a directory into relay-safe chunks (< max_chars each)."""

from __future__ import annotations

import argparse
from pathlib import Path

SKIP = {".git", "node_modules", "dist", ".wrangler"}


def iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP for part in p.parts):
            continue
        out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--max-chars", type=int, default=2400)
    ap.add_argument("--out", type=Path, default=Path("/tmp/muse-mailbox-chunks"))
    args = ap.parse_args()
    root = args.root.resolve()
    files = iter_files(root)
    args.out.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        parts.append(buf)
        buf = ""

    for path in files:
        rel = path.relative_to(root)
        body = path.read_text(encoding="utf-8")
        header = f"FILE {rel}\n"
        chunk_n = 0
        start = 0
        while start < len(body) or start == 0:
            room = args.max_chars - len(header) - 32
            if room < 200:
                raise SystemExit("max-chars too small")
            piece = body[start : start + room]
            block = header + (f"CHUNK {chunk_n}\n" if start else "") + piece
            if buf and len(buf) + 2 + len(block) > args.max_chars:
                flush()
            if len(block) > args.max_chars:
                # should not happen given room
                flush()
                parts.append(block[: args.max_chars])
                start += room
                chunk_n += 1
                continue
            buf = block if not buf else buf + "\n" + block
            start += len(piece)
            chunk_n += 1
            if start >= len(body):
                break
            header = f"FILE {rel} CONTINUED\n"
    flush()
    manifest = [f"parts={len(parts)} files={len(files)} root={root.name}"]
    (args.out / "00-MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    for i, text in enumerate(parts, 1):
        (args.out / f"{i:02d}.txt").write_text(text, encoding="utf-8")
        print(f"{i:02d} {len(text)} chars")
    print(f"wrote {len(parts)} parts to {args.out}")


if __name__ == "__main__":
    main()
