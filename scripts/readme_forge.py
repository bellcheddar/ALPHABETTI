#!/usr/bin/env python3
"""
readme_forge.py  -  Marc Deller's README forge.

One pass, two jobs:

  1. POLISH   crude  README.md  ->  <name>.polished.md
     - branded header: optional emoji H1, tagline, shields badges, contact table
     - section-heading emoji (keyword map)
     - standard "Author" footer block
     - GitHub links rewritten to the specific repo
     - rel="noopener noreferrer" added to target="_blank" links

  2. CONVERT  polished markdown  ->  <name>.html            (Elementor HTML widget)
                                     <name>.preview.html     (open locally to check)
                                     elementor-widget-markdown.css  (widget Custom CSS)

The mechanical work is automatic.  The two things a script can't invent - the
one-line TAGLINE and the "why it matters" INTRO paragraph - are read from an
optional  readme_meta.json  (keyed by filename); where absent, the source is
preserved and an HTML-comment placeholder is left so you can see where to type.

  readme_meta.json  (all keys optional):
  {
    "site-doctor.md": {
      "repo": "site-doctor",
      "emoji": "🩺",
      "title": "Site Doctor",
      "tagline": "One pass. Full diagnosis.",
      "why": "Why it matters: ... It is useful for: ...",
      "live": "site-doctor.mdeller.com",
      "badges": [["python","3.8+","3776AB","python"], ["dependencies","zero","00897B",""]]
    }
  }

  "live" is the deployed host, with or without a scheme. It renders the green linked
  badge that leads the row, and is expected on anything that is deployed. "badges" is
  the rest of the row: supply it rather than leaning on the heuristics below, which can
  only ever produce a stub, and give versions read off the machine that serves the app
  rather than the >= floors in requirements.txt. The author badge is appended for you
  and always ends the row.

Requires: pip install markdown
Usage:    python3 readme_forge.py            # every *.md in the folder
          python3 readme_forge.py site-doctor.md AnotherRepo.md
"""

import os, re, sys, glob, json, html as htmllib

try:
    import markdown
except ImportError:
    sys.exit("Missing dependency. Run:  pip install markdown")

# ============================ IDENTITY / BRAND =============================
AUTHOR_NAME   = "Marc C. Deller, D.Phil."
AUTHOR_ROLE   = "Structural biologist & drug discovery scientist"
WEBSITE       = "https://marcdeller.com"
EMAIL         = "marc@marcdeller.com"
GH_USER       = "bellcheddar"
BRAND_NAVY    = "1C244B"
META_FILE     = "readme_meta.json"
CSS_FILE      = "elementor-widget-markdown.css"

# section-heading keyword -> emoji (first match wins; case-insensitive)
SECTION_EMOJI = [
    (r"\bauthor\b", "👤"), (r"\blicen[sc]e\b", "📄"), (r"\bcitation", "📑"),
    (r"\bfeatures?\b", "✨"), (r"\brequirements?\b", "📋"), (r"\bprerequisite", "📋"),
    (r"\binstall", "🔧"), (r"\busage\b", "🚀"), (r"\bquick ?start\b", "🚀"),
    (r"\bhow to run\b|\brunning\b|\brun\b", "▶️"), (r"\boutput", "📊"),
    (r"\bconfig", "🛠️"), (r"\btech ?stack\b|\bstack\b", "🧱"),
    (r"\btraining\b", "🎓"), (r"\beval", "🧪"), (r"\bcorpus\b|\bdata\b", "📚"),
    (r"\bchanges?\b|\bchangelog\b", "📝"), (r"\bto ?do\b|\broadmap\b", "✅"),
    (r"\bscoring\b|\bscore", "🎯"), (r"\bcontext\b", "🧭"),
    (r"\bmodels?\b", "🤗"), (r"\bnotes?\b", "🗒️"), (r"\btroubleshoot", "🩹"),
    (r"\bhow it works\b|\barchitecture\b", "⚙️"), (r"\bsafety\b", "🛡️"),
    (r"\bextend", "🧩"), (r"\bmobile\b", "📱"), (r"\bscience\b", "🔬"),
    (r"\bcompatibility\b", "🔗"),
]

# lightweight tech-badge heuristics: (regex on lowercased body) -> badge tuple
BADGE_HEURISTICS = [
    (r"```python|import \w|python 3|\.py\b", ("python", "3.x", "3776AB", "python")),
    (r"```bash|```zsh|#!/bin/bash|#!/usr/bin/env zsh|\.sh\b|\.zsh\b", ("shell", "bash/zsh", "4EAA25", "gnubash")),
    (r"single[- ]file html|\.html\b|<canvas|<!doctype", ("type", "HTML", "E34F26", "html5")),
    (r"\brdkit\b", ("RDKit", "cheminformatics", "00897B", "")),
    (r"\bpymol\b", ("PyMOL", "visualisation", "0B5394", "")),
    (r"\bboltz\b", ("engine", "Boltz-2", "467FF7", "")),
    (r"\bplip\b", ("PLIP", "profiling", "9b51e0", "")),
    (r"\bwordpress\b", ("scope", "WordPress", "21759B", "wordpress")),
    (r"\bollama\b", ("LLM", "Ollama", "000000", "")),
]

# ============================== POLISH STAGE ==============================
_H1_RE      = re.compile(r"^#\s+(.*)$")
_H2_RE      = re.compile(r"^(#{2,3})\s+(.*)$")
_TAGLINE_RE = re.compile(r"^>\s")
_EMOJI_START = re.compile(r"^[^\w`#\[(<*_]")  # heading text already starts with a symbol/emoji

def _badge(label, value, colour, logo=""):
    lab = label.replace(" ", "%20").replace("-", "--")
    val = value.replace(" ", "%20").replace("-", "--")
    logo_part = f"?logo={logo}&logoColor=white" if logo else ""
    return f"![{label}](https://img.shields.io/badge/{lab}-{val}-{colour}{logo_part})"

def _live_badge(host):
    """The deployed-app badge: green, first in the row, and the only one that links.

    A link, so `_badge()` cannot produce it: that emits a plain image. Anything deployed
    gets one, because it is the single thing a reader of the README most wants to click
    and the heuristics below can never infer a hostname.
    """
    host = host.replace("https://", "").replace("http://", "").rstrip("/")
    img = _badge("live", host, "00d084", "icloud")
    return f"[{img}](https://{host})"


def _badge_row(meta, body):
    tuples = meta.get("badges")
    if not tuples:
        low = body.lower()
        seen, tuples = set(), []
        for rx, tup in BADGE_HEURISTICS:
            if re.search(rx, low) and tup[0] not in seen:
                tuples.append(tup); seen.add(tup[0])
    badges = [_badge(*t) for t in tuples]
    badges.append(_badge("author", AUTHOR_NAME, BRAND_NAVY))
    # The live badge leads; the author badge always ends the row.
    live = meta.get("live")
    return " ".join(([_live_badge(live)] if live else []) + badges)

def _contact_table(repo):
    url = f"https://github.com/{GH_USER}/{repo}"
    return (
        "<table>\n<tr>\n"
        f'<td>🌐 <b>Website</b></td><td><a href="{WEBSITE}" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>\n'
        f'<td>✉️ <b>Contact</b></td><td><a href="mailto:{EMAIL}">{EMAIL}</a></td>\n'
        f'<td>🐙 <b>GitHub</b></td><td><a href="{url}" target="_blank" rel="noopener noreferrer">{GH_USER}/{repo}</a></td>\n'
        "</tr>\n</table>"
    )

def _author_footer(repo):
    url = f"https://github.com/{GH_USER}/{repo}"
    return (
        "\n---\n\n## 👤 Author\n\n"
        f"**{AUTHOR_NAME}**  \n{AUTHOR_ROLE}  \n\n"
        "<table>\n<tr>\n"
        f'<td>🌐</td><td><a href="{WEBSITE}" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>\n'
        f'<td>✉️</td><td><a href="mailto:{EMAIL}">{EMAIL}</a></td>\n'
        f'<td>🐙</td><td><a href="{url}" target="_blank" rel="noopener noreferrer">github.com/{GH_USER}/{repo}</a></td>\n'
        "</tr>\n</table>\n"
    )

def _detect_repo(path, meta, body):
    if meta.get("repo"):
        return meta["repo"]
    m = re.search(rf"github\.com/{GH_USER}/([A-Za-z0-9._-]+)", body)
    if m:
        return m.group(1)
    return os.path.splitext(os.path.basename(path))[0]

def _add_section_emoji(md):
    out = []
    for line in md.split("\n"):
        m = _H2_RE.match(line)
        if m and not _EMOJI_START.match(m.group(2)):
            text = m.group(2)
            for rx, emoji in SECTION_EMOJI:
                if re.search(rx, text, re.I):
                    line = f"{m.group(1)} {emoji} {text}"
                    break
        out.append(line)
    return "\n".join(out)

def _fix_github_links(md, repo):
    url = f"https://github.com/{GH_USER}/{repo}"
    md = md.replace(
        f'<a href="https://github.com/{GH_USER}" target="_blank" rel="noopener noreferrer">@{GH_USER}</a>',
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">{GH_USER}/{repo}</a>')
    md = md.replace(
        f'<a href="https://github.com/{GH_USER}" target="_blank" rel="noopener noreferrer">github.com/{GH_USER}</a>',
        f'<a href="{url}" target="_blank" rel="noopener noreferrer">github.com/{GH_USER}/{repo}</a>')
    return md

def _ensure_noopener(md):
    def repl(m):
        tag = m.group(0)
        if 'rel=' in tag:
            return tag
        return tag[:-1] + ' rel="noopener noreferrer">'
    return re.sub(r'<a\b[^>]*target="_blank"[^>]*>', repl, md)

def polish(md, path, meta):
    repo = _detect_repo(path, meta, md)
    lines = md.split("\n")

    # locate H1
    h1_idx = next((i for i, l in enumerate(lines) if _H1_RE.match(l)), None)
    already_branded = ("img.shields.io" in md) or ("🐙 <b>GitHub</b>" in md)

    if h1_idx is not None and not already_branded:
        # optional emoji + title rebuild
        h1_text = _H1_RE.match(lines[h1_idx]).group(1)
        emoji = meta.get("emoji", "")
        title = meta.get("title", h1_text)
        if emoji and not _EMOJI_START.match(h1_text) and not h1_text.startswith("<"):
            lines[h1_idx] = f"# {emoji} {title}"
        elif meta.get("title"):
            lines[h1_idx] = f"# {title}" if not emoji else f"# {emoji} {title}"

        # find end of any existing tagline blockquote after H1
        j = h1_idx + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        tagline_present = j < len(lines) and _TAGLINE_RE.match(lines[j])
        insert_at = h1_idx + 1
        block = []

        if not tagline_present:
            if meta.get("tagline"):
                block += ["", f'> **{meta["tagline"]}**']
            else:
                block += ["", "> **<!-- tagline: add a one-line strap line -->**"]
        else:
            # skip past the blockquote lines so we insert AFTER the tagline
            while j < len(lines) and (_TAGLINE_RE.match(lines[j]) or lines[j].strip() == ""):
                if lines[j].strip() == "" and j > h1_idx + 1 and not _TAGLINE_RE.match(lines[j]):
                    break
                j += 1
            insert_at = j
            if meta.get("tagline"):  # override tagline if supplied
                pass

        block += ["", _badge_row(meta, md), "", _contact_table(repo), "", "---", ""]
        lines[insert_at:insert_at] = block

    md = "\n".join(lines)

    # optional "why it matters" paragraph after first body paragraph
    if "why it matters" not in md.lower():
        if meta.get("why"):
            md = re.sub(r"(\n---\n\n)(.+?\n)(\n)",
                        lambda m: m.group(1) + m.group(2) + "\n" + meta["why"] + "\n" + m.group(3),
                        md, count=1, flags=re.S)
        else:
            md = re.sub(r"(\n---\n\n)(.+?\n)(\n)",
                        lambda m: m.group(1) + m.group(2) +
                        "\n<!-- Add a \"Why it matters: ... It is useful for: ...\" paragraph here -->\n" + m.group(3),
                        md, count=1, flags=re.S)

    md = _add_section_emoji(md)
    md = _fix_github_links(md, repo)

    if not re.search(r"^#{2,3}\s+(👤\s+)?Author\b", md, re.M | re.I):
        md = md.rstrip() + "\n" + _author_footer(repo)

    md = _ensure_noopener(md)
    md = re.sub(r"\n{3,}", "\n\n", md)  # tidy any blank-line runs from insertion
    return md

# =========================== HTML CONVERT STAGE ===========================
CSS = r"""
selector { color:#111111; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
  font-size:clamp(16px,1rem + 0.2vw,18px); line-height:1.7; overflow-wrap:anywhere; width:100%; max-width:none; }
selector *,selector *::before,selector *::after { box-sizing:border-box; }
selector h1,selector h2,selector h3,selector h4,selector h5,selector h6 { line-height:1.2; margin:1.4em 0 0.6em; overflow-wrap:anywhere; }
selector h1 { font-size:clamp(2rem,1.5rem + 2.4vw,2.75rem); margin-top:0.2em; }
selector h1 img { vertical-align:middle; margin-right:0.4rem; }
selector h2 { font-size:clamp(1.5rem,1.25rem + 1.4vw,2rem); border-bottom:1px solid #e5e7eb; padding-bottom:0.3em; }
selector h3 { font-size:clamp(1.2rem,1.05rem + 0.9vw,1.5rem); }
selector p,selector li,selector blockquote,selector pre { max-width:none; }
selector p,selector ul,selector ol,selector blockquote,selector pre,selector table,selector .emd-table-wrap,selector hr { margin:0 0 1rem; }
selector ul,selector ol { padding-left:1.4rem; }
selector ul.emd-tasklist { list-style:none; padding-left:0.2rem; }
selector ul.emd-tasklist li { margin:0.15rem 0; }
selector a { color:#467FF7; text-decoration:none; overflow-wrap:anywhere; }
selector a:hover { text-decoration:underline; }
selector img { max-width:100%; height:auto; }
selector p img { vertical-align:middle; }
selector pre,selector code { font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace; }
selector code { background:#f6f8fa; padding:0.15em 0.35em; border-radius:6px; overflow-wrap:anywhere; }
selector pre { background:#f6f8fa; padding:1rem; overflow-x:auto; border-radius:10px; border:1px solid #e5e7eb; }
selector pre code { background:transparent; padding:0; overflow-wrap:normal; }
selector blockquote { padding:0.2rem 1rem; border-left:4px solid #467FF7; color:#555555; }
selector hr { border:0; border-top:1px solid #e5e7eb; }
selector .emd-table-wrap { width:100%; overflow-x:auto; }
selector table { width:100%; border-collapse:collapse; border-spacing:0; font-size:0.95rem; }
selector th,selector td { border:1px solid #e5e7eb; padding:0.7rem 0.8rem; text-align:left; vertical-align:top; white-space:normal; word-break:break-word; overflow-wrap:anywhere; }
selector th { font-weight:700; }
selector .emd-data-table { table-layout:fixed; }
selector .emd-info-table { width:auto; max-width:100%; }
selector .emd-info-table th,selector .emd-info-table td { border:0; padding:0.25rem 0.5rem; }
@media (max-width:767px){
  selector { font-size:16px; line-height:1.65; }
  selector .emd-table-wrap { overflow-x:visible; }
  selector .emd-data-table,selector .emd-data-table tbody,selector .emd-data-table tr,selector .emd-data-table td { display:block !important; width:100% !important; }
  selector .emd-data-table thead,selector .emd-data-table th { display:none !important; }
  selector .emd-data-table { font-size:0.84rem; border:0; }
  selector .emd-data-table tr { margin:0 0 0.8rem; border:1px solid #e5e7eb; border-radius:10px; overflow:hidden; background:#ffffff; }
  selector .emd-data-table td { border:0; border-top:1px solid #e5e7eb; padding:0.45rem 0.7rem; line-height:1.35; }
  selector .emd-data-table tr td:first-child { border-top:0; }
  selector .emd-data-table td::before { content:attr(data-label); display:block; font-size:0.7rem; line-height:1.2; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; color:#555555; margin-bottom:0.2rem; }
  selector .emd-data-table td[data-label=""]::before { content:none; }
  selector .emd-info-table,selector .emd-info-table tbody,selector .emd-info-table tr { display:block; }
  selector .emd-info-table td { display:inline-block; width:auto; padding:0.15rem 0.4rem 0.15rem 0; }
}
"""

_TASK_RE = re.compile(r'^(\s*)[-*+]\s+\[( |x|X)\]\s+(.*)$')
def _pre_tasklists(md):
    out = []
    for line in md.split("\n"):
        m = _TASK_RE.match(line)
        if m:
            indent, mark, rest = m.groups()
            glyph = "&#9989;" if mark.lower() == "x" else "&#11036;"
            out.append(f'{indent}- <!--emd-task--><span class="emd-check">{glyph}</span> {rest}')
        else:
            out.append(line)
    return "\n".join(out)

def _tag_tasklists(html):
    def repl(m):
        b = m.group(0)
        if "<!--emd-task-->" in b:
            b = b.replace("<ul>", '<ul class="emd-tasklist">', 1)
        return b
    html = re.sub(r'<ul>.*?</ul>', repl, html, flags=re.S)
    return html.replace("<!--emd-task-->", "")

_TABLE_RE = re.compile(r'<table\b[^>]*>.*?</table>', re.S)
_THEAD_RE = re.compile(r'<thead\b[^>]*>(.*?)</thead>', re.S)
_TBODY_RE = re.compile(r'<tbody\b[^>]*>(.*?)</tbody>', re.S)
_TR_RE    = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.S)
_TH_RE    = re.compile(r'<th\b[^>]*>(.*?)</th>', re.S)
_CELL_RE  = re.compile(r'(<td\b[^>]*>)(.*?)(</td>)', re.S)
def _strip(s): return re.sub(r'<[^>]*>', '', s).strip()

def _process_tables(html):
    def repl(match):
        table = match.group(0)
        head = _THEAD_RE.search(table)
        if not head:
            return re.sub(r'<table\b[^>]*>', '<table class="emd-info-table">', table, count=1)
        headers = [_strip(h) for h in _TH_RE.findall(head.group(1))]
        def add_labels(tb):
            def row_repl(rm):
                row = rm.group(1); idx = {"i": 0}
                def cell_repl(cm):
                    ot, inner, ct = cm.groups(); i = idx["i"]; idx["i"] += 1
                    label = htmllib.escape(headers[i] if i < len(headers) else "", quote=True)
                    if 'data-label' not in ot:
                        ot = ot[:-1] + f' data-label="{label}">'
                    return f'{ot}{inner}{ct}'
                return f'<tr>{_CELL_RE.sub(cell_repl, row)}</tr>'
            return f'<tbody>{_TR_RE.sub(row_repl, tb.group(1))}</tbody>'
        table = _TBODY_RE.sub(add_labels, table, count=1)
        table = re.sub(r'<table\b[^>]*>', '<table class="emd-data-table">', table, count=1)
        return f'<div class="emd-table-wrap">{table}</div>'
    return _TABLE_RE.sub(repl, html)

_LIST_ITEM_RE = re.compile(r'^(\s*)([-*+]\s+|\d+[.)]\s+)')
_HEADER_LINE_RE = re.compile(r'^#{1,6}\s')
_HR_LINE_RE = re.compile(r'^(-{3,}|\*{3,}|_{3,})\s*$')
_TABLE_SEP_RE = re.compile(r'^:?-+:?(\s*\|\s*:?-+:?)*\s*\|?\s*$')

