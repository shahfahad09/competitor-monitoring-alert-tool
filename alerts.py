import os, smtplib
from datetime import date, timedelta
from email.message import EmailMessage
import requests

def build(host, posts, price_change, first_run, enrich_status, slack=False):
    week = (date.today() - timedelta(days=7)).isoformat()
    recent = [p for p in posts if (p.get("published") or "") >= week]
    head = (f"{host}: first scan found {len(posts)} posts (baseline saved)" if first_run
            else f"{host} published {len(posts)} new post(s) ({len(recent)} dated in the last 7 days)")
    lines = [f"*{head}*" if slack else head, ""]
    for p in posts[:15]:
        vol = f"{p['volume']:,}" if isinstance(p.get("volume"), int) else (f"~{p['demand']}/100 demand (est.)" if p.get("demand") is not None else "n/a")
        title = f"<{p['url']}|{p['title']}>" if slack else p["title"]
        lines.append(f"• {title}" + ("" if slack else f"\n  {p['url']}"))
        if p.get("summary"): lines.append(f"  {p['summary']}")
        lines.append(f"  [{p.get('category') or 'General'}] keyword: {p.get('keyword')} | est. monthly searches: {vol} | published: {p.get('published') or 'unknown'}")
    if len(posts) > 15: lines.append(f"...and {len(posts) - 15} more")
    if price_change:
        lines += ["", "*Pricing changed*" if slack else "Pricing changed",
                  f"  added: {', '.join(price_change['added']) or '-'}", f"  removed: {', '.join(price_change['removed']) or '-'}"]
    if not enrich_status.startswith("ok"): lines += ["", f"Note: {enrich_status}"]
    return "\n".join(lines)

def deliver(subject, slack_text, plain, webhook=None, email=None):
    webhook = webhook or os.environ.get("SLACK_WEBHOOK_URL")
    email = email or os.environ.get("ALERT_EMAIL")
    res = []
    if webhook:
        try:
            r = requests.post(webhook, json={"text": slack_text}, timeout=10)
            res.append({"channel": "slack", "ok": r.ok, "detail": "sent" if r.ok else f"Slack returned HTTP {r.status_code}"})
        except requests.RequestException as e:
            res.append({"channel": "slack", "ok": False, "detail": type(e).__name__})
    if email:
        try:
            m = EmailMessage(); m["To"] = email
            m["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "alerts@localhost")
            m["Subject"] = subject; m.set_content(plain)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
                s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)
            res.append({"channel": "email", "ok": True, "detail": "sent"})
        except (KeyError, OSError, smtplib.SMTPException) as e:
            res.append({"channel": "email", "ok": False, "detail": f"{type(e).__name__}: check SMTP_* settings"})
    return res or [{"channel": "none", "ok": False, "detail": "No Slack webhook or email configured"}]

def notify(host, posts, price_change, first_run, enrich_status, webhook=None, email=None):
    return deliver(f"[Competitor alert] {host}: {len(posts)} new post(s)",
                   build(host, posts, price_change, first_run, enrich_status, True),
                   build(host, posts, price_change, first_run, enrich_status), webhook, email)

def send_text(subject, text, webhook=None, email=None):
    return deliver(subject, f"*{subject}*\n\n{text}", f"{subject}\n\n{text}", webhook, email)
