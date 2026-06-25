# Setup for GitHub

This guide assumes you want to create a **new public repository** on your personal GitHub account and push all the documentation there.

---

## Quick Setup

### 1. Create a new repo on GitHub

Go to https://github.com/new and create:
- **Repository name:** `google-batch-guide`
- **Description:** "A practical guide for submitting containerized workflows to Google Batch via Verily Workbench"
- **Visibility:** Public
- **Initialize:** No (we'll push from scratch)

GitHub will show you the setup commands.

### 2. Clone locally and push

```bash
# Create repo directory
mkdir google-batch-guide
cd google-batch-guide

# Initialize git
git init

# Add all files (already present)
git add .

# Commit
git commit -m "Initial commit: Google Batch guide with xTea case study"

# Add remote (replace with your username)
git remote add origin https://github.com/<YOUR_USERNAME>/google-batch-guide.git

# Push
git branch -M main
git push -u origin main
```

### 3. Verify

Check GitHub: https://github.com/<YOUR_USERNAME>/google-batch-guide

You should see:
- README.md (main page)
- GUIDE_GoogleBatch_Workbench.md
- TROUBLESHOOTING.md
- examples/ (folder)
- scripts/ (folder)
- .gitignore (optional)

---

## Directory Structure

```
google-batch-guide/
├── README.md                           # Main entry point
├── GUIDE_GoogleBatch_Workbench.md      # Detailed step-by-step guide
├── TROUBLESHOOTING.md                  # Common problems + solutions
├── examples/
│   ├── worker_template.py              # Generic template for any tool
│   ├── job_config_template.json        # Batch config template
│   └── xtea_case_study/
│       ├── README.md                   # xTea-specific setup + validation
│       └── xtea_worker.py              # Full xTea worker (optional to add)
└── scripts/
    └── submit_batch_job.py             # Helper to submit jobs without JSON
```

---

## Add a .gitignore

Optional, but recommended:

```bash
cat > .gitignore <<EOF
# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/

# Local configs with actual values (don't commit these!)
*_local.json
*.credentials
.env

# Temporary files
*.tmp
*.log

# OS
.DS_Store
.swp
*~

# IDE
.vscode/
.idea/
EOF

git add .gitignore
git commit -m "Add .gitignore"
git push
```

---

## Customize Placeholders

The documentation contains placeholders like `<YOUR_PROJECT_ID>`. You have two options:

### Option A: Leave placeholders (recommended)

This makes the guide reusable. Users replace with their own values.

### Option B: Create a public example config

Add a file `examples/EXAMPLE_PROJECT_SETUP.md` with a concrete example:

```markdown
## Example: Actual setup (anonymized)

If you want to see a real, working example:

```json
{
  "project": "my-gcp-project-12345",
  "region": "europe-west4",
  "bucket": "my-research-bucket",
  "network": "my-vpc",
  "machine": "e2-highmem-16"
}
```

(Values anonymized to avoid leaking credentials or project details)
```

---

## Optional: Add GitHub Pages (nice-to-have)

If you want a rendered website instead of just markdown:

```bash
# Create docs folder
mkdir -p docs

# Move README there
cp README.md docs/index.md

# Create GitHub Pages config
cat > docs/_config.yml <<EOF
title: Google Batch Guide
description: Submitting containerized workflows to Google Batch
theme: jekyll-theme-minimal
EOF

git add docs/
git commit -m "Add GitHub Pages"
git push

# Enable in GitHub Settings:
# 1. Go to repo Settings → Pages
# 2. Set "Source" to "main" branch / "docs" folder
# 3. Save
# 4. Visit https://<YOUR_USERNAME>.github.io/google-batch-guide
```

---

## Share with the team

Once it's on GitHub:

```markdown
**Google Batch Guide — Ready for collaboration:**

https://github.com/<YOUR_USERNAME>/google-batch-guide

Start with the README, then follow GUIDE_GoogleBatch_Workbench.md.

The xTea case study in examples/ shows a production example.

Questions or issues? Open a GitHub issue or submit a PR.
```

---

## Future updates

As the team discovers new patterns or fixes:

```bash
# Update locally
echo "New section" >> GUIDE_GoogleBatch_Workbench.md

# Commit and push
git add GUIDE_GoogleBatch_Workbench.md
git commit -m "Add new section: X"
git push
```

Others can then pull the latest version:

```bash
cd google-batch-guide
git pull
```

---

## Collaborative workflow (if others contribute)

1. Team forks your repo
2. They make changes on a branch (`git checkout -b fix-typo`)
3. They commit and push to their fork
4. They open a Pull Request (PR) on your repo
5. You review and merge

GitHub has built-in PR review tools; check their docs if needed.

---

Done! Your guide is now public and shareable. 🚀

