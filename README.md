# Tweet Deleter

Bulk-delete tweets from your X/Twitter archive by domain or keyword — run locally, no data ever leaves your machine.

---

## ⚠️ Security Warning — Read Before Using

> **This tool is for personal, local use only. Never deploy it to a public server.**

The web UI accepts your X Developer API credentials (Consumer Key, Consumer Secret, Access Token, Access Token Secret). If you run this on a public or shared server, anyone who can reach the URL could:

- View and steal your API credentials
- Delete tweets on your behalf
- Use your API tokens to impersonate you

**Run this exclusively on `localhost` on your own machine.** Treat it the same as you would a local password manager — it is not designed to be internet-facing.

---

## What It Does

Tweet Deleter reads your local Twitter/X data archive (`tweets.js`) and lets you bulk-delete matched tweets through the official X API.

- **Filter by domain** — finds tweets where your target domain appears in the tweet text *or* in any linked URL (even `t.co` shortened ones are expanded and checked)
- **Filter by keyword** — matches tweets that contain specific words or phrases in the tweet text, case-insensitively
- **Review before deleting** — browse all matched tweets, select individually, by page, or all at once
- **Safe deletion rate** — paced at one tweet every 3.5 seconds, safely under the X API rate limit of 300 deletions per 15 minutes
- **Upload archive in-browser** — drop your `tweets.js` file directly into the Settings page if you can't place it in the project folder
- **Persistent credentials** — your API keys are saved to a local `credentials.json` so you don't need to re-enter them every time the server restarts; clicking Disconnect deletes the file
- **Persistent filters** — your target sites and keywords are saved to `filters.json` and restored automatically on restart; they are not cleared when you disconnect
- **Mobile-friendly** — responsive layout with a bottom tab bar on small screens; Disconnect is available in the Settings tab

---

## How It Works

```
tweets.js (your archive)  →  local FastAPI server  →  filter & display
                                                  ↓
                                    X API  ←  delete selected tweets
```

1. **No live reading from X** — tweets are loaded from your local archive file, not fetched via the API. The API is only used for deletion.
2. **No data sent externally** — the archive is parsed on your machine. The only outbound calls are the delete requests to `api.twitter.com`.
3. **Session tracking** — deleted tweet IDs are tracked in memory so they disappear from the list immediately, without needing to re-parse the archive.

---

## Requirements

- Python 3.11+
- An X Developer account with a project and app that has **Read and Write** permissions
- Your Twitter/X data archive (`tweets.js`)

### Getting Your API Credentials

1. Go to [developer.twitter.com](https://developer.twitter.com) and create a project and app
2. Under **App permissions**, set it to **Read and Write**
3. Generate your **Consumer Key**, **Consumer Secret**, **Access Token**, and **Access Token Secret**
4. Make sure the access token was generated *after* you set Read+Write permissions — tokens generated before that change will only have read access

### Getting Your Twitter Archive

1. On X (Twitter), go to **Settings → Your account → Download an archive of your data**
2. X will email you when the archive is ready (can take up to 24 hours)
3. Download and extract the archive — you need the `tweets.js` file inside the `data/` folder

---

## Setup & Running

```bash
# Clone the repo
git clone https://github.com/Ennygabby01/delete_tweets.git
cd delete_tweets

# Install dependencies
pip install -r requirements.txt

# Place your tweets.js in the project root (or upload it via the UI later)
cp /path/to/your/archive/data/tweets.js .

# Start the server
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

> **Important:** Use `--host 127.0.0.1`, not `0.0.0.0`. Binding to `0.0.0.0` exposes the server on your network.

---

## Usage

### 1. Sign In
Enter your four API credentials on the login screen. They are saved to `credentials.json` locally so the server auto-loads them on restart.

### 2. Upload Your Archive
If you didn't place `tweets.js` in the project root, go to **Settings → Tweet Archive** and drag-and-drop (or click to browse) your `tweets.js` file. The server validates and stores it for you.

### 3. Configure Filters
In **Settings**, add:
- **Target Sites** — domains whose links you want to remove (e.g. `myblog.wordpress.com`). Matches both the raw tweet text and expanded URLs behind `t.co` links.
- **Keywords** — words or phrases that appear in the tweet body (e.g. `Check out my post`). Case-insensitive.

A tweet only needs to match one filter to appear in the list.

### 4. Review and Delete
Switch to the **Tweets** tab to see all matched tweets. You can:
- Click individual tweets to select/deselect them
- Use **Select page** to select all tweets on the current page
- Use **Select all** to select every matched tweet across all pages
- Use the search bar to narrow the list further before selecting

Once you have a selection, click **Delete** and confirm. Deletions are paced at 1 every 3.5 seconds. The progress bar shows real-time status, including any failures.

### 5. Disconnect
Click **Disconnect** in the left sidebar, or go to **Settings → Account** on any screen size (including mobile). This clears the in-memory session and deletes `credentials.json` from disk. Your target sites and keywords are preserved in `filters.json` and will be restored on next login.

---

## Project Structure

```
delete_tweets/
├── app/
│   ├── main.py          # FastAPI app setup, lifespan, static file serving
│   ├── state.py         # In-memory app state (credentials, filters, deleted IDs)
│   ├── parser.py        # tweets.js parsing and filtering logic
│   ├── twitter.py       # X API client (Tweepy), deletion, error handling
│   ├── credentials.py   # Save/load/clear credentials.json
│   └── routes/
│       ├── admin.py     # Auth, site/keyword management, archive upload
│       └── tweets.py    # Tweet listing, stats, deletion endpoint
├── static/
│   └── index.html       # Single-page frontend (Alpine.js)
├── tweets.js            # Your archive file (not committed)
├── credentials.json     # Your API keys (not committed, created on first login)
├── filters.json         # Saved sites + keywords (not committed, created on first add)
└── requirements.txt
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework and API routing |
| `uvicorn` | ASGI server |
| `tweepy` | X (Twitter) API v1.1 client |
| `python-multipart` | File upload support for `tweets.js` |

Frontend uses [Alpine.js](https://alpinejs.dev) (loaded from CDN, no build step).

---

## Limitations (Important)

- **Archive only, not live timeline** — Tweet Deleter reads your static archive, not your live account. If you've posted tweets since the archive was generated, they won't appear here.
- **No undo** — deletions via the X API are permanent. There is no way to recover deleted tweets.
- **One archive at a time** — the server caches the parsed archive in memory. Uploading a new `tweets.js` replaces it (server restart not required).
- **Rate limits** — X's API allows 300 tweet deletions per 15 minutes. The 3.5-second delay between deletions keeps you safely under this limit.
- **Personal API credentials required** — X does not provide a public OAuth flow for deletion tools. You must use your own developer account credentials.

---

## License

MIT — do whatever you like with it, but don't use it to delete other people's tweets.

---

Built by [GBT3K](https://github.com/Ennygabby01)