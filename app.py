import csv, hashlib, hmac, io, ipaddress, json, os, re, socket, threading, time, uuid
from collections import Counter
from datetime import date
from functools import wraps
from flask import Flask, Response, jsonify, render_template, request, session
import ai, alerts, db, enrich, nlp, scraper

ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(("cw:" + ADMIN_PW).encode()).hexdigest()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)
db.init()
JOBS = {}
# SECRETS = ("dataforseo_password", "kwe_key", "slack_webhook", "anthropic_key")
# SETTING_KEYS = ("dataforseo_login", "dataforseo_password", "kwe_key", "anthropic_key", "slack_webhook", "alert_email", "my_domain")

SECRETS = ("dataforseo_password", "kwe_key", "slack_webhook", "gemini_key")
SETTING_KEYS = ("dataforseo_login", "dataforseo_password", "kwe_key", "gemini_key", "slack_webhook", "alert_email", "my_domain")
NOISE = {"guide", "tips", "best", "your", "what", "with", "from", "this", "that", "about", "into", "more", "make", "using", "complete", "ways", "need", "know", "have", "will", "india"}

# ---------- access control ----------
# Local use (no ADMIN_PASSWORD): everyone is admin. Hosted (ADMIN_PASSWORD set): visitors are read-only.
def is_admin(): return (not ADMIN_PW) or bool(session.get("admin"))

def admin_only(fn):
    @wraps(fn)
    def w(*a, **k):
        if not is_admin(): return jsonify(error="Admin only. Please log in."), 403
        return fn(*a, **k)
    return w

def backfill():  # categorize old rows saved before NLP existed
    rows = [p for p in db.posts_by(limit=100000) if not p.get("category")]
    for p in rows: p["category"], p["fmt"] = nlp.categorize(p)
    if rows: db.update_enrichment(rows)
backfill()

