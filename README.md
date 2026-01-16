# ebany kruzhok bot

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=for-the-badge&logo=python)
![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?style=for-the-badge&logo=telegram)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Enabled-green?style=for-the-badge&logo=ffmpeg)

This Telegram bot is designed to convert media files and TikTok links into native Telegram formats (Video Notes and Voice Messages).

The bot automatically downloads TikTok videos without watermarks and processes files uploaded by users.

## Features

* **TikTok Downloader**:
    * Accepts TikTok links.
    * Offers a choice: convert to **Video Note** (circle) or **Voice Message**.
* **Video Conversion**:
    * Converts standard video files (.mp4, etc.) into circular Video Notes.
    * Automatically crops video to a 1:1 aspect ratio (center) and resizes to 640x640.
* **Audio Conversion**:
    * Converts audio files (.mp3, etc.) into Voice Messages (Opus codec).
* **Logging**:
    * Maintains detailed logs with date-based rotation in the `logs/` directory.
* **Auto-Restart**:
    * Built-in protection against crashes and network errors.

## Requirements

To run the bot, you need:

1.  **Python 3.8+**
2.  **FFmpeg** (system-level) — Critical for media processing.

### Installing FFmpeg:

* **Ubuntu/Debian**: `sudo apt update && sudo apt upgrade && sudo apt install ffmpeg`
* **Windows**:
    * **Method 1 (Recommended):** Open a terminal (PowerShell or CMD) and run:
      ```cmd
      winget install Gyan.FFmpeg
      ```
      > **⚠️ "winget" command not found?** > If you are using an older version of Windows 10 and the command is missing, download and install the **App Installer** from the [official GitHub releases](https://github.com/microsoft/winget-cli/releases) (look for the file ending in `.msixbundle`).
    
    * **Method 2 (Manual):** Download the archive from the [official repository](https://github.com/GyanD/codexffmpeg/releases), unzip it, and add the path to the `bin` folder to your system environment variables (PATH).
      ```cmd
      C:\ffmpeg\bin
      ```
* **MacOS**: `brew install ffmpeg`

## Installation & Usage

### 1. Clone the repository

```bash
git clone https://github.com/Vovan999222/ebany_kruzhok_bot.git

cd ebany_kruzhok_bot
```

### 2. Create a virtual environment (Recommended)

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Linux/Mac:
source venv/bin/activate

```

### 3. Install dependencies

You can install the libraries manually:

```bash
pip install python-telegram-bot imageio-ffmpeg ffmpeg-python yt-dlp

```

Or create a `requirements.txt` file and install via:

```bash
pip install -r requirements.txt

```

### 4. Configuration

Open the `bot.py` file and find the following line:

```python
TOKEN = ""  # Paste your token from @BotFather here

```

Insert your bot token inside the quotes.

### 5. Run the bot

```bash
python bot.py

```

## Technical Details

* **Limits**: The bot processes files up to **50 MB**.
* **Video**: Encoded in H.264, preset `veryfast`, CRF 28 (optimized for Telegram).
* **Audio**: Encoded in Opus, 48kHz (standard for Telegram voice messages).

## License

This project is distributed under the [MIT License.](https://raw.githubusercontent.com/Vovan999222/ebany_kruzhok_bot/refs/heads/main/LICENSE)
