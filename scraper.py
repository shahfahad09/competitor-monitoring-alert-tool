"""Own scraper: homepage -> sitemap + RSS + blog/resource listing pages -> article pages."""
import re, json, hashlib
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dparse

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"})
MAX_ARTICLES = 40

class ScrapeError(Exception): pass
class BlockedError(ScrapeError): pass
class NoBlogError(ScrapeError): pass

BLOCK_RE = re.compile(r"just a moment|attention required|captcha|access denied|verify you are human|are you a robot", re.I)
BLOG_HINT = re.compile(r"/(blog|blogs|resources?|articles?|insights?|news|learn|guides?|stories|posts?|library|knowledge|academy)(/|$)", re.I)
SKIP = re.compile(r"/(tag|tags|category|categories|author|authors|page|topics?|feed|search|wp-content|wp-json)(/|$)|\.(jpg|jpeg|png|gif|svg|pdf|zip|xml|css|js)$", re.I)

def get(url, timeout=15):
    try:
        r = S.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise ScrapeError(f"Could not reach {url} ({type(e).__name__})")
    if r.status_code in (401, 403, 429, 503):
        raise BlockedError(f"{urlparse(url).netloc} blocked the request (HTTP {r.status_code})")
    if r.status_code == 200 and len(r.text) < 30000 and BLOCK_RE.search(r.text[:3000]):
        raise BlockedError(f"{urlparse(url).netloc} served a bot-check / captcha page")
    if r.status_code >= 400:
        raise ScrapeError(f"HTTP {r.status_code} for {url}")
    return r

def normalize(user_input):
    s = user_input.strip()
    if not re.match(r"https?://", s):
        s = "https://" + s
    p = urlparse(s)
    if not p.netloc or "." not in p.netloc:
        raise ScrapeError("That doesn't look like a valid domain")
    return f"{p.scheme}://{p.netloc}", p.netloc.lower().removeprefix("www.")

def same_host(url, host):
    return urlparse(url).netloc.lower().removeprefix("www.") == host

def looks_like_article(url):
    path = urlparse(url).path
    segs = [s for s in path.split("/") if s]
    return bool(BLOG_HINT.search(path)) and len(segs) >= 2 and not SKIP.search(path)

def _text(el, name):
    for c in el.iter():
        if c.tag.split("}")[-1] == name and c.text and c.text.strip():
            return c.text.strip()
    return ""

def _iso(s):
    if not s: return None
    try: return dparse.parse(s).date().isoformat()
    except (ValueError, OverflowError): return None

def _clean(u):
    p = urlparse(u)
    return p._replace(query="", fragment="").geturl().rstrip("/")

# ---------- discovery ----------
def sitemap_candidates(base, host):
    queue = []
    try: queue += re.findall(r"(?im)^sitemap:\s*(\S+)", get(base + "/robots.txt").text)
    except ScrapeError: pass
    queue += [base + "/sitemap.xml", base + "/sitemap_index.xml", base + "/wp-sitemap.xml"]
    seen, out, n = set(), {}, 0
    while queue and n < 12:
        sm = queue.pop(0)
        if sm in seen: continue
        seen.add(sm); n += 1
        try: root = ET.fromstring(get(sm).content)
        except (ScrapeError, ET.ParseError): continue
        for el in root:
            tag = el.tag.split("}")[-1]
            loc = _text(el, "loc")
            if not loc: continue
            if tag == "sitemap":
                if re.search(r"product|tag|categor|author|image|video|page-sitemap", loc, re.I): continue
                if re.search(r"post|blog|article|news|resource", loc, re.I): queue.insert(0, loc)
                else: queue.append(loc)
            elif tag == "url" and same_host(loc, host) and looks_like_article(loc):
                out[_clean(loc)] = {"lastmod": _iso(_text(el, "lastmod"))}
    return out

def feed_candidates(base, host, soup):
    urls = [urljoin(base + "/", l.get("href", "")) for l in soup.select("link[rel~=alternate]")
            if re.search(r"rss|atom|xml", l.get("type", ""))]
    urls += [base + p for p in ("/feed", "/blog/feed", "/rss.xml", "/blog/rss.xml", "/feed.xml", "/blog/feed.xml", "/atom.xml")]
    out = {}
    for u in dict.fromkeys(urls):
        try: root = ET.fromstring(get(u).content)
        except (ScrapeError, ET.ParseError): continue
        for it in root.iter():
            if it.tag.split("}")[-1] not in ("item", "entry"): continue
            link = _text(it, "link")
            if not link:
                for c in it:
                    if c.tag.split("}")[-1] == "link" and c.get("href"):
                        link = c.get("href"); break
            if not link or not same_host(link, host): continue
            desc = _text(it, "description") or _text(it, "summary")
            out[_clean(link)] = {
                "title": _text(it, "title"),
                "published": _iso(_text(it, "pubDate") or _text(it, "published") or _text(it, "updated")),
                "description": BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)[:300]}
        if out: break
    return out

