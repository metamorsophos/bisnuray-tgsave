<h1 align="center">Restricted Content Downloader Telegram Bot</h1>

<p align="center">
  <a href="https://github.com/bisnuray/RestrictedContentDL/stargazers"><img src="https://img.shields.io/github/stars/bisnuray/RestrictedContentDL?color=blue&style=flat" alt="GitHub Repo stars"></a>
  <a href="https://github.com/bisnuray/RestrictedContentDL/issues"><img src="https://img.shields.io/github/issues/bisnuray/RestrictedContentDL" alt="GitHub issues"></a>
  <a href="https://github.com/bisnuray/RestrictedContentDL/pulls"><img src="https://img.shields.io/github/issues-pr/bisnuray/RestrictedContentDL" alt="GitHub pull requests"></a>
  <a href="https://github.com/bisnuray/RestrictedContentDL/graphs/contributors"><img src="https://img.shields.io/github/contributors/bisnuray/RestrictedContentDL?style=flat" alt="GitHub contributors"></a>
  <a href="https://github.com/bisnuray/RestrictedContentDL/network/members"><img src="https://img.shields.io/github/forks/bisnuray/RestrictedContentDL?style=flat" alt="GitHub forks"></a>
</p>

<p align="center">
  <em>Restricted Content Downloader: An advanced Telegram bot script to download restricted content such as photos, videos, audio files, or documents from Telegram private chats or channels. This bot can also copy text messages from Telegram posts.</em>
</p>
<hr>

## Features

- 📥 Download media (photos, videos, audio, documents).
- ✅ Supports downloading from both single media posts and media groups.
- 🔄 Progress bar showing real-time downloading progress.
- ✍️ Copy text messages or captions from Telegram posts.

## Requirements

Before you begin, ensure you have met the following requirements:

- Python 3.8 or higher. recommended Python 3.11
- `pyrofork`, `pyleaves` and `tgcrypto` libraries.
- A Telegram bot token (you can get one from [@BotFather](https://t.me/BotFather) on Telegram).
- API ID and Hash: You can get these by creating an application on [my.telegram.org](https://my.telegram.org).
- To Get `SESSION_STRING` Open [@SmartUtilBot](https://t.me/SmartUtilBot). Bot and use /pyro command and then follow all instructions.

## Installation

To install `pyrofork`, `pyleaves` and `tgcrypto`, run the following command:

```bash
pip install -r -U requirements.txt
```

**Note: If you previously installed `pyrogram`, uninstall it before installing `pyrofork`.**

## Configuration

1. Open the `config.env` file in your favorite text editor.
2. Replace the placeholders for `API_ID`, `API_HASH`, `SESSION_STRING`, and `BOT_TOKEN` with your actual values:
   - **`API_ID`**: Your API ID from [my.telegram.org](https://my.telegram.org).
   - **`API_HASH`**: Your API Hash from [my.telegram.org](https://my.telegram.org).
   - **`SESSION_STRING`**: The session string generated using [@SmartUtilBot](https://t.me/SmartUtilBot).
   - **`BOT_TOKEN`**: The token you obtained from [@BotFather](https://t.me/BotFather).

## Deploy the Bot

```sh
git clone https://github.com/bisnuray/RestrictedContentDL
cd RestrictedContentDL
python main.py
```

## Deploy the Bot Using Docker Compose

```sh
git clone https://github.com/bisnuray/RestrictedContentDL
cd RestrictedContentDL
docker compose up --build --remove-orphans
```

Make sure you have Docker and Docker Compose installed on your system. The bot will run in a containerized environment with all dependencies automatically managed.

To stop the bot:

```sh
docker compose down
```

## Deploy to Heroku

You can run this bot on Heroku (Free/Basic dynos) as a worker process.

### One-Click (if you forked and enabled the button)

Use a Deploy button referencing `app.json` in your fork (example snippet):

```
[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)
```

### Manual Deploy Steps

1. Create a Heroku app:
  ```sh
  heroku create my-media-bot
  ```
2. Set required config vars (replace with your real values):
  ```sh
  heroku config:set API_ID=123456 API_HASH=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa BOT_TOKEN=123456:abcdefSESSION SESSION_STRING=YOUR_LONG_STRING TZ=UTC -a my-media-bot
  ```
3. Push code:
  ```sh
  git push heroku main
  ```
4. Scale the worker (Procfile defines `worker: python main.py`):
  ```sh
  heroku ps:scale worker=1
  ```
5. View logs:
  ```sh
  heroku logs --tail
  ```

### Generating SESSION_STRING for Heroku

Locally run:
```sh
python generate_session.py
```
Copy the printed session string and set it in Heroku config vars as `SESSION_STRING`.

### Notes

- Heroku ephemeral filesystem means downloaded media is temporary; this bot sends media immediately, so it's fine.
- Keep downloads small to avoid hitting the 512 MB RAM limit on free tiers.
- If you change environment variables, restart the dyno: `heroku restart`.
- Ensure you never expose `SESSION_STRING` in public logs or commits.
- For video thumbnails & metadata (duration) you need ffmpeg/ffprobe. Add a file named `Aptfile` with a single line `ffmpeg` then push again; Heroku will build it. Without it, the bot still works (just no custom thumbnails/duration extraction).

### Optional: Add ffmpeg on Heroku (recommended)

1. Create `Aptfile` in project root with:
  ```
  ffmpeg
  ```
2. Commit & push. Heroku will install ffmpeg during slug compile.



## Usage

- **`/start`** – Welcomes you and gives a brief introduction.  
- **`/help`** – Shows detailed instructions and examples.  
- **`/dl <post_URL>`** or simply paste a Telegram post link – Fetch photos, videos, audio, or documents from that post.  
- **`/bdl <start_link> <end_link>`** – Batch-download a range of posts in one go.  

  > 💡 Example: `/bdl https://t.me/mychannel/100 https://t.me/mychannel/120`  
- **`/killall`** – Cancel any pending downloads if the bot hangs.  
- **`/logs`** – Download the bot’s logs file.  
- **`/stats`** – View current status (uptime, disk, memory, network, CPU, etc.).  

> **Note:** Make sure that your user session is a member of the source chat or channel before downloading.

## Author

- Name: Bisnu Ray
- Telegram: [@itsSmartDev](https://t.me/itsSmartDev)

> **Note**: If you found this repo helpful, please fork and star it. Also, feel free to share with proper credit!
