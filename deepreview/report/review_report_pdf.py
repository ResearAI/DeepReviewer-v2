from __future__ import annotations

import html
import io
import logging
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = A4

FONT_ENGLISH_NAME = 'DS-Satoshi-Medium'
FONT_CHINESE_NAME = 'DS-NotoSerifSC'
FONT_MONO_NAME = 'Courier'
FONT_MONO_UNICODE_NAME = 'DS-DejaVuSansMono'
FONT_OVERLAY_UNICODE_NAME = 'DS-NotoSerifSC-Overlay'
FONT_OVERLAY_MONO_UNICODE_NAME = 'DS-DejaVuSansMono-Overlay'
FONT_CHINESE_FALLBACK_NAME = 'STSong-Light'

FONT_ENGLISH_RELATIVE = Path('assets/fonts/Satoshi-Medium-ByP-Zb-9.woff3')
FONT_ENGLISH_FALLBACK_RELATIVE = Path('assets/fonts/Satoshi-Medium-ByP-Zb-9.woff2')
FONT_ENGLISH_LEGACY_RELATIVE = Path('frontend/public/fonts/Satoshi-Medium-ByP-Zb-9.woff3')
FONT_ENGLISH_FALLBACK_LEGACY_RELATIVE = Path('frontend/public/fonts/Satoshi-Medium-ByP-Zb-9.woff2')
FONT_ENGLISH_RELATIVE_CANDIDATES = (
    FONT_ENGLISH_RELATIVE,
    FONT_ENGLISH_FALLBACK_RELATIVE,
    FONT_ENGLISH_LEGACY_RELATIVE,
    FONT_ENGLISH_FALLBACK_LEGACY_RELATIVE,
)

FONT_CHINESE_RELATIVE = Path('assets/fonts/NotoSerifSC-Regular-C94HN_ZN.ttf')
FONT_CHINESE_LEGACY_RELATIVE = Path('frontend/public/fonts/NotoSerifSC-Regular-C94HN_ZN.ttf')
FONT_CHINESE_RELATIVE_CANDIDATES = (
    FONT_CHINESE_RELATIVE,
    FONT_CHINESE_LEGACY_RELATIVE,
)
FONT_MONO_UNICODE_CANDIDATES = (
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
)

LOGO_CANDIDATES = (
    Path('assets/logo-small.png'),
    Path('assets/logo.png'),
    Path('frontend/public/logo-small.png'),
    Path('frontend/public/logo.png'),
)


@dataclass(frozen=True)
class ReportFonts:
    body: str
    heading: str
    mono: str


_FONTS_CACHE: ReportFonts | None = None
_MARKDOWN_PARSER: MarkdownIt | None = None
_FONT_AVAILABLE_CACHE: dict[str, bool] = {}
_MARKDOWN_EMPHASIS_FONT_CACHE: dict[tuple[str, bool, bool], str] = {}
_OVERLAY_FONT_FILE_BY_NAME: dict[str, str] = {}
_FITZ_FONT_METRICS_CACHE: dict[str, Any] = {}
_FITZ_FONT_METRICS_CACHE_MISS = object()
_FITZ_FONT_CANONICAL_ALIASES: dict[str, str] = {
    'helvetica': 'helv',
    'times': 'tiro',
    'times-roman': 'tiro',
    'courier': 'cour',
    'stsong-light': 'china-s',
    'stsonglight': 'china-s',
}

_LATEX_COMMAND_REPLACEMENTS: dict[str, str] = {
    'alpha': 'α',
    'beta': 'β',
    'gamma': 'γ',
    'delta': 'δ',
    'epsilon': 'ϵ',
    'varepsilon': 'ε',
    'zeta': 'ζ',
    'eta': 'η',
    'theta': 'θ',
    'vartheta': 'ϑ',
    'iota': 'ι',
    'kappa': 'κ',
    'lambda': 'λ',
    'mu': 'μ',
    'nu': 'ν',
    'xi': 'ξ',
    'pi': 'π',
    'varpi': 'ϖ',
    'rho': 'ρ',
    'varrho': 'ϱ',
    'sigma': 'σ',
    'varsigma': 'ς',
    'tau': 'τ',
    'upsilon': 'υ',
    'phi': 'ϕ',
    'varphi': 'φ',
    'chi': 'χ',
    'psi': 'ψ',
    'omega': 'ω',
    'Gamma': 'Γ',
    'Delta': 'Δ',
    'Theta': 'Θ',
    'Lambda': 'Λ',
    'Xi': 'Ξ',
    'Pi': 'Π',
    'Sigma': 'Σ',
    'Upsilon': 'Υ',
    'Phi': 'Φ',
    'Psi': 'Ψ',
    'Omega': 'Ω',
    'times': '×',
    'cdot': '·',
    'otimes': '⊗',
    'oplus': '⊕',
    'pm': '±',
    'mp': '∓',
    'leq': '≤',
    'geq': '≥',
    'neq': '≠',
    'approx': '≈',
    'sim': '∼',
    'in': '∈',
    'notin': '∉',
    'subset': '⊂',
    'subseteq': '⊆',
    'supset': '⊃',
    'supseteq': '⊇',
    'cup': '∪',
    'cap': '∩',
    'forall': '∀',
    'exists': '∃',
    'neg': '¬',
    'land': '∧',
    'lor': '∨',
    'to': '→',
    'rightarrow': '→',
    'leftarrow': '←',
    'leftrightarrow': '↔',
    'mapsto': '↦',
    'implies': '⇒',
    'iff': '⇔',
    'nabla': '∇',
    'partial': '∂',
    'infty': '∞',
    'sum': '∑',
    'prod': '∏',
    'int': '∫',
    'top': '⊤',
    'bot': '⊥',
    'perp': '⊥',
    'ell': 'ℓ',
}

_LATEX_SIMPLE_FORMATTING_COMMANDS = {
    'text',
    'textrm',
    'mathrm',
    'mathit',
    'mathbf',
    'mathsf',
    'mathtt',
    'operatorname',
    'operatorname*',
}

_LATEX_MATHBB_MAP = {
    'N': 'ℕ',
    'Z': 'ℤ',
    'Q': 'ℚ',
    'R': 'ℝ',
    'C': 'ℂ',
}


@dataclass(frozen=True)
class AnnotationOverlayItem:
    annotation_id: str
    page_number: int
    object_type: str
    severity: Optional[str]
    review_item_id: Optional[str]
    display_markdown: str
    display_text: str
    color: Optional[str]
    rects: list[dict[str, float]]
    bounding_rect: Optional[dict[str, float]]


@dataclass(frozen=True)
class OverlayStyledRun:
    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False


@dataclass(frozen=True)
class OverlayPalette:
    stroke: tuple[float, float, float]
    fill: tuple[float, float, float]
    callout_border: tuple[float, float, float]
    callout_fill: tuple[float, float, float]
    label_fill: tuple[float, float, float]


@dataclass
class AnnotationContinuationItem:
    marker: str
    item: AnnotationOverlayItem
    remaining_lines: list[list[OverlayStyledRun]]
    source_marker_rect: tuple[float, float, float, float]
    source_target_point: tuple[float, float]
    source_output_page_index: int | None = None
    first_continuation_page_index: int | None = None
    first_continuation_rect: tuple[float, float, float, float] | None = None
    next_part_index: int = 1


@dataclass
class PreparedOverlayRenderable:
    marker: str
    item: AnnotationOverlayItem
    palette: OverlayPalette
    clipped_highlight_rects: list[Any]
    union_rect: Any
    anchor_x: float
    anchor_y: float
    marker_rect: Any
    styled_lines: list[list[OverlayStyledRun]]
    source_target_point: tuple[float, float]


_OBJECT_TYPE_PALETTES: dict[str, OverlayPalette] = {
    'issue': OverlayPalette(
        stroke=(0.55, 0.45, 0.46),
        fill=(0.90, 0.86, 0.86),
        callout_border=(0.55, 0.45, 0.46),
        callout_fill=(0.95, 0.93, 0.93),
        label_fill=(0.84, 0.78, 0.78),
    ),
    'suggestion': OverlayPalette(
        stroke=(0.42, 0.49, 0.54),
        fill=(0.84, 0.88, 0.90),
        callout_border=(0.42, 0.49, 0.54),
        callout_fill=(0.92, 0.94, 0.95),
        label_fill=(0.78, 0.83, 0.86),
    ),
    'verification': OverlayPalette(
        stroke=(0.58, 0.49, 0.31),
        fill=(0.93, 0.90, 0.82),
        callout_border=(0.58, 0.49, 0.31),
        callout_fill=(0.97, 0.95, 0.90),
        label_fill=(0.90, 0.84, 0.70),
    ),
}

CALLOUT_LABEL_HEIGHT = 15.0
CALLOUT_LINE_HEIGHT = 11.2
CALLOUT_BOX_BASE_HEIGHT = 44.0
CALLOUT_LANE_GAP = 10.0
CALLOUT_VERTICAL_GAP = 5.0
CALLOUT_TEXT_PADDING = 5.0


def _normalize_newlines(value: str) -> str:
    return value.replace('\r\n', '\n').replace('\r', '\n')


def _escape(value: Any) -> str:
    return html.escape(_normalize_newlines(str(value or ''))).replace('\n', '<br/>')


def _markdown_parser() -> MarkdownIt:
    global _MARKDOWN_PARSER
    if _MARKDOWN_PARSER is None:
        _MARKDOWN_PARSER = MarkdownIt('gfm-like', {'linkify': False, 'typographer': False})
        _MARKDOWN_PARSER.use(
            dollarmath_plugin,
            allow_labels=False,
            allow_space=False,
            allow_digits=True,
            double_inline=True,
        )
    return _MARKDOWN_PARSER


def _token_attr(token: Any, key: str) -> str | None:
    if token is None:
        return None
    try:
        value = token.attrGet(key)
        if isinstance(value, str):
            return value
    except Exception:
        pass

    attrs = getattr(token, 'attrs', None)
    if isinstance(attrs, dict):
        value = attrs.get(key)
        return str(value) if value is not None else None
    if isinstance(attrs, (list, tuple)):
        for item in attrs:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and str(item[0]) == key
            ):
                return str(item[1])
    return None


def _escape_attr(value: Any) -> str:
    return html.escape(str(value or ''), quote=True)


def _looks_like_formula_text(value: str) -> bool:
    text = str(value or '').strip()
    if not text:
        return False
    if '$' in text:
        return True
    if re.search(r'\\[A-Za-z]+', text):
        return True
    if ('_' in text or '^' in text) and re.search(r'[A-Za-z0-9][_^]|[_^][A-Za-z0-9{\\(]', text):
        return True
    if any(sym in text for sym in ('≤', '≥', '∈', '→', '↦', '⊤', '⊥', 'ℝ', 'ℓ', '⋅', '∑', '∫')):
        return True
    return False


def _consume_braced_segment(source: str, start: int) -> tuple[str, int]:
    if start >= len(source) or source[start] != '{':
        return '', start
    depth = 0
    cursor = start
    parts: list[str] = []
    while cursor < len(source):
        token = source[cursor]
        if token == '{':
            depth += 1
            if depth > 1:
                parts.append(token)
            cursor += 1
            continue
        if token == '}':
            depth -= 1
            if depth == 0:
                return ''.join(parts), cursor + 1
            parts.append(token)
            cursor += 1
            continue
        parts.append(token)
        cursor += 1
    return ''.join(parts), cursor


def _consume_script_segment(source: str, start: int) -> tuple[str, int]:
    if start >= len(source):
        return '', start
    token = source[start]
    if token == '{':
        return _consume_braced_segment(source, start)
    if token == '\\':
        command_match = re.match(r'\\([A-Za-z]+)', source[start:])
        if command_match:
            command = command_match.group(1)
            return _LATEX_COMMAND_REPLACEMENTS.get(command, command), start + len(command_match.group(0))
        if start + 1 < len(source):
            return source[start + 1], start + 2
    return token, start + 1


def _normalize_latex_text(value: str) -> str:
    text = _normalize_newlines(str(value or '')).strip()
    if not text:
        return ''
    text = (
        text.replace('−', '-')
        .replace('–', '-')
        .replace('—', '-')
        .replace('⋅', '·')
        .replace('×', '×')
    )
    text = re.sub(r'\\left\s*', '', text)
    text = re.sub(r'\\right\s*', '', text)
    text = re.sub(r'\\,|\\;|\\!|\\\s', ' ', text)
    text = re.sub(r'\\mathbb\{([A-Za-z])\}', lambda m: _LATEX_MATHBB_MAP.get(m.group(1), m.group(1)), text)
    text = re.sub(r'\\mathbf\{([^{}]+)\}', r'\1', text)
    text = re.sub(r'\\boldsymbol\{([^{}]+)\}', r'\1', text)
    text = re.sub(r'\\textbf\{([^{}]+)\}', r'\1', text)
    text = re.sub(r'\\emph\{([^{}]+)\}', r'\1', text)

    for command in _LATEX_SIMPLE_FORMATTING_COMMANDS:
        pattern = rf'\\{re.escape(command)}\{{([^{{}}]+)\}}'
        text = re.sub(pattern, r'\1', text)

    def _replace_command(match: re.Match[str]) -> str:
        command = match.group(1)
        return _LATEX_COMMAND_REPLACEMENTS.get(command, command)

    text = re.sub(r'\\([A-Za-z]+)', _replace_command, text)
    text = text.replace('\\_', '_').replace('\\^', '^')
    text = text.replace('{', '{').replace('}', '}')
    return text


