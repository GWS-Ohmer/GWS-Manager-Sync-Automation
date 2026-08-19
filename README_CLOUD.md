# GWS Manager Sync Automation

A scheduled GitHub Actions workflow that automatically syncs the HelloFresh USA Managers Google Group membership daily.

## How It Works

1. Scans the Google Workspace directory for active USA employees
2. Identifies their managers
3. Syncs the target Google Group to match verified active managers
4. Sends a Slack DM summary on completion

## Schedule

Runs daily at **1:00 AM UTC** (5:00 PM PST). Can also be triggered manually via the Actions tab.

## Required GitHub Secrets

Configure these in **Settings → Secrets and variables → Actions**:

| Secret Name | Description |
|---|---|
| `GWS_MASTER_TOKEN_JSON` | Google Workspace OAuth token (JSON format) |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_TARGET_EMAIL` | Email address to send Slack DM notifications to |

## Security

- All credentials are injected at runtime via GitHub Secrets — nothing is stored on disk
- No credential values are logged or printed
- Token refresh happens in memory only