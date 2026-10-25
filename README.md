```markdown
# Competitor Watch & Alert Tool

A powerful, AI-driven web application built with Flask and Google Gemini API to monitor competitor websites, detect new content publications, track pricing changes, and generate automated executive digests and instant alerts.

---

## Features

- **Automated Web Scraping:** Crawls competitor homepages and blog/resource listing pages to discover recent posts.
- **Content & Pricing Change Detection:** Tracks new articles, titles, publication dates, meta descriptions, and pricing adjustments since the last check.
- **AI-Powered Enrichment:** Leverages the Google Gemini API to automatically categorize articles, generate concise summaries, and estimate target search keywords.
- **Competitive Intelligence & Comparison:** Provides a side-by-side comparison brief highlighting competitor leads, content gaps, and recommended actions.
- **Automated Weekly Digest:** Compiles a 7-day executive summary of competitor activities.
- **Instant Notifications:** Integrates with Slack webhooks and email to send automated alerts.
- **Secure & Multi-Tenant Settings:** Allows each user to configure their own Gemini API key and Slack webhook securely via a dedicated Settings interface.

---

## Tech Stack

- **Backend:** Python, Flask, Requests, BeautifulSoup (Custom Scraper)
- **AI Integration:** Google Gemini API (`gemini-1.5-flash`)
- **Database:** SQLite (`monitor.db`)
- **Frontend:** HTML, CSS, JavaScript (Responsive Dashboard UI)

---

## Installation & Local Setup

1. **Clone the Repository:**
   ```bash
   git clone [https://github.com/shahfahad09/competitor-monitoring-alert-tool.git](https://github.com/shahfahad09/competitor-monitoring-alert-tool.git)
   cd competitor-monitoring-alert-tool

```

2. **Create and Activate Virtual Environment:**
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

```


3. **Install Dependencies:**
```bash
pip install -r requirements.txt

```


4. **Run the Application:**
```bash
python app.py

```


5. **Access the App:**
Open your browser and navigate to `https://competitor-monitoring-alert-tool-ecsm.onrender.com/`

---

## Configuration

Navigate to the **Settings** page within the app UI to input and save your:

* **Google Gemini API Key** (Generated via Google AI Studio)
* **Slack Webhook URL** (For receiving instant alert notifications)

```

```