def _render_latex_markup(value: str) -> str:
    source = _normalize_latex_text(value)
    if not source:
        return ''

    cursor = 0
    parts: list[str] = []
    while cursor < len(source):
        token = source[cursor]
        if token in {'^', '_'}:
            segment, next_cursor = _consume_script_segment(source, cursor + 1)
            if segment:
                tag = 'super' if token == '^' else 'sub'
                parts.append(f'<{tag}>{_render_latex_markup(segment)}</{tag}>')
            cursor = max(next_cursor, cursor + 1)
            continue
        if token == '{':
            segment, next_cursor = _consume_braced_segment(source, cursor)
            if segment:
                parts.append(_render_latex_markup(segment))
            cursor = max(next_cursor, cursor + 1)
            continue
        if token == '}':
            cursor += 1
            continue
        if token == '\\':
            if cursor + 1 < len(source):
                parts.append(_escape(source[cursor + 1]))
                cursor += 2
            else:
                cursor += 1
            continue
        parts.append(_escape(token))
        cursor += 1
    return ''.join(parts)


def _render_formula_chunk(value: str, *, formula_font: str | None) -> str:
    markup = _render_latex_markup(value)
    if not markup:
        return _escape(value)
    font_token = str(formula_font or '').strip()
    if font_token:
        return f'<font name="{_escape_attr(font_token)}">{markup}</font>'
    return markup


def _render_formula_aware_text(value: str, *, formula_font: str | None) -> str:
    text = str(value or '')
    if not _looks_like_formula_text(text):
        return _escape(text)

    parts: list[str] = []
    tokens = re.split(r'(\s+)', text)
    trailing_punctuation = '.,;:!?，。；：！？'
    leading_punctuation = ''

    for token in tokens:
        if not token:
            continue
        if token.isspace():
            parts.append(_escape(token))
            continue

        core = token
        leading = ''
        trailing = ''
        while core and core[0] in leading_punctuation:
            leading += core[0]
            core = core[1:]
        while core and core[-1] in trailing_punctuation:
            trailing = core[-1] + trailing
            core = core[:-1]

        if core and _looks_like_formula_text(core):
            rendered_core = _render_formula_chunk(core, formula_font=formula_font)
            parts.append(_escape(leading) + rendered_core + _escape(trailing))
        else:
            parts.append(_escape(token))

    return ''.join(parts)


def _font_available(font_name: str | None) -> bool:
    token = str(font_name or '').strip()
    if not token:
        return False
    cached = _FONT_AVAILABLE_CACHE.get(token)
    if cached is not None:
        return cached
    try:
        pdfmetrics.getFont(token)
        _FONT_AVAILABLE_CACHE[token] = True
        return True
    except Exception:
        _FONT_AVAILABLE_CACHE[token] = False
        return False


def _contains_non_ascii(value: str) -> bool:
    return any(ord(char) > 127 for char in str(value or ''))


def _contains_cjk(value: str) -> bool:
    for char in str(value or ''):
        code = ord(char)
        if (
            0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= code <= 0x4DBF  # CJK Extension A
            or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        ):
            return True
    return False


def _register_overlay_measure_font(font_name: str | None, font_path: Path | None) -> None:
    token = str(font_name or '').strip()
    if not token or font_path is None:
        return
    try:
        resolved = str(font_path.resolve())
    except Exception:
        resolved = str(font_path)
    if not resolved:
        return
    _OVERLAY_FONT_FILE_BY_NAME[token] = resolved
    _FITZ_FONT_METRICS_CACHE.pop(token, None)


def _resolve_fitz_measure_font(font_name: str | None):
    token = str(font_name or '').strip()
    if not token:
        return None

    cached = _FITZ_FONT_METRICS_CACHE.get(token)
    if cached is _FITZ_FONT_METRICS_CACHE_MISS:
        return None
    if cached is not None:
        return cached

    try:
        import pymupdf as fitz
    except Exception:
        return None

    candidate_specs: list[tuple[str, str]] = []
    cached_path = _OVERLAY_FONT_FILE_BY_NAME.get(token)
    if cached_path:
        candidate_specs.append(('fontfile', cached_path))

    token_lower = token.lower()
    alias = _FITZ_FONT_CANONICAL_ALIASES.get(token_lower)
    if alias:
        candidate_specs.append(('fontname', alias))
    candidate_specs.append(('fontname', token))

    if token in {FONT_CHINESE_NAME, FONT_OVERLAY_UNICODE_NAME}:
        chinese_path = _first_existing_relative_path(
            _repo_root(),
            FONT_CHINESE_RELATIVE_CANDIDATES,
        )
        if chinese_path is not None:
            candidate_specs.insert(0, ('fontfile', str(chinese_path)))

    visited: set[tuple[str, str]] = set()
    for mode, value in candidate_specs:
        candidate_key = (mode, value)
        if candidate_key in visited:
            continue
        visited.add(candidate_key)
        try:
            if mode == 'fontfile':
                font_obj = fitz.Font(fontfile=value)
            else:
                font_obj = fitz.Font(fontname=value)
            _FITZ_FONT_METRICS_CACHE[token] = font_obj
            return font_obj
        except Exception:
            continue

    _FITZ_FONT_METRICS_CACHE[token] = _FITZ_FONT_METRICS_CACHE_MISS
    return None


def _resolve_markdown_emphasis_font(base_font: str | None, *, bold: bool, italic: bool) -> str:
    token = str(base_font or '').strip()
    cache_key = (token, bool(bold), bool(italic))
    cached = _MARKDOWN_EMPHASIS_FONT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if not bold and not italic:
        _MARKDOWN_EMPHASIS_FONT_CACHE[cache_key] = token
        return token

    if bold and italic:
        fallback = 'Helvetica-BoldOblique'
    elif bold:
        fallback = 'Helvetica-Bold'
    else:
        fallback = 'Helvetica-Oblique'

    if not token:
        _MARKDOWN_EMPHASIS_FONT_CACHE[cache_key] = fallback
        return fallback

    candidates: list[str] = []
    if bold and italic:
        candidates.extend(
            [
                f'{token}-BoldItalic',
                f'{token}-BoldOblique',
                f'{token} Bold Italic',
                f'{token} Bold Oblique',
            ]
        )
    elif bold:
        candidates.extend(
            [
                f'{token}-Bold',
                f'{token} Bold',
            ]
        )
    else:
        candidates.extend(
            [
                f'{token}-Italic',
                f'{token}-Oblique',
                f'{token} Italic',
                f'{token} Oblique',
            ]
        )

    for candidate in candidates:
        if _font_available(candidate):
            _MARKDOWN_EMPHASIS_FONT_CACHE[cache_key] = candidate
            return candidate

    resolved = fallback if _font_available(fallback) else token
    _MARKDOWN_EMPHASIS_FONT_CACHE[cache_key] = resolved
    return resolved


def _find_closing_markdown_token(tokens: list[Any], start_index: int, open_type: str, close_type: str) -> int:
    depth = 1
    cursor = start_index + 1
    while cursor < len(tokens):
        token_type = str(getattr(tokens[cursor], 'type', ''))
        if token_type == open_type:
            depth += 1
        elif token_type == close_type:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return len(tokens) - 1


def _render_markdown_inline_children(
    children: list[Any] | None,
    *,
    inline_code_font: str,
    body_font: str | None = None,
    formula_font: str | None = None,
) -> str:
    if not children:
        return ''

    parts: list[str] = []
    link_depth = 0
    strong_depth = 0
    italic_depth = 0
    strike_depth = 0
    base_font = str(body_font or '').strip()
    effective_formula_font = str(formula_font or inline_code_font or body_font or '').strip()

    def _apply_strike(markup: str) -> str:
        if not markup:
            return ''
        if strike_depth > 0:
            return f'<strike>{markup}</strike>'
        return markup

    for token in children:
        token_type = str(getattr(token, 'type', '') or '').strip().lower()
        token_content = str(getattr(token, 'content', '') or '')

        if token_type == 'text':
            escaped_text = _render_formula_aware_text(
                token_content,
                formula_font=effective_formula_font or None,
            )
            if strong_depth > 0 or italic_depth > 0:
                emphasis_font = _resolve_markdown_emphasis_font(
                    base_font,
                    bold=strong_depth > 0,
                    italic=italic_depth > 0,
                )
                if (
                    emphasis_font
                    and emphasis_font != base_font
                    and not (
                        _contains_non_ascii(token_content)
                        and emphasis_font.lower().startswith('helvetica')
                    )
                ):
                    parts.append(
                        _apply_strike(
                            f'<font name="{_escape_attr(emphasis_font)}">{escaped_text}</font>'
                        )
                    )
                else:
                    parts.append(_apply_strike(escaped_text))
            else:
                parts.append(_apply_strike(escaped_text))
            continue
        if token_type in {'softbreak', 'hardbreak'}:
            parts.append('<br/>')
            continue
        if token_type == 'code_inline':
            if _looks_like_formula_text(token_content):
                parts.append(_apply_strike(
                    _render_formula_aware_text(
                        token_content,
                        formula_font=effective_formula_font or None,
                    )
                ))
            else:
                escaped_code = _escape(token_content)
                inline_font = str(inline_code_font or '').strip()
                if _contains_non_ascii(token_content):
                    inline_font = str(body_font or inline_code_font or '').strip()
                if inline_font:
                    parts.append(_apply_strike(
                        f'<font name="{_escape_attr(inline_font)}">{escaped_code}</font>'
                    ))
                else:
                    parts.append(_apply_strike(escaped_code))
            continue
        if token_type == 'math_inline':
            parts.append(
                _apply_strike(
                    _render_formula_chunk(token_content, formula_font=effective_formula_font or None)
                )
            )
            continue
        if token_type == 'strong_open':
            strong_depth += 1
            continue
        if token_type == 'strong_close':
            strong_depth = max(0, strong_depth - 1)
            continue
        if token_type == 'em_open':
            italic_depth += 1
            continue
        if token_type == 'em_close':
            italic_depth = max(0, italic_depth - 1)
            continue
        if token_type in {'s_open', 'strikethrough_open', 'del_open'}:
            strike_depth += 1
            continue
        if token_type in {'s_close', 'strikethrough_close', 'del_close'}:
            strike_depth = max(0, strike_depth - 1)
            continue
        if token_type == 'link_open':
            href = _token_attr(token, 'href') or ''
            href = href.strip()
            if href:
                parts.append(f'<a href="{_escape_attr(href)}">')
                link_depth += 1
            continue
        if token_type == 'link_close':
            if link_depth > 0:
                parts.append('</a>')
                link_depth -= 1
            continue
        if token_type == 'image':
            alt_text = _token_attr(token, 'alt') or token_content or 'image'
            parts.append(_apply_strike(_escape(f'[Image: {alt_text}]')))
            continue

        if token_content:
            parts.append(_apply_strike(_escape(token_content)))

    if link_depth > 0:
        parts.extend(['</a>'] * link_depth)
    return ''.join(parts).strip()


