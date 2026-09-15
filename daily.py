#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 情报日报

从多个 RSS / API 源抓取 AI 资讯，去重、打分、分组，
生成一份可搜索、可筛选、带归档的本地简报网页。仅依赖 Python 标准库。

用法:
    python daily.py                 抓取并生成今日简报
    python daily.py --hours 72      放宽时间窗口到 72 小时
    python daily.py --rebuild       用已缓存的 JSON 重新渲染，不联网
    python daily.py --open          生成后尝试用默认浏览器打开
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = ROOT / "archive"
OUT_HTML = ROOT / "index.html"

# PWA 静态资源：输出到独立目录（如 GitHub Pages 的 _site）时需一并复制
PWA_ASSETS = [
    "manifest.json",
    "sw.js",
    "icon-192.png",
    "icon-512.png",
    "apple-touch-icon.png",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

GROUP_ORDER = ["今日要点", "官方发布", "行业动态", "研究前沿", "社区热议"]

SOURCES = [
    {"id": "openai",     "name": "OpenAI",          "group": "官方发布", "kind": "rss", "weight": 1.3,
     "url": "https://openai.com/blog/rss.xml", "window_h": 336, "max": 12},
    {"id": "deepmind",   "name": "Google DeepMind", "group": "官方发布", "kind": "rss", "weight": 1.3,
     "url": "https://deepmind.google/blog/rss.xml", "window_h": 336, "max": 12},
    {"id": "googleai",   "name": "Google AI",       "group": "官方发布", "kind": "rss", "weight": 1.1,
     "url": "https://blog.google/technology/ai/rss/", "window_h": 336, "max": 10},
    {"id": "qbitai",     "name": "量子位",           "group": "行业动态", "kind": "rss", "weight": 1.2,
     "url": "https://www.qbitai.com/feed", "window_h": 72, "max": 20},
    {"id": "infoqcn",    "name": "InfoQ 中国",       "group": "行业动态", "kind": "rss", "weight": 0.9,
     "url": "https://www.infoq.cn/feed", "window_h": 72, "max": 15},
    {"id": "techcrunch", "name": "TechCrunch AI",   "group": "行业动态", "kind": "rss", "weight": 1.0,
     "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "window_h": 48, "max": 20},
    {"id": "verge",      "name": "The Verge AI",    "group": "行业动态", "kind": "rss", "weight": 1.0,
     "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "window_h": 48, "max": 20},
    {"id": "venturebeat","name": "VentureBeat AI",  "group": "行业动态", "kind": "rss", "weight": 1.0,
     "url": "https://venturebeat.com/category/ai/feed/", "window_h": 48, "max": 15},
    {"id": "mittr",      "name": "MIT Tech Review", "group": "行业动态", "kind": "rss", "weight": 1.0,
     "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
     "window_h": 96, "max": 12},
    {"id": "arxiv",      "name": "arXiv cs.AI",     "group": "研究前沿", "kind": "rss", "weight": 0.9,
     "url": "https://export.arxiv.org/rss/cs.AI", "window_h": 48, "max": 25},
    {"id": "simon",      "name": "Simon Willison",  "group": "社区热议", "kind": "rss", "weight": 1.2,
     "url": "https://simonwillison.net/atom/everything/", "window_h": 96, "max": 12},
    {"id": "hn",         "name": "Hacker News",     "group": "社区热议", "kind": "hn", "weight": 1.0,
     "url": "https://hn.algolia.com/api/v1/search", "window_h": 48, "max": 25},
]

TERMS = [
    ("gpt", 3), ("claude", 3), ("gemini", 3), ("llm", 3), ("大模型", 3),
    ("openai", 3), ("anthropic", 3), ("deepseek", 3), ("deepmind", 2),
    ("kimi", 2), ("qwen", 2), ("通义", 2), ("llama", 2), ("mistral", 2),
    ("glm", 2), ("智谱", 2), ("agent", 2), ("智能体", 2), ("copilot", 2),
    ("多模态", 2), ("multimodal", 2), ("开源", 2), ("open-source", 2),
    ("benchmark", 2), ("刷榜", 2), ("排名", 2), ("榜单", 2),
    ("融资", 2), ("funding", 2), ("芯片", 2), ("gpu", 2),
    ("nvidia", 2), ("英伟达", 2), ("transformer", 1), ("diffusion", 1),
    ("微调", 1), ("fine-tun", 1), ("推理", 1), ("reasoning", 1),
    ("发布", 1), ("launch", 1), ("release", 1), ("api", 1),
]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def clean_text(raw: str | None, limit: int = 220) -> str:
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub(" ", raw))
    text = WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CST)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CST)
    except Exception:
        pass
    return None


