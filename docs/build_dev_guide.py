"""Renders docs/DEVELOPER_GUIDE.md into docs/DEVELOPER_GUIDE.pdf.

Pipeline: python-markdown -> styled HTML -> Chromium print-to-PDF (via
Playwright), which gives real typography, syntax-highlighted-ish code
blocks, page numbers, and proper page breaks with ~60 lines of CSS.

Requirements (dev-only, not part of the app):
    pip install markdown playwright
    playwright install chromium        # or set CHROMIUM_PATH to an existing
                                       # Chromium/Chrome executable

Usage:
    python docs/build_dev_guide.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
SOURCE = DOCS / "DEVELOPER_GUIDE.md"
OUTPUT = DOCS / "DEVELOPER_GUIDE.pdf"

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root {{ --ink:#1c2430; --dim:#5b6673; --accent:#1d4ed8; --line:#d7dce2; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI", -apple-system, Helvetica, Arial, sans-serif;
    color: var(--ink); font-size: 10.5pt; line-height: 1.55; margin: 0;
  }}
  h1 {{ font-size: 21pt; margin: 0 0 4pt; letter-spacing: -.3pt; }}
  h1 + p em {{ color: var(--dim); }}
  h2 {{
    font-size: 14pt; color: var(--accent); margin: 22pt 0 8pt;
    padding-top: 6pt; border-top: 1.5pt solid var(--line);
    page-break-after: avoid;
  }}
  h3 {{ font-size: 11.5pt; margin: 14pt 0 5pt; page-break-after: avoid; }}
  h4 {{ font-size: 10.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }}
  p {{ margin: 5pt 0; }}
  li {{ margin: 2.5pt 0; }}
  strong {{ color: #10192a; }}
  code {{
    font-family: Consolas, "Courier New", monospace; font-size: 8.8pt;
    background: #f0f2f6; border-radius: 3pt; padding: .5pt 3pt;
  }}
  pre {{
    background: #f5f7fa; border: .75pt solid var(--line); border-radius: 5pt;
    padding: 8pt 10pt; overflow: hidden; page-break-inside: avoid;
    line-height: 1.4;
  }}
  pre code {{ background: none; padding: 0; font-size: 8.3pt; }}
  table {{
    border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.3pt;
    page-break-inside: avoid;
  }}
  th {{ text-align: left; background: #eef1f6; }}
  th, td {{ border: .75pt solid var(--line); padding: 4pt 7pt; vertical-align: top; }}
  hr {{ border: none; border-top: .75pt solid var(--line); margin: 14pt 0; }}
  blockquote {{ margin: 6pt 0; padding: 2pt 10pt; border-left: 2.5pt solid var(--accent); color: var(--dim); }}
</style></head><body>{body}</body></html>"""

FOOTER = """<div style="width:100%; font-size:7.5pt; color:#5b6673;
  font-family:'Segoe UI',Helvetica,sans-serif; padding:0 0.55in; display:flex;">
  <span>Medical Concierge &mdash; Developer Guide</span>
  <span style="margin-left:auto;">Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
</div>"""


async def render(html_path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH") or None
        )
        page = await browser.new_page()
        await page.goto(html_path.as_uri())
        await page.pdf(
            path=str(OUTPUT),
            format="Letter",
            margin={"top": "0.7in", "bottom": "0.8in", "left": "0.75in", "right": "0.75in"},
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=FOOTER,
            print_background=True,
        )
        await browser.close()


def main() -> None:
    body = markdown.markdown(
        SOURCE.read_text(encoding="utf-8"),
        extensions=["fenced_code", "tables"],
    )
    html_path = OUTPUT.with_suffix(".build.html")
    html_path.write_text(TEMPLATE.format(body=body), encoding="utf-8")
    try:
        asyncio.run(render(html_path))
    finally:
        html_path.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