def _wrap_markdown_code_lines(
    lines: list[str],
    *,
    width: int = 94,
    font_name: str | None = None,
    font_size: float | None = None,
    max_width_pt: float | None = None,
) -> str:
    wrapped_lines: list[str] = []
    effective_width = max(24, int(width))
    use_pdf_width_wrap = (
        isinstance(font_name, str)
        and bool(font_name.strip())
        and isinstance(font_size, (int, float))
        and float(font_size) > 0
        and isinstance(max_width_pt, (int, float))
        and float(max_width_pt) > 0
    )
    fallback_char_width = None
    if use_pdf_width_wrap:
        try:
            fallback_char_width = max(1.0, pdfmetrics.stringWidth('M', font_name, float(font_size)) * 0.62)
        except Exception:
            fallback_char_width = max(1.0, float(font_size) * 0.62)

    for raw_line in lines:
        line = str(raw_line or '').replace('\t', '    ')
        if not line:
            wrapped_lines.append('')
            continue
        normalized = _normalize_newlines(line).replace('\n', '')
        if use_pdf_width_wrap:
            max_line_width = max(60.0, float(max_width_pt) - 6.0)
            split_lines = _wrap_code_line_by_points(
                normalized,
                max_width_points=max_line_width,
                font_name=str(font_name),
                font_size=float(font_size),
            )
            wrapped_lines.extend(split_lines or [''])
        else:
            chunks = textwrap.wrap(
                normalized,
                width=effective_width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            wrapped_lines.extend(chunks or [''])

    return '\n'.join(wrapped_lines)


def _wrap_code_line_by_points(
    text_line: str,
    *,
    max_width_points: float,
    font_name: str,
    font_size: float,
) -> list[str]:
    line = str(text_line or '')
    if not line:
        return ['']

    tokens = re.findall(r'\s+|\S+', line)
    wrapped: list[str] = []
    current = ''

    def _flush_current() -> None:
        nonlocal current
        if current:
            wrapped.append(current.rstrip())
            current = ''

    for token in tokens:
        if not token:
            continue

        candidate = f'{current}{token}'
        if _measure_text_width(candidate, font_name=font_name, font_size=font_size) <= max_width_points:
            current = candidate
            continue

        if current.strip():
            _flush_current()
            if token.isspace():
                continue
            token = token.lstrip()

        if not token:
            continue

        if token.isspace():
            current = token
            continue

        if _measure_text_width(token, font_name=font_name, font_size=font_size) <= max_width_points:
            current = token
            continue

        split_chunks = _split_token_by_width(
            token,
            max_width_points=max_width_points,
            font_name=font_name,
            font_size=font_size,
        )
        if not split_chunks:
            continue
        wrapped.extend(chunk.rstrip() for chunk in split_chunks[:-1] if chunk)
        current = split_chunks[-1]

    if current:
        wrapped.append(current.rstrip())

    return wrapped or ['']


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_file(path: Path | None) -> Path | None:
    if path is None:
        return None
    if path.exists() and path.is_file():
        return path
    return None


def _first_existing_relative_path(repo_root: Path, candidates: Iterable[Path]) -> Path | None:
    for relative in candidates:
        resolved = _safe_file(repo_root / relative)
        if resolved is not None:
            return resolved
    return None


def _existing_relative_paths(repo_root: Path, candidates: Iterable[Path]) -> list[Path]:
    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for relative in candidates:
        resolved = _safe_file(repo_root / relative)
        if resolved is None:
            continue
        token = str(resolved.resolve())
        if token in seen:
            continue
        seen.add(token)
        resolved_paths.append(resolved)
    return resolved_paths


def _register_ttf_font(font_name: str, font_path: Path, *, quiet: bool = False) -> bool:
    if font_name in pdfmetrics.getRegisteredFontNames():
        return True

    try:
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        return True
    except Exception as exc:
        if quiet:
            logger.info('Skipped PDF font %s from %s: %s', font_name, font_path, exc)
        else:
            logger.warning('Failed to register PDF font %s from %s: %s', font_name, font_path, exc)
        return False


def _convert_woff_font_to_ttf(source_path: Path) -> Path | None:
    try:
        from fontTools.ttLib import TTFont as FontToolsTTFont
    except Exception:
        logger.warning('fontTools is unavailable; cannot convert %s', source_path)
        return None

    if not source_path.exists():
        return None

    cache_dir = _repo_root() / 'backend' / '.cache' / 'pdf_fonts'
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_path = cache_dir / f'{source_path.name}.converted.ttf'

    def _is_truetype_outline_font(path: Path) -> bool:
        try:
            inspected = FontToolsTTFont(str(path))
            return 'glyf' in inspected
        except Exception:
            return False

    try:
        if target_path.exists() and target_path.stat().st_mtime >= source_path.stat().st_mtime:
            if _is_truetype_outline_font(target_path):
                return target_path
            try:
                target_path.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

    try:
        font = FontToolsTTFont(str(source_path))
        if 'glyf' not in font:
            logger.info(
                'Skipping WOFF font %s for PDF heading font: unsupported outlines (CFF/PostScript).',
                source_path,
            )
            return None
        font.flavor = None
        font.save(str(target_path))
        if not _is_truetype_outline_font(target_path):
            logger.info(
                'Converted font %s is not TrueType-outline; skip for ReportLab registration.',
                target_path,
            )
            return None
        return target_path
    except Exception as exc:
        logger.warning('Failed to convert WOFF font %s: %s', source_path, exc)
        return None


def _resolve_report_fonts() -> ReportFonts:
    global _FONTS_CACHE
    if _FONTS_CACHE is not None:
        return _FONTS_CACHE

    heading_font = 'Helvetica-Bold'
    body_font = 'Times-Roman'
    has_cjk_body_font = False

    repo_root = _repo_root()
    chinese_font_path = _first_existing_relative_path(repo_root, FONT_CHINESE_RELATIVE_CANDIDATES)
    if chinese_font_path and _register_ttf_font(FONT_CHINESE_NAME, chinese_font_path):
        body_font = FONT_CHINESE_NAME
        heading_font = FONT_CHINESE_NAME
        has_cjk_body_font = True
    elif FONT_CHINESE_FALLBACK_NAME not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(FONT_CHINESE_FALLBACK_NAME))
            body_font = FONT_CHINESE_FALLBACK_NAME
            heading_font = FONT_CHINESE_FALLBACK_NAME
            has_cjk_body_font = True
        except Exception as exc:
            logger.warning(
                'Failed to register fallback Chinese PDF font %s: %s',
                FONT_CHINESE_FALLBACK_NAME,
                exc,
            )
    else:
        body_font = FONT_CHINESE_FALLBACK_NAME
        heading_font = FONT_CHINESE_FALLBACK_NAME
        has_cjk_body_font = True

    english_sources = _existing_relative_paths(repo_root, FONT_ENGLISH_RELATIVE_CANDIDATES)
    primary_english_source = english_sources[0] if english_sources else None

    for english_font_source in english_sources:
        english_font_ttf = _convert_woff_font_to_ttf(english_font_source)
        quiet_register = english_font_source == primary_english_source
        if english_font_ttf and _register_ttf_font(
            FONT_ENGLISH_NAME,
            english_font_ttf,
            quiet=quiet_register,
        ):
            if not has_cjk_body_font:
                heading_font = FONT_ENGLISH_NAME
            break

    mono_font = FONT_MONO_NAME
    for mono_source in FONT_MONO_UNICODE_CANDIDATES:
        mono_path = _safe_file(mono_source)
        if not mono_path:
            continue
        if _register_ttf_font(FONT_MONO_UNICODE_NAME, mono_path, quiet=True):
            mono_font = FONT_MONO_UNICODE_NAME
            break

    _FONTS_CACHE = ReportFonts(body=body_font, heading=heading_font, mono=mono_font)
    return _FONTS_CACHE


def _resolve_overlay_font_resource() -> tuple[str, Path | None]:
    repo_root = _repo_root()
    chinese_font_path = _first_existing_relative_path(repo_root, FONT_CHINESE_RELATIVE_CANDIDATES)
    if chinese_font_path is not None:
        return FONT_OVERLAY_UNICODE_NAME, chinese_font_path
    return 'china-s', None


def _resolve_overlay_mono_font_resource() -> tuple[str, Path | None]:
    for mono_source in FONT_MONO_UNICODE_CANDIDATES:
        mono_path = _safe_file(mono_source)
        if mono_path is not None:
            return FONT_OVERLAY_MONO_UNICODE_NAME, mono_path
    return 'cour', None


def _ensure_overlay_font(
    page,
    *,
    font_name: str,
    font_path: Path | None,
) -> str:
    token = str(font_name or '').strip() or 'china-s'
    _register_overlay_measure_font(token, font_path)
    if font_path is None:
        return token
    try:
        page.insert_font(fontname=token, fontfile=str(font_path))
        _register_overlay_measure_font(token, font_path)
        return token
    except Exception as exc:
        logger.debug('Failed to register overlay font %s from %s: %s', token, font_path, exc)
        return 'china-s'


def _resolve_logo_path() -> Path | None:
    root = _repo_root()
    for relative in LOGO_CANDIDATES:
        candidate = _safe_file(root / relative)
        if candidate:
            return candidate
    return None


