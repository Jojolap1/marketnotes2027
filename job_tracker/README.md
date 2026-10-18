# Free APAC Sales & Trading job tracker

Monitors publicly accessible Greenhouse and Lever company job feeds for matching jobs in Hong Kong and Singapore. Sends **new** matches to Telegram and remembers previously sent job IDs.

## Setup (about 10 minutes)

1. Create a **private GitHub repository** and upload the contents of this folder, including `.github/workflows/jobs.yml` (GitHub's browser uploader may hide dotfolders; using Git is safer).
2. On Telegram, open **@BotFather**, send `/newbot`, follow the prompts, and copy the bot token. Keep it private.
3. Send `/start` to your newly created bot. In a browser, visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` (substitute your token locally, don't share this URL). Find `message.chat.id` in the response. If empty, message the bot again and refresh.
4. In GitHub repository **Settings → Secrets and variables → Actions**, add two **repository secrets**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Under **Settings → Actions → General**, enable Actions and allow workflows to have **Read and write permissions** (needed to commit `seen_jobs.json`).
6. Go to **Actions → Job tracker → Run workflow** to test. The first run sends all currently matching jobs in accessible feeds, not only today's publications. Subsequent runs only send unseen IDs.
7. The schedule checks approximately hourly, subject to GitHub scheduling delays. Public repositories have generous Actions quotas; private repositories have monthly free-minute quotas that vary by plan. Review usage in GitHub Billing.

## Edit the coverage

Edit `config.json` to add **verified** company job-board identifiers. For Greenhouse, the board token is the portion of the job-board URL after `boards.greenhouse.io/`; for Lever, it's the segment after `jobs.lever.co/`. Example:

```json
{"platform": "greenhouse", "company": "Example Company", "board": "examplecompany"}
```

**Important:** The included board tokens are initial *candidates*, not verified live integrations. A source can fail (404 or blocked) or have zero matching roles; failures are logged in GitHub Actions. Most major banks (including SG, BNP Paribas, HSBC, Citi) use other systems, so **they are not covered** until an appropriate permitted integration is added. This tracker does not scrape LinkedIn or Workday, does not cover all banks, and cannot guarantee instant alerts.

### Notes

- Title-based filters can miss roles with generic titles or include more experienced roles; edit regex filters as needed.
- Failed Telegram sends cause the workflow to fail and retain the previous state; if a batch partially sends, some notifications may repeat on the next run.
- The file `seen_jobs.json` is committed to the repo to persist deduplication across runs. Avoid parallel manual runs.
- You can change the cron schedule to `*/30 * * * *` for approximately every 30 minutes, but monitor your free-minute quota.
- No paid services or API keys are required for the job feeds used here.
