# What this zip is

This is NOT your full repo — I don't have your React/Vite source, so I
can't zip up your whole project. This contains only the new/changed
pieces needed to fix the /api/agent-reply 404 and stop leaking your
Anthropic key. Copy these into your existing `agenticdemo` repo and VPS
as described below.

## 1. Copy into your GitHub repo (`cilpn/agenticdemo`)

Copy these paths as-is into the repo root (same relative paths):

    server-fastapi/main.py
    server-fastapi/requirements.txt
    server-fastapi/Dockerfile
    .github/workflows/deploy.yml   <- overwrite your existing workflow file
                                       (delete the old one first if it has
                                       a different filename, so you don't
                                       end up with two workflows both
                                       trying to deploy)

Your existing React app, its Dockerfile, and everything else in the repo
stays exactly as-is. `getAgentReply.ts` needs NO changes — it already
calls fetch("/api/agent-reply"), which is what the new FastAPI service
answers.

Also delete/remove any `VITE_ANTHROPIC_API_KEY` GitHub secret reference
if your old workflow file set one — the new deploy.yml never uses or
bakes it in.

## 2. On the VPS: /opt/agenticdemo/

Replace `docker-compose.yml` with the one in this zip (adds a second
container, `api`, alongside your existing `app` frontend container).

Then create a `.env` file in the SAME directory (`/opt/agenticdemo/.env`)
containing:

    ANTHROPIC_API_KEY=sk-ant-your-real-key-here

Docker Compose reads `.env` from the same folder automatically — this
keeps the real key off GitHub entirely; it only ever lives on the VPS.

## 3. Rotate your old key

Your previous workflow baked VITE_ANTHROPIC_API_KEY into the frontend JS
bundle, and that bundle is already published in image layers on GHCR
(ghcr.io/cilpn/agenticdemo:latest and every :<sha> tag pushed so far).
Revoke/rotate that key in the Anthropic console — removing it from the
workflow going forward does not erase it from already-pushed images.

## 4. Push and deploy

    git add server-fastapi .github/workflows/deploy.yml
    git commit -m "Add FastAPI backend for /api/agent-reply, stop leaking API key to client"
    git push origin main

This triggers your existing GitHub Actions pipeline:
- builds ghcr.io/cilpn/agenticdemo:latest (frontend, unchanged Dockerfile)
- builds ghcr.io/cilpn/agenticdemo-api:latest (new, from server-fastapi/)
- SSHs into your VPS, does `docker compose pull && up -d`

## 5. Verify

    docker ps                                 # should show agenticdemo AND agenticdemo-api
    docker logs -f agenticdemo-api             # watch for incoming requests
    curl -X POST https://aidemo.cilpron.com/api/agent-reply \
      -H "Content-Type: application/json" \
      -d '{"systemPrompt":"You are a helpful assistant.","history":[{"role":"user","content":"hi"}]}'

Should return {"reply": "..."} instead of the old nginx 404 HTML page.

If it still 404s: check `docker logs traefik` for routing errors, and
confirm both containers show `Up` in `docker ps` (not restarting/crashed
— a missing ANTHROPIC_API_KEY in .env would crash-loop or 500, not 404).