def _safe_canvas_font(canvas, font_name: str, size: float) -> None:
    candidates = [
        str(font_name or '').strip(),
        FONT_CHINESE_NAME,
        FONT_CHINESE_FALLBACK_NAME,
        'Helvetica',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            canvas.setFont(candidate, size)
            return
        except Exception:
            continue


def _iter_text_sections(payload: dict[str, Any], keys: Iterable[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        rows.append((key.replace('_', ' ').title(), text))
    return rows


def _split_paragraphs(text: str) -> list[str]:
    blocks = [part.strip() for part in re.split(r'\n\s*\n', text or '')]
    return [item for item in blocks if item]


def _format_decision(decision: str | None) -> str:
    if not decision:
        return 'Pending'
    normalized = str(decision).strip().replace('_', ' ')
    if not normalized:
        return 'Pending'
    return normalized.title()


def _format_datetime(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')


def _build_styles(fonts: ReportFonts) -> StyleSheet1:
    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name='CoverBrand',
            parent=styles['Normal'],
            fontName=fonts.heading,
            fontSize=12,
            leading=14,
            textColor=colors.HexColor('#9F1D1D'),
            alignment=1,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name='CoverTitle',
            parent=styles['Heading1'],
            fontName=fonts.heading,
            fontSize=24,
            leading=30,
            textColor=colors.HexColor('#111827'),
            alignment=1,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name='CoverWorkspaceTitle',
            parent=styles['Heading2'],
            fontName=fonts.body,
            fontSize=13,
            leading=18,
            textColor=colors.HexColor('#1F2937'),
            alignment=1,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name='CoverMeta',
            parent=styles['Normal'],
            fontName=fonts.body,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#4B5563'),
            alignment=1,
        )
    )
    styles.add(
        ParagraphStyle(
            name='SectionTitle',
            parent=styles['Heading2'],
            fontName=fonts.heading,
            fontSize=14,
            leading=19,
            textColor=colors.HexColor('#111827'),
            spaceBefore=8,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name='SectionSubtitle',
            parent=styles['Normal'],
            fontName=fonts.body,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor('#6B7280'),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name='BodyTextEnterprise',
            parent=styles['Normal'],
            fontName=fonts.body,
            fontSize=10.5,
            leading=16,
            textColor=colors.HexColor('#111827'),
            spaceAfter=3,
            wordWrap='CJK',
        )
    )
    styles.add(
        ParagraphStyle(
            name='LabelText',
            parent=styles['Normal'],
            fontName=fonts.heading,
            fontSize=10,
            leading=13,
            textColor=colors.HexColor('#1F2937'),
            spaceBefore=3,
            spaceAfter=1,
        )
    )
    styles.add(
        ParagraphStyle(
            name='SmallMutedText',
            parent=styles['Normal'],
            fontName=fonts.body,
            fontSize=8.8,
            leading=12,
            textColor=colors.HexColor('#6B7280'),
            wordWrap='CJK',
        )
    )
    styles.add(
        ParagraphStyle(
            name='AppendixRawText',
            parent=styles['Normal'],
            fontName=fonts.body,
            fontSize=9.2,
            leading=13,
            textColor=colors.HexColor('#1F2937'),
            wordWrap='CJK',
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownHeadingL2',
            parent=styles['Heading3'],
            fontName=fonts.heading,
            fontSize=12,
            leading=16,
            textColor=colors.HexColor('#111827'),
            spaceBefore=6,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownHeadingL3',
            parent=styles['Heading4'],
            fontName=fonts.heading,
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor('#1F2937'),
            spaceBefore=4,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownBullet',
            parent=styles['BodyTextEnterprise'],
            fontName=fonts.body,
            fontSize=10.1,
            leading=14.5,
            leftIndent=12,
            firstLineIndent=-6,
            spaceAfter=1.5,
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownCode',
            parent=styles['Normal'],
            fontName=fonts.mono,
            fontSize=8.9,
            leading=11.2,
            textColor=colors.HexColor('#111827'),
            backColor=colors.HexColor('#F8FAFC'),
            borderColor=colors.HexColor('#E5E7EB'),
            borderWidth=0.6,
            borderPadding=6,
            leftIndent=2,
            rightIndent=2,
            spaceBefore=2,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownMathBlock',
            parent=styles['BodyTextEnterprise'],
            fontName=fonts.mono,
            fontSize=10.2,
            leading=14.5,
            leftIndent=10,
            rightIndent=10,
            alignment=1,
            textColor=colors.HexColor('#111827'),
            backColor=colors.HexColor('#F8FAFC'),
            borderColor=colors.HexColor('#E5E7EB'),
            borderWidth=0.5,
            borderPadding=5,
            spaceBefore=2,
            spaceAfter=4,
            wordWrap='CJK',
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownTableHeader',
            parent=styles['BodyTextEnterprise'],
            fontName=fonts.heading,
            fontSize=9.4,
            leading=12.5,
            spaceAfter=0,
            wordWrap='CJK',
        )
    )
    styles.add(
        ParagraphStyle(
            name='MarkdownTableCell',
            parent=styles['BodyTextEnterprise'],
            fontName=fonts.body,
            fontSize=9.3,
            leading=12.2,
            spaceAfter=0,
            wordWrap='CJK',
        )
    )

    return styles


def _append_section_header(
    story: list,
    styles: StyleSheet1,
    *,
    title: str,
    subtitle: str | None = None,
) -> None:
    story.append(Paragraph(_escape(title), styles['SectionTitle']))
    if subtitle:
        story.append(Paragraph(_escape(subtitle), styles['SectionSubtitle']))
    story.append(
        HRFlowable(
            width='100%',
            thickness=0.6,
            color=colors.HexColor('#D1D5DB'),
            lineCap='round',
            spaceBefore=1,
            spaceAfter=5,
        )
    )


def _append_labeled_blocks(
    story: list,
    styles: StyleSheet1,
    *,
    label: str,
    text: str,
    render_markdown: bool = False,
) -> None:
    clean = str(text or '').strip()
    if not clean:
        return

    story.append(Paragraph(_escape(label), styles['LabelText']))
    if render_markdown:
        _append_markdown_report(story, styles, markdown=clean)
        return

    for block in _split_paragraphs(clean):
        story.append(Paragraph(_escape(block), styles['BodyTextEnterprise']))


def _looks_like_ascii_tree(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped:
        return False
    if stripped.lower().startswith('root:'):
        return True
    tree_tokens = ('|-', '`-', '+-', '└', '├', '│')
    return any(token in stripped for token in tree_tokens)


def _append_markdown_report(story: list, styles: StyleSheet1, *, markdown: str) -> None:
    clean = _normalize_newlines(str(markdown or '')).strip()
    if not clean:
        return

    tokens = _markdown_parser().parse(clean)

    def _list_style_for_depth(depth: int) -> ParagraphStyle:
        normalized_depth = max(0, int(depth))
        style_name = f'MarkdownBulletDepth{normalized_depth}'
        existing = styles.byName.get(style_name)
        if existing is not None:
            return existing
        parent = styles['MarkdownBullet']
        style = ParagraphStyle(
            name=style_name,
            parent=parent,
            leftIndent=parent.leftIndent + (normalized_depth * 8),
            firstLineIndent=parent.firstLineIndent,
            spaceAfter=parent.spaceAfter,
        )
        styles.add(style)
        return style

    def _append_code_block(content: str, *, lang: str | None = None) -> None:
        code_lines = _normalize_newlines(str(content or '')).split('\n')
        code_style = styles['MarkdownCode']
        render_style = code_style
        if _contains_non_ascii('\n'.join(code_lines)):
            render_style = ParagraphStyle(
                name='MarkdownCodeRuntimeCJK',
                parent=code_style,
                fontName=styles['BodyTextEnterprise'].fontName,
                wordWrap='CJK',
            )
        border_padding = float(getattr(code_style, 'borderPadding', 0) or 0)
        border_width = float(getattr(code_style, 'borderWidth', 0) or 0)
        left_indent = float(getattr(code_style, 'leftIndent', 0) or 0)
        right_indent = float(getattr(code_style, 'rightIndent', 0) or 0)
        content_width = PAGE_WIDTH - (40 * mm)
        horizontal_deductions = (
            left_indent
            + right_indent
            + (2.0 * border_padding)
            + (2.0 * border_width)
            + 2.0
        )
        max_text_width = max(120.0, content_width - horizontal_deductions)
        approx_width = max(36, int(max_text_width / max(1.0, float(code_style.fontSize) * 0.62)))
        wrapped = _wrap_markdown_code_lines(
            code_lines,
            width=approx_width,
            font_name=str(render_style.fontName),
            font_size=float(render_style.fontSize),
            max_width_pt=max_text_width,
        )
        story.append(
            Preformatted(
                wrapped,
                render_style,
                maxLineLength=approx_width,
            )
        )

    def _append_math_block(content: str) -> None:
        formula = _normalize_newlines(str(content or '')).strip()
        if not formula:
            return
        formula_font = str(styles['MarkdownCode'].fontName or '').strip()
        if _contains_non_ascii(formula):
            formula_font = str(styles['BodyTextEnterprise'].fontName or '').strip() or formula_font
        story.append(Paragraph(_escape('Equation'), styles['LabelText']))
        story.append(
            Paragraph(
                _render_formula_chunk(
                    formula,
                    formula_font=formula_font or None,
                ),
                styles['MarkdownMathBlock'],
            )
        )

    def _append_logic_tree(lines: list[str]) -> None:
        normalized_lines = [str(line or '').rstrip() for line in lines if str(line or '').strip()]
        if not normalized_lines:
            return
        story.append(Paragraph(_escape('Logic Tree'), styles['LabelText']))
        story.append(
            Preformatted(
                _wrap_markdown_code_lines(normalized_lines, width=94),
                styles['MarkdownCode'],
                maxLineLength=94,
            )
        )

    def _consume_table(start_index: int) -> int:
        rows: list[tuple[bool, list[str]]] = []
        cursor = start_index + 1
        in_header = False
        current_row: list[str] = []

        while cursor < len(tokens):
            token = tokens[cursor]
            token_type = str(getattr(token, 'type', ''))

            if token_type == 'table_close':
                break
            if token_type == 'thead_open':
                in_header = True
                cursor += 1
                continue
            if token_type == 'thead_close':
                in_header = False
                cursor += 1
                continue
            if token_type == 'tr_open':
                current_row = []
                cursor += 1
                continue
            if token_type in {'th_open', 'td_open'}:
                close_type = 'th_close' if token_type == 'th_open' else 'td_close'
                cell_markup = ''
                inner_cursor = cursor + 1
                while inner_cursor < len(tokens):
                    inner_token = tokens[inner_cursor]
                    inner_type = str(getattr(inner_token, 'type', ''))
                    if inner_type == close_type:
                        break
                    if inner_type == 'inline':
                        cell_markup += _render_markdown_inline_children(
                            getattr(inner_token, 'children', None),
                            inline_code_font=styles['MarkdownCode'].fontName,
                            body_font=(
                                styles['MarkdownTableHeader'].fontName
                                if in_header
                                else styles['MarkdownTableCell'].fontName
                            ),
                            formula_font=styles['MarkdownCode'].fontName,
                        )
                    inner_cursor += 1
                current_row.append(cell_markup.strip())
                cursor = inner_cursor + 1
                continue
            if token_type == 'tr_close':
                if current_row:
                    rows.append((in_header, list(current_row)))
                current_row = []
                cursor += 1
                continue

            cursor += 1

        if rows:
            max_cols = max(len(cells) for _, cells in rows)
            if max_cols > 0:
                content_width = PAGE_WIDTH - (40 * mm)
                col_width = content_width / max_cols

                table_data: list[list[Any]] = []
                header_rows: list[int] = []
                for row_index, (is_header, cells) in enumerate(rows):
                    if is_header:
                        header_rows.append(row_index)
                    padded = list(cells) + [''] * (max_cols - len(cells))
                    row_flowables = []
                    for cell in padded:
                        style_name = 'MarkdownTableHeader' if is_header else 'MarkdownTableCell'
                        content = cell or '&nbsp;'
                        row_flowables.append(Paragraph(content, styles[style_name]))
                    table_data.append(row_flowables)

                markdown_table = Table(
                    table_data,
                    colWidths=[col_width] * max_cols,
                    hAlign='LEFT',
                    repeatRows=1 if header_rows else 0,
                )
                table_style = [
                    ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#CBD5E1')),
                    ('INNERGRID', (0, 0), (-1, -1), 0.45, colors.HexColor('#E5E7EB')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 5),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]
                for row_index in header_rows:
                    table_style.extend(
                        [
                            ('BACKGROUND', (0, row_index), (-1, row_index), colors.HexColor('#F8FAFC')),
                            ('TEXTCOLOR', (0, row_index), (-1, row_index), colors.HexColor('#0F172A')),
                        ]
                    )
                markdown_table.setStyle(TableStyle(table_style))
                story.append(markdown_table)
                story.append(Spacer(1, 1.5 * mm))

        return cursor + 1

    def _consume_list(start_index: int, *, depth: int) -> int:
        open_token = tokens[start_index]
        open_type = str(getattr(open_token, 'type', ''))
        close_type = 'ordered_list_close' if open_type == 'ordered_list_open' else 'bullet_list_close'
        list_style = _list_style_for_depth(depth)

        start_value = _token_attr(open_token, 'start')
        try:
            item_counter = int(start_value) if start_value else 1
        except Exception:
            item_counter = 1

        cursor = start_index + 1
        while cursor < len(tokens):
            token = tokens[cursor]
            token_type = str(getattr(token, 'type', ''))
            if token_type == close_type:
                break
            if token_type != 'list_item_open':
                cursor += 1
                continue

            item_close_index = _find_closing_markdown_token(tokens, cursor, 'list_item_open', 'list_item_close')
            item_cursor = cursor + 1
            item_paragraph_parts: list[str] = []
            item_rendered = False

            def _render_current_item_paragraph() -> None:
                nonlocal item_paragraph_parts, item_rendered, item_counter
                if not item_paragraph_parts:
                    return
                prefix = f'{item_counter}. ' if open_type == 'ordered_list_open' else '• '
                body = '<br/>'.join(item_paragraph_parts).strip()
                if body:
                    story.append(Paragraph(f'{prefix}{body}', list_style))
                    item_rendered = True
                item_paragraph_parts = []

            while item_cursor < item_close_index:
                item_token = tokens[item_cursor]
                item_type = str(getattr(item_token, 'type', ''))

                if item_type == 'paragraph_open':
                    inline_token = tokens[item_cursor + 1] if item_cursor + 1 < len(tokens) else None
                    if inline_token is not None and str(getattr(inline_token, 'type', '')) == 'inline':
                        markup = _render_markdown_inline_children(
                            getattr(inline_token, 'children', None),
                            inline_code_font=styles['MarkdownCode'].fontName,
                            body_font=list_style.fontName,
                            formula_font=styles['MarkdownCode'].fontName,
                        )
                        if markup:
                            item_paragraph_parts.append(markup)
                    item_cursor += 3
                    continue

                if item_type in {'bullet_list_open', 'ordered_list_open'}:
                    _render_current_item_paragraph()
                    item_cursor = _consume_list(item_cursor, depth=depth + 1)
                    continue

                if item_type in {'fence', 'code_block'}:
                    _render_current_item_paragraph()
                    _append_code_block(
                        str(getattr(item_token, 'content', '')),
                        lang=str(getattr(item_token, 'info', '') or '').strip() or None,
                    )
                    item_cursor += 1
                    continue

                if item_type == 'table_open':
                    _render_current_item_paragraph()
                    item_cursor = _consume_table(item_cursor)
                    continue

                item_cursor += 1

            _render_current_item_paragraph()
            if not item_rendered and item_paragraph_parts:
                _render_current_item_paragraph()

            if open_type == 'ordered_list_open':
                item_counter += 1
            cursor = item_close_index + 1

        story.append(Spacer(1, 1.2 * mm))
        return cursor + 1

    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        token_type = str(getattr(token, 'type', ''))

        if token_type == 'heading_open':
            inline_token = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
            level_match = re.search(r'(\d+)', str(getattr(token, 'tag', '') or ''))
            level = int(level_match.group(1)) if level_match else 2
            heading_body_font = (
                styles['SectionTitle'].fontName
                if level <= 1
                else styles['MarkdownHeadingL2'].fontName
                if level == 2
                else styles['MarkdownHeadingL3'].fontName
            )
            heading_text = _render_markdown_inline_children(
                getattr(inline_token, 'children', None) if inline_token else None,
                inline_code_font=styles['MarkdownCode'].fontName,
                body_font=heading_body_font,
                formula_font=styles['MarkdownCode'].fontName,
            )

            if level <= 1:
                story.append(Paragraph(heading_text or '&nbsp;', styles['SectionTitle']))
                story.append(
                    HRFlowable(
                        width='100%',
                        thickness=0.45,
                        color=colors.HexColor('#D1D5DB'),
                        lineCap='round',
                        spaceBefore=1,
                        spaceAfter=4,
                    )
                )
            elif level == 2:
                story.append(Paragraph(heading_text or '&nbsp;', styles['MarkdownHeadingL2']))
            else:
                story.append(Paragraph(heading_text or '&nbsp;', styles['MarkdownHeadingL3']))
            cursor += 3
            continue

        if token_type == 'paragraph_open':
            inline_token = tokens[cursor + 1] if cursor + 1 < len(tokens) else None
            raw_content = _normalize_newlines(str(getattr(inline_token, 'content', '') or ''))
            paragraph_lines = [line.rstrip() for line in raw_content.split('\n') if line.strip()]
            looks_like_tree = len(paragraph_lines) >= 2 and any(
                _looks_like_ascii_tree(line) for line in paragraph_lines
            )

            if looks_like_tree:
                _append_logic_tree(paragraph_lines)
            else:
                paragraph_markup = _render_markdown_inline_children(
                    getattr(inline_token, 'children', None) if inline_token else None,
                    inline_code_font=styles['MarkdownCode'].fontName,
                    body_font=styles['BodyTextEnterprise'].fontName,
                    formula_font=styles['MarkdownCode'].fontName,
                )
                if paragraph_markup:
                    story.append(Paragraph(paragraph_markup, styles['BodyTextEnterprise']))
            cursor += 3
            continue

        if token_type in {'bullet_list_open', 'ordered_list_open'}:
            cursor = _consume_list(cursor, depth=0)
            continue

        if token_type == 'table_open':
            cursor = _consume_table(cursor)
            continue

        if token_type in {'fence', 'code_block'}:
            _append_code_block(
                str(getattr(token, 'content', '')),
                lang=str(getattr(token, 'info', '') or '').strip() or None,
            )
            cursor += 1
            continue

        if token_type == 'math_block':
            _append_math_block(str(getattr(token, 'content', '')))
            cursor += 1
            continue

        if token_type == 'blockquote_open':
            close_index = _find_closing_markdown_token(tokens, cursor, 'blockquote_open', 'blockquote_close')
            inner_cursor = cursor + 1
            quote_parts: list[str] = []
            while inner_cursor < close_index:
                inner_token = tokens[inner_cursor]
                if str(getattr(inner_token, 'type', '')) == 'inline':
                    quote_markup = _render_markdown_inline_children(
                        getattr(inner_token, 'children', None),
                        inline_code_font=styles['MarkdownCode'].fontName,
                        body_font=styles['BodyTextEnterprise'].fontName,
                        formula_font=styles['MarkdownCode'].fontName,
                    )
                    if quote_markup:
                        quote_parts.append(quote_markup)
                inner_cursor += 1
            if quote_parts:
                story.append(
                    Paragraph(
                        f'❝ {"<br/>".join(quote_parts)}',
                        styles['BodyTextEnterprise'],
                    )
                )
            cursor = close_index + 1
            continue

        if token_type == 'hr':
            story.append(
                HRFlowable(
                    width='100%',
                    thickness=0.4,
                    color=colors.HexColor('#D1D5DB'),
                    lineCap='round',
                    spaceBefore=2,
                    spaceAfter=3,
                )
            )
            cursor += 1
            continue

        cursor += 1


def _merge_with_pypdf(report_pdf_bytes: bytes, source_pdf_bytes: bytes) -> bytes | None:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:
        logger.warning('pypdf unavailable for source PDF appendix merge: %s', exc)
        return None

    try:
        writer = PdfWriter()

        report_reader = PdfReader(io.BytesIO(report_pdf_bytes))
        for page in report_reader.pages:
            writer.add_page(page)

        source_reader = PdfReader(io.BytesIO(source_pdf_bytes))
        if getattr(source_reader, 'is_encrypted', False):
            try:
                source_reader.decrypt('')
            except Exception:
                logger.warning('Source PDF is encrypted; skip source appendix merge.')
                return None

        for page in source_reader.pages:
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception as exc:
        logger.warning('Failed to merge source PDF with pypdf: %s', exc)
        return None


def _merge_with_pymupdf(report_pdf_bytes: bytes, source_pdf_bytes: bytes) -> bytes | None:
    try:
        import pymupdf as fitz
    except Exception as exc:
        logger.warning('PyMuPDF unavailable for source PDF appendix merge: %s', exc)
        return None

    report_doc = None
    source_doc = None
    try:
        report_doc = fitz.open(stream=report_pdf_bytes, filetype='pdf')
        source_doc = fitz.open(stream=source_pdf_bytes, filetype='pdf')

        if source_doc.is_encrypted:
            authenticated = False
            try:
                authenticated = bool(source_doc.authenticate(''))
            except Exception:
                authenticated = False
            if not authenticated:
                logger.warning('Source PDF is encrypted; skip source appendix merge.')
                return None

        report_doc.insert_pdf(source_doc)
        return report_doc.tobytes(garbage=3, deflate=True)
    except Exception as exc:
        logger.warning('Failed to merge source PDF with PyMuPDF: %s', exc)
        return None
    finally:
        if source_doc is not None:
            source_doc.close()
        if report_doc is not None:
            report_doc.close()


def _normalize_overlay_object_type(value: object) -> str:
    token = str(value or '').strip().lower()
    if token == 'evidence':
        return 'suggestion'
    if token in {'verification', 'needs_verification', 'needs verification', 'verify', 'uncertain'}:
        return 'verification'
    if token in {'issue', 'suggestion'}:
        return token
    return 'suggestion'


def _parse_hex_color(value: object) -> tuple[float, float, float] | None:
    token = str(value or '').strip()
    if not re.fullmatch(r'#?[0-9a-fA-F]{6}', token):
        return None
    if token.startswith('#'):
        token = token[1:]
    r = int(token[0:2], 16) / 255.0
    g = int(token[2:4], 16) / 255.0
    b = int(token[4:6], 16) / 255.0
    return (r, g, b)


def _coerce_overlay_rect(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x1 = float(raw.get('x1'))
        y1 = float(raw.get('y1'))
        x2 = float(raw.get('x2'))
        y2 = float(raw.get('y2'))
    except (TypeError, ValueError):
        return None

    try:
        width = float(raw.get('width', 100.0))
    except (TypeError, ValueError):
        width = 100.0
    try:
        height = float(raw.get('height', 100.0))
    except (TypeError, ValueError):
        height = 100.0

    if x2 <= x1 or y2 <= y1:
        return None
    if width <= 0:
        width = 100.0
    if height <= 0:
        height = 100.0

    return {
        'x1': x1,
        'y1': y1,
        'x2': x2,
        'y2': y2,
        'width': width,
        'height': height,
    }


def _normalize_overlay_markdown_source(value: str) -> str:
    text = _normalize_newlines(str(value or '')).strip()
    if not text:
        return ''

    table_splitter_re = re.compile(r'\s*\|\s*')
    table_separator_re = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')

    text = re.sub(
        r'```[^\n`]*\n([\s\S]*?)\n```',
        lambda m: _normalize_newlines(m.group(1)),
        text,
    )
    text = re.sub(r'```', '', text)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*+]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*(\d+)\.\s+', r'\1) ', text, flags=re.MULTILINE)
    normalized_lines: list[str] = []
    for raw_line in text.split('\n'):
        if table_separator_re.fullmatch(raw_line):
            continue
        stripped = raw_line.strip()
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 2:
            cells = [cell.strip() for cell in table_splitter_re.split(stripped.strip('|')) if cell.strip()]
            if cells:
                normalized_lines.append(' | '.join(cells))
                continue
        normalized_lines.append(raw_line)
    text = '\n'.join(normalized_lines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _markdown_to_overlay_text(value: str) -> str:
    text = _normalize_overlay_markdown_source(value)
    if not text:
        return ''

    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    return text.strip()


def _parse_overlay_inline_runs(line: str) -> list[OverlayStyledRun]:
    source = str(line or '')
    if not source:
        return []

    runs: list[OverlayStyledRun] = []
    buffer: list[str] = []
    bold = False
    italic = False
    strike = False
    code = False
    cursor = 0

    def _flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = ''.join(buffer)
        buffer = []
        if not text:
            return
        if (
            runs
            and runs[-1].bold == bold
            and runs[-1].italic == italic
            and runs[-1].strike == strike
            and runs[-1].code == code
        ):
            previous = runs[-1]
            runs[-1] = OverlayStyledRun(
                text=f'{previous.text}{text}',
                bold=previous.bold,
                italic=previous.italic,
                strike=previous.strike,
                code=previous.code,
            )
            return
        runs.append(
            OverlayStyledRun(
                text=text,
                bold=bold,
                italic=italic,
                strike=strike,
                code=code,
            )
        )

    while cursor < len(source):
        if source.startswith('\\', cursor) and cursor + 1 < len(source):
            buffer.append(source[cursor + 1])
            cursor += 2
            continue

        if not code and source.startswith('***', cursor):
            _flush_buffer()
            if bold and italic:
                bold = False
                italic = False
            else:
                bold = True
                italic = True
            cursor += 3
            continue

        if not code and source.startswith('~~', cursor):
            _flush_buffer()
            strike = not strike
            cursor += 2
            continue

        if not code and source.startswith('**', cursor):
            _flush_buffer()
            bold = not bold
            cursor += 2
            continue

        if not code and source[cursor] == '*':
            _flush_buffer()
            italic = not italic
            cursor += 1
            continue

        if source[cursor] == '`':
            _flush_buffer()
            code = not code
            cursor += 1
            continue

        buffer.append(source[cursor])
        cursor += 1

    _flush_buffer()
    return runs


def _overlay_run_font_name(
    run: OverlayStyledRun,
    *,
    base_font: str,
    mono_font: str,
) -> str:
    if run.code:
        # Keep CJK in base font; route non-CJK code/formula text to unicode mono
        # so math symbols (e.g., α, β, ≤, ≥) do not render as tofu boxes.
        if _contains_cjk(run.text):
            return base_font
        return mono_font if mono_font else base_font

    if not run.bold and not run.italic:
        return base_font

    emphasis_font = _resolve_markdown_emphasis_font(
        base_font,
        bold=run.bold,
        italic=run.italic,
    )
    if (
        _contains_non_ascii(run.text)
        and str(emphasis_font).lower().startswith('helvetica')
    ):
        return base_font
    return emphasis_font or base_font


def _wrap_overlay_markdown_lines(
    markdown: str,
    *,
    max_width_points: float,
    base_font: str,
    mono_font: str,
    font_size: float,
) -> list[list[OverlayStyledRun]]:
    normalized = _normalize_overlay_markdown_source(markdown)
    if not normalized:
        return [[OverlayStyledRun(text='(no text provided)')]]

    wrapped_lines: list[list[OverlayStyledRun]] = []
    for raw_line in normalized.split('\n'):
        if not raw_line.strip():
            if wrapped_lines and wrapped_lines[-1]:
                wrapped_lines.append([])
            continue

        line_runs = _parse_overlay_inline_runs(raw_line)
        if not line_runs:
            continue

        token_runs: list[OverlayStyledRun] = []
        for run in line_runs:
            normalized_text = str(run.text or '').replace('\u00a0', ' ').replace('\u3000', ' ')
            if not run.code:
                normalized_text = re.sub(r'[ \t]+', ' ', normalized_text)
            parts = re.findall(r'\s+|\S+', normalized_text)
            if not parts:
                continue
            token_runs.extend(
                OverlayStyledRun(
                    text=part,
                    bold=run.bold,
                    italic=run.italic,
                    strike=run.strike,
                    code=run.code,
                )
                for part in parts
            )

        current_line: list[OverlayStyledRun] = []
        current_width = 0.0

        def _flush_current_line() -> None:
            nonlocal current_line, current_width
            while current_line and current_line[-1].text.isspace():
                current_line.pop()
            if current_line:
                wrapped_lines.append(list(current_line))
            current_line = []
            current_width = 0.0

        for token_run in token_runs:
            token_text = token_run.text
            if not token_text:
                continue
            if token_text.isspace() and not current_line:
                continue

            token_font = _overlay_run_font_name(
                token_run,
                base_font=base_font,
                mono_font=mono_font,
            )
            token_width = _measure_text_width(
                token_text,
                font_name=token_font,
                font_size=font_size,
            )
            if token_width <= 0:
                token_width = max(1.0, font_size * 0.56 * len(token_text))

            if current_line and (current_width + token_width) > max_width_points:
                _flush_current_line()
                if token_text.isspace():
                    continue
                token_text = token_text.lstrip()
                if not token_text:
                    continue
                token_run = OverlayStyledRun(
                    text=token_text,
                    bold=token_run.bold,
                    italic=token_run.italic,
                    strike=token_run.strike,
                    code=token_run.code,
                )
                token_font = _overlay_run_font_name(
                    token_run,
                    base_font=base_font,
                    mono_font=mono_font,
                )
                token_width = _measure_text_width(
                    token_run.text,
                    font_name=token_font,
                    font_size=font_size,
                )

            if token_width <= max_width_points or token_run.text.isspace():
                current_line.append(token_run)
                current_width += max(0.0, token_width)
                continue

            split_chunks = _split_token_by_width(
                token_run.text,
                max_width_points=max_width_points,
                font_name=token_font,
                font_size=font_size,
            )
            if not split_chunks:
                continue

            for index, chunk in enumerate(split_chunks):
                if not chunk:
                    continue
                chunk_run = OverlayStyledRun(
                    text=chunk,
                    bold=token_run.bold,
                    italic=token_run.italic,
                    strike=token_run.strike,
                    code=token_run.code,
                )
                if index > 0 and current_line:
                    _flush_current_line()
                current_line.append(chunk_run)
                current_width += _measure_text_width(
                    chunk,
                    font_name=token_font,
                    font_size=font_size,
                )
                if index < len(split_chunks) - 1:
                    _flush_current_line()

        if current_line:
            _flush_current_line()

    return wrapped_lines or [[OverlayStyledRun(text='(no text provided)')]]


def _normalize_overlay_item(raw: dict[str, Any]) -> AnnotationOverlayItem | None:
    if not isinstance(raw, dict):
        return None
    try:
        page_number = int(raw.get('page_number'))
    except (TypeError, ValueError):
        return None
    if page_number < 1:
        return None

    rects_raw = raw.get('rects') if isinstance(raw.get('rects'), list) else []
    rects = [value for value in (_coerce_overlay_rect(item) for item in rects_raw) if value is not None]
    bounding_rect = _coerce_overlay_rect(raw.get('bounding_rect'))
    if not rects and bounding_rect is not None:
        rects = [bounding_rect]
    if not rects:
        return None

    display_text_raw = str(
        raw.get('display_text')
        or raw.get('comment')
        or raw.get('content_text')
        or ''
    ).strip()
    display_markdown = _normalize_overlay_markdown_source(display_text_raw)
    display_text = _markdown_to_overlay_text(display_text_raw) or '(no text provided)'

    severity = str(raw.get('severity') or '').strip().lower() or None
    if severity not in {'critical', 'major', 'minor'}:
        severity = None

    review_item_id = str(raw.get('review_item_id') or '').strip() or None
    if review_item_id and len(review_item_id) > 100:
        review_item_id = review_item_id[:100]

    return AnnotationOverlayItem(
        annotation_id=str(raw.get('annotation_id') or '').strip() or 'unknown',
        page_number=page_number,
        object_type=_normalize_overlay_object_type(raw.get('object_type')),
        severity=severity,
        review_item_id=review_item_id,
        display_markdown=display_markdown,
        display_text=display_text,
        color=str(raw.get('color') or '').strip() or None,
        rects=rects,
        bounding_rect=bounding_rect,
    )


def _overlay_palette(item: AnnotationOverlayItem) -> OverlayPalette:
    base = _OBJECT_TYPE_PALETTES.get(item.object_type, _OBJECT_TYPE_PALETTES['suggestion'])
    custom = _parse_hex_color(item.color)
    if custom is None:
        return base
    return OverlayPalette(
        stroke=custom,
        fill=base.fill,
        callout_border=custom,
        callout_fill=base.callout_fill,
        label_fill=base.label_fill,
    )


def _normalize_overlay_items(raw_items: list[dict[str, Any]] | None) -> list[AnnotationOverlayItem]:
    if not raw_items:
        return []
    normalized: list[AnnotationOverlayItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = _normalize_overlay_item(raw)
        if item is None:
            continue
        normalized.append(item)
    return normalized


def _to_page_rect(page, rect: dict[str, float]):
    import pymupdf as fitz

    page_rect = page.rect
    width_ref = rect.get('width', 100.0) or 100.0
    height_ref = rect.get('height', 100.0) or 100.0

    x1 = page_rect.x0 + (rect['x1'] / width_ref) * page_rect.width
    y1 = page_rect.y0 + (rect['y1'] / height_ref) * page_rect.height
    x2 = page_rect.x0 + (rect['x2'] / width_ref) * page_rect.width
    y2 = page_rect.y0 + (rect['y2'] / height_ref) * page_rect.height
    return fitz.Rect(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _measure_text_width(
    text: str,
    *,
    font_name: str,
    font_size: float,
) -> float:
    text_value = str(text or '')
    if not text_value:
        return 0.0
    normalized_font_size = max(1.0, float(font_size))

    try:
        import pymupdf as fitz

        measured = float(
            fitz.get_text_length(text_value, fontname=font_name, fontsize=normalized_font_size)
        )
        if measured > 0:
            return measured
    except Exception:
        pass

    fitz_font = _resolve_fitz_measure_font(font_name)
    if fitz_font is not None:
        try:
            measured = float(fitz_font.text_length(text_value, fontsize=normalized_font_size))
            if measured > 0:
                return measured
        except Exception:
            pass

    try:
        measured = float(pdfmetrics.stringWidth(text_value, font_name, normalized_font_size))
        if measured > 0:
            return measured
    except Exception:
        pass

    width = 0.0
    for char in text_value:
        if char.isspace():
            width += normalized_font_size * 0.45
            continue
        if ord(char) > 127:
            width += normalized_font_size * 0.98
            continue
        width += normalized_font_size * 0.56
    if width > 0:
        return width

    try:
        import pymupdf as fitz

        return float(len(text_value)) * normalized_font_size * 0.52
    except Exception:
        return float(len(text_value)) * normalized_font_size * 0.52


def _split_token_by_width(
    token: str,
    *,
    max_width_points: float,
    font_name: str,
    font_size: float,
) -> list[str]:
    if not token:
        return []

    chunks: list[str] = []
    current = ''
    for char in token:
        candidate = f'{current}{char}'
        if _measure_text_width(candidate, font_name=font_name, font_size=font_size) <= max_width_points:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = char
            continue
        chunks.append(char)
        current = ''
    if current:
        chunks.append(current)
    return chunks


def _build_callout_header(
    item: AnnotationOverlayItem,
    *,
    marker: str | None = None,
    part_label: str | None = None,
    continued: bool = False,
) -> str:
    parts: list[str] = []
    if marker:
        parts.append(marker)

    object_label = {
        'issue': 'ISSUE',
        'suggestion': 'SUGGESTION',
        'verification': 'NEEDS VERIFICATION',
    }.get(item.object_type, item.object_type.upper())
    label = object_label
    parts.append(label)
    if item.severity:
        parts.append(item.severity.upper())
    if item.review_item_id:
        parts.append(f'#{item.review_item_id}')
    if part_label:
        parts.append(part_label)
    if continued:
        parts.append('CONT.')
    return ' · '.join(parts)


def _draw_callout(
    page,
    *,
    box_rect,
    header_text: str,
    lines: list[str],
    styled_lines: list[list[OverlayStyledRun]] | None = None,
    palette: OverlayPalette,
    font_name: str,
    mono_font_name: str = 'cour',
) -> None:
    import pymupdf as fitz

    page.draw_rect(
        box_rect,
        color=palette.callout_border,
        fill=palette.callout_fill,
        width=0.9,
        fill_opacity=0.92,
        stroke_opacity=0.95,
        overlay=True,
    )

    label_rect = box_rect + (0, 0, 0, -(box_rect.height - CALLOUT_LABEL_HEIGHT))
    page.draw_rect(
        label_rect,
        color=palette.callout_border,
        fill=palette.label_fill,
        width=0,
        fill_opacity=0.95,
        stroke_opacity=0.95,
        overlay=True,
    )

    page.insert_textbox(
        label_rect + (CALLOUT_TEXT_PADDING, 1, -CALLOUT_TEXT_PADDING, 0),
        header_text,
        fontsize=8.0,
        color=(0.1, 0.1, 0.1),
        fontname=font_name,
        align=0,
        overlay=True,
    )

    text_rect = box_rect + (
        CALLOUT_TEXT_PADDING,
        CALLOUT_LABEL_HEIGHT + 2,
        -CALLOUT_TEXT_PADDING,
        -CALLOUT_TEXT_PADDING,
    )
    if styled_lines is None:
        page.insert_textbox(
            text_rect,
            '\n'.join(lines),
            fontsize=7.6,
            color=(0.08, 0.08, 0.08),
            fontname=font_name,
            align=0,
            overlay=True,
        )
        return

    cursor_y = float(text_rect.y0) + 7.6
    max_y = float(text_rect.y1)
    for line_runs in styled_lines:
        if cursor_y > max_y:
            break
        cursor_x = float(text_rect.x0)
        if not line_runs:
            cursor_y += CALLOUT_LINE_HEIGHT
            continue
        for run in line_runs:
            run_text = str(run.text or '')
            if not run_text:
                continue
            run_font = _overlay_run_font_name(
                run,
                base_font=font_name,
                mono_font=mono_font_name,
            )
            try:
                page.insert_text(
                    fitz.Point(cursor_x, cursor_y),
                    run_text,
                    fontsize=7.6,
                    color=(0.08, 0.08, 0.08),
                    fontname=run_font,
                    overlay=True,
                )
            except Exception:
                page.insert_text(
                    fitz.Point(cursor_x, cursor_y),
                    run_text,
                    fontsize=7.6,
                    color=(0.08, 0.08, 0.08),
                    fontname=font_name,
                    overlay=True,
                )
                run_font = font_name

            run_width = _measure_text_width(
                run_text,
                font_name=run_font,
                font_size=7.6,
            )
            if run.strike and run_text.strip():
                strike_y = cursor_y - (7.6 * 0.32)
                page.draw_line(
                    (cursor_x, strike_y),
                    (cursor_x + run_width, strike_y),
                    color=(0.08, 0.08, 0.08),
                    width=0.72,
                    stroke_opacity=0.95,
                    overlay=True,
                )
            cursor_x += run_width
        cursor_y += CALLOUT_LINE_HEIGHT


def _draw_page_identity_tag(
    page,
    *,
    source_page_number: int,
    continuation_page_no: int | None = None,
) -> None:
    import pymupdf as fitz

    page_rect = page.rect
    margin_x = 10.0
    margin_y = 8.0
    tag_width = 176.0
    tag_height = 16.0
    x1 = page_rect.x1 - margin_x
    x0 = x1 - tag_width
    y0 = page_rect.y0 + margin_y
    y1 = y0 + tag_height
    tag_rect = fitz.Rect(x0, y0, x1, y1)

    label = f'PAGE ID: P{source_page_number:03d}'
    if continuation_page_no is not None:
        label = f'{label}-C{continuation_page_no:02d}'

    page.draw_rect(
        tag_rect,
        color=(0.60, 0.62, 0.64),
        fill=(0.95, 0.95, 0.94),
        width=0.7,
        fill_opacity=0.98,
        stroke_opacity=0.95,
        overlay=True,
    )
    page.insert_textbox(
        tag_rect + (5.0, 2.0, -5.0, -1.0),
        label,
        fontsize=7.8,
        color=(0.20, 0.22, 0.24),
        fontname='helv',
        align=1,
        overlay=True,
    )
    page.insert_text(
        fitz.Point(tag_rect.x0 + 6.0, tag_rect.y1 - 4.0),
        label,
        fontsize=7.6,
        color=(0.20, 0.22, 0.24),
        fontname='helv',
        overlay=True,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _resolve_callout_slots(
    *,
    lane_top: float,
    lane_bottom: float,
    preferred_top: float,
    box_height: float,
    occupied: list[tuple[float, float]],
    gap: float,
) -> float | None:
    available_height = lane_bottom - lane_top
    if available_height <= 0 or box_height > available_height:
        return None

    min_top = lane_top
    max_top = lane_bottom - box_height
    preferred = _clamp(preferred_top, min_top, max_top)

    normalized: list[tuple[float, float]] = []
    for occ_top, occ_bottom in occupied:
        top = _clamp(occ_top, lane_top, lane_bottom)
        bottom = _clamp(occ_bottom, lane_top, lane_bottom)
        if bottom <= top:
            continue
        normalized.append((top, bottom))

    if not normalized:
        return preferred

    normalized.sort(key=lambda pair: pair[0])

    merged: list[list[float]] = []
    for top, bottom in normalized:
        if not merged or top > merged[-1][1]:
            merged.append([top, bottom])
            continue
        merged[-1][1] = max(merged[-1][1], bottom)

    free_ranges: list[tuple[float, float]] = []
    cursor = lane_top
    for top, bottom in merged:
        free_end = top - gap
        if free_end - cursor >= box_height:
            free_ranges.append((cursor, free_end))
        cursor = max(cursor, bottom + gap)

    if lane_bottom - cursor >= box_height:
        free_ranges.append((cursor, lane_bottom))

    if not free_ranges:
        return None

    best_top: float | None = None
    best_distance: float | None = None
    for free_start, free_end in free_ranges:
        candidate = _clamp(preferred, free_start, free_end - box_height)
        distance = abs(candidate - preferred)
        if best_top is None or best_distance is None or distance < best_distance:
            best_top = candidate
            best_distance = distance

    return best_top


def _estimate_callout_box_height(line_count: int) -> float:
    lines = max(1, int(line_count))
    return CALLOUT_BOX_BASE_HEIGHT + CALLOUT_LINE_HEIGHT * lines


def _max_callout_lines_for_height(height: float) -> int:
    available = max(0.0, float(height) - CALLOUT_BOX_BASE_HEIGHT - 8.0)
    return max(0, int(available // CALLOUT_LINE_HEIGHT))


def _find_best_callout_layout(
    *,
    total_lines: int,
    preferred_top: float,
    lane_top: float,
    lane_bottom: float,
    occupied: list[tuple[float, float]],
) -> tuple[int, float | None]:
    if total_lines <= 0:
        return 0, None

    lane_height = lane_bottom - lane_top
    max_lines = min(total_lines, _max_callout_lines_for_height(lane_height))
    if max_lines <= 0:
        return 0, None

    best_lines = 0
    best_top: float | None = None
    low = 1
    high = max_lines

    while low <= high:
        mid = (low + high) // 2
        box_height = _estimate_callout_box_height(mid)
        top = _resolve_callout_slots(
            lane_top=lane_top,
            lane_bottom=lane_bottom,
            preferred_top=preferred_top,
            box_height=box_height,
            occupied=occupied,
            gap=CALLOUT_VERTICAL_GAP,
        )
        if top is not None:
            best_lines = mid
            best_top = top
            low = mid + 1
        else:
            high = mid - 1

    return best_lines, best_top


def _draw_annotation_overlay_on_page(
    page,
    items: list[AnnotationOverlayItem],
    *,
    font_name: str = 'china-s',
    font_path: Path | None = None,
) -> list[AnnotationContinuationItem]:
    if not items:
        return []

    import pymupdf as fitz

    overlay_mono_font_token, overlay_mono_font_path = _resolve_overlay_mono_font_resource()
    overlay_font_name = _ensure_overlay_font(
        page,
        font_name=font_name,
        font_path=font_path,
    )
    overlay_mono_font_name = _ensure_overlay_font(
        page,
        font_name=overlay_mono_font_token,
        font_path=overlay_mono_font_path,
    )

    page_rect = page.rect
    margin_x = 10.0
    margin_y = 10.0
    lane_gap = max(8.0, min(14.0, page_rect.width * 0.012))
    lane_width_base = _clamp(page_rect.width * 0.42, 170.0, 260.0)
    lane_width = _clamp(lane_width_base * 0.9, 155.0, 250.0)
    highlight_max_x = page_rect.x1 - margin_x - lane_width - lane_gap
    if highlight_max_x <= page_rect.x0 + margin_x + 26.0:
        lane_width = _clamp(page_rect.width * 0.36, 140.0, 220.0)
        highlight_max_x = page_rect.x1 - margin_x - lane_width - lane_gap
    right_lane_left = page_rect.x1 - margin_x - lane_width
    callout_max_width_points = max(72.0, lane_width - (2 * CALLOUT_TEXT_PADDING) - 2.0)

    lane_top = page_rect.y0 + margin_y
    lane_bottom = page_rect.y1 - margin_y

    sorted_items = sorted(
        items,
        key=lambda item: (
            item.page_number,
            item.bounding_rect['y1'] if item.bounding_rect else item.rects[0]['y1'],
            item.bounding_rect['x1'] if item.bounding_rect else item.rects[0]['x1'],
        ),
    )

    prepared_items: list[PreparedOverlayRenderable] = []
    for annotation_index, item in enumerate(sorted_items, start=1):
        marker = f'#P{item.page_number:02d}-A{annotation_index:02d}'
        palette = _overlay_palette(item)
        highlight_rects = [_to_page_rect(page, rect) for rect in item.rects]
        highlight_rects = [rect for rect in highlight_rects if rect.get_area() > 0]
        if not highlight_rects:
            continue

        clipped_highlight_rects: list[fitz.Rect] = []
        for rect in highlight_rects:
            clipped = fitz.Rect(
                _clamp(rect.x0, page_rect.x0 + margin_x, highlight_max_x),
                _clamp(rect.y0, page_rect.y0 + margin_y, page_rect.y1 - margin_y),
                _clamp(rect.x1, page_rect.x0 + margin_x, highlight_max_x),
                _clamp(rect.y1, page_rect.y0 + margin_y, page_rect.y1 - margin_y),
            )
            if clipped.x1 <= clipped.x0 or clipped.y1 <= clipped.y0:
                continue
            clipped_highlight_rects.append(clipped)

        if not clipped_highlight_rects:
            continue

        union_rect = clipped_highlight_rects[0]
        for rect in clipped_highlight_rects[1:]:
            union_rect |= rect

        anchor_x = _clamp(union_rect.x1, page_rect.x0 + margin_x, highlight_max_x)
        anchor_y = _clamp((union_rect.y0 + union_rect.y1) / 2.0, lane_top, lane_bottom)

        marker_width = 54.0
        marker_height = 11.0
        marker_x0 = _clamp(union_rect.x1 - marker_width, page_rect.x0 + margin_x, highlight_max_x - marker_width)
        marker_y0 = _clamp(
            union_rect.y0 - marker_height - 1.0,
            page_rect.y0 + margin_y,
            page_rect.y1 - margin_y - marker_height,
        )
        marker_rect = fitz.Rect(
            marker_x0,
            marker_y0,
            marker_x0 + marker_width,
            marker_y0 + marker_height,
        )
        styled_lines = _wrap_overlay_markdown_lines(
            item.display_markdown or item.display_text,
            max_width_points=callout_max_width_points,
            base_font=overlay_font_name,
            mono_font=overlay_mono_font_name,
            font_size=7.6,
        )
        source_target_point = (
            float(_clamp((union_rect.x0 + union_rect.x1) / 2.0, page_rect.x0, page_rect.x1)),
            float(_clamp((union_rect.y0 + union_rect.y1) / 2.0, page_rect.y0, page_rect.y1)),
        )
        prepared_items.append(
            PreparedOverlayRenderable(
                marker=marker,
                item=item,
                palette=palette,
                clipped_highlight_rects=clipped_highlight_rects,
                union_rect=union_rect,
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                marker_rect=marker_rect,
                styled_lines=styled_lines,
                source_target_point=source_target_point,
            )
        )

    if not prepared_items:
        return []

    simulated_occupied: list[tuple[float, float]] = []
    page_overflow_mode = False
    for prepared in prepared_items:
        full_lines_to_draw, full_box_top = _find_best_callout_layout(
            total_lines=len(prepared.styled_lines),
            preferred_top=prepared.anchor_y,
            lane_top=lane_top,
            lane_bottom=lane_bottom,
            occupied=simulated_occupied,
        )
        if full_box_top is None or full_lines_to_draw < len(prepared.styled_lines):
            page_overflow_mode = True
            break
        full_box_height = _estimate_callout_box_height(full_lines_to_draw)
        simulated_occupied.append((full_box_top, full_box_top + full_box_height))
        simulated_occupied.sort(key=lambda pair: pair[0])

    right_occupied: list[tuple[float, float]] = []
    continuation_items: list[AnnotationContinuationItem] = []

    for prepared in prepared_items:
        for clipped in prepared.clipped_highlight_rects:
            page.draw_rect(
                clipped,
                color=prepared.palette.stroke,
                fill=prepared.palette.fill,
                width=0.8,
                stroke_opacity=0.95,
                fill_opacity=0.35,
                overlay=True,
            )

        page.draw_rect(
            prepared.marker_rect,
            color=prepared.palette.callout_border,
            fill=prepared.palette.label_fill,
            width=0.6,
            fill_opacity=0.95,
            stroke_opacity=0.95,
            overlay=True,
        )
        page.insert_textbox(
            prepared.marker_rect + (3.0, 1.2, -3.0, -1.0),
            prepared.marker,
            fontsize=6.8,
            color=(0.16, 0.16, 0.16),
            fontname=overlay_font_name,
            align=0,
            overlay=True,
        )

        if page_overflow_mode:
            index_lines = [
                f'Index ID: {prepared.marker}',
                'Full text moved to continuation pages.',
            ]
            index_lines_to_draw, index_box_top = _find_best_callout_layout(
                total_lines=len(index_lines),
                preferred_top=prepared.anchor_y,
                lane_top=lane_top,
                lane_bottom=lane_bottom,
                occupied=right_occupied,
            )
            link_rect = prepared.marker_rect
            if index_box_top is not None and index_lines_to_draw > 0:
                visible_index_lines = index_lines[:index_lines_to_draw]
                index_box_height = _estimate_callout_box_height(len(visible_index_lines))
                index_box_bottom = index_box_top + index_box_height
                right_occupied.append((index_box_top, index_box_bottom))
                right_occupied.sort(key=lambda pair: pair[0])

                index_box_rect = fitz.Rect(
                    right_lane_left,
                    index_box_top,
                    right_lane_left + lane_width,
                    index_box_bottom,
                )
                page.draw_line(
                    (prepared.anchor_x + 1.0, prepared.anchor_y),
                    (
                        index_box_rect.x0 - 2.0,
                        _clamp(prepared.anchor_y, index_box_rect.y0 + 6.0, index_box_rect.y1 - 6.0),
                    ),
                    color=prepared.palette.callout_border,
                    width=0.8,
                    stroke_opacity=0.9,
                    overlay=True,
                )
                _draw_callout(
                    page,
                    box_rect=index_box_rect,
                    header_text=_build_callout_header(
                        prepared.item,
                        marker=prepared.marker,
                        part_label='INDEX',
                        continued=True,
                    ),
                    lines=visible_index_lines,
                    palette=prepared.palette,
                    font_name=overlay_font_name,
                )
                link_rect = index_box_rect

            continuation_items.append(
                AnnotationContinuationItem(
                    marker=prepared.marker,
                    item=prepared.item,
                    remaining_lines=[list(line) for line in prepared.styled_lines],
                    source_marker_rect=(
                        float(link_rect.x0),
                        float(link_rect.y0),
                        float(link_rect.x1),
                        float(link_rect.y1),
                    ),
                    source_target_point=prepared.source_target_point,
                )
            )
            continue

        full_lines_to_draw, full_box_top = _find_best_callout_layout(
            total_lines=len(prepared.styled_lines),
            preferred_top=prepared.anchor_y,
            lane_top=lane_top,
            lane_bottom=lane_bottom,
            occupied=right_occupied,
        )
        if full_box_top is None or full_lines_to_draw <= 0:
            continuation_items.append(
                AnnotationContinuationItem(
                    marker=prepared.marker,
                    item=prepared.item,
                    remaining_lines=[list(line) for line in prepared.styled_lines],
                    source_marker_rect=(
                        float(prepared.marker_rect.x0),
                        float(prepared.marker_rect.y0),
                        float(prepared.marker_rect.x1),
                        float(prepared.marker_rect.y1),
                    ),
                    source_target_point=prepared.source_target_point,
                )
            )
            continue

        visible_lines = [list(line) for line in prepared.styled_lines[:full_lines_to_draw]]
        remaining_lines = [list(line) for line in prepared.styled_lines[full_lines_to_draw:]]
        box_height = _estimate_callout_box_height(len(visible_lines))
        box_bottom = full_box_top + box_height
        right_occupied.append((full_box_top, box_bottom))
        right_occupied.sort(key=lambda pair: pair[0])

        box_rect = fitz.Rect(
            right_lane_left,
            full_box_top,
            right_lane_left + lane_width,
            box_bottom,
        )

        page.draw_line(
            (prepared.anchor_x + 1.0, prepared.anchor_y),
            (
                box_rect.x0 - 2.0,
                _clamp(prepared.anchor_y, box_rect.y0 + 6.0, box_rect.y1 - 6.0),
            ),
            color=prepared.palette.callout_border,
            width=0.8,
            stroke_opacity=0.9,
            overlay=True,
        )

        _draw_callout(
            page,
            box_rect=box_rect,
            header_text=_build_callout_header(prepared.item, marker=prepared.marker),
            lines=[''.join(run.text for run in line).strip() for line in visible_lines],
            styled_lines=visible_lines,
            palette=prepared.palette,
            font_name=overlay_font_name,
            mono_font_name=overlay_mono_font_name,
        )

        if remaining_lines:
            continuation_items.append(
                AnnotationContinuationItem(
                    marker=prepared.marker,
                    item=prepared.item,
                    remaining_lines=remaining_lines,
                    source_marker_rect=(
                        float(box_rect.x0),
                        float(box_rect.y0),
                        float(box_rect.x1),
                        float(box_rect.y1),
                    ),
                    source_target_point=prepared.source_target_point,
                )
            )

    return continuation_items


def _estimate_continuation_block_height(line_count: int) -> float:
    lines = max(1, int(line_count))
    return 18.0 + 6.0 + (CALLOUT_LINE_HEIGHT * lines) + 8.0


def _max_continuation_lines_for_height(height: float) -> int:
    fixed_height = 18.0 + 6.0 + 8.0
    available = max(0.0, float(height) - fixed_height)
    return max(0, int(available // CALLOUT_LINE_HEIGHT))


def _insert_internal_link(
    page,
    *,
    from_rect,
    target_page_index: int | None,
    target_point: tuple[float, float],
) -> None:
    if target_page_index is None:
        return

    try:
        import pymupdf as fitz

        page.insert_link(
            {
                'kind': fitz.LINK_GOTO,
                'from': from_rect,
                'page': int(target_page_index),
                'to': fitz.Point(float(target_point[0]), float(target_point[1])),
                'zoom': 0.0,
            }
        )
    except Exception as exc:
        logger.debug('Failed to insert internal PDF link: %s', exc)


def _append_annotation_continuation_pages(
    doc,
    *,
    source_page_number: int,
    continuation_items: list[AnnotationContinuationItem],
    insert_after_page_index: int,
    source_page_size: tuple[float, float],
    font_name: str = 'china-s',
    font_path: Path | None = None,
) -> int:
    if not continuation_items:
        return insert_after_page_index

    import pymupdf as fitz

    page_width = float(source_page_size[0]) if source_page_size else float(PAGE_WIDTH)
    page_height = float(source_page_size[1]) if source_page_size else float(PAGE_HEIGHT)
    if page_width <= 0 or page_height <= 0:
        page_width = float(PAGE_WIDTH)
        page_height = float(PAGE_HEIGHT)

    margin_x = _clamp(page_width * 0.022, 10.0, 20.0)
    top_margin = _clamp(page_height * 0.028, 16.0, 30.0)
    bottom_margin = _clamp(page_height * 0.026, 16.0, 28.0)
    content_width = max(120.0, page_width - (2 * margin_x))
    continuation_text_width = max(80.0, content_width - 16.0)
    overlay_mono_font_token, overlay_mono_font_path = _resolve_overlay_mono_font_resource()
    overlay_mono_font_name = overlay_mono_font_token

    pending_items: list[AnnotationContinuationItem] = []
    for continuation_item in continuation_items:
        continuation_item.remaining_lines = _wrap_overlay_markdown_lines(
            continuation_item.item.display_markdown or continuation_item.item.display_text,
            max_width_points=continuation_text_width,
            base_font=font_name,
            mono_font=overlay_mono_font_name,
            font_size=8.0,
        )
        continuation_item.next_part_index = 1
        continuation_item.first_continuation_page_index = None
        continuation_item.first_continuation_rect = None
        if continuation_item.remaining_lines:
            pending_items.append(continuation_item)
    continuation_page_no = 0

    while pending_items:
        continuation_page_no += 1
        page = doc.new_page(
            pno=insert_after_page_index + 1,
            width=page_width,
            height=page_height,
        )
        insert_after_page_index += 1
        page_rect = page.rect
        overlay_font_name = _ensure_overlay_font(
            page,
            font_name=font_name,
            font_path=font_path,
        )
        overlay_mono_font_name = _ensure_overlay_font(
            page,
            font_name=overlay_mono_font_token,
            font_path=overlay_mono_font_path,
        )

        banner_height = _clamp(page_height * 0.06, 34.0, 46.0)
        banner_rect = fitz.Rect(
            margin_x,
            top_margin,
            page_rect.x1 - margin_x,
            top_margin + banner_height,
        )
        page.draw_rect(
            banner_rect,
            color=(0.64, 0.66, 0.69),
            fill=(0.92, 0.92, 0.91),
            width=0.9,
            fill_opacity=0.94,
            stroke_opacity=0.95,
            overlay=True,
        )

        title = f'Annotation Continuation · Source Page {source_page_number}'
        if continuation_page_no > 1:
            title = f'{title} (cont. {continuation_page_no})'
        page.insert_text(
            fitz.Point(banner_rect.x0 + 8.0, banner_rect.y0 + 14.0),
            title,
            fontsize=11.0,
            color=(0.20, 0.22, 0.24),
            fontname=overlay_font_name,
            overlay=True,
        )
        page.insert_textbox(
            banner_rect + (8.0, 18.0, -8.0, -2.0),
            'Continuation sheet linked to source highlights. Click marker headers to jump back to the paper region.',
            fontsize=8.0,
            color=(0.42, 0.44, 0.46),
            fontname=overlay_font_name,
            align=0,
            overlay=True,
        )

        cursor_y = banner_rect.y1 + 10.0
        rendered_any = False
        next_pending_items: list[AnnotationContinuationItem] = []

        for continuation_item in pending_items:
            if not continuation_item.remaining_lines:
                continue

            available_height = page_height - bottom_margin - cursor_y
            fittable_lines = _max_continuation_lines_for_height(available_height)
            if fittable_lines <= 0:
                next_pending_items.append(continuation_item)
                continue

            line_runs = continuation_item.remaining_lines[:fittable_lines]
            continuation_item.remaining_lines = continuation_item.remaining_lines[fittable_lines:]
            if not line_runs:
                next_pending_items.append(continuation_item)
                continue
            rendered_any = True

            block_height = _estimate_continuation_block_height(len(line_runs))
            block_rect = fitz.Rect(
                margin_x,
                cursor_y,
                margin_x + content_width,
                cursor_y + block_height,
            )
            label_rect = fitz.Rect(
                block_rect.x0,
                block_rect.y0,
                block_rect.x1,
                block_rect.y0 + 18.0,
            )

            palette = _overlay_palette(continuation_item.item)
            page.draw_rect(
                block_rect,
                color=palette.callout_border,
                fill=palette.callout_fill,
                width=0.85,
                fill_opacity=0.9,
                stroke_opacity=0.95,
                overlay=True,
            )
            page.draw_rect(
                label_rect,
                color=palette.callout_border,
                fill=palette.label_fill,
                width=0,
                fill_opacity=0.95,
                stroke_opacity=0.95,
                overlay=True,
            )

            part_label = f'Part {continuation_item.next_part_index}'
            continuation_item.next_part_index += 1
            header_text = _build_callout_header(
                continuation_item.item,
                marker=continuation_item.marker,
                part_label=part_label,
                continued=bool(continuation_item.remaining_lines),
            )
            page.insert_textbox(
                label_rect + (6.0, 2.0, -6.0, -1.0),
                header_text,
                fontsize=8.2,
                color=(0.08, 0.08, 0.08),
                fontname=overlay_font_name,
                align=0,
                overlay=True,
            )

            body_y = label_rect.y1 + 8.0
            max_body_y = block_rect.y1 - 5.0
            for runs in line_runs:
                if body_y > max_body_y:
                    break
                body_x = block_rect.x0 + 8.0
                if not runs:
                    body_y += CALLOUT_LINE_HEIGHT
                    continue
                for run in runs:
                    run_text = str(run.text or '')
                    if not run_text:
                        continue
                    run_font = _overlay_run_font_name(
                        run,
                        base_font=overlay_font_name,
                        mono_font=overlay_mono_font_name,
                    )
                    try:
                        page.insert_text(
                            fitz.Point(body_x, body_y),
                            run_text,
                            fontsize=8.0,
                            color=(0.07, 0.07, 0.07),
                            fontname=run_font,
                            overlay=True,
                        )
                    except Exception:
                        page.insert_text(
                            fitz.Point(body_x, body_y),
                            run_text,
                            fontsize=8.0,
                            color=(0.07, 0.07, 0.07),
                            fontname=overlay_font_name,
                            overlay=True,
                        )
                        run_font = overlay_font_name

                    run_width = _measure_text_width(
                        run_text,
                        font_name=run_font,
                        font_size=8.0,
                    )
                    if run.strike and run_text.strip():
                        strike_y = body_y - (8.0 * 0.32)
                        page.draw_line(
                            (body_x, strike_y),
                            (body_x + run_width, strike_y),
                            color=(0.07, 0.07, 0.07),
                            width=0.76,
                            stroke_opacity=0.95,
                            overlay=True,
                        )
                    body_x += run_width
                body_y += CALLOUT_LINE_HEIGHT

            _insert_internal_link(
                page=page,
                from_rect=label_rect,
                target_page_index=continuation_item.source_output_page_index,
                target_point=continuation_item.source_target_point,
            )

            if continuation_item.first_continuation_page_index is None:
                continuation_item.first_continuation_page_index = page.number
                continuation_item.first_continuation_rect = (
                    float(label_rect.x0),
                    float(label_rect.y0),
                    float(label_rect.x1),
                    float(label_rect.y1),
                )
                if continuation_item.source_output_page_index is not None:
                    try:
                        source_page = doc.load_page(continuation_item.source_output_page_index)
                        source_link_rect = fitz.Rect(*continuation_item.source_marker_rect)
                        _insert_internal_link(
                            source_page,
                            from_rect=source_link_rect,
                            target_page_index=page.number,
                            target_point=(float(label_rect.x0 + 4.0), float(label_rect.y0 + 4.0)),
                        )
                    except Exception as exc:
                        logger.debug(
                            'Failed to bind source-to-continuation link for %s: %s',
                            continuation_item.marker,
                            exc,
                        )

            cursor_y = block_rect.y1 + 10.0
            if continuation_item.remaining_lines:
                next_pending_items.append(continuation_item)

        if not rendered_any:
            logger.warning(
                'Failed to layout annotation continuation content for source page %s; stopping append loop.',
                source_page_number,
            )
            break

        _draw_page_identity_tag(
            page,
            source_page_number=source_page_number,
            continuation_page_no=continuation_page_no,
        )

        pending_items = next_pending_items

    return insert_after_page_index


def _render_annotated_source_pdf(
    source_pdf_bytes: bytes,
    source_annotations: list[dict[str, Any]],
) -> bytes | None:
    if not source_pdf_bytes or not source_annotations:
        return None

    try:
        import pymupdf as fitz
    except Exception as exc:
        logger.warning('PyMuPDF unavailable for annotation overlay: %s', exc)
        return None

    overlay_items = _normalize_overlay_items(source_annotations)
    if not overlay_items:
        return None
    overlay_font_name, overlay_font_path = _resolve_overlay_font_resource()

    grouped: dict[int, list[AnnotationOverlayItem]] = defaultdict(list)
    for item in overlay_items:
        grouped[item.page_number].append(item)

    source_doc = None
    output_doc = None
    try:
        source_doc = fitz.open(stream=source_pdf_bytes, filetype='pdf')
        if source_doc.is_encrypted:
            authenticated = False
            try:
                authenticated = bool(source_doc.authenticate(''))
            except Exception:
                authenticated = False
            if not authenticated:
                logger.warning('Source PDF encrypted; skip annotation overlay rendering.')
                return None

        output_doc = fitz.open()

        total_pages = source_doc.page_count
        for source_page_index in range(total_pages):
            source_page_number = source_page_index + 1
            output_doc.insert_pdf(
                source_doc,
                from_page=source_page_index,
                to_page=source_page_index,
            )
            output_page_index = output_doc.page_count - 1
            output_page = output_doc.load_page(output_page_index)
            page_items = grouped.get(source_page_number, [])
            continuation_items = _draw_annotation_overlay_on_page(
                output_page,
                page_items,
                font_name=overlay_font_name,
                font_path=overlay_font_path,
            )
            _draw_page_identity_tag(
                output_page,
                source_page_number=source_page_number,
            )
            source_page_size = (float(output_page.rect.width), float(output_page.rect.height))
            for continuation_item in continuation_items:
                continuation_item.source_output_page_index = output_page_index
            output_page_index = _append_annotation_continuation_pages(
                output_doc,
                source_page_number=source_page_number,
                continuation_items=continuation_items,
                insert_after_page_index=output_page_index,
                source_page_size=source_page_size,
                font_name=overlay_font_name,
                font_path=overlay_font_path,
            )

        return output_doc.tobytes(garbage=3, deflate=True)
    except Exception as exc:
        logger.warning('Failed to render annotated source PDF: %s', exc)
        return None
    finally:
        if output_doc is not None:
            output_doc.close()
        if source_doc is not None:
            source_doc.close()


def _merge_report_with_source_pdf_pages(report_pdf_bytes: bytes, source_pdf_bytes: bytes | None) -> bytes:
    if not source_pdf_bytes:
        return report_pdf_bytes

    merged = _merge_with_pypdf(report_pdf_bytes, source_pdf_bytes)
    if merged:
        return merged

    merged = _merge_with_pymupdf(report_pdf_bytes, source_pdf_bytes)
    if merged:
        return merged

    return report_pdf_bytes


def _merge_report_with_annotated_source_pdf_pages(
    report_pdf_bytes: bytes,
    *,
    source_pdf_bytes: bytes | None,
    source_annotations: list[dict[str, Any]] | None,
) -> bytes:
    if not source_pdf_bytes:
        return report_pdf_bytes

    annotated_source_pdf_bytes: bytes | None = None
    if source_annotations:
        annotated_source_pdf_bytes = _render_annotated_source_pdf(
            source_pdf_bytes,
            source_annotations,
        )

    return _merge_report_with_source_pdf_pages(
        report_pdf_bytes,
        annotated_source_pdf_bytes or source_pdf_bytes,
    )



def _draw_header_footer(
    canvas,
    doc,
    *,
    fonts: ReportFonts,
    logo_path: Path | None,
    document_no: str,
) -> None:
    canvas.saveState()

    top_line_y = PAGE_HEIGHT - 14 * mm
    bottom_line_y = 13.5 * mm

    canvas.setStrokeColor(colors.HexColor('#D1D5DB'))
    canvas.setLineWidth(0.7)
    canvas.line(doc.leftMargin, top_line_y, PAGE_WIDTH - doc.rightMargin, top_line_y)
    canvas.line(doc.leftMargin, bottom_line_y, PAGE_WIDTH - doc.rightMargin, bottom_line_y)

    content_top_y = top_line_y + 2.2 * mm
    cursor_x = doc.leftMargin

    if logo_path is not None:
        try:
            canvas.drawImage(
                str(logo_path),
                cursor_x,
                content_top_y,
                width=7 * mm,
                height=7 * mm,
                preserveAspectRatio=True,
                mask='auto',
            )
            cursor_x += 9.2 * mm
        except Exception as exc:
            logger.warning('Failed to draw review PDF logo from %s: %s', logo_path, exc)

    canvas.setFillColor(colors.HexColor('#B91C1C'))
    _safe_canvas_font(canvas, fonts.heading, 9.2)
    canvas.drawString(cursor_x, content_top_y + 2.6, 'DeepScientist')

    right_x = PAGE_WIDTH - doc.rightMargin
    canvas.setFillColor(colors.HexColor('#111827'))
    _safe_canvas_font(canvas, fonts.body, 8.3)
    canvas.drawRightString(right_x, content_top_y + 2.6, f'Document No. {document_no}')

    footer_y = 8.5 * mm
    canvas.setFillColor(colors.HexColor('#6B7280'))
    _safe_canvas_font(canvas, fonts.body, 7.8)
    canvas.drawString(doc.leftMargin, footer_y, 'Generated by DeepScientist · Structured AI Review Report')
    canvas.drawRightString(right_x, footer_y, f'Page {canvas.getPageNumber()}')

    canvas.restoreState()


def build_review_report_pdf(
    *,
    workspace_title: str,
    source_pdf_name: str,
    run_id: str,
    status: str,
    decision: str | None,
    estimated_cost: int,
    actual_cost: int | None,
    exported_at: datetime,
    meta_review: dict[str, Any],
    reviewers: list[dict[str, Any]],
    raw_output: str | None,
    final_report_markdown: str | None = None,
    source_pdf_bytes: bytes | None = None,
    source_annotations: list[dict[str, Any]] | None = None,
    review_display_id: str | None = None,
    owner_email: str | None = None,
    token_usage: dict[str, Any] | None = None,
    agent_model: str | None = None,
) -> bytes:
    fonts = _resolve_report_fonts()
    logo_path = _resolve_logo_path()
    styles = _build_styles(fonts)
    meta_review = meta_review if isinstance(meta_review, dict) else {}

    token_payload = token_usage if isinstance(token_usage, dict) else {}
    token_requests = max(0, int(token_payload.get('requests') or 0))
    token_input = max(0, int(token_payload.get('input_tokens') or 0))
    token_output = max(0, int(token_payload.get('output_tokens') or 0))
    token_total = max(0, int(token_payload.get('total_tokens') or 0))
    if token_total <= 0:
        token_total = max(0, token_input + token_output)

    document_no_display = str(run_id or '').strip() or '-'
    report_code = str(review_display_id or '').strip().upper()
    if not re.fullmatch(r'DS-RV-[0-9A-Z]{9}', report_code):
        fallback_token = re.sub(r'[^0-9A-Z]', '', str(run_id).upper())
        if not fallback_token:
            fallback_token = 'UNKN'
        report_code = f"DS-RV-{fallback_token[:9].ljust(9, 'X')}"
    model_display = str(agent_model or '').strip() or '-'

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=24 * mm,
        bottomMargin=20 * mm,
        title='DeepScientist AI Review Report',
        author='DeepScientist',
        subject='Structured review report',
    )

    story: list = []

    # Cover page
    story.append(Spacer(1, 20 * mm))
    if logo_path is not None:
        try:
            story.append(Image(str(logo_path), width=30 * mm, height=30 * mm, kind='proportional'))
            story.append(Spacer(1, 6 * mm))
        except Exception as exc:
            logger.warning('Failed to render cover logo for review PDF: %s', exc)

    story.append(Paragraph('DeepScientist', styles['CoverBrand']))
    story.append(Paragraph('AI REVIEW REPORT', styles['CoverTitle']))
    story.append(Paragraph(_escape(workspace_title or 'Review Workspace'), styles['CoverWorkspaceTitle']))
    story.append(Paragraph(f'Source file: {_escape(source_pdf_name or "-")}', styles['CoverMeta']))
    story.append(Spacer(1, 9 * mm))

    cover_table_data = [
        [
            Paragraph('<b>Document No.</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape(document_no_display), styles['BodyTextEnterprise']),
            Paragraph('<b>Work ID</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape(report_code), styles['BodyTextEnterprise']),
        ],
        [
            Paragraph('<b>Status</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape((status or 'unknown').title()), styles['BodyTextEnterprise']),
            Paragraph('<b>Agent Model</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape(model_display), styles['BodyTextEnterprise']),
        ],
        [
            Paragraph('<b>Token Usage</b>', styles['BodyTextEnterprise']),
            Paragraph(
                _escape(f'Input {token_input} | Output {token_output} | Total {token_total}'),
                styles['BodyTextEnterprise'],
            ),
            Paragraph('<b>LLM Requests</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape(str(token_requests)), styles['BodyTextEnterprise']),
        ],
        [
            Paragraph('<b>Generated At</b>', styles['BodyTextEnterprise']),
            Paragraph(_escape(_format_datetime(exported_at)), styles['BodyTextEnterprise']),
            Paragraph('<b>Producer</b>', styles['BodyTextEnterprise']),
            Paragraph('DeepReviewer 2.0', styles['BodyTextEnterprise']),
        ],
    ]

    cover_table = Table(
        cover_table_data,
        colWidths=[30 * mm, 56 * mm, 30 * mm, 54 * mm],
        hAlign='CENTER',
    )
    cover_table.setStyle(
        TableStyle(
            [
                ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#CBD5E1')),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F8FAFC')),
                ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#FCFCFD')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 4.5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
            ]
        )
    )
    story.append(cover_table)
    story.append(Spacer(1, 10 * mm))

    story.append(
        Paragraph(
            'This document is generated for professional review, archival, and collaborative decision making. '
            'All conclusions should be interpreted with domain expertise and final human verification.',
            styles['SmallMutedText'],
        )
    )
    story.append(PageBreak())

    final_report_text = str(final_report_markdown or '').strip()
    if final_report_text:
        _append_section_header(
            story,
            styles,
            title='Final Agent Report',
            subtitle='Structured markdown synthesized by the annotation agent.',
        )
        _append_markdown_report(story, styles, markdown=final_report_text)

    if source_pdf_bytes:
        story.append(PageBreak())
        _append_section_header(
            story,
            styles,
            title='Appendix · Original Paper PDF',
            subtitle='The following pages preserve source layout and include review annotations when available.',
        )
        story.append(
            Paragraph(
                'The appendix below attaches source paper pages and overlays review highlights for ISSUE / SUGGESTION / EVIDENCE objects.',
                styles['BodyTextEnterprise'],
            )
        )
        story.append(
            Paragraph(
                'Callout boxes are placed near page margins to avoid covering core content and remain within page boundaries.',
                styles['SmallMutedText'],
            )
        )

    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            'End of report · DeepScientist Review Export',
            styles['SmallMutedText'],
        )
    )

    def _on_page(canvas, doc):
        canvas.setProducer('DeepReviewer 2.0')
        _draw_header_footer(
            canvas,
            doc,
            fonts=fonts,
            logo_path=logo_path,
            document_no=document_no_display,
        )

    document.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    report_pdf_bytes = buffer.getvalue()
    return _merge_report_with_annotated_source_pdf_pages(
        report_pdf_bytes,
        source_pdf_bytes=source_pdf_bytes,
        source_annotations=source_annotations,
    )
