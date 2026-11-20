#!/usr/bin/env python3
"""Render the GitHub profile README cards into cards/ as self-contained animated SVGs.

The cards are shown through <img> tags, which puts hard limits on what the SVG may
reference: no external stylesheet, no external image, no webfont URL. So every style
lives in an inline <style> block, the portrait and icons are inlined as data URIs, and
the two fonts are subset with pyftsubset and embedded as base64 WOFF data URIs.

    python3 render_cards.py            # animated cards (what gets committed)
    STATIC=1 python3 render_cards.py   # final frame only, for screenshots

Data comes from data/contributions.json + data/repos.json, refreshed daily by
scripts/refresh_data.py from .github/workflows/profile-cards.yml.
"""
import base64
import datetime
import json
import os
import pathlib
import re
import string
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent
CARDS = ROOT / "cards"
STATIC = os.environ.get("STATIC") == "1"

# --- brand: matches the palette the old README used (0d1117 panel, 58a6ff accent)
BG = "#0d1117"
PANEL = "#161b22"
SUNKEN = "#10161e"
BORDER = "#30363d"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
DIM = "#6e7681"
ACCENT = "#58a6ff"
EASE = "cubic-bezier(.2,.75,.3,1)"
GRAPH_LEVELS = ["#21262d", "#1b4a7d", "#2d6cb5", "#4293e6", "#58a6ff"]  # level 0 must differ from PANEL
LANG_COLORS = {
    "PHP": "#4F5D95", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Java": "#b07219", "Dart": "#00B4AB", "Python": "#3572A5", "HTML": "#e34c26",
    "CSS": "#563d7c", "Blade": "#f7523f", "Shell": "#89e051", "Kotlin": "#A97BFF",
}

# Card entrance delays, so the page plays top to bottom.
BASE = {"hero": 0, "tile": 120, "chart": 220, "repo": 320, "graph": 640}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CONTENT = {}  # every string in CONTENT gets subset into the embedded fonts


# --------------------------------------------------------------------------- fonts
FONTS = [  # source, family, weight range rendered, axis limits to pin before subsetting
    ("Inter.ttf", "Inter", "400 700", ["opsz=16", "wght=400:700"]),
    ("JetBrainsMono.ttf", "JetBrains Mono", "400 800", ["wght=400:800"]),
]


def pin_axes(src, spec):
    """Limit the variable axes first: a full-range variable font is 2-3x the payload."""
    out = ROOT / "fonts" / (src.stem + ".pinned.ttf")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(
            [sys.executable, "-m", "fontTools.varLib.instancer", "--quiet", str(src)] + spec + ["-o", str(out)],
            check=True,
        )
    return out


def subset_font(src, text):
    out = ROOT / "fonts" / (src.stem.replace(".pinned", "") + ".woff")
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(
            [sys.executable, "-m", "fontTools.subset", str(src), "--text=" + text,
             "--flavor=woff", "--layout-features=kern,liga", "--no-hinting",
             "--desubroutinize", "--output-file=" + str(out)],
            check=True,
        )
    return out.read_bytes()


def data_uri(raw, mime):
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def font_css():
    chars = "".join(sorted(set("".join(map(str, CONTENT.values())) + string.printable)))
    css = []
    for src, family, weight, spec in FONTS:
        pinned = pin_axes(ROOT / "fonts" / src, spec)
        uri = data_uri(subset_font(pinned, chars), "font/woff")
        css.append(
            "@font-face{font-family:'%s';font-style:normal;font-weight:%s;src:url(%s) format('woff')}"
            % (family, weight, uri)
        )
    return "\n".join(css)


# ------------------------------------------------------------------------ helpers
RULES = []  # per-card CSS rules, reset by svg()


def anim(key, delay, dur=760):
    """Entrance animation as a class name; empty under STATIC so the final frame renders.

    It has to live in the stylesheet: an inline style="animation:..." survives
    @media (prefers-reduced-motion) {*{animation:none!important}}, so the card would keep
    animating for users who asked for no motion.
    """
    if STATIC:
        return ""
    RULES.append(".a%d{animation:%s %dms %s both;animation-delay:%dms}" % (len(RULES), key, dur, EASE, delay))
    return "a%d" % (len(RULES) - 1)