def listing_candidates(base, host, soup):
    listings = []
    for a in soup.find_all("a", href=True):
        u = _clean(urljoin(base + "/", a["href"]))
        p = urlparse(u)
        depth = len([s for s in p.path.split("/") if s])
        if same_host(u, host) and BLOG_HINT.search(p.path) and depth <= 2 and not SKIP.search(p.path):
            listings.append(u)
    pages, visited, out = list(dict.fromkeys(listings))[:4], set(), {}
    while pages and len(visited) < 8:
        lu = pages.pop(0)
        if lu in visited: continue
        visited.add(lu)
        try: s = BeautifulSoup(get(lu).text, "html.parser")
        except ScrapeError: continue
        lp = urlparse(lu).path.rstrip("/")
        ldepth = len([x for x in lp.split("/") if x])
        for a in s.find_all("a", href=True):
            raw = urljoin(lu + "/", a["href"]).split("#")[0]
            if not same_host(raw, host): continue
            if re.search(r"/page/\d+|[?&]page=\d+", raw) and urlparse(raw).path.startswith(lp) and len(pages) < 2:
                pages.append(raw.rstrip("/")); continue
            u = _clean(raw); p = urlparse(u)
            if p.path.startswith(lp + "/") and len([x for x in p.path.split("/") if x]) > ldepth and not SKIP.search(p.path):
                out[u] = {}
    return out

# ---------- article parsing ----------
def _meta(soup, *names):
    for n in names:
        t = (soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
             or soup.find("meta", attrs={"itemprop": n}))
        if t and t.get("content"): return t["content"].strip()
    return ""

def _walk(o):
    if isinstance(o, dict):
        if o.get("datePublished"): return o["datePublished"]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = _walk(v) if isinstance(v, (dict, list)) else None
            if r: return r

def _find_date(soup):
    d = _iso(_meta(soup, "article:published_time", "datePublished", "date", "publish_date", "pubdate"))
    if d: return d
    for sc in soup.find_all("script", type="application/ld+json"):
        try: d = _iso(_walk(json.loads(sc.string or "")))
        except (ValueError, TypeError): d = None
        if d: return d
    t = soup.find("time", attrs={"datetime": True})
    return _iso(t["datetime"]) if t else None

def parse_article(item):
    url, hint = item
    fallback = {"url": url, "title": hint.get("title", ""), "published": hint.get("published") or hint.get("lastmod"),
                "description": hint.get("description", ""), "meta_keywords": ""}
    try:
        soup = BeautifulSoup(get(url).text, "html.parser")
    except ScrapeError:
        return fallback if fallback["title"] else None
    h1 = soup.find("h1")
    title = (_meta(soup, "og:title", "twitter:title") or (h1.get_text(" ", strip=True) if h1 else "")
             or (soup.title.string.strip() if soup.title and soup.title.string else "") or hint.get("title", ""))
    if not title: return None
    return {"url": url, "title": title,
            "published": _find_date(soup) or hint.get("published") or hint.get("lastmod"),
            "description": _meta(soup, "og:description", "description", "twitter:description") or hint.get("description", ""),
            "meta_keywords": _meta(soup, "keywords", "news_keywords")}

# ---------- pricing ----------
PRICE_RE = re.compile(r"(?:[$€£₹]|USD|EUR|INR)\s?\d[\d,]*(?:\.\d+)?(?:\s?/\s?[a-zA-Z]+)?")

def scrape_pricing(base, soup):
    paths = [base + p for p in ("/pricing", "/plans", "/price")]
    for a in soup.find_all("a", href=True):
        if re.search(r"pricing|plans", a["href"], re.I):
            paths.insert(0, urljoin(base + "/", a["href"])); break
    for u in dict.fromkeys(paths):
        try: r = get(u)
        except ScrapeError: continue
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        prices = sorted(set(m.group(0).strip() for m in PRICE_RE.finditer(text)))
        if prices:
            return {"url": r.url, "prices": prices, "hash": hashlib.sha256("|".join(prices).encode()).hexdigest()}
    return None

# ---------- main ----------
def scrape(base, host, known):
    home = get(base)  # raises BlockedError / ScrapeError for bot-walls or dead sites
    fin = urlparse(home.url)
    base = f"{fin.scheme}://{fin.netloc}"
    host = fin.netloc.lower().removeprefix("www.")
    soup = BeautifulSoup(home.text, "html.parser")

    cands = {}
    def merge(d):
        for u, h in d.items():
            cands.setdefault(u, {}).update({k: v for k, v in h.items() if v})
    merge(sitemap_candidates(base, host))
    merge(listing_candidates(base, host, soup))
    merge(feed_candidates(base, host, soup))
    merge({_clean(urljoin(base + "/", a["href"])): {} for a in soup.find_all("a", href=True)
           if same_host(urljoin(base + "/", a["href"]), host) and looks_like_article(urljoin(base + "/", a["href"]))})
    pricing = scrape_pricing(base, soup)
    if not cands:
        raise NoBlogError(f"No blog or resource section found on {host} (no sitemap posts, feed, or listing pages).")

    new = [u for u in cands if u not in known]
    new.sort(key=lambda u: cands[u].get("published") or cands[u].get("lastmod") or "", reverse=True)
    truncated = len(new) > MAX_ARTICLES
    with ThreadPoolExecutor(6) as ex:
        posts = [p for p in ex.map(parse_article, [(u, cands[u]) for u in new[:MAX_ARTICLES]]) if p]
    return {"posts": posts, "pricing": pricing, "stats": {
        "urls_found": len(cands), "already_known": len(cands) - len(new),
        "fetched": len(posts), "truncated": truncated},
        "skipped": new[MAX_ARTICLES:]}
