# FairSplit — Cloudflare Pages + Render + Supabase

A no-login group-work assignment site with unique random topic draws, organizer dashboards, CSV exports, radio-button demographics (optional and consent-based), and student utilities.

## Deploy the whole stack

### 1. Put this folder in a GitHub repository

Push the contents of `FairSplit_Deploy` to a new GitHub repository (e.g. `fairsplit`). Keep secrets out of git. The `render.yaml` file is a Render Blueprint for the backend.

### 2. Supabase (PostgreSQL)

Create a **new** Supabase project for FairSplit. In Supabase > Connect, obtain the PostgreSQL **session pooler** URI (recommended for a persistent Render service that needs IPv4). It typically uses port 5432. Store the full URI only in Render's `DATABASE_URL` secret; never paste it into Cloudflare frontend files. For Supabase projects with direct IPv6 connectivity, the direct connection URI also works. Include TLS (`sslmode=require`) as appropriate for the provided connection string.

The backend creates its four tables at startup and enables RLS and revokes anon/authenticated table privileges. All database access goes through the Render backend. No Supabase public API key is needed. On an existing Supabase project, inspect table names before deployment; a fresh project is strongly recommended.

### 3. Render (Python API)

From Render, choose **New > Blueprint** and select the GitHub repo containing `render.yaml`, or manually create a Python web service with:

- Root directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `python server.py`
- Health check: `/health`
- `DATABASE_URL`: Supabase Postgres URI, secret
- `FRONTEND_ORIGIN`: final Cloudflare URL, e.g. `https://fairsplit.pages.dev` (no trailing slash)

Note your generated Render URL, e.g. `https://fairsplit-api.onrender.com`.

### 4. Cloudflare Pages (static website)

Edit `frontend/config.js` and replace `https://REPLACE-WITH-YOUR-RENDER-SERVICE.onrender.com` with the exact Render HTTPS origin. Commit and push.

In Cloudflare Pages, connect the same GitHub repo, set the **root directory** to `frontend`, build command blank, and output directory `.`. Alternatively upload the `frontend` folder via Direct Upload. The public site uses `index.html`, `config.js`, and `_headers`.

Update Render `FRONTEND_ORIGIN` to the actual Cloudflare Pages domain (or your final custom domain), then redeploy/restart Render. The CORS allowlist is exact-match.

### 5. Verify end to end

1. Visit `https://<your-render-service>.onrender.com/health`; expect `{ "status": "ok" }`.
2. Open your Cloudflare Pages site and create an Aggregation I session (the four questions are preloaded).
3. Open its participant link in a separate browser and draw a question. Repeat with four aliases; no question should repeat.
4. Open organizer link, verify assignments, CSV export, and close/reopen session.
5. Confirm demographic data is only stored with consent; use **Withdraw research data consent** to remove it.

## Local smoke test

`python tests/smoke.py` runs with SQLite and no extra Python dependencies. For local two-server preview, run `python backend/server.py` (port 8000) and `python -m http.server 8001 -d frontend` in another terminal; temporarily set `frontend/config.js` to `http://localhost:8000`. Restore the production Render URL before pushing.

## Security and limitations

- Organizer access uses a high-entropy private URL. Anyone possessing it can access organizer data; don't share it.
- Draws use a PostgreSQL session row lock and unique indexes to prevent duplicate question allocation across concurrent Render instances.
- A participant token stored in the browser retrieves the original draw. This does not prevent someone using multiple browsers/aliases to claim multiple slots.
- A per-instance IP-based request limiter reduces casual spam; it is not a distributed rate limiter or a complete abuse defense.
- The optional demographic data is not collected from participants choosing the under-18 age band. Users may withdraw research data consent while retaining their draw.
- **Research governance remains required** before collecting demographic data: appropriate ethics review, purpose/retention statement, consent versioning, access restrictions, deletion workflow, backups, and a plan for handling small-group re-identification risk. Organizer CSV includes aliases, so it is not anonymized.
- SQLite is for local development only; production must set `DATABASE_URL` and use persistent PostgreSQL.
- Render free web services may spin down when idle. Supabase free projects may pause with inactivity; consult current provider terms.
- This repo does not configure a custom domain, monitoring, email, or automatic database backups.

## Files

- `frontend/index.html` — responsive site and tools
- `frontend/config.js` — public API base URL (not a secret)
- `frontend/_headers` — Cloudflare security headers
- `backend/server.py` — API and business logic
- `backend/db.py` — SQLite/Postgres adapter and schema
- `backend/requirements.txt` — psycopg dependency
- `render.yaml` — Render Blueprint
- `tests/smoke.py` — integration smoke test