def cls(*names):
    joined = " ".join(n for n in names if n)
    return ('class="%s"' % joined) if joined else ""


def smil(attrs):
    """Ambient loop; dropped under STATIC (CSS shows the frozen frame there)."""
    if STATIC:
        return ""
    return "".join("<animate %s/>" % a for a in attrs)


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def panel(w, h, rx=16, fill=PANEL):
    return '<rect x=".5" y=".5" width="%s" height="%s" rx="%s" fill="%s" stroke="%s"/>' % (
        w - 1, h - 1, rx, fill, BORDER)


def text(x, y, s, size=12, fill=TEXT, weight=400, anchor="start", css="", extra=""):
    return '<text x="%s" y="%s" font-size="%s" font-weight="%s" fill="%s" text-anchor="%s" %s%s>%s</text>' % (
        x, y, size, weight, fill, anchor, ('class="%s" ' % css) if css else "", ('style="%s" ' % extra) if extra else "", esc(s))


def svg(width, height, body, extra_css=""):
    """Wrap a card body in a full SVG document with the shared stylesheet."""
    rules = "\n".join(RULES)
    RULES.clear()
    return """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="%(w)s" height="%(h)s" viewBox="0 0 %(w)s %(h)s" role="img">
<style>
%(fonts)s
text{font-family:'Inter',ui-sans-serif,system-ui,sans-serif}
.mono{font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes pop{from{opacity:0;transform:scale(.55)}to{opacity:1;transform:scale(1)}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes growY{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes slideL{from{opacity:0;transform:translateX(-16px)}to{opacity:1;transform:none}}
.pop{transform-box:fill-box;transform-origin:50%% 50%%}
.originL{transform-box:fill-box;transform-origin:0%% 50%%}
.originB{transform-box:fill-box;transform-origin:50%% 100%%}
%(extra_css)s
%(rules)s
@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
  .smil-o{opacity:1!important}
  .smil-t{transform:none!important}
  .smil-hide{display:none!important}
}
</style>
%(body)s
</svg>
""" % {"w": width, "h": height, "fonts": font_css(), "body": body, "ease": EASE,
        "extra_css": extra_css, "rules": rules}


def write(name, content):
    CARDS.mkdir(exist_ok=True)
    (CARDS / name).write_text(content)
    return name


def asset_uri(path, mime):
    return data_uri(path.read_bytes(), mime)


def icon_path(name):
    """Extract the path data of a 24x24 icon so it can be inlined (no external fetch from <img>)."""
    raw = (ROOT / "assets" / "icons" / (name + ".svg")).read_text()
    return re.search(r'\sd="([^"]+)"', raw).group(1)


# ------------------------------------------------------------------- card: hero
def card_hero():
    w, h = 880, 196
    r = 54
    cx, cy = 782, 98
    body = panel(w, h)
    body += ('<linearGradient id="glow" x1="0" x2="1"><stop offset="0" stop-color="%s" stop-opacity=".75"/>'
             '<stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient>' % (ACCENT, ACCENT))
    body += '<rect x="0" y="0" width="%s" height="3" rx="1.5" fill="url(#glow)"/>' % w

    body += '<g %s>' % cls(anim("rise", BASE["hero"]))
    body += text(44, 92, CONTENT["name"], size=46, weight=700, anchor="start", extra="letter-spacing:2px")
    body += "</g>"
    body += ('<rect x="44" y="106" width="132" height="4" rx="2" fill="%s" %s/>'
             % (ACCENT, cls("originL", anim("grow", BASE["hero"] + 220, 900))))
    body += '<g %s>%s%s</g>' % (
        cls(anim("fade", BASE["hero"] + 320)),
        text(44, 140, CONTENT["tagline"], size=15, fill=MUTED),
        text(44, 170, CONTENT["handle"], size=11, fill=DIM, css="mono"),
    )

    # portrait: avatar inlined as a data URI because <img>-embedded SVG cannot fetch it
    body += ('<clipPath id="ava"><circle cx="%s" cy="%s" r="%s"/></clipPath>' % (cx, cy, r))
    body += ('<g %s>' % cls(anim("pop", BASE["hero"] + 200, 820)))
    body += ('<image clip-path="url(#ava)" x="%s" y="%s" width="%s" height="%s" xlink:href="%s" preserveAspectRatio="xMidYMid slice"/>'
             % (cx - r, cy - r, 2 * r, 2 * r, CONTENT["avatar"]))
    body += ('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="%s" stroke-width="1.5" stroke-opacity=".55" '
             'stroke-dasharray="6 10" class="smil-t smil-rot">%s</circle>'
             % (cx, cy, r + 8, ACCENT,
                smil(['attributeName="transform" type="rotate" from="0 %s %s" to="360 %s %s" dur="26s" '
                      'repeatCount="indefinite"' % (cx, cy, cx, cy)])))
    body += "</g>"
    return svg(w, h, body, "hero")