def _assert_public(host):
    try:
        for info in socket.getaddrinfo(host.split(":")[0], None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise scraper.ScrapeError("Private or local addresses are not allowed")
    except socket.gaierror:
        raise scraper.ScrapeError(f"Domain {host} does not resolve")

def run_check(job_id, domain):
    job = JOBS[job_id]
    try:
        base, host = scraper.normalize(domain)
        _assert_public(host)
        job["host"] = host
        known = db.known_urls(host)
        first_run = db.run_count(host) == 0
        res = scraper.scrape(base, host, known)
        posts = res["posts"]
        vol_status, ai_status = enrich.enrich(posts) if posts else ("nothing new", "-")
        db.save_posts(host, posts, is_new=0 if first_run else 1)
        if first_run: db.add_seen(host, res["skipped"])

        price_change, pr = None, res["pricing"]
        if pr:
            last = db.last_pricing(host)
            if not last: db.save_pricing(host, pr["url"], pr["hash"], pr["prices"])
            elif last["hash"] != pr["hash"]:
                old, new = set(json.loads(last["prices"])), set(pr["prices"])
                price_change = {"url": pr["url"], "added": sorted(new - old), "removed": sorted(old - new)}
                db.save_pricing(host, pr["url"], pr["hash"], pr["prices"])

        sent = []
        if posts or price_change:
            sent = alerts.notify(host, posts, price_change, first_run, vol_status, db.get_setting("slack_webhook"), db.get_setting("alert_email"))
        db.add_run(host, len(posts), "ok")
        job.update(status="done", result={
            "outcome": "ok", "host": host, "first_run": first_run, "new_posts": posts,
            "pricing": {"tracked": bool(pr), "url": pr and pr["url"], "change": price_change},
            "enrichment": vol_status, "ai": ai_status, "alerts": sent, "stats": res["stats"]})
    except scraper.ScrapeError as e:
        outcome = {scraper.BlockedError: "blocked", scraper.NoBlogError: "no_blog"}.get(type(e), "error")
        msg = str(e) + (". The site blocks automated access, so nothing was stored." if outcome == "blocked" else "")
        if job.get("host"): db.add_run(job["host"], 0, outcome)
        job.update(status="done", result={"outcome": outcome, "message": msg})
    except Exception as e:  # last-resort guard so the UI never hangs
        if job.get("host"): db.add_run(job["host"], 0, "error")
        job.update(status="done", result={"outcome": "error", "message": f"Unexpected error: {type(e).__name__}"})

def _digest_inputs():
    pc = [c for c in db.pricing_changes() if c["ts"] > time.time() - 7 * 86400]
    return db.recent_posts(7), pc

def _scheduler(hours):
    while True:
        time.sleep(hours * 3600)
        for c in db.overview()["competitors"]:
            jid = uuid.uuid4().hex; JOBS[jid] = {"status": "running"}
            run_check(jid, c["domain"])
        try:  # weekly AI digest, sent to Slack/email
            last = db.cache_get("digest")
            if ai.enabled() and (not last or time.time() - last["ts"] > 6.5 * 86400):
                text = ai.digest(*_digest_inputs()); db.cache_set("digest", text)
                alerts.send_text("Weekly competitor digest", text, db.get_setting("slack_webhook"), db.get_setting("alert_email"))
        except ai.AIError: pass
if os.environ.get("SCHEDULE_HOURS"):
    threading.Thread(target=_scheduler, args=(float(os.environ["SCHEDULE_HOURS"]),), daemon=True).start()

# ---------- comparison ----------
def profile(host, skip=()):
    ps = db.posts_by(host, limit=5000)
    today, ages = date.today(), []
    for p in ps:
        try: ages.append((today - date.fromisoformat(p["published"])).days)
        except (TypeError, ValueError): pass
    brand, terms = host.split(".")[0], Counter()
    for p in ps:
        words = re.findall(r"[a-z]{4,}", ((p["title"] or "") + " " + (p["keyword"] or "")).lower())
        terms.update({w for w in words if w not in enrich.STOP and w not in NOISE and w != brand and w not in skip})
    vols = [p["volume"] for p in ps if isinstance(p["volume"], int)]
    dem = [p["demand"] for p in ps if p["demand"] is not None]
    cats, fmts = Counter(p["category"] or "General" for p in ps), Counter(p["fmt"] or "Article" for p in ps)
    pr = db.last_pricing(host)
    dated = [p["published"] for p in ps if p["published"]]
    return {"domain": host, "total": len(ps), "last30": sum(a <= 30 for a in ages), "last90": sum(a <= 90 for a in ages),
            "per_month": round(sum(a <= 90 for a in ages) / 3, 1), "latest": max(dated) if dated else None,
            "top_cat": cats.most_common(1)[0][0] if cats else None, "cats": dict(cats), "fmts": dict(fmts),
            "avg_volume": round(sum(vols) / len(vols)) if vols else None, "avg_demand": round(sum(dem) / len(dem)) if dem else None,
            "terms": terms.most_common(12), "prices": json.loads(pr["prices"])[:8] if pr else []}, terms

def compare_data(a, b):
    skip = {a.split(".")[0], b.split(".")[0]}
    pa, ta = profile(a, skip); pb, tb = profile(b, skip)
    gap = lambda x, y: [t for t, n in x.most_common(80) if y.get(t, 0) == 0 and n >= 2][:12]
    return {"a": pa, "b": pb, "b_not_a": gap(tb, ta), "a_not_b": gap(ta, tb)}

def _host(arg):
    if not arg: return None
    try: return scraper.normalize(arg)[1]
    except scraper.ScrapeError: return "__invalid__"

# ---------- auth routes ----------
@app.get("/")
def index(): return render_template("index.html")

@app.get("/api/me")
def me(): return jsonify(admin=is_admin(), protected=bool(ADMIN_PW), ai=ai.enabled(), my_domain=db.get_setting("my_domain"))

@app.post("/api/login")
def login():
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not ADMIN_PW or hmac.compare_digest(pw.encode(), ADMIN_PW.encode()):
        session["admin"] = True; session.permanent = True
        return jsonify(ok=True)
    time.sleep(1)
    return jsonify(error="Wrong password"), 401

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

# ---------- scanning ----------
@app.post("/api/check")
@admin_only
def check():
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("domain"): return jsonify(error="Enter a competitor domain"), 400
    jid = uuid.uuid4().hex; JOBS[jid] = {"status": "running"}
    threading.Thread(target=run_check, args=(jid, d["domain"]), daemon=True).start()
    return jsonify(job=jid)

@app.get("/api/job/<jid>")
def job(jid): return jsonify(JOBS.get(jid, {"status": "missing"}))

@app.post("/api/reanalyze")
@admin_only
def reanalyze():  # recompute keyword, category, summary and volume for stored articles (latest 400)
    jid = uuid.uuid4().hex; JOBS[jid] = {"status": "running"}
    def work():
        try:
            ps = db.posts_by(limit=400)
            vs, ai_s = enrich.enrich(ps); db.update_enrichment(ps)
            JOBS[jid].update(status="done", result={"count": len(ps), "status": vs, "ai": ai_s})
        except Exception as e:
            JOBS[jid].update(status="done", result={"count": 0, "status": f"Failed: {type(e).__name__}", "ai": ""})
    threading.Thread(target=work, daemon=True).start()
    return jsonify(job=jid)

# ---------- read-only data ----------
@app.get("/api/posts")
def posts():
    a = request.args
    return jsonify(db.posts_by(_host(a.get("domain")), a.get("keyword"), a.get("category"), a.get("new") == "1", int(a.get("limit", 500))))

@app.get("/api/overview")
def overview():
    o = db.overview(); o["my_domain"] = db.get_setting("my_domain"); return jsonify(o)

@app.get("/api/keywords")
def keywords(): return jsonify(db.keywords())

@app.get("/api/categories")
def categories(): return jsonify(db.categories())

@app.get("/api/pricing")
def pricing(): return jsonify(db.pricing_changes())

@app.get("/api/runs")
def runs(): return jsonify(db.runs())

@app.get("/api/compare")
def compare():
    a, b = _host(request.args.get("a")), _host(request.args.get("b"))
    if not a or not b: return jsonify(error="Pick two companies"), 400
    return jsonify(compare_data(a, b))

@app.get("/api/export.csv")
def export():
    ps = db.posts_by(_host(request.args.get("domain")), limit=100000)
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["company", "title", "url", "published", "category", "format", "keyword", "volume", "demand_estimate", "summary"])
    for p in ps: w.writerow([p["domain"], p["title"], p["url"], p["published"], p["category"], p["fmt"], p["keyword"], p["volume"], p["demand"], p["summary"] or p["description"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=articles.csv"})

# ---------- AI (generation = admin only, reading saved results = public) ----------
@app.get("/api/ai/insights")
def get_insight():
    return jsonify(db.cache_get(f"cmp:{_host(request.args.get('a'))}|{_host(request.args.get('b'))}") or {})

@app.post("/api/ai/insights")
@admin_only
def make_insight():
    d = request.get_json(silent=True) or {}
    a, b = _host(d.get("a")), _host(d.get("b"))
    if not a or not b or "__invalid__" in (a, b) or a == b: return jsonify(error="Pick two different companies"), 400
    try: text = ai.insights(compare_data(a, b), db.get_setting("my_domain"))
    except ai.AIError as e: return jsonify(error=str(e)), 400
    db.cache_set(f"cmp:{a}|{b}", text)
    return jsonify(db.cache_get(f"cmp:{a}|{b}"))

@app.get("/api/ai/digest")
def get_digest(): return jsonify(db.cache_get("digest") or {})

@app.post("/api/ai/digest")
@admin_only
def make_digest():
    try: text = ai.digest(*_digest_inputs())
    except ai.AIError as e: return jsonify(error=str(e)), 400
    db.cache_set("digest", text)
    return jsonify(db.cache_get("digest"))

@app.post("/api/ai/digest/send")
@admin_only
def send_digest():
    d = db.cache_get("digest")
    if not d: return jsonify(error="Generate the digest first"), 400
    return jsonify(results=alerts.send_text("Weekly competitor digest", d["text"], db.get_setting("slack_webhook"), db.get_setting("alert_email")))

# ---------- admin: settings + deletes ----------
@app.get("/api/settings")
@admin_only
def get_settings():
    out = {}
    for k in SETTING_KEYS:
        v = db.get_setting(k)
        out[k] = {"set": bool(v), "value": "" if k in SECRETS else (v or "")}
    return jsonify(out)

@app.post("/api/settings")
@admin_only
def save_settings():
    d = request.get_json(silent=True) or {}
    for k in SETTING_KEYS:
        v = (d.get(k) or "").strip()
        if k == "my_domain" and v:
            try: v = scraper.normalize(v)[1]
            except scraper.ScrapeError: return jsonify(error="Invalid domain"), 400
        if v: db.set_setting(k, v)
    for k in d.get("clear", []):
        if k in SETTING_KEYS: db.set_setting(k, None)
    return jsonify(ok=True)

@app.post("/api/seen")
@admin_only
def seen(): db.mark_seen(); return jsonify(ok=True)

@app.delete("/api/post")
@admin_only
def del_post(): db.delete_post((request.get_json(silent=True) or {}).get("url", "")); return jsonify(ok=True)

@app.delete("/api/competitor/<host>")
@admin_only
def del_comp(host): db.delete_competitor(host); return jsonify(ok=True)

@app.delete("/api/run/<int:rid>")
@admin_only
def del_run(rid): db.delete_run(rid); return jsonify(ok=True)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
