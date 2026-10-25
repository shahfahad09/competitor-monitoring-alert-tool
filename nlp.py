"""Lightweight NLP: stemmed lexicon scoring (title x3, URL slug x2, description x1) + URL hints -> topic category and content format."""
import re
from urllib.parse import urlparse

def stem(w):
    for suf in ("ings", "ing", "ies", "ied", "ers", "er", "es", "ed", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w

def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())]

LEX = {
 "SEO & Content Marketing": "seo keyword backlink link serp ranking organic content blog marketing traffic search backlinks copywriting",
 "Social & Email Marketing": "social email newsletter instagram tiktok facebook linkedin youtube influencer campaign engagement community creator brand",
 "Ads & Paid Growth": "ads advertising ppc paid adwords roas cpc conversion funnel growth acquisition spend",
 "E-commerce & Retail": "ecommerce store retail merchant checkout shopify dropshipping inventory shipping fulfillment logistics 3pl order cart marketplace commerce",
 "Sales & CRM": "sales crm lead pipeline prospecting b2b deal outreach customer relationship onboarding retention revenue",
 "AI & Automation": "ai artificial intelligence automation chatgpt llm machine learning agent copilot generative bot",
 "Product Updates & Features": "launch release feature update introducing announcing changelog integration api roadmap beta",
 "Customer Stories & Case Studies": "case study story success achieved results brands testimonial",
 "Pricing & Plans": "pricing price plan cost cheap free trial discount subscription",
 "Finance, Payments & Legal": "payment tax invoice accounting finance fraud compliance legal gdpr privacy security regulation",
 "Industry Trends & Reports": "trend report survey statistics benchmark research industry forecast market outlook ranked",
 "Company News & Culture": "announces hires appoints acquisition funding raises award culture careers hiring team ceo partnership press",
 "Events & Webinars": "webinar event conference summit podcast live workshop",
 "Education & Training": "course academy training certification lesson bootcamp tutorial beginner learn",
}
CATS = {k: {stem(w) for w in v.split()} for k, v in LEX.items()}
URLHINT = [(r"case-stud|/customers?/|/stories/|success-stor", "Customer Stories & Case Studies"),
           (r"/news|/press|company-news|/newsroom", "Company News & Culture"),
           (r"/webinar|/events?/|/podcast", "Events & Webinars"),
           (r"/academy|/courses?/", "Education & Training"),
           (r"changelog|release-notes|product-update|/features?/", "Product Updates & Features"),
           (r"/pricing", "Pricing & Plans")]
FORMATS = [("Case study", r"case stud|customer stor|success stor|how .{3,40} (achieved|grew|scaled)"),
           ("Comparison", r"\bvs\.?\b|versus|alternatives?\b|compar"),
           ("Listicle", r"^\d+\s|\b\d+ (best|ways|tips|examples|tools|ideas|reasons)|\btop \d+|^best "),
           ("Explainer", r"^what (is|are)|explained|definition|meaning"),
           ("How-to guide", r"how to|step[- ]by[- ]step|tutorial|guide|beginner|checklist"),
           ("Report / Research", r"report|survey|study|statistics|benchmark|trends|index|ranked"),
           ("News / Announcement", r"announc|introduc|launch|releas|now available|appoint|names |hires|raises")]

def categorize(p):
    title = p.get("title") or ""
    path = urlparse(p.get("url") or "").path.lower()
    score = {k: 0 for k in CATS}
    for weight, text in ((3, title + " " + (p.get("keyword") or "")), (2, re.sub(r"[-_/]", " ", path)), (1, p.get("description") or "")):
        for t in set(tokens(text)):
            for k, words in CATS.items():
                if t in words: score[k] += weight
    for rx, k in URLHINT:
        if re.search(rx, path): score[k] += 4
    best = max(score, key=score.get)
    cat = best if score[best] >= 3 else "General"
    low = title.lower()
    fmt = next((n for n, rx in FORMATS if re.search(rx, low) or (n == "Case study" and re.search(r"case-stud|/customers?/", path))), "Article")
    return cat, fmt