# ------------------------------------------------------------------- card: tiles
def card_tile(key, title, value):
    w, h = 300, 104
    body = panel(w, h, rx=14) + '<g %s>' % cls(anim("rise", BASE["tile"]))
    body += ('<rect x="18" y="24" width="52" height="52" rx="13" fill="%s" stroke="%s" stroke-opacity=".35"/>'
             % (SUNKEN, ACCENT))
    if key == "linkedin":  # simple-icons dropped the LinkedIn mark, so it is drawn from primitives
        body += '<g %s>' % cls(anim("pop", BASE["tile"] + 120, 620))
        body += text(44, 60, "in", size=20, weight=700, fill=ACCENT, anchor="middle", css="mono")
        body += "</g>"
    else:
        # the CSS animation owns `transform`, so the icon offset lives on an outer group
        body += ('<g transform="translate(31 37)"><g %s><path d="%s" fill="%s"/></g></g>'
                 % (cls(anim("pop", BASE["tile"] + 120, 620)), icon_path(key), ACCENT))
    body += text(84, 46, title, size=13, weight=600)
    body += text(84, 66, value, size=11, fill=MUTED, css="mono")
    body += ('<rect x="84" y="78" width="64" height="3" rx="1.5" fill="%s" %s/>'
             % (ACCENT, cls("originL", anim("grow", BASE["tile"] + 240, 800))))
    body += "</g>"
    return svg(w, h, body, "tile")


