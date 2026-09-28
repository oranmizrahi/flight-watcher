# Flight watcher

Monitors a SkyGini results page and sends a Telegram message on ANY change (price, flight added/removed, times).

## Setup (5 minutes)
1. **Telegram bot**: in Telegram open @BotFather -> /newbot -> copy the token.
   Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
2. **GitHub**: create a new *private* repo, upload these files (keep the `.github` folder).
3. Repo -> Settings -> Secrets and variables -> Actions -> add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Actions tab -> flight-watch -> Run workflow. You should get "הבוט פעיל".

## Notes
- Runs every 10 minutes. Change the cron in `.github/workflows/watch.yml`.
- To watch another search, change `WATCH_URL` in `watch.py`.
- Every run uploads `last.png`/`last.txt` (Actions -> run -> Artifacts) to debug what the bot saw.
- The page shows the whole text, so tiny changes (e.g. a banner) also trigger alerts;
  tell me if too noisy and I'll narrow it to flight cards only.