def _tag_bare_fences(md):
    """Give every bare (no-language, e.g. plain '```') opening code fence an explicit
    `text` language tag.

    python-markdown's `fenced_code` extension mis-renders a bare fence into a broken
    inline `<code>` span with a literal embedded newline -- instead of a proper
    `<pre><code>` block -- when one appears deep inside a large real-world document
    (confirmed directly: reproduces in the full multi-thousand-line document, does
    NOT reproduce testing the same fence content in isolation, and disappears
    entirely once the fence carries any language tag at all). Purely a rendering
    workaround: `text` changes nothing about what's displayed (no syntax
    highlighting either way), just gives the extension an info string to key off.
    """
    lines = md.split("\n")
    out = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if not in_fence and stripped == "```":
            out.append(line.replace("```", "```text", 1))
            in_fence = True
        elif not in_fence and re.match(r'^```\S', stripped):
            in_fence = True
            out.append(line)
        elif in_fence and stripped.startswith("```"):
            in_fence = False
            out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


def _unwrap_soft_wraps(md):
    """Join manually soft-wrapped source lines (this file's own editor-width line
    breaks within one paragraph or list item) into a single line each.

    Necessary because a raw '\\n' left inside a paragraph is technically harmless in
    a real browser (CSS collapses it to a space) but is NOT harmless once the HTML
    is pasted into WordPress: wpautop() (WP's automatic paragraph filter, which
    Elementor's widgets can still run content through) converts every '\\n' inside
    a text block into a literal '<br>', turning our own source-editor line wrapping
    into visible mid-sentence line breaks on the live page -- confirmed as the
    actual cause of a real "wordwrapped badly in lots of places" report, not a
    theoretical concern.

    Conservative by construction: only ever joins two adjacent, non-blank lines that
    are both plain flowing text (list-item continuation lines included). Never
    touches fenced code blocks, raw HTML blocks (CommonMark's own rule: one starts
    at a line beginning with '<' and ends at the next blank line), headers, list-item
    *start* lines and horizontal rules), blockquotes, table rows (leading-'|' or
    bare '---'-style separator rows), or a line already ending in an explicit
    Markdown hard-break (two trailing spaces or a trailing backslash).
    """
    lines = md.split("\n")
    out = []
    in_fence = False
    fence_delim = None
    in_html_block = False
    buf = None

    def flush():
        nonlocal buf
        if buf is not None:
            out.append(buf)
            buf = None

    for line in lines:
        stripped = line.strip()
        fence_match = re.match(r'^(```+|~~~+)', stripped)

        if in_fence:
            out.append(line)
            if fence_match and stripped.startswith(fence_delim):
                in_fence = False
            continue
        if fence_match:
            flush()
            in_fence = True
            fence_delim = fence_match.group(1)[:3]
            out.append(line)
            continue

        if stripped == "":
            flush()
            in_html_block = False
            out.append(line)
            continue

        if in_html_block:
            out.append(line)
            continue
        if stripped.startswith("<"):
            flush()
            in_html_block = True
            out.append(line)
            continue

        is_block_start = bool(
            _LIST_ITEM_RE.match(line) or _HEADER_LINE_RE.match(stripped) or
            _HR_LINE_RE.match(stripped) or _TABLE_SEP_RE.match(stripped) or
            stripped.startswith(">") or stripped.startswith("|")
        )
        hard_break = buf is not None and (buf.endswith("  ") or buf.endswith("\\"))

        if is_block_start or buf is None or hard_break:
            flush()
            buf = line
        else:
            buf = buf.rstrip() + " " + stripped
    flush()
    return "\n".join(out)


