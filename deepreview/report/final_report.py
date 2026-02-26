from __future__ import annotations

import re
from dataclasses import dataclass


_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?")
_CHINESE_CHAR_PATTERN = re.compile(r'[\u4e00-\u9fff]')
_MARKDOWN_CODE_FENCE_PATTERN = re.compile(r'```[\s\S]*?```')
_INLINE_CODE_PATTERN = re.compile(r'`[^`\n]+`')
_MARKDOWN_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')

_REQUIRED_SECTION_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ('Summary', ('summary', '摘要', '总结')),
    ('Strengths', ('strengths', '优点', '优势')),
    ('Weaknesses', ('weaknesses', '缺点', '问题')),
    ('Key Issues', ('key issues', '核心问题', '关键问题')),
    ('Actionable Suggestions', ('actionable suggestions', '建议', '可执行建议')),
    (
        'Storyline Options + Writing Outlines',
        ('storyline options', 'writing outlines', '叙事方案', '写作提纲'),
    ),
    ('Priority Revision Plan', ('priority revision plan', '修订计划', '优先级修订计划')),
    (
        'Experiment Inventory & Research Experiment Plan',
        ('experiment inventory', 'research experiment plan', '实验清单', '研究实验计划'),
    ),
    (
        'Novelty Verification & Related-Work Matrix',
        ('novelty verification', 'related-work matrix', '新颖性验证', '相关工作矩阵'),
    ),
    ('References', ('references', 'reference', '参考文献')),
    ('Scores', ('scores', 'final score', '评分', '最终评分')),
]


@dataclass
class LanguageStats:
    primary_language: str
    english_words: int
    chinese_chars: int
    english_ratio: float
    chinese_ratio: float


@dataclass
class FinalReportValidation:
    ok: bool
    reason: str | None
    message: str
    language_stats: LanguageStats
    missing_sections: list[str]


def _extract_markdown_headings(markdown_text: str) -> list[str]:
    headings: list[str] = []
    for line in str(markdown_text or '').splitlines():
        stripped = line.strip()
        if not stripped.startswith('#'):
            continue
        text = stripped.lstrip('#').strip().lower()
        if text:
            headings.append(text)
    return headings


def find_missing_required_sections(markdown_text: str) -> list[str]:
    headings = _extract_markdown_headings(markdown_text)
    if not headings:
        return [label for label, _ in _REQUIRED_SECTION_GROUPS]

    missing: list[str] = []
    for label, aliases in _REQUIRED_SECTION_GROUPS:
        if not any(any(alias in heading for heading in headings) for alias in aliases):
            missing.append(label)
    return missing


def _sanitize_markdown_for_length_count(text: str) -> str:
    normalized = str(text or '')
    if not normalized:
        return ''
    normalized = _MARKDOWN_CODE_FENCE_PATTERN.sub(' ', normalized)
    normalized = _INLINE_CODE_PATTERN.sub(' ', normalized)
    normalized = _MARKDOWN_LINK_PATTERN.sub(r'\1', normalized)
    normalized = _URL_PATTERN.sub(' ', normalized)
    normalized = normalized.replace('|', ' ')
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def analyze_report_language(text: str) -> LanguageStats:
    cleaned = _sanitize_markdown_for_length_count(text)
    english_words = len(_ENGLISH_WORD_PATTERN.findall(cleaned))
    chinese_chars = len(_CHINESE_CHAR_PATTERN.findall(cleaned))

    total_units = english_words + chinese_chars
    if total_units <= 0:
        return LanguageStats(
            primary_language='en',
            english_words=english_words,
            chinese_chars=chinese_chars,
            english_ratio=0.0,
            chinese_ratio=0.0,
        )

    chinese_ratio = chinese_chars / total_units
    english_ratio = english_words / total_units
    primary = 'zh-CN' if chinese_ratio > 0.5 else 'en'
    return LanguageStats(
        primary_language=primary,
        english_words=english_words,
        chinese_chars=chinese_chars,
        english_ratio=english_ratio,
        chinese_ratio=chinese_ratio,
    )


def validate_final_report(
    *,
    markdown: str,
    min_english_words: int,
    min_chinese_chars: int,
    force_english_output: bool = True,
) -> FinalReportValidation:
    text = str(markdown or '').strip()
    if not text:
        return FinalReportValidation(
            ok=False,
            reason='markdown_required',
            message='Final report markdown is empty.',
            language_stats=analyze_report_language(''),
            missing_sections=[label for label, _ in _REQUIRED_SECTION_GROUPS],
        )

    missing_sections = find_missing_required_sections(text)
    stats = analyze_report_language(text)

    if force_english_output and stats.chinese_chars > 0:
        return FinalReportValidation(
            ok=False,
            reason='english_required',
            message='Final report must be written in English only for this deployment.',
            language_stats=stats,
            missing_sections=[],
        )

    if missing_sections:
        return FinalReportValidation(
            ok=False,
            reason='final_report_sections_not_met',
            message='Final report missing required sections: ' + ', '.join(missing_sections),
            language_stats=stats,
            missing_sections=missing_sections,
        )

    if min_english_words > 0 and stats.primary_language == 'en' and stats.english_words < min_english_words:
        return FinalReportValidation(
            ok=False,
            reason='final_report_length_not_met',
            message=(
                f'English report is too short: {stats.english_words} words, '
                f'required >= {min_english_words}.'
            ),
            language_stats=stats,
            missing_sections=[],
        )

    if min_chinese_chars > 0 and stats.primary_language == 'zh-CN' and stats.chinese_chars < min_chinese_chars:
        return FinalReportValidation(
            ok=False,
            reason='final_report_length_not_met',
            message=(
                f'Chinese report is too short: {stats.chinese_chars} chars, '
                f'required >= {min_chinese_chars}.'
            ),
            language_stats=stats,
            missing_sections=[],
        )

    return FinalReportValidation(
        ok=True,
        reason=None,
        message='ok',
        language_stats=stats,
        missing_sections=[],
    )
