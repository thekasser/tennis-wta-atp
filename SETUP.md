# Setup — finish the git init on your Mac

The Cowork sandbox started a `.git` directory but couldn't complete the first commit (filesystem permission quirk on the mount). Finish it locally:

## 1. Open Terminal and cd in
```bash
cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboardd"
```

## 2. Reset the broken `.git` and re-init
```bash
rm -rf .git
git init -b main
git config user.email "kasserconnor@gmail.com"
git config user.name "Connor Kasser"
```

## 3. First commit
```bash
git add -A
git status        # sanity check — should be ~12 files, no scripts/cache/, no .DS_Store
git commit -m "Initial commit: tennis dashboard (ATP/WTA 2026)"
```

## 4. Create a private GitHub repo and push

**Easiest path — install GitHub CLI once, then one command:**
```bash
# One-time install (skip if you already have it):
brew install gh
gh auth login   # follow the prompts; pick HTTPS + browser auth

# Create the repo + push (run from inside the dashboard folder):
gh repo create tennis-dashboard --private --source=. --remote=origin --push
```

That command creates `github.com/kasserconnor/tennis-dashboard` (private), wires it as `origin`, and pushes `main`.

**Alternative — use the GitHub website:**
1. https://github.com/new → name: `tennis-dashboard`, visibility: **Private**, don't add README/license/gitignore (we already have them)
2. Then locally:
```bash
git remote add origin git@github.com:kasserconnor/tennis-dashboard.git
git push -u origin main
```

## 5. When you're ready to flip private → public
Either:
- `gh repo edit kasserconnor/tennis-dashboard --visibility public --accept-visibility-change-consequences`
- Or: GitHub web → repo → Settings → bottom of "General" → "Change visibility" → Public

## 6. Wire up GitHub Pages (optional, once public)
Settings → Pages → Source: `Deploy from a branch` → Branch: `main` / `(root)` → Save.

Your dashboard will be live at:
`https://kasserconnor.github.io/tennis-dashboard/wta_analytics.html`
`https://kasserconnor.github.io/tennis-dashboard/trapezoid.html`

## Why this lives here, not in the repo itself
This file is fine to commit (it's just instructions), but it's tagged so you can `rm SETUP.md && git rm SETUP.md` after you've done it once if you want a tidier root. Or keep it — it's also a "how I'd onboard a new machine" cheat sheet.
