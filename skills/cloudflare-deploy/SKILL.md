---
name: cloudflare-deploy
description: Deploy web projects to Cloudflare Pages/Workers using wrangler CLI
tags: [cloudflare, deploy, hosting, web, pages, workers]
---

# Cloudflare Deploy Skill

Deploy static sites to **Cloudflare Pages** and serverless APIs to **Cloudflare Workers** using the `wrangler` CLI.

## Prerequisites

- `wrangler` CLI installed: `npm install -g wrangler` (or `npx wrangler`)
- Authenticated: `wrangler login` or set `CLOUDFLARE_API_TOKEN` env var
- Account ID in `CLOUDFLARE_ACCOUNT_ID` env var (or `wrangler whoami` to find it)

## Architecture: GitHub → Cloudflare

The recommended flow:
1. Code lives in a **GitHub repo** (CI/CD via GitHub Actions)
2. Cloudflare Pages **connects to the repo** and auto-deploys on push
3. OR use `wrangler` for manual/CLI deploys

## Cloudflare Pages (Static Sites)

### Option A: Git Integration (Recommended)

Connect a GitHub repo to Pages — auto-deploys on every push:

```bash
# Create via dashboard or API. Once connected:
# - Push to main → production deploy
# - Push to branches → preview deploys
# - URL: https://<project>.pages.dev
```

### Option B: Direct Upload via CLI

```bash
# Deploy a directory of static files
wrangler pages deploy ./dist --project-name=my-site

# First deploy creates the project automatically
# Subsequent deploys update it
```

### wrangler.toml for Pages (optional)

```toml
name = "my-site"
pages_build_output_dir = "./dist"

# If using Pages Functions (server-side)
# Place functions in ./functions/ directory
```

### Build Commands (common frameworks)

| Framework | Build Command | Output Dir |
|-----------|--------------|------------|
| Vite/React | `npm run build` | `dist` |
| Next.js (static) | `npx next build && npx next export` | `out` |
| Astro | `npx astro build` | `dist` |
| Hugo | `hugo` | `public` |
| Plain HTML | (none) | `.` |

## Cloudflare Workers (Serverless APIs)

### Create a Worker

```bash
# Scaffold a new Worker project
npm create cloudflare@latest my-api

# Or manually create wrangler.toml + src/index.ts
```

### wrangler.toml for Workers

```toml
name = "my-api"
main = "src/index.ts"  # or index.js
compatibility_date = "2024-01-01"

# Optional bindings
# [vars]
# API_KEY = "..."

# KV namespace
# [[kv_namespaces]]
# binding = "MY_KV"
# id = "abc123"

# R2 bucket
# [[r2_buckets]]
# binding = "MY_BUCKET"
# bucket_name = "my-bucket"

# D1 database
# [[d1_databases]]
# binding = "DB"
# database_name = "my-db"
# database_id = "xxx"
```

### Minimal Worker (TypeScript)

```typescript
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    
    if (url.pathname === "/api/hello") {
      return Response.json({ message: "Hello from Workers!" });
    }
    
    return new Response("Not Found", { status: 404 });
  },
};
```

### Deploy

```bash
# Deploy to production
wrangler deploy

# Deploy to staging (route based)
wrangler deploy --env staging

# Dev mode (local)
wrangler dev
```

## Free Tier Limits (Important!)

### Pages
- **100 projects** per account
- **500 builds/month**
- **20,000 files** per project
- **25 MB** per file
- **Unlimited bandwidth** (no transfer fees)
- **Custom domains**: 100 per project
- Default URL: `https://<project>.pages.dev`

### Workers
- **100,000 requests/day**
- **10ms CPU time** per request (wall time is unlimited — waiting on fetch/KV doesn't count)
- **100 Workers** per account
- **3 MB** script size
- **5 Cron Triggers** per account
- **128 MB** memory

### R2 (Object Storage)
- **10 GB** storage
- **1M writes/month**, **10M reads/month**
- **Zero egress fees** (this is the killer feature)

### KV
- **1 GB** storage
- **100K reads/day**, **1K writes/day**

### D1 (SQLite)
- **5 GB** storage
- **5M reads/day**, **100K writes/day**

## Common Patterns

### Static Site + API

```
my-project/
├── site/           → Cloudflare Pages (static)
│   ├── index.html
│   └── assets/
├── api/            → Cloudflare Workers (dynamic)
│   ├── src/index.ts
│   └── wrangler.toml
└── .github/
    └── workflows/
        └── deploy.yml  → GitHub Actions builds both
```

### GitHub Actions → Cloudflare Deploy

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: npm ci && npm run build
      - uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          # For Pages:
          command: pages deploy dist --project-name=my-site
          # For Workers:
          # command: deploy
```

### Custom Domain Setup

1. Add domain to Cloudflare DNS (free plan)
2. In Pages project → Custom domains → Add
3. SSL is automatic
4. For Workers: use routes in wrangler.toml

```toml
# Workers custom domain route
routes = [
  { pattern = "api.example.com/*", zone_name = "example.com" }
]
```

## CLI Quick Reference

```bash
# Auth
wrangler login                    # Browser-based login
wrangler whoami                   # Check current auth + account ID

# Pages
wrangler pages project list       # List all Pages projects
wrangler pages deploy ./dir       # Deploy static files
  --project-name=NAME

# Workers  
wrangler deploy                   # Deploy worker (uses wrangler.toml)
wrangler dev                      # Local dev server
wrangler tail                     # Live logs from production

# R2
wrangler r2 bucket list           # List buckets
wrangler r2 bucket create NAME    # Create bucket
wrangler r2 object put BUCKET/KEY --file=./path  # Upload
wrangler r2 object get BUCKET/KEY --file=./path  # Download

# KV
wrangler kv namespace list
wrangler kv key put --binding=NS KEY VALUE
wrangler kv key get --binding=NS KEY

# D1
wrangler d1 create my-db
wrangler d1 execute my-db --command="SELECT * FROM users"
```

## Troubleshooting

- **"Authentication error"**: Run `wrangler login` or check `CLOUDFLARE_API_TOKEN`
- **CPU time exceeded**: Move heavy computation to GitHub Actions, serve results from R2/KV
- **Script too large (>3MB free)**: Use dynamic imports, tree-shake dependencies
- **Pages build fails**: Check build command and output directory
- **Worker not updating**: `wrangler deploy` forces a new version; check routes
