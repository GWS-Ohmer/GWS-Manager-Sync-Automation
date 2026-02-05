# ☁️ GWS Manager Sync: Cloud Deployment Guide

This folder contains the **Security-Hardened** version of the V3 Sync Engine, designed specifically for **GitHub Actions**.

## 🔐 SECURITY WARNING
- **NEVER** commit `master_project_token.json` or any other `.json` token file to GitHub.
- This version reads everything from **Environment Variables** (Memory) to prevent leaks.

## 🚀 Setup Instructions

### 1. Upload to GitHub
Upload the **contents** of this `cloud-sync-v3/` folder to your private GitHub repository.
- Ensure the `.github/workflows/` path is preserved.

### 2. Configure GitHub Secrets
Go to your GitHub Repository **Settings** -> **Secrets and variables** -> **Actions** and add these 3 Secrets:

| Secret Name | Value |
| :--- | :--- |
| `GWS_MASTER_TOKEN_JSON` | Copy the **ENTIRE TEXT** from your `master_project_token.json` file. |
| `SLACK_BOT_TOKEN` | Your `xoxb-...` token. |
| `SLACK_TARGET_EMAIL` | `ohmer.sulit@helloconnect.org` |

### 3. Verify
- The automation is scheduled for **5:00 PM PST daily**.
- You can trigger it manually by going to the **Actions** tab in GitHub, selecting "GWS Manager Sync Automation," and clicking **"Run workflow"**.

## 🛡️ Protection Features
- **No Disk Trace**: Credentials exist only in the runner's RAM.
- **Auto-Retry**: Handles Google 503 errors automatically.
- **Owner Immunity**: Detects and protects group owners dynamically.
