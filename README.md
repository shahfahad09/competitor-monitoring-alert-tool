# Competitor Watch (AI edition)

Scan a competitor domain -> own scraper finds blog posts + pricing -> stored in SQLite -> only NEW posts are flagged on later scans -> enriched with AI + search volume -> Slack/email alert.

## Run locally
    pip install -r requirements.txt
    python app.py        # http://localhost:5000
Locally everyone is admin. Open **Settings** and paste your keys.

## AI features (Claude, set the Anthropic key in Settings)
1. Smart category for every article  2. Target keyword = the Google query the article aims at (so volume lookups are accurate)
3. One-line summary per article (also in alerts)  4. **AI strategy brief** on the Compare page (gaps + 5 article ideas + next moves)
5. **Weekly digest** (generate, send to Slack/email, or auto-weekly when `SCHEDULE_HOURS` is set).
Without a key the app falls back to the built-in rule-based NLP (`nlp.py`). Scraped text is treated as untrusted data in every prompt.
Models are configurable via `AI_MODEL_FAST` / `AI_MODEL_SMART`.

## Public hosting: what visitors can and cannot do
Set `ADMIN_PASSWORD`. Then visitors are **read-only**: they can browse articles, categories, keywords, history, Compare (numbers) and read saved AI briefs/digest.
Only the admin (log in via sidebar) can scan, delete, open Settings (API keys, Slack, email), and generate AI text, so nobody can spend your credits or see your keys. Keys are never sent back to the browser.

## Search volume
Real volume needs DataForSEO (trial credit) or Keywords Everywhere. Without it a labelled free demand estimate (0-100, Google Autocomplete) is shown.

## Deploy (Render / Railway)
Build `pip install -r requirements.txt`; start command is in `Procfile` (one worker, job state is in memory). Set `ADMIN_PASSWORD`, and `DB_PATH` on a persistent disk.

## Failure behaviour
No blog -> "No blog found". Bot-blocked -> "Site blocked us". Bad/missing volume or AI key -> articles still saved with fallback data and the reason is shown. Alert failures are shown; data is never lost.
