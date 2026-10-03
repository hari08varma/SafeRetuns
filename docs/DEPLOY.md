# Deploying SafeReturns

The web app (Next.js) runs on **Vercel**. The backend (FastAPI, worker, Postgres) runs in
**Docker**, because it needs a long-running worker and a database, which Vercel functions
are not designed for. The browser only talks to Vercel; Vercel proxies `/api/*` to the
backend, so no CORS setup is needed.

## 1. Backend (Docker)

Pick one:

- **Render (simplest, free tier):** Render dashboard → *New → Blueprint* → choose this repo.
  `render.yaml` creates the API (Docker, worker embedded) and Postgres. Then set:
  - `PII_ENCRYPTION_KEY`: generate with
    `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  - `FIREBASE_PROJECT_ID`: your Firebase project id (e.g. `vapsi-8f370`)
  - optional: `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY`
- **Any machine with Docker** (VM, your laptop + a tunnel):
  `docker compose up -d --build postgres api worker` with secrets in `services/.env`
  (see `.env.example`). Put HTTPS in front (Caddy, Cloudflare Tunnel) before giving Vercel the URL.

The API runs migrations on start. Create the first admin once:

```
docker compose exec api python -m returns_agent.seed.admin --email owner@yourstore.in
# Render: Shell tab →  python -m returns_agent.seed.admin --email owner@yourstore.in
```

Note: uploaded photos are stored on the container disk (`EVIDENCE_DIR`); on free hosting
they disappear on restart. Use a persistent disk or S3/MinIO for real use.

## 2. Web app (Vercel)

Vercel → *Add New → Project* → import this repo, then:

- **Root directory:** `apps/web` (framework is detected as Next.js)
- **Environment variables:**
  - `API_URL` = your backend URL, e.g. `https://saferetuns-api.onrender.com`
  - `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`,
    `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID` (Firebase web app config)
- Deploy.

## 3. Firebase

Authentication → Settings → **Authorized domains**: add your Vercel domain
(e.g. `saferetuns.vercel.app`). Google Cloud console → Credentials: restrict the web API key
to that domain.

## 4. Try it

1. Open the Vercel URL → *Sign in* with a Firebase test number → fill in your details.
2. Console → sign in as the admin → *Admin → Test orders*: create an order for that number.
3. Back as the customer → *My orders* → *Start a return*.