# ------------------------------------------------------------------- card: chart
def card_chart(months, stats):
    w, h = 880, 274
    x0, x1 = 44, 596      # plot area
    base_y, top_y = 216, 78
    peak = max([m["count"] for m in months] + [1])
    axis = max(10, -(-peak // 10) * 10)  # round the axis so the ticks are readable
    slot = (x1 - x0) / len(months)
    bar_w = min(26, slot - 12)

    body = panel(w, h) + '<g %s>' % cls(anim("rise", BASE["chart"]))
    body += text(x0, 44, CONTENT["chart_title"], size=16, weight=600)
    body += text(w - 44, 44, CONTENT["chart_sub"], size=12, fill=MUTED, anchor="end", css="mono")
    # gridlines
    for frac in (0, 0.5, 1):
        y = base_y - (base_y - top_y) * frac
        body += ('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1" stroke-dasharray="3 5"/>'
                 % (x0, y, x1, y, BORDER))
        body += text(x0 - 8, y + 3.5, str(round(axis * frac)), size=9, fill=DIM, anchor="end", css="mono")
    body += "</g>"

    for i, m in enumerate(months):
        bx = x0 + i * slot + (slot - bar_w) / 2
        bh = (base_y - top_y) * m["count"] / axis
        bright = i >= len(months) - 3  # newest months carry the accent
        body += ('<rect x="%.1f" y="%.1f" width="%s" height="%.1f" rx="4" fill="%s" fill-opacity="%s" %s/>'
                 % (bx, base_y - bh, bar_w, max(bh, 0.6), ACCENT, "1" if bright else ".55",
                    cls("originB", anim("growY", BASE["chart"] + 120 + i * 55, 620))))
        body += ('<g %s>%s%s</g>' % (
            cls(anim("fade", BASE["chart"] + 420 + i * 55)),
            text(bx + bar_w / 2, base_y - bh - 7, m["count"], size=9, fill=MUTED, anchor="middle", css="mono"),
            text(bx + bar_w / 2, base_y + 18, m["label"], size=9.5, fill=DIM, anchor="middle", css="mono"),
        ))

    # busiest day marker: SMIL pulse on top of the peak bar
    peak_i = max(range(len(months)), key=lambda i: months[i]["count"])
    px = x0 + peak_i * slot + slot / 2
    body += ('<circle class="smil-o" cx="%.1f" cy="%s" r="3" fill="%s">%s</circle>'
             % (px, top_y - 12, ACCENT,
                smil(['attributeName="opacity" values=".25;1;.25" dur="2.4s" repeatCount="indefinite" begin="%dms"' % (BASE["chart"] + 900)])))

    # stats column
    body += ('<rect x="628" y="66" width="212" height="164" rx="12" fill="%s" stroke="%s"/>' % (SUNKEN, BORDER))
    for i, (label, value, note) in enumerate(stats):
        y = 100 + i * 52
        body += ('<g %s>%s%s%s</g>' % (
            cls(anim("slideL", BASE["chart"] + 260 + i * 130)),
            text(650, y, label.upper(), size=9, fill=DIM, css="mono", extra="letter-spacing:1px"),
            text(650, y + 22, value, size=19, weight=700, fill=ACCENT, css="mono"),
            text(828, y + 21, note, size=9.5, fill=MUTED, anchor="end"),
        ))
    return svg(w, h, body, "chart")


# -------------------------------------------------------------------- card: repo
def card_repo(repo, index):
    w, h = 430, 118
    lang = repo["language"] or "no language"
    color = LANG_COLORS.get(repo["language"], DIM)
    pushed = datetime.datetime.strptime(repo["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")
    body = panel(w, h, rx=14) + '<g %s>' % cls(anim("rise", BASE["repo"] + index * 90))
    body += text(26, 38, repo["name"], size=14, weight=600, fill=ACCENT, css="mono")
    body += text(w - 26, 38, "pushed " + CONTENT["pushed_fmt"][index], size=9.5, fill=DIM, anchor="end", css="mono")
    body += ('<circle class="smil-o" cx="30" cy="61" r="4" fill="%s" fill-opacity=".85">%s</circle>'
             % (color, smil(['attributeName="opacity" values=".5;1;.5" dur="3.6s" repeatCount="indefinite" begin="%dms"' % (BASE["repo"] + index * 90 + 700)])))
    body += text(42, 65, lang, size=11, fill=MUTED, css="mono")
    body += text(w - 26, 65, CONTENT["repo_meta"][index], size=10, fill=DIM, anchor="end", css="mono")
    if repo["description"]:  # no placeholder text: a repo without a description shows none
        body += text(26, 92, repo["description"][:62], size=11, fill=MUTED)
    body += "</g>"
    return svg(w, h, body, "repo")


# ------------------------------------------------------------------- card: graph
def card_graph(days):
    w, h = 880, 208
    cell, gap = 11, 3
    step = cell + gap
    gx, gy = 44, 74
    weeks = {}
    for d in days:
        date = datetime.date.fromisoformat(d["date"])
        weeks.setdefault(date.isocalendar()[:2], []).append(d)
    keys = sorted(weeks)
    body = panel(w, h) + '<g %s>' % cls(anim("rise", BASE["graph"]))
    body += text(gx, 44, CONTENT["graph_title"], size=16, weight=600)
    body += text(w - gx, 44, CONTENT["graph_sub"], size=12, fill=MUTED, anchor="end", css="mono")

    # month labels sit above the column where the month starts
    seen = set()
    for wi, key in enumerate(keys):
        for d in weeks[key]:
            month = d["date"][:7]
            if month not in seen:
                seen.add(month)
                body += text(gx + wi * step, 63, CONTENT["month_short"][month], size=9.5,
                             fill=DIM, css="mono")
                break

    for wi, key in enumerate(keys):
        for d in weeks[key]:
            date = datetime.date.fromisoformat(d["date"])
            row = (date.weekday() + 1) % 7  # GitHub columns start on Sunday
            x, y = gx + wi * step, gy + row * step
            body += ('<rect x="%s" y="%s" width="%s" height="%s" rx="2.5" fill="%s" %s/>'
                     % (x, y, cell, cell, GRAPH_LEVELS[min(d["level"], 4)],
                        cls("pop", anim("pop", BASE["graph"] + 60 + wi * 14, 460))))
            if d["date"] == days[-1]["date"]:  # ambient marker on the latest day
                body += ('<rect class="smil-o" x="%s" y="%s" width="%s" height="%s" rx="2.5" fill="none" '
                         'stroke="%s" stroke-width="1.5">%s</rect>'
                         % (x, y, cell, cell, ACCENT,
                            smil(['attributeName="opacity" values=".25;1;.25" dur="3s" repeatCount="indefinite"'])))
    body += "</g>"

    # legend + foot note
    ly = h - 22
    body += text(w - gx, ly, CONTENT["graph_foot"], size=9.5, fill=DIM, anchor="end", css="mono")
    body += text(gx, ly, "Less", size=9.5, fill=DIM, css="mono")
    for i, c in enumerate(GRAPH_LEVELS):
        body += ('<rect x="%s" y="%s" width="%s" height="%s" rx="2.5" fill="%s" %s/>'
                 % (gx + 34 + i * step, ly - 9, cell, cell, c,
                    cls(anim("pop", BASE["graph"] + 700 + i * 60, 400))))
    body += text(gx + 34 + 5 * step + 4, ly, "More", size=9.5, fill=DIM, css="mono")
    return svg(w, h, body, "graph")


# ------------------------------------------------------------------- data + run
def load(path, key=None):
    data = json.loads((ROOT / "data" / path).read_text())
    return data[key] if key else data


def compute_stats(days):
    counts = [(datetime.date.fromisoformat(d["date"]), d["count"]) for d in days]
    longest = current = 0
    run = 0
    for i, (date, count) in enumerate(counts):
        if count > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    current = run
    active = sum(1 for _, c in counts if c > 0)
    busiest = max(counts, key=lambda t: t[1])
    return {"active": active, "longest": longest, "current": current,
            "busiest": busiest[1], "busiest_date": busiest[0],
            "total": sum(c for _, c in counts)}


def month_buckets(days, limit=12):
    buckets = {}
    for d in days:
        buckets.setdefault(d["date"][:7], 0)
        buckets[d["date"][:7]] += d["count"]
    keys = sorted(buckets)[-limit:]
    return [{"key": k, "label": MONTHS[int(k[5:7]) - 1], "count": buckets[k]} for k in keys]


def month_label(key):
    return "%s %s" % (MONTHS[int(key[5:7]) - 1], key[2:4])


def day_label(iso):
    d = datetime.date.fromisoformat(iso)
    return "%d %s %s" % (d.day, MONTHS[d.month - 1], str(d.year)[2:])


def build_content(days, repos, stats, months, avatar):
    CONTENT.update({
        "name": "ZULFAHMI",
        "tagline": "Building reliable web and mobile applications with clean architecture and scalable solutions.",
        "handle": "@f4hmii  ·  fullstack developer  ·  laravel, flutter, react, node.js",
        "avatar": avatar,
        "chart_title": "Contributions per month",
        "chart_sub": "%d contributions  ·  %s – %s" % (
            sum(m["count"] for m in months), month_label(months[0]["key"]), month_label(months[-1]["key"])),
        "graph_title": "Contribution graph",
        "graph_sub": "%d contributions  ·  %d active days  ·  %s – %s" % (
            stats["total"], stats["active"], day_label(days[0]["date"]), day_label(days[-1]["date"])),
        "graph_foot": "last updated " + CONTENT_TS,
        "month_short": {k: MONTHS[int(k[5:7]) - 1] for k in sorted({d["date"][:7] for d in days})},
        "pushed_fmt": [], "repo_meta": [],
        "tiles": [
            ("github", "GitHub", "github.com/f4hmii"),
            ("linkedin", "LinkedIn", "linkedin.com/in/zulfahmi"),
            ("gmail", "Email", "fahmif3211@gmail.com"),
        ],
        "stats": [
            ("Streak", "%d days" % stats["longest"], "longest"),
            ("Active days", str(stats["active"]), "this year"),
            ("Busiest day", "%d commits" % stats["busiest"],
             stats["busiest_date"].strftime("%d %b").lstrip("0")),
        ],
        "chars": "",
    })
    for repo in repos:
        pushed = datetime.datetime.strptime(repo["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")
        CONTENT["pushed_fmt"].append(pushed.strftime("%d %b %y").lstrip("0"))
        CONTENT["repo_meta"].append("%d stars  ·  %d forks" % (repo["stars"], repo["forks"]))


CONTENT_TS = ""


def validate(paths):
    """Self-check: every card must be standalone XML with no external reference."""
    for name in paths:
        raw = (CARDS / name).read_text()
        root = ET.fromstring(raw)
        uris = re.findall(r'(?:href|src)="([^"]+)"', raw)
        assert all(u.startswith(("data:", "#")) for u in uris), "%s references an external resource" % name
        assert "@font-face" in raw and "data:font/woff;base64," in raw, "%s has no embedded font" % name
        assert root.tag.endswith("svg"), "%s is not an svg" % name
        # CSS transform silently beats a transform attribute, so never put an animated
        # transform class on an element that also carries a transform attribute.
        keys = {c: k for c, k in re.findall(r"\.(a\d+)\{animation:(\w+)", raw)}
        for tag in re.findall(r"<[^>]+>", raw):
            if 'transform="' in tag:
                animated = [keys[c] for c in re.findall(r'class="([^"]+)"', tag) for c in c.split() if c in keys]
                assert not [k for k in animated if k in ("rise", "pop", "grow", "growY", "slideL")], \
                    "%s: transform-animating class %s on an element with a transform attribute" % (name, animated)
        if not STATIC:
            assert "@keyframes" in raw, "%s lost its entrance animation" % name
            assert "prefers-reduced-motion" in raw, "%s has no reduced-motion rule" % name
    for name in paths:  # the cards that carry SMIL loops must keep them
        if not STATIC and name.startswith(("hero", "chart", "repo", "graph")):
            assert "<animate " in (CARDS / name).read_text(), "%s lost its SMIL loop" % name
    return True


def main():
    global CONTENT_TS
    days = load("contributions.json", "days")
    repos = load("repos.json", "repos")
    assert days and repos, "missing contribution or repo data; run scripts/refresh_data.py"
    stats = compute_stats(days)
    months = month_buckets(days)
    avatar = asset_uri(ROOT / "assets" / "avatar.png", "image/png")
    CONTENT_TS = datetime.date.fromisoformat(days[-1]["date"]).strftime("%d %b %Y").lstrip("0")
    build_content(days, repos, stats, months, avatar)

    names = [write("hero.svg", card_hero())]
    for key, title, value in CONTENT["tiles"]:
        names.append(write("tile-%s.svg" % key, card_tile(key, title, value)))
    names.append(write("chart.svg", card_chart(months, CONTENT["stats"])))
    for i, repo in enumerate(repos):
        names.append(write("repo-%s.svg" % repo["name"], card_repo(repo, i)))
    names.append(write("graph.svg", card_graph(days)))

    validate(names)
    referenced = set(re.findall(r'src="(cards/[^"]+\.svg)"', (ROOT / "README.md").read_text()))
    written = {"cards/" + n for n in names}
    assert referenced == written, "README.md and cards/ disagree: %s" % sorted(referenced ^ written)
    total = sum((CARDS / n).stat().st_size for n in names)
    print("%s %d cards in cards/ (%.0f KB total)" % ("STATIC" if STATIC else "animated", len(names), total / 1024))
    for n in names:
        print("  %-32s %6.1f KB" % (n, (CARDS / n).stat().st_size / 1024))
    if STATIC:
        print("WARNING: cards/ holds the final frame only. Re-run without STATIC=1 before pushing.")


if __name__ == "__main__":
    main()