def norm_key(title: str) -> str:
    return PUNCT_RE.sub("", title.lower())[:120]


def http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
            "Accept-Encoding": "identity",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def local_tag(name: str) -> str:
    return name.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def parse_rss(payload: bytes, source: dict) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    is_atom = local_tag(root.tag) == "feed"
    entries = root.findall(".//{*}entry") if is_atom else root.findall(".//item")
    if is_atom and not entries:
        entries = root.findall(".//entry")

    items: list[dict] = []
    for node in entries:
        title = clean_text(_child_text(node, "title"), 200)
        if not title:
            continue

        link = ""
        if is_atom:
            for a in node.findall("{*}link"):
                href = (a.get("href") or "").strip()
                rel = (a.get("rel") or "alternate").strip()
                if href and rel == "alternate":
                    link = href
                    break
                if href and not link:
                    link = href
        else:
            link = (_child_text(node, "link") or "").strip()

        published = parse_date(
            _child_text(node, "pubDate")
            or _child_text(node, "published")
            or _child_text(node, "updated")
            or _child_text(node, "date")
        )

        summary = (
            _child_text(node, "description")
            or _child_text(node, "summary")
            or _child_text(node, "content")
            or ""
        )

        items.append(
            {
                "title": title,
                "url": link,
                "source": source["name"],
                "source_id": source["id"],
                "group": source["group"],
                "published": published.isoformat() if published else None,
                "summary": clean_text(summary),
                "points": 0,
            }
        )
    return items


def _child_text(node, name: str) -> str:
    """读取子节点文本，兼容 RSS 2.0 与带命名空间的 Atom。"""
    local = name.rsplit("}", 1)[-1] if name.startswith("{") else name
    child = node.find(local)
    if child is None:
        try:
            child = node.find("{*}" + local)
        except SyntaxError:
            child = None
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def parse_hn(payload: bytes, source: dict, since: datetime) -> list[dict]:
    data = json.loads(payload.decode("utf-8", "replace"))
    items: list[dict] = []
    for hit in data.get("hits", []):
        title = (hit.get("title") or hit.get("story_title") or "").strip()
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title:
            continue
        created = hit.get("created_at")
        published = parse_date(created)
        if published and published < since:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "source": source["name"],
                "source_id": source["id"],
                "group": source["group"],
                "published": published.isoformat() if published else None,
                "summary": "",
                "points": int(hit.get("points") or 0),
            }
        )
    return items


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def fetch_hn(source: dict, since: datetime) -> list[dict]:
    """Algolia 不支持 OR 语法，改为多关键词分别查询后合并。"""
    import urllib.parse

    seen: set[str] = set()
    items: list[dict] = []
    for term in ("AI", "LLM", "GPT", "Claude", "agent"):
        query = urllib.parse.urlencode(
            {
                "query": term,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(since.timestamp())}",
                "hitsPerPage": 30,
            }
        )
        try:
            payload = http_get(f"{source['url']}?{query}")
        except Exception:  # noqa: BLE001
            continue
        for item in parse_hn(payload, source, since):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)
    items.sort(key=lambda x: x.get("points", 0), reverse=True)
    return items


