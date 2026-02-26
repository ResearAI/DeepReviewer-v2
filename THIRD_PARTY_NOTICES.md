# Third-Party Notices

This repository includes or references third-party components.

## Included Source Code

1. PASA-derived modules under `pasa/pasa/` and `pasa/legacy/`
   - Upstream: https://github.com/bytedance/pasa
   - License: Apache License 2.0
   - Evidence: file headers in `pasa/pasa/utils.py` and related modules

## Bundled Font Assets

1. `assets/fonts/NotoSerifSC-Regular-C94HN_ZN.ttf`
   - Family: Noto Serif SC
   - Upstream: https://github.com/notofonts/noto-cjk/tree/main/google-fonts
   - License: SIL Open Font License 1.1
   - License text source: https://github.com/notofonts/noto-cjk/blob/main/Serif/LICENSE
   - Notes: OFL allows redistribution/bundling with software under OFL conditions.

2. `assets/fonts/Satoshi-Medium-ByP-Zb-9.woff2` / `assets/fonts/Satoshi-Medium-ByP-Zb-9.woff3`
   - Family: Satoshi
   - Publisher: Indian Type Foundry (Fontshare)
   - Font metadata license URL: https://fontshare.com/terms
   - Fontshare API record (license type): https://api.fontshare.com/v2/fonts/20e9fcdc-1e41-4559-a43d-1ede0adc8896
   - License type in upstream metadata: `itf_ffl`
   - Notes: This is not marked as SIL OFL in upstream metadata. Distribution/redistribution must comply with ITF Fontshare terms (ITF FFL). If compliance is unclear for your target distribution channel, remove these binaries before release and require end users to obtain them from Fontshare.

## Runtime Dependencies (installed via `pip`)

Dependencies are declared in `pyproject.toml` and are governed by their own licenses.
Examples include `openai`, `openai-agents`, `reportlab`, `pymupdf`, `fonttools`, and others.