EXTENSIONS = ["fenced_code", "tables", "sane_lists", "md_in_html", "attr_list"]
def to_html(md):
    md = _tag_bare_fences(md)
    md = _unwrap_soft_wraps(md)
    md = _pre_tasklists(md)
    html = markdown.markdown(md, extensions=EXTENSIONS, output_format="html5")
    html = _tag_tasklists(html)
    return _process_tables(html)

def preview(fragment):
    css = CSS.replace("selector", ".emd")
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<style>\n{css}\n</style>\n</head>\n<body>\n<div class=\"emd\">\n{fragment}\n</div>\n</body>\n</html>\n")

# ================================= MAIN ===================================
def main(argv):
    meta_all = {}
    if os.path.exists(META_FILE):
        with open(META_FILE, encoding="utf-8") as f:
            meta_all = json.load(f)

    paths = argv[1:] if len(argv) > 1 else sorted(
        p for p in glob.glob("*.md") if not p.endswith(".polished.md"))
    if not paths:
        print("No .md files found."); return

    for path in paths:
        with open(path, encoding="utf-8") as f:
            crude = f.read()
        meta = meta_all.get(os.path.basename(path), {})
        polished = polish(crude, path, meta)

        stem = os.path.splitext(path)[0]
        with open(stem + ".polished.md", "w", encoding="utf-8") as f:
            f.write(polished if polished.endswith("\n") else polished + "\n")

        fragment = to_html(polished)
        with open(stem + ".html", "w", encoding="utf-8") as f:
            f.write(fragment + "\n")
        with open(stem + ".preview.html", "w", encoding="utf-8") as f:
            f.write(preview(fragment))
        print(f"forged {path} -> {stem}.polished.md + {stem}.html + {stem}.preview.html")

    with open(CSS_FILE, "w", encoding="utf-8") as f:
        f.write(CSS)
    print(f"wrote {CSS_FILE}")

if __name__ == "__main__":
    main(sys.argv)
