"""PDF 版面几何去噪、排印页眉页脚过滤与逻辑段落合成模块。"""

from __future__ import annotations

import re
import unicodedata

from ..models import SourceBlock


def _compact_statement(text: str, *, max_chars: int = 260) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    floor = max(80, max_chars // 2)
    cut = -1
    for marker in "。！？；;.!?":
        pos = compact.rfind(marker, floor, max_chars)
        cut = max(cut, pos + 1 if pos >= 0 else -1)
    if cut < floor:
        cut = max_chars - 1
    else:
        cut = min(cut, max_chars - 1)
    return compact[:cut].rstrip() + "…"


def _normalize_marker(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _exclude_after_marker(blocks: list[SourceBlock], marker: str) -> list[SourceBlock]:
    normalized_marker = _normalize_marker(marker)
    if not normalized_marker:
        raise ValueError("exclude-after marker cannot be blank")
    filtered: list[SourceBlock] = []
    found = False
    for block in blocks:
        normalized_text = _normalize_marker(block.text)
        if normalized_marker in normalized_text:
            found = True
            original_text = unicodedata.normalize("NFKC", block.text).casefold()
            literal_pos = original_text.find(unicodedata.normalize("NFKC", marker).casefold())
            if literal_pos > 0 and block.table_rows is None:
                prefix = block.text[:literal_pos].strip()
                if prefix:
                    filtered.append(block.model_copy(update={"text": prefix}))
            break
        filtered.append(block)
    if not found:
        raise ValueError(f"exclude-after marker was not found in parsed text: {marker}")
    return filtered


def _union_bbox(boxes: list[tuple[float, float, float, float] | None]) -> tuple[float, float, float, float] | None:
    valid = [box for box in boxes if box is not None]
    if not valid:
        return None
    return (
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    )


def _layout_signature(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def _page_bbox_stats(blocks: list[SourceBlock]) -> dict[int, tuple[float, float, float, float]]:
    grouped: dict[int, list[tuple[float, float, float, float]]] = {}
    for block in blocks:
        if block.bbox is not None:
            grouped.setdefault(block.page_number, []).append(block.bbox)
    result: dict[int, tuple[float, float, float, float]] = {}
    for page, boxes in grouped.items():
        result[page] = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
    return result


def _is_page_number_noise(block: SourceBlock, page_stats: dict[int, tuple[float, float, float, float]]) -> bool:
    if block.table_rows is not None or block.bbox is None:
        return False
    compact = "".join(block.text.split())
    if not re.fullmatch(r"\d{1,4}", compact):
        return False
    stats = page_stats.get(block.page_number)
    if stats is None:
        return False
    _, _, _, page_bottom = stats
    return block.bbox[1] >= page_bottom - 32


def _detect_repetitive_furniture(blocks: list[SourceBlock]) -> set[str]:
    """Detect repeated running headers/footers without deleting the raw provenance blocks."""
    page_stats = _page_bbox_stats(blocks)
    occurrences: dict[str, list[SourceBlock]] = {}
    for block in blocks:
        if block.table_rows is not None or block.bbox is None:
            continue
        signature = _layout_signature(block.text)
        if not signature or len(signature) > 140:
            continue
        stats = page_stats.get(block.page_number)
        if stats is None:
            continue
        _, top, _, bottom = stats
        height = max(1.0, bottom - top)
        band = max(18.0, min(26.0, height * 0.04))
        in_margin_band = block.bbox[1] <= top + band or block.bbox[3] >= bottom - band
        if in_margin_band:
            occurrences.setdefault(signature, []).append(block)

    furniture: set[str] = set()
    for repeated in occurrences.values():
        pages = {block.page_number for block in repeated}
        if len(pages) >= 3:
            furniture.update(block.id for block in repeated)
    return furniture


def _is_heading_block(
    block: SourceBlock, *, page_left: float, page_right: float
) -> bool:
    if block.table_rows is not None or block.bbox is None:
        return False
    text = " ".join(block.text.split())
    compact = text.replace(" ", "")
    if not text or len(text) > 70:
        return False
    if re.match(r"^(?:第?[一二三四五六七八九十0-9]+[、.．].+|参考文献|附录|续表\s*\d+|表\s*\d+|图\s*\d+)", compact):
        return True
    if re.match(r"^[（(][一二三四五六七八九十0-9]+[）)].+", compact):
        return True
    width = max(1.0, page_right - page_left)
    center = (block.bbox[0] + block.bbox[2]) / 2
    page_center = (page_left + page_right) / 2
    block_width = block.bbox[2] - block.bbox[0]
    return abs(center - page_center) <= width * 0.08 and block_width <= width * 0.72


def _join_separator(left: str, right: str) -> str:
    if not left or not right:
        return ""
    a, b = left[-1], right[0]
    if a == "-" and b.isascii() and b.isalpha():
        return ""
    if a.isascii() and b.isascii() and (a.isalnum() or a in ",.;:)]}") and b.isalnum():
        return " "
    return ""


def _logical_units(blocks: list[SourceBlock]) -> list[tuple[str, list[SourceBlock]]]:
    """Return logical evidence units.

    Docling source blocks are already semantic document items with explicit roles, so
    they are kept as individual units and furniture is excluded deterministically.
    Legacy PyMuPDF blocks keep the previous layout-cleaning reconstruction path.
    """
    if any((block.source_label or "").startswith("docling:") for block in blocks):
        units: list[tuple[str, list[SourceBlock]]] = []
        page_stats = _page_bbox_stats(blocks)
        for block in blocks:
            if block.role == "furniture" or _is_page_number_noise(block, page_stats):
                continue
            if _normalize_marker(block.text) in {"paper", "article"}:
                continue
            role = block.role
            if block.table_rows is not None:
                role = "table"
            if role not in {"body", "heading", "front_matter", "footnote", "table"}:
                role = "body"
            units.append((role, [block]))
        return units

    furniture_ids = _detect_repetitive_furniture(blocks)
    page_stats = _page_bbox_stats(blocks)
    by_page: dict[int, list[SourceBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_number, []).append(block)

    units: list[tuple[str, list[SourceBlock]]] = []
    for page_number in sorted(by_page):
        page_blocks = sorted(
            by_page[page_number],
            key=lambda block: (
                block.bbox[1] if block.bbox else 0.0,
                block.bbox[0] if block.bbox else 0.0,
                0 if block.table_rows is None else 1,
            ),
        )
        stats = page_stats.get(page_number, (0.0, 0.0, 1.0, 1.0))
        page_left, _, page_right, page_bottom = stats
        body_candidates = [
            block
            for block in page_blocks
            if block.bbox is not None
            and block.table_rows is None
            and block.id not in furniture_ids
            and not _is_page_number_noise(block, page_stats)
            and len("".join(block.text.split())) >= 12
        ]
        left_values = sorted(block.bbox[0] for block in body_candidates if block.bbox is not None)
        body_left = left_values[len(left_values) // 2] if left_values else page_left

        current: list[SourceBlock] = []
        current_role = "body"

        def flush() -> None:
            nonlocal current, current_role
            if current:
                units.append((current_role, current))
            current = []
            current_role = "body"

        previous: SourceBlock | None = None
        for block in page_blocks:
            if block.table_rows is not None:
                flush()
                units.append(("table", [block]))
                previous = None
                continue
            if block.id in furniture_ids or _is_page_number_noise(block, page_stats):
                flush()
                previous = None
                continue
            if _normalize_marker(block.text) in {"paper", "article"}:
                flush()
                previous = None
                continue
            if block.bbox is None:
                role = "body"
            elif _is_heading_block(block, page_left=page_left, page_right=page_right):
                role = "heading"
            elif page_number == 1 and block.bbox[1] < min(480.0, page_bottom * 0.72):
                role = "front_matter"
            elif block.bbox[1] >= page_bottom - 145 and re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", block.text.strip()):
                role = "footnote"
            elif current_role == "footnote" and block.bbox[1] >= page_bottom - 145:
                role = "footnote"
            else:
                role = "body"

            if role == "heading":
                flush()
                units.append((role, [block]))
                previous = None
                continue

            should_break = False
            if current and previous is not None and previous.bbox is not None and block.bbox is not None:
                gap = block.bbox[1] - previous.bbox[3]
                previous_text = previous.text.rstrip()
                indented_start = block.bbox[0] >= body_left + 14
                previous_ended = bool(re.search(r"[。！？!?；;．.]\s*$", previous_text))
                if role != current_role:
                    should_break = True
                elif gap > 20 and role != "footnote":
                    should_break = True
                elif indented_start and previous_ended and role != "footnote":
                    should_break = True
            if should_break:
                flush()

            if not current:
                current_role = role
            current.append(block)
            previous = block
        flush()
    return units


def _compose_logical_text(
    blocks: list[SourceBlock],
) -> tuple[str, list[tuple[int, int, SourceBlock, int, int]]]:
    text_parts: list[str] = []
    segments: list[tuple[int, int, SourceBlock, int, int]] = []
    cursor = 0
    previous_piece = ""
    for block in blocks:
        raw = block.text
        source_start = len(raw) - len(raw.lstrip())
        source_end = len(raw.rstrip())
        if source_end <= source_start:
            continue
        piece = raw[source_start:source_end]
        separator = _join_separator(previous_piece, piece)
        if separator:
            text_parts.append(separator)
            cursor += len(separator)
        combined_start = cursor
        text_parts.append(piece)
        cursor += len(piece)
        segments.append((combined_start, cursor, block, source_start, source_end))
        previous_piece = piece
    return "".join(text_parts), segments
