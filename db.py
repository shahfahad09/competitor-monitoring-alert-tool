import os, sqlite3, time, json
from contextlib import contextmanager

DB = os.environ.get("DB_PATH", "monitor.db")

@contextmanager
def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS posts(url TEXT PRIMARY KEY, domain TEXT, title TEXT, published TEXT,
            description TEXT, keyword TEXT, volume INTEGER, first_seen REAL);
        CREATE TABLE IF NOT EXISTS seen(url TEXT PRIMARY KEY, domain TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, ts REAL, new_posts INTEGER, status TEXT);
        CREATE TABLE IF NOT EXISTS pricing(id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, url TEXT, hash TEXT, prices TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, text TEXT, ts REAL);
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(posts)")}
        for col, typ in (("category", "TEXT"), ("fmt", "TEXT"), ("demand", "INTEGER"), ("is_new", "INTEGER DEFAULT 0"), ("summary", "TEXT"), ("ai", "INTEGER DEFAULT 0")):
            if col not in cols:
                c.execute(f"ALTER TABLE posts ADD COLUMN {col} {typ}")
                if col == "is_new":  # old rows: anything saved after a domain's first scan counts as new
                    c.execute("""UPDATE posts SET is_new = CASE WHEN first_seen >
                        (SELECT MIN(ts) FROM runs r WHERE r.domain=posts.domain AND r.status='ok') THEN 1 ELSE 0 END""")

# ---- settings ----
def get_setting(k, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r and r["value"] else default

def set_setting(k, v):
    with conn() as c:
        if not v: c.execute("DELETE FROM settings WHERE key=?", (k,))
        else: c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (k, v))

# ---- posts ----
def known_urls(domain):
    with conn() as c:
        a = {r["url"] for r in c.execute("SELECT url FROM posts WHERE domain=?", (domain,))}
        b = {r["url"] for r in c.execute("SELECT url FROM seen WHERE domain=?", (domain,))}
        return a | b

def add_seen(domain, urls):
    with conn() as c:
        c.executemany("INSERT OR IGNORE INTO seen(url,domain) VALUES (?,?)", [(u, domain) for u in urls])

def run_count(domain):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM runs WHERE domain=? AND status='ok'", (domain,)).fetchone()[0]

def save_posts(domain, posts, is_new=0):
    now = time.time()
    with conn() as c:
        for p in posts:
            c.execute("""INSERT OR IGNORE INTO posts(url,domain,title,published,description,keyword,volume,demand,category,fmt,summary,ai,first_seen,is_new)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["url"], domain, p["title"], p.get("published"), p.get("description"), p.get("keyword"), p.get("volume"),
                 p.get("demand"), p.get("category"), p.get("fmt"), p.get("summary"), int(p.get("ai") or 0), now, int(is_new)))

def update_enrichment(posts):
    with conn() as c:
        c.executemany("UPDATE posts SET keyword=?,volume=?,demand=?,category=?,fmt=?,summary=?,ai=? WHERE url=?",
                      [(p.get("keyword"), p.get("volume"), p.get("demand"), p.get("category"), p.get("fmt"),
                        p.get("summary"), int(p.get("ai") or 0), p["url"]) for p in posts])

def posts_by(domain=None, keyword=None, category=None, only_new=False, limit=500):
    q, a = "SELECT * FROM posts WHERE 1=1", []
    if domain: q += " AND domain=?"; a.append(domain)
    if keyword: q += " AND keyword=?"; a.append(keyword)
    if category: q += " AND COALESCE(category,'General')=?"; a.append(category)
    if only_new: q += " AND is_new=1"
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY first_seen DESC, published DESC LIMIT ?", a + [limit])]

def mark_seen():
    with conn() as c: c.execute("UPDATE posts SET is_new=0")

def categories():
    out = {}
    with conn() as c:
        for r in c.execute("SELECT domain, COALESCE(category,'General') cat, COUNT(*) n FROM posts GROUP BY domain, cat"):
            out.setdefault(r["domain"], {})[r["cat"]] = r["n"]
    return out

def keywords():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT keyword, MAX(volume) volume, MAX(demand) demand, COUNT(*) n, GROUP_CONCAT(DISTINCT domain) domains FROM posts "
            "WHERE keyword IS NOT NULL AND keyword<>'' GROUP BY keyword "
            "ORDER BY (MAX(volume) IS NULL), MAX(volume) DESC, MAX(demand) DESC, n DESC")]

def delete_post(url):
    with conn() as c:
        r = c.execute("SELECT domain FROM posts WHERE url=?", (url,)).fetchone()
        c.execute("DELETE FROM posts WHERE url=?", (url,))
        if r: c.execute("INSERT OR IGNORE INTO seen(url,domain) VALUES (?,?)", (url, r["domain"]))

def delete_competitor(domain):
    with conn() as c:
        for t in ("posts", "seen", "runs", "pricing"):
            c.execute(f"DELETE FROM {t} WHERE domain=?", (domain,))

# ---- runs / overview ----
def add_run(domain, new_posts, status):
    with conn() as c:
        c.execute("INSERT INTO runs(domain,ts,new_posts,status) VALUES (?,?,?,?)", (domain, time.time(), new_posts, status))

def runs(limit=100):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

def delete_run(rid):
    with conn() as c: c.execute("DELETE FROM runs WHERE id=?", (rid,))

def overview():
    with conn() as c:
        return {
            "articles": c.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
            "with_volume": c.execute("SELECT COUNT(*) FROM posts WHERE volume IS NOT NULL").fetchone()[0],
            "new_count": c.execute("SELECT COUNT(*) FROM posts WHERE is_new=1").fetchone()[0],
            "ai_count": c.execute("SELECT COUNT(*) FROM posts WHERE ai=1").fetchone()[0],
            "last_scan": c.execute("SELECT MAX(ts) FROM runs").fetchone()[0],
            "competitors": [dict(r) for r in c.execute(
                "SELECT domain, (SELECT COUNT(*) FROM posts p WHERE p.domain=r.domain) posts, MAX(ts) last_scan "
                "FROM runs r GROUP BY domain ORDER BY last_scan DESC")]}

# ---- pricing ----
def last_pricing(domain):
    with conn() as c:
        r = c.execute("SELECT * FROM pricing WHERE domain=? ORDER BY id DESC LIMIT 1", (domain,)).fetchone()
        return dict(r) if r else None

def save_pricing(domain, url, h, prices):
    with conn() as c:
        c.execute("INSERT INTO pricing(domain,url,hash,prices,ts) VALUES (?,?,?,?,?)", (domain, url, h, json.dumps(prices), time.time()))

def pricing_changes():
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM pricing ORDER BY domain, id")]
    out, prev = [], {}
    for r in rows:
        cur = set(json.loads(r["prices"])); p = prev.get(r["domain"])
        if p is not None and cur != p:
            out.append({"domain": r["domain"], "ts": r["ts"], "url": r["url"], "added": sorted(cur - p), "removed": sorted(p - cur)})
        prev[r["domain"]] = cur
    return sorted(out, key=lambda x: -x["ts"])[:20]

# ---- AI cache (insights, digest) ----
def cache_get(key):
    with conn() as c:
        r = c.execute("SELECT text, ts FROM ai_cache WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

def cache_set(key, text):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO ai_cache(key,text,ts) VALUES (?,?,?)", (key, text, time.time()))

def recent_posts(days=7):
    since = time.time() - days * 86400
    cutoff = time.strftime("%Y-%m-%d", time.localtime(since))
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM posts WHERE first_seen>? AND (is_new=1 OR published>=?) ORDER BY first_seen DESC LIMIT 80", (since, cutoff))]
