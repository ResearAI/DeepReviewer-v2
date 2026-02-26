from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepreview.types import AnnotationItem


@dataclass
class _ContentLine:
    page_number: int
    text: str
    bbox: tuple[float, float, float, float] | None


def _normalize_object_type(value: Any) -> str:
    token = str(value or '').strip().lower()
    if token in {'issue', 'suggestion', 'verification'}:
        return token
    if token == 'evidence':
        return 'suggestion'
    return 'suggestion'


def _coerce_bbox(value: Any) -> tuple[float, float, float, float] | None:
    x1: float
    y1: float
    x2: float
    y2: float
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            x1 = float(value[0])
            y1 = float(value[1])
            x2 = float(value[2])
            y2 = float(value[3])
        except (TypeError, ValueError):
            return None
    elif isinstance(value, dict):
        try:
            x1 = float(value.get('x1'))
            y1 = float(value.get('y1'))
            x2 = float(value.get('x2'))
            y2 = float(value.get('y2'))
        except (TypeError, ValueError):
            return None
    else:
        return None

    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _collect_content_lines(
    content_list: list[dict[str, Any]] | None,
) -> tuple[dict[int, list[_ContentLine]], dict[int, tuple[float, float]]]:
    per_page: dict[int, list[_ContentLine]] = {}
    page_refs: dict[int, tuple[float, float]] = {}
    max_xy: dict[int, tuple[float, float]] = {}

    for row in content_list or []:
        if not isinstance(row, dict):
            continue

        page_number: int | None = None
        page_idx = row.get('page_idx')
        if isinstance(page_idx, int):
            page_number = page_idx + 1
        else:
            for key in ('page_number', 'pageNumber', 'page'):
                raw = row.get(key)
                try:
                    if raw is not None:
                        page_number = int(raw)
                        break
                except (TypeError, ValueError):
                    continue

        if page_number is None or page_number < 1:
            continue

        text = str(row.get('text') or '').strip()
        if not text:
            continue

        bbox = _coerce_bbox(row.get('bbox'))
        per_page.setdefault(page_number, []).append(_ContentLine(page_number=page_number, text=text, bbox=bbox))

        if bbox is not None:
            prev_x, prev_y = max_xy.get(page_number, (100.0, 100.0))
            max_xy[page_number] = (max(prev_x, bbox[2]), max(prev_y, bbox[3]))

    for page_number, (mx, my) in max_xy.items():
        page_refs[page_number] = (max(100.0, float(mx)), max(100.0, float(my)))

    return per_page, page_refs


def _to_rect_dict(
    bbox: tuple[float, float, float, float],
    *,
    width_ref: float,
    height_ref: float,
) -> dict[str, float]:
    x1, y1, x2, y2 = bbox
    return {
        'x1': float(x1),
        'y1': float(y1),
        'x2': float(x2),
        'y2': float(y2),
        'width': float(max(1.0, width_ref)),
        'height': float(max(1.0, height_ref)),
    }


def _union_rects(rects: list[dict[str, float]]) -> dict[str, float] | None:
    if not rects:
        return None
    width_ref = float(rects[0].get('width') or 100.0)
    height_ref = float(rects[0].get('height') or 100.0)
    return {
        'x1': min(float(item['x1']) for item in rects),
        'y1': min(float(item['y1']) for item in rects),
        'x2': max(float(item['x2']) for item in rects),
        'y2': max(float(item['y2']) for item in rects),
        'width': width_ref,
        'height': height_ref,
    }


def _fallback_line_ratio_rect(
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> dict[str, float]:
    total = max(1, int(total_lines))
    start_idx = max(0, int(start_line) - 1)
    end_idx = max(start_idx + 1, int(end_line))
    y1 = (start_idx / total) * 100.0
    y2 = (end_idx / total) * 100.0
    y1 = max(0.0, min(98.0, y1))
    y2 = max(y1 + 1.2, min(100.0, y2))
    return {
        'x1': 8.0,
        'y1': y1,
        'x2': 92.0,
        'y2': y2,
        'width': 100.0,
        'height': 100.0,
    }


def _coerce_annotation_item(value: AnnotationItem | dict[str, Any]) -> AnnotationItem | None:
    if isinstance(value, AnnotationItem):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return AnnotationItem.model_validate(value)
    except Exception:
        return None


def build_source_annotations_for_export(
    *,
    annotations: list[AnnotationItem] | list[dict[str, Any]],
    content_list: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    content_lines_by_page, page_refs = _collect_content_lines(content_list)
    output: list[dict[str, Any]] = []

    for index, raw_item in enumerate(annotations, start=1):
        ann = _coerce_annotation_item(raw_item)
        if ann is None:
            continue

        page_number = max(1, int(ann.page))
        page_lines = content_lines_by_page.get(page_number, [])
        start_line = max(1, int(ann.start_line))
        end_line = max(start_line, int(ann.end_line))
        start_idx = max(0, start_line - 1)
        end_idx = min(len(page_lines), end_line)

        selected = page_lines[start_idx:end_idx] if page_lines else []
        selected_boxes = [line.bbox for line in selected if line.bbox is not None]

        if not selected_boxes and page_lines:
            nearby_start = max(0, start_idx - 2)
            nearby_end = min(len(page_lines), end_idx + 2)
            selected_boxes = [
                line.bbox
                for line in page_lines[nearby_start:nearby_end]
                if line.bbox is not None
            ]

        rects: list[dict[str, float]] = []
        if selected_boxes:
            width_ref, height_ref = page_refs.get(page_number, (100.0, 100.0))
            rects = [
                _to_rect_dict(
                    bbox,
                    width_ref=width_ref,
                    height_ref=height_ref,
                )
                for bbox in selected_boxes
            ]
        else:
            rects = [
                _fallback_line_ratio_rect(
                    start_line=start_line,
                    end_line=end_line,
                    total_lines=max(len(page_lines), end_line),
                )
            ]

        bounding_rect = _union_rects(rects)
        if not rects or bounding_rect is None:
            continue

        comment = str(ann.comment or '').strip()
        content_text = str(ann.text or '').strip()
        display_text = comment or content_text or '(no text provided)'

        output.append(
            {
                'annotation_id': str(ann.id),
                'page_number': page_number,
                'rects': rects,
                'bounding_rect': bounding_rect,
                'object_type': _normalize_object_type(ann.object_type),
                'severity': str(ann.severity or '').strip().lower() or None,
                'review_item_id': f'R{index:03d}',
                'display_text': display_text,
                'comment': comment,
                'content_text': content_text,
                'summary': str(ann.summary or '').strip() or None,
                'color': None,
                'tags': ['review_annotation'],
            }
        )

    return output