def fetch_source(source: dict, base_hours: int) -> tuple[list[dict], str | None]:
    hours = max(base_hours, int(source.get("window_h", 0)))
    since = datetime.now(CST) - timedelta(hours=hours)
    try:
        if source["kind"] == "hn":
            items = fetch_hn(source, since)
        else:
            items = parse_rss(http_get(source["url"]), source)
    except urllib.error.URLError as exc:
        return [], f"{type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:60]}"

    kept = []
    for it in items:
        if it["published"] is None:
            continue
        if datetime.fromisoformat(it["published"]) < since:
            continue
        kept.append(it)
    return kept, None


# --------------------------------------------------------------------------
# 打分与去重
# --------------------------------------------------------------------------
def relevance(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    return sum(weight for term, weight in TERMS if term in text)


# 日报要点应以新闻为主、论文为辅，避免 arXiv 靠关键词密度霸榜
GROUP_BONUS = {"官方发布": 7, "行业动态": 5, "社区热议": 3, "研究前沿": 0}


def score_item(item: dict, now: datetime, source_weight: float) -> float:
    value = relevance(item["title"], item["summary"]) * 1.0
    value += source_weight * 4
    value += GROUP_BONUS.get(item["group"], 0)
    if item["published"]:
        hours = (now - datetime.fromisoformat(item["published"])).total_seconds() / 3600
        value += max(0.0, 8 - hours / 8)
    value += min(item.get("points", 0), 300) / 40
    return round(value, 2)


def dedupe(items: list[dict]) -> list[dict]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = norm_key(it["title"])
        url = it["url"].split("#")[0].rstrip("/")
        if key in seen_titles or (url and url in seen_urls):
            continue
        seen_titles.add(key)
        if url:
            seen_urls.add(url)
        out.append(it)
    return out


def pick_highlights(items: list[dict], limit: int = 5) -> list[dict]:
    ranked = sorted(items, key=lambda x: x["score"], reverse=True)
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    for it in ranked:
        count = per_source.get(it["source_id"], 0)
        if count >= 2:
            continue
        picked.append(it)
        per_source[it["source_id"]] = count + 1
        if len(picked) >= limit:
            break
    return picked


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:#faf9f7;color:#2c2c2a;line-height:1.6;padding:32px 20px 64px}
.wrap{max-width:920px;margin:0 auto}
header{margin-bottom:24px}
.brand{font-size:13px;letter-spacing:.14em;color:#888780;text-transform:uppercase}
h1{font-size:26px;font-weight:500;margin:6px 0 4px}
.sub{font-size:13px;color:#5f5e5a}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.stat{background:#fff;border:.5px solid rgba(0,0,0,.12);border-radius:8px;padding:10px 14px;min-width:96px}
.stat .k{font-size:11px;color:#888780}
.stat .v{font-size:19px;font-weight:500}
.toolbar{position:sticky;top:0;z-index:5;background:#faf9f7;padding:14px 0 10px;margin:22px 0 8px;
border-bottom:.5px solid rgba(0,0,0,.1)}
#q{width:100%;padding:10px 12px;font-size:14px;border:.5px solid rgba(0,0,0,.2);
border-radius:8px;background:#fff;color:#2c2c2a;font-family:inherit}
#q:focus{outline:2px solid #185fa5;outline-offset:-1px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.chip{font-size:12px;padding:5px 12px;border:.5px solid rgba(0,0,0,.2);border-radius:999px;
background:#fff;color:#5f5e5a;cursor:pointer;user-select:none;font-family:inherit}
.chip:hover{border-color:rgba(0,0,0,.4)}
.chip.on{background:#2c2c2a;color:#fff;border-color:#2c2c2a}
h2{font-size:15px;font-weight:500;margin:26px 0 12px;display:flex;align-items:center;gap:8px}
h2 .n{font-size:12px;color:#888780;font-weight:400}
.item{display:block;text-decoration:none;color:inherit;background:#fff;
border:.5px solid rgba(0,0,0,.12);border-radius:12px;padding:14px 16px;margin-bottom:10px;
transition:border-color .12s,transform .12s}
.item:hover{border-color:rgba(0,0,0,.35);transform:translateY(-1px)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:11px;color:#888780;margin-bottom:6px}
.src{background:#f1efe8;border-radius:4px;padding:2px 7px;color:#5f5e5a}
.hot{background:#fcebeb;color:#a32d2d;border-radius:4px;padding:2px 7px}
.item h3{font-size:15px;font-weight:500;line-height:1.45;margin-bottom:4px}
.item p{font-size:13px;color:#5f5e5a;line-height:1.6}
.hl{background:#fff;border:.5px solid rgba(0,0,0,.12);border-left:3px solid #7f77dd;
border-radius:8px;padding:14px 16px;margin-bottom:10px}
.hl a{text-decoration:none;color:inherit;display:block}
.hl h3{font-size:15px;font-weight:500;line-height:1.45;margin:4px 0}
.hl p{font-size:13px;color:#5f5e5a}
.empty{background:#fff;border:.5px dashed rgba(0,0,0,.2);border-radius:12px;
padding:28px;text-align:center;color:#888780;font-size:13px}
footer{margin-top:34px;padding-top:16px;border-top:.5px solid rgba(0,0,0,.1);
font-size:12px;color:#888780;line-height:1.8}
footer code{background:#f1efe8;padding:1px 5px;border-radius:4px;font-size:11px}
.warn{color:#a32d2d}
@media(max-width:600px){
body{padding:18px 12px 48px}
h1{font-size:22px}
.brand{font-size:12px}
.sub{font-size:12px}
.stat{min-width:76px;padding:8px 10px}
.stat .v{font-size:17px}
.item{padding:12px 13px;border-radius:10px}
.item h3{font-size:15px}
.hl{padding:12px 13px}
#q{font-size:16px}
.chip{padding:6px 13px}
}
"""

JS = """
(function(){
  var q=document.getElementById('q');
  var chips=[].slice.call(document.querySelectorAll('.chip'));
  var blocks=[].slice.call(document.querySelectorAll('[data-group]'));
  var filter=function(){
    var kw=(q.value||'').trim().toLowerCase();
    var active=chips.filter(function(c){return c.classList.contains('on')})[0];
    var want=active?active.getAttribute('data-g'):'ALL';
    var shown=0;
    blocks.forEach(function(b){
      var okG=(want==='ALL'||b.getAttribute('data-group')===want);
      var hay=(b.getAttribute('data-hay')||'').toLowerCase();
      var okK=(!kw||hay.indexOf(kw)>-1);
      var vis=okG&&okK;
      b.style.display=vis?'':'none';
      if(vis)shown++;
    });
    var heads=[].slice.call(document.querySelectorAll('h2[data-sec]'));
    heads.forEach(function(h){
      var key=h.getAttribute('data-sec');
      var any=blocks.some(function(b){
        return b.getAttribute('data-group')===key && b.style.display!=='none';
      });
      h.style.display=any?'':'none';
    });
    var e=document.getElementById('noresult');
    e.style.display=shown?'none':'';
  };
  if(q)q.addEventListener('input',filter);
  chips.forEach(function(c){
    c.addEventListener('click',function(){
      chips.forEach(function(x){x.classList.remove('on')});
      c.classList.add('on');
      filter();
    });
  });
})();
"""


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def rel_time(iso: str | None, now: datetime) -> str:
    if not iso:
        return "时间未知"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "时间未知"
    delta = now - dt
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"


def item_card(item: dict, now: datetime) -> str:
    hot = ""
    if item.get("points", 0) >= 100:
        hot = f'<span class="hot">{item["points"]} 分</span>'
    elif item.get("points", 0) > 0:
        hot = f'<span class="src">{item["points"]} 分</span>'
    summary = f'<p>{esc(item["summary"])}</p>' if item["summary"] else ""
    return (
        f'<a class="item" href="{esc(item["url"])}" target="_blank" rel="noopener" '
        f'data-group="{esc(item["group"])}" '
        f'data-hay="{esc((item["title"] + " " + item["summary"] + " " + item["source"]).lower())}">'
        f'<div class="meta"><span class="src">{esc(item["source"])}</span>'
        f"{hot}<span>{rel_time(item['published'], now)}</span></div>"
        f"<h3>{esc(item['title'])}</h3>{summary}</a>"
    )


def render_html(items: list[dict], highlights: list[dict], now: datetime,
                failures: dict[str, str], hours: int) -> str:
    chips = ['<span class="chip on" data-g="ALL">全部</span>']
    for group in GROUP_ORDER[1:]:
        chips.append(f'<span class="chip" data-g="{esc(group)}">{esc(group)}</span>')

    hl_html = ""
    for idx, item in enumerate(highlights, 1):
        hl_html += (
            '<div class="hl">'
            f'<a href="{esc(item["url"])}" target="_blank" rel="noopener">'
            f'<div class="meta"><span class="src">{esc(item["source"])}</span>'
            f'<span>{rel_time(item["published"], now)}</span></div>'
            f"<h3>{idx}. {esc(item['title'])}</h3>"
            + (f'<p>{esc(item["summary"])}</p>' if item["summary"] else "")
            + "</a></div>"
        )

    sections = ""
    for group in GROUP_ORDER[1:]:
        group_items = [i for i in items if i["group"] == group]
        if not group_items:
            continue
        body = "".join(item_card(i, now) for i in group_items)
        sections += (
            f'<h2 data-sec="{esc(group)}">{esc(group)} <span class="n">{len(group_items)} 条</span></h2>'
            + body
        )

    if not sections:
        sections = '<div class="empty">当前时间窗口内没有抓到内容，试试加大 <code>--hours</code>。</div>'

    src_count = len({i["source_id"] for i in items})
    fail_html = ""
    if failures:
        fail_html = "<br><span class='warn'>未响应源：" + esc(
            "、".join(f"{k}({v})" for k, v in failures.items())
        ) + "</span>"

    weekday = "一二三四五六日"[now.weekday()]
    date_str = f"{now.year} 年 {now.month} 月 {now.day} 日 · 星期{weekday}"

    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='theme-color' content='#faf9f7'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='default'>"
        "<meta name='apple-mobile-web-app-title' content='AI日报'>"
        "<link rel='manifest' href='manifest.json'>"
        "<link rel='apple-touch-icon' href='apple-touch-icon.png'>"
        "<link rel='icon' type='image/png' sizes='192x192' href='icon-192.png'>"
        f"<title>AI 情报日报 · {esc(date_str)}</title><style>{CSS}</style></head><body><div class='wrap'>"
        "<header><div class='brand'>AI Daily Brief</div>"
        f"<h1>AI 情报日报</h1><div class='sub'>{esc(date_str)} · 覆盖最近 {hours} 小时</div>"
        "<div class='stats'>"
        f"<div class='stat'><div class='k'>条目</div><div class='v'>{len(items)}</div></div>"
        f"<div class='stat'><div class='k'>信源</div><div class='v'>{src_count}</div></div>"
        f"<div class='stat'><div class='k'>要点</div><div class='v'>{len(highlights)}</div></div>"
        f"<div class='stat'><div class='k'>更新</div><div class='v'>{now:%H:%M}</div></div>"
        "</div></header>"
        "<div class='toolbar'>"
        "<input id='q' type='search' placeholder='搜索标题、摘要或来源…' autocomplete='off'>"
        f"<div class='chips'>{''.join(chips)}</div></div>"
        f"<h2 data-sec='今日要点'>今日要点 <span class='n'>按热度与时效排序</span></h2>"
        + (hl_html or '<div class="empty">暂无要点</div>')
        + sections
        + "<div class='empty' id='noresult' style='display:none'>没有匹配的条目，换个关键词试试。</div>"
        + "<footer>数据源：量子位 · InfoQ 中国 · arXiv cs.AI · OpenAI · Google DeepMind · "
        "Google AI · TechCrunch · The Verge · VentureBeat · MIT Technology Review · "
        "Simon Willison · Hacker News"
        f"{fail_html}<br>本地生成，无追踪、无外链脚本。重新抓取：<code>python daily.py</code>"
        "</footer></div>"
        f"<script>{JS}</script>"
        "<script>if('serviceWorker' in navigator){window.addEventListener('load',"
        "function(){navigator.serviceWorker.register('sw.js').catch(function(){});});}</script>"
        "</body></html>"
    )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def load_cached(now: datetime, hours: int) -> list[dict] | None:
    if not DATA_DIR.exists():
        return None
    files = sorted(DATA_DIR.glob("items-*.json"), reverse=True)
    for path in files:
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("items", [])
        except Exception:  # noqa: BLE001
            continue
    return None


def collect_all(base_hours: int, verbose: bool = False) -> tuple[list[dict], dict[str, str]]:
    """并发抓取全部数据源。"""
    failures: dict[str, str] = {}
    collected: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_source, s, base_hours): s for s in SOURCES}
        for future, source in futures.items():
            got, err = future.result()
            if err:
                failures[source["name"]] = err
                if verbose:
                    print(f"  x {source['name']}: {err}")
            else:
                if verbose:
                    print(f"  v {source['name']}: {len(got)} 条")
                collected.extend(got)
    return collected, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AI 情报日报")
    parser.add_argument("--hours", type=int, default=48, help="抓取时间窗口（小时）")
    parser.add_argument("--rebuild", action="store_true", help="用缓存 JSON 重新渲染，不联网")
    parser.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    parser.add_argument("--out", type=str, default=None,
                        help="输出目录，默认为脚本所在目录；GitHub Pages 用 --out _site")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    now = datetime.now(CST)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    out_arg = Path(args.out) if args.out else ROOT
    out_dir = (out_arg if out_arg.is_absolute() else ROOT / out_arg).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_html = out_dir / "index.html"

    if args.rebuild:
        items = load_cached(now, args.hours) or []
        failures: dict[str, str] = {}
        print(f"[rebuild] 使用缓存，共 {len(items)} 条")
    else:
        collected, failures = collect_all(args.hours, verbose=True)
        if len(collected) < 10 and args.hours < 168:
            print("[info] 条目偏少，自动放宽到 7 天窗口重抓…")
            collected, failures = collect_all(168, verbose=False)

        weights = {s["id"]: s["weight"] for s in SOURCES}
        for item in collected:
            item["score"] = score_item(item, now, weights.get(item["source_id"], 1.0))

        maxes = {s["id"]: s.get("max", 20) for s in SOURCES}
        by_source: dict[str, list[dict]] = {}
        for item in collected:
            by_source.setdefault(item["source_id"], []).append(item)
        trimmed: list[dict] = []
        for sid, group in by_source.items():
            group.sort(key=lambda x: x["score"], reverse=True)
            trimmed.extend(group[: maxes.get(sid, 20)])

        items = dedupe(trimmed)
        items.sort(key=lambda x: x["score"], reverse=True)

        cache = {
            "generated_at": now.isoformat(),
            "hours": args.hours,
            "failures": failures,
            "items": items,
        }
        (DATA_DIR / f"items-{now:%Y-%m-%d}.json").write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    highlights = pick_highlights(items) if items else []
    page = render_html(items, highlights, now, failures, args.hours)
    out_html.write_text(page, encoding="utf-8")
    (ARCHIVE_DIR / f"daily-{now:%Y-%m-%d}.html").write_text(page, encoding="utf-8")

    for name in PWA_ASSETS:
        src = (ROOT / name).resolve()
        dst = (out_dir / name).resolve()
        if src.exists() and src != dst:
            shutil.copy2(src, dst)

    print(f"\n完成：{len(items)} 条 · {len(highlights)} 条要点")
    print(f"输出：{out_html}")
    if failures:
        print(f"失败源：{failures}")

    if args.open:
        webbrowser.open(out_html.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
