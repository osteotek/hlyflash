"""Discover and download official Hanlinyue firmware (.ufw) per model.

Each model has a stable firmware page on hanlinyue.com.cn that server-renders a
set of firmware "cards" -- one per variant (different target e-readers and
button layouts). We scrape the current cards at call time, so the list always
reflects the latest firmware the vendor publishes. Files live on
cdn.hanlinyue.com.cn. No API key or login is needed.
"""
from __future__ import annotations

import html
import re
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import date

BASE = "https://hanlinyue.com.cn/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# model key -> (display name, stable firmware page id, serial-report aliases)
MODELS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "free2":      ("Free 2",        "1755595121764", ("free2", "free 2", "free02", "f2")),
    "free3":      ("Free 3",        "1762607565939", ("free3", "free 3", "free03", "f3")),
    "free2-2026": ("Free 2 (2026)", "1766830643878", ("free2 2026", "free2-2026", "free 2 2026")),
    "e1pro":      ("E1 Pro",        "1763541876714", ("e1pro", "e1 pro", "e1-pro")),
}


@dataclass
class Variant:
    fw_id: str          # vendor firmware number, e.g. "5A0FLJ"
    url: str            # direct .ufw URL
    devices: str        # 适用设备 -- which e-readers it targets
    direction: str      # 按键方向 -- button layout
    changelog: str      # 更新日志
    title: str = ""
    released: str = ""  # ISO date from 发布时间, not the site's update badge


def resolve_model(name: str) -> str | None:
    """Map a free-form / serial-reported model name to a MODELS key."""
    n = re.sub(r"[\W_]+", " ", name.lower()).strip()
    compact = re.sub(r"\s+", "", n)
    for key, (disp, _pid, aliases) in MODELS.items():
        if compact in {re.sub(r"[\W_]+", "", a.lower()) for a in (key, disp, *aliases)}:
            return key
    # A future generation must not silently inherit an older model's catalog.
    if re.search(r"\b(?:19|20)\d{2}\b", n) and "2026" not in n.split():
        return None
    # loose contains match (e.g. "Free 3" variants)
    for key, (_disp, _pid, aliases) in sorted(
            MODELS.items(), key=lambda item: max(map(len, item[1][2])), reverse=True):
        if any(re.search(r"(?<!\w)" + re.escape(re.sub(r"[\W_]+", " ", a)) + r"(?!\w)", n)
               for a in aliases):
            return key
    return None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_variants(page: str) -> list[Variant]:
    # Include each card's title/date so selection never depends on an ID's order.
    marks = [m.start() for m in re.finditer(
        r'<div\b[^>]*class=["\'][^"\']*\bdriver-item\b[^"\']*["\']', page)]
    if not marks:
        marks = [m.start() for m in re.finditer("固件编号", page)]
    marks.append(len(page))
    variants: list[Variant] = []
    for i in range(len(marks) - 1):
        card = page[marks[i]:marks[i + 1]]
        url_m = re.search(r'https://cdn\.hanlinyue\.com\.cn/([A-Za-z0-9._-]+\.ufw)', card)
        if not url_m:
            continue

        def field(label: str) -> str:
            m = re.search(label + r"[：:]\s*</div>\s*<div[^>]*>(.*?)</div>", card, re.S)
            return _clean(m.group(1)) if m else ""

        fw_id = field("固件编号") or url_m.group(1).rsplit(".", 1)[0]
        title = re.search(r'class=["\']driver-name["\'][^>]*>\s*<span[^>]*>(.*?)</span>',
                          card, re.S)
        released = re.search(r"发布时间[：:]\s*(\d{4})[-.](\d{1,2})[-.](\d{1,2})", card)
        published = ""
        if released:
            try:
                published = date(*map(int, released.groups())).isoformat()
            except ValueError:
                pass
        variants.append(Variant(
            fw_id=fw_id,
            url=url_m.group(0),
            devices=field("适用设备"),
            direction=field("按键方向") or field("按键方式"),
            changelog=field("更新日志") or field("基础说明"),
            title=_clean(re.split(r"<span\b", title.group(1), maxsplit=1)[0]) if title else "",
            released=published,
        ))
    # de-dup by url, preserve order
    seen: set[str] = set()
    uniq: list[Variant] = []
    for v in variants:
        if v.url not in seen:
            seen.add(v.url)
            uniq.append(v)
    return uniq


def list_variants(model_key: str) -> list[Variant]:
    _, pid, _ = MODELS[model_key]
    return parse_variants(_fetch(BASE + pid).decode("utf-8", "replace"))


def latest_general(variants: list[Variant]) -> Variant:
    """Select only explicitly labelled general/multi-device releases."""
    candidates = [v for v in variants if v.title in ("多设备固件", "通用固件")]
    if not candidates:
        raise ValueError("The catalog has no explicitly labelled general firmware; use --list and --id")
    if len(candidates) == 1:
        return candidates[0]
    if any(not v.released for v in candidates):
        raise ValueError("General releases have missing dates; select an explicit --id")
    newest = max(v.released for v in candidates)
    matches = [v for v in candidates if v.released == newest]
    if len(matches) != 1:
        raise ValueError("Multiple general releases share the latest date; select an explicit --id")
    return matches[0]


def download(url: str, dest: str) -> int:
    data = _fetch(url)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)
