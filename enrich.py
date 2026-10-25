"""Target keyword + search volume (DataForSEO / Keywords Everywhere) + free demand estimate fallback + NLP category."""
import os, re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import requests
import ai, db, nlp

STOP = set("a an the and or of to in for on with your you how what why when is are be best top guide vs from by at as it this that our we i can do does should will new complete ultimate".split())
TAIL = {"guide", "tips", "examples", "tutorial", "checklist", "explained", "practical", "complete", "ultimate", "overview", "definition", "india"}
GENERIC = {"overview", "introduction", "intro", "summary", "home", "blog", "news", "academy", "untitled", ""}
LEAD = re.compile(r"^(what (is|are)|the|a|an)\s+")

def _clean(text):
    t = re.sub(r"\([^)]*\)|\[[^]]*\]", "", text)
    t = re.split(r"\s[|–—-]\s", t)[0]
    t = re.split(r"[?:]", t)[0].lower()
    s = LEAD.sub("", " ".join(re.findall(r"[a-z0-9']+", t)))
    words = [w for w in s.split() if not re.fullmatch(r"20\d\d", w)]
    if len(words) > 6: words = [w for w in words if w not in STOP][:5]
    while words and (words[-1] in TAIL or words[-1] in STOP): words.pop()
    while words and words[0] in STOP and words[0] != "how": words.pop(0)
    return " ".join(words[:6])

def guess_keyword(p):
    mk = (p.get("meta_keywords") or "").split(",")[0].strip().lower()
    if 2 <= len(mk.split()) <= 6: return mk
    kw = _clean(p.get("title") or "")
    if kw in GENERIC or len(kw) < 3:
        slug = urlparse(p["url"]).path.rstrip("/").split("/")[-1]
        kw = _clean(re.sub(r"[-_]+", " ", slug)) or kw or slug
    return kw[:80]

def _dataforseo(kws, login, pw):
    out = {}
    for i in range(0, len(kws), 700):
        r = requests.post("https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live", auth=(login, pw), timeout=60,
                          json=[{"keywords": kws[i:i + 700], "location_code": 2840, "language_code": "en"}])
        if r.status_code in (401, 403): raise PermissionError("DataForSEO credentials rejected (HTTP %d)" % r.status_code)
        r.raise_for_status()
        j = r.json()
        if j.get("status_code") in (40100, 40101, 40102): raise PermissionError("DataForSEO credentials rejected")
        for t in j.get("tasks", []):
            if t.get("status_code") in (40200, 40201): raise PermissionError("DataForSEO account has no credit left")
            for it in (t.get("result") or []):
                out[it["keyword"].lower()] = it.get("search_volume")
    return out

def _kwe(kws, key):
    out = {}
    for i in range(0, len(kws), 100):
        r = requests.post("https://api.keywordseverywhere.com/v1/get_keyword_data", timeout=40,
                          headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                          data={"country": "us", "currency": "USD", "dataSource": "gkp", "kw[]": kws[i:i + 100]})
        if r.status_code in (401, 403): raise PermissionError("Keywords Everywhere API key rejected (HTTP %d)" % r.status_code)
        r.raise_for_status()
        for it in r.json().get("data", []):
            out[it["keyword"].lower()] = it.get("vol")
    return out

def _suggest(kw):
    """Free 0-100 demand estimate from Google Autocomplete. NOT real search volume."""
    try:
        s = requests.get("https://suggestqueries.google.com/complete/search", params={"client": "firefox", "q": kw},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=6).json()[1]
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return None
    s = [x.lower() for x in s]
    score = min(60, len(s) * 6)
    if s and s[0].startswith(kw.lower()): score += 25
    if kw.lower() in s: score += 15
    return min(100, score)

def _demand(posts):
    kws = sorted({p["keyword"] for p in posts})
    with ThreadPoolExecutor(6) as ex:
        d = dict(zip(kws, ex.map(_suggest, kws)))
    for p in posts: p["demand"] = d.get(p["keyword"])

def _cfg(name, env): return db.get_setting(name) or os.environ.get(env)

def enrich(posts):
    """Rules -> optional AI (category, keyword, summary) -> volume or demand estimate.
    Returns (volume_status, ai_status). Never raises."""
    for p in posts:
        p["keyword"] = guess_keyword(p); p["volume"] = None; p["demand"] = None
        p["category"], p["fmt"] = nlp.categorize(p)
    if not posts: return "nothing to enrich", "-"
    if ai.enabled():
        try: ai_status = f"AI analyzed {ai.analyze_posts(posts)} articles"
        except ai.AIError as e: ai_status = f"AI skipped: {e}"
    else:
        ai_status = "AI off (add an Anthropic key in Settings)"
    heads = {k: " ".join(k.split()[:3]) for k in {p["keyword"] for p in posts}}
    kws = sorted(set(heads) | set(heads.values()))
    login, pw = _cfg("dataforseo_login", "DATAFORSEO_LOGIN"), _cfg("dataforseo_password", "DATAFORSEO_PASSWORD")
    kwe = _cfg("kwe_key", "KEYWORDS_EVERYWHERE_API_KEY")
    vols, src, status = {}, None, None
    try:
        if login and pw: vols, src = _dataforseo(kws, login, pw), "DataForSEO"
        elif kwe: vols, src = _kwe(kws, kwe), "Keywords Everywhere"
        else: status = "No volume API key set (open Settings). Showing a free demand estimate instead of real volume"
    except PermissionError as e:
        status = f"{e}. Showing a free demand estimate instead"
    except (requests.RequestException, ValueError, KeyError) as e:
        status = f"Keyword API error ({type(e).__name__}). Showing a free demand estimate instead"
    for p in posts:
        v = vols.get(p["keyword"].lower())
        p["volume"] = v if v is not None else vols.get(heads[p["keyword"]].lower())
    missing = [p for p in posts if p["volume"] is None]
    if missing: _demand(missing)
    return (status or f"ok ({src})"), ai_status
