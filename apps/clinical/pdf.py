"""Minimal, dependency-free PDF writer for unsigned prescription documents.

Produces a single-page (or multi-page) Helvetica text document. It is not meant
for layout-heavy documents; a signed PDF uploaded by the physician replaces it.
"""

from __future__ import annotations

LINES_PER_PAGE = 50


def _escape(text: str) -> str:
    safe = text.encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def simple_pdf(lines: list[str]) -> bytes:
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, max(len(lines), 1), LINES_PER_PAGE)]
    objects: list[bytes] = []
    font_id = 3
    page_ids: list[int] = []
    next_id = 4
    page_objects: list[tuple[int, int, bytes]] = []
    for page_lines in pages or [[]]:
        content = "BT /F1 11 Tf 14 TL 50 790 Td\n"
        content += "".join(f"({_escape(line)}) Tj T*\n" for line in page_lines)
        content += "ET"
        stream = content.encode("latin-1")
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        page_objects.append((page_id, content_id, stream))

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    for page_id, content_id, stream in page_objects:
        _ = page_id
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(output)


__all__ = ["simple_pdf"]
