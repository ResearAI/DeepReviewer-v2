from __future__ import annotations

from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def _contains_cjk(text: str) -> bool:
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            return True
    return False


def _pick_font(markdown_text: str, preferred: str) -> str:
    if _contains_cjk(markdown_text):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
            return 'STSong-Light'
        except Exception:
            return preferred
    return preferred


def _flush_bullets(story: list, bullets: list[str], bullet_style: ParagraphStyle) -> None:
    if not bullets:
        return
    for item in bullets:
        story.append(Paragraph('• ' + escape(item), bullet_style))
    story.append(Spacer(1, 2 * mm))
    bullets.clear()


def markdown_to_pdf(
    *,
    markdown_text: str,
    output_path: Path,
    font_name: str = 'Helvetica',
    title_font_size: int = 15,
    body_font_size: int = 10,
    margin: int = 48,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font = _pick_font(markdown_text, font_name)
    styles = getSampleStyleSheet()

    normal = ParagraphStyle(
        'DRBody',
        parent=styles['Normal'],
        fontName=font,
        fontSize=body_font_size,
        leading=max(13, int(body_font_size * 1.45)),
        spaceAfter=4,
    )
    h1 = ParagraphStyle(
        'DRH1',
        parent=styles['Heading1'],
        fontName=font,
        fontSize=title_font_size,
        leading=max(18, int(title_font_size * 1.35)),
        spaceBefore=8,
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        'DRH2',
        parent=styles['Heading2'],
        fontName=font,
        fontSize=max(12, int(title_font_size * 0.82)),
        leading=max(14, int(title_font_size * 1.1)),
        spaceBefore=6,
        spaceAfter=4,
    )
    h3 = ParagraphStyle(
        'DRH3',
        parent=styles['Heading3'],
        fontName=font,
        fontSize=max(11, int(title_font_size * 0.72)),
        leading=max(13, int(title_font_size * 1.0)),
        spaceBefore=5,
        spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        'DRBullet',
        parent=normal,
        leftIndent=12,
        firstLineIndent=0,
        spaceAfter=2,
    )

    story: list = []
    bullets: list[str] = []

    lines = markdown_text.splitlines()
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            _flush_bullets(story, bullets, bullet_style)
            story.append(Spacer(1, 2 * mm))
            continue

        if stripped.startswith('# '):
            _flush_bullets(story, bullets, bullet_style)
            story.append(Paragraph(escape(stripped[2:].strip()), h1))
            continue

        if stripped.startswith('## '):
            _flush_bullets(story, bullets, bullet_style)
            story.append(Paragraph(escape(stripped[3:].strip()), h2))
            continue

        if stripped.startswith('### '):
            _flush_bullets(story, bullets, bullet_style)
            story.append(Paragraph(escape(stripped[4:].strip()), h3))
            continue

        if stripped.startswith('- ') or stripped.startswith('* '):
            bullets.append(stripped[2:].strip())
            continue

        _flush_bullets(story, bullets, bullet_style)
        story.append(Paragraph(escape(stripped), normal))

    _flush_bullets(story, bullets, bullet_style)

    if not story:
        story.append(Paragraph('Empty report', normal))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title='DeepReviewer Final Report',
    )
    doc.build(story)


def markdown_file_to_pdf(
    *,
    markdown_path: Path,
    output_path: Path,
    font_name: str = 'Helvetica',
    title_font_size: int = 15,
    body_font_size: int = 10,
    margin: int = 48,
) -> None:
    markdown_text = markdown_path.read_text(encoding='utf-8')
    markdown_to_pdf(
        markdown_text=markdown_text,
        output_path=output_path,
        font_name=font_name,
        title_font_size=title_font_size,
        body_font_size=body_font_size,
        margin=margin,
    )
