"""Text from the Chromium contract PDFs.

The files use subset fonts and a ToUnicode map. There is no scanned image.
"""

from __future__ import annotations

import re
import zlib
from collections import defaultdict
from pathlib import Path


def _cmap(data: bytes) -> dict[bytes, str]:
    text = data.decode("latin1", errors="replace")
    mapping: dict[bytes, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[bytes.fromhex(src)] = bytes.fromhex(dst).decode("utf-16-be", errors="replace")
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block, re.S
        ):
            start = int(match.group(1), 16)
            width = max(1, len(match.group(1)) // 2)
            for offset, value in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", match.group(3))):
                mapping[(start + offset).to_bytes(width, "big")] = bytes.fromhex(value).decode(
                    "utf-16-be", errors="replace"
                )
        for src, end, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
        ):
            width = max(1, len(src) // 2)
            for offset, code in enumerate(range(int(src, 16), int(end, 16) + 1)):
                mapping[code.to_bytes(width, "big")] = chr(int(dst, 16) + offset)
    return mapping


def _objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    starts = list(re.finditer(rb"(\d+)\s+\d+\s+obj\b", data))
    objects: dict[int, tuple[bytes, bytes | None]] = {}
    for index, match in enumerate(starts):
        number = int(match.group(1))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(data)
        body = data[match.end() : end]
        stream_at = re.search(rb"stream\r?\n", body)
        if not stream_at:
            objects[number] = (body, None)
            continue
        header = body[: stream_at.start()]
        length = re.search(rb"/Length\s+(\d+)", header)
        stream = None
        if length:
            raw = body[stream_at.end() : stream_at.end() + int(length.group(1))]
            try:
                stream = zlib.decompress(raw)
            except zlib.error:
                stream = None
        objects[number] = (header, stream)
    return objects


def _decode(hex_text: str, cmap: dict[bytes, str]) -> str:
    if not hex_text or not cmap:
        return ""
    raw = bytes.fromhex(hex_text)
    width = max(len(key) for key in cmap)
    chars: list[str] = []
    index = 0
    while index < len(raw):
        key = raw[index : index + width]
        if key in cmap:
            chars.append(cmap[key])
            index += width
        elif raw[index : index + 1] in cmap:
            chars.append(cmap[raw[index : index + 1]])
            index += 1
        else:
            index += width or 1
    return "".join(chars)


def extract_text(path: Path) -> str:
    objects = _objects(path.read_bytes())
    cmaps = {
        number: _cmap(stream)
        for number, (_, stream) in objects.items()
        if stream and (b"beginbfchar" in stream or b"beginbfrange" in stream)
    }
    font_cmap: dict[int, dict[bytes, str]] = {}
    for number, (header, _) in objects.items():
        link = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", header)
        if link:
            font_cmap[number] = cmaps.get(int(link.group(1)), {})

    pages: list[tuple[int, list[int], dict[str, dict[bytes, str]]]] = []
    for number, (header, _) in objects.items():
        if b"/Type /Page" not in header or b"/Type /Pages" in header:
            continue
        array = re.search(rb"/Contents\s+\[(.*?)\]", header, re.S)
        if array:
            contents = [int(value) for value in re.findall(rb"(\d+)\s+0\s+R", array.group(1))]
        else:
            contents = [int(value) for value in re.findall(rb"/Contents\s+(\d+)\s+0\s+R", header)]
        fonts = {
            name.decode(): font_cmap.get(int(ref), {})
            for name, ref in re.findall(rb"/(F\d+)\s+(\d+)\s+0\s+R", header)
        }
        pages.append((number, contents, fonts))
    pages.sort()

    token = re.compile(
        r"/(F\d+)\s+([\d.]+)\s+Tf"
        r"|([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s+([-+0-9.]+)\s+Tm"
        r"|([-+0-9.]+)\s+([-+0-9.]+)\s+Td"
        r"|<([0-9A-Fa-f]+)>\s*Tj"
    )
    lines: list[str] = []
    for _, contents, fonts in pages:
        fragments: list[tuple[float, float, float, str]] = []
        for content_id in contents:
            stream = objects.get(content_id, (b"", None))[1]
            if not stream:
                continue
            text = stream.decode("latin1", errors="replace")
            x = y = 0.0
            size = 10.0
            cmap: dict[bytes, str] = {}
            for match in token.finditer(text):
                if match.group(1):
                    cmap = fonts.get(match.group(1), {})
                    size = float(match.group(2))
                elif match.group(3):
                    x = float(match.group(7))
                    y = float(match.group(8))
                elif match.group(9):
                    x += float(match.group(9))
                    y += float(match.group(10))
                elif match.group(11) is not None:
                    chunk = _decode(match.group(11), cmap)
                    if chunk:
                        fragments.append((round(y, 1), x, size, chunk))
        rows: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
        for y_pos, x_pos, size, chunk in fragments:
            rows[round(y_pos)].append((x_pos, size, chunk))
        for y_pos in sorted(rows):
            pieces = sorted(rows[y_pos])
            built = ""
            previous_x = None
            previous_size = 10.0
            for x_pos, size, chunk in pieces:
                if previous_x is not None and x_pos - previous_x > max(previous_size * 0.95, 9):
                    if not built.endswith(" "):
                        built += " "
                built += chunk
                previous_x = x_pos
                previous_size = size
            if built.strip():
                lines.append(built.strip())
    return "\n".join(lines)


def tidy(text: str) -> str:
    """Join digits the font split inside a line, without gluing the next row on."""
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"(?<=\d)[ \t]+(?=\d)", "", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)
