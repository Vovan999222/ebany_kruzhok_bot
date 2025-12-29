import os
import logging
import subprocess
import sys
import importlib.metadata
import re
import uuid
import asyncio
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.error import NetworkError 
from telegram.request import HTTPXRequest
import ffmpeg
import yt_dlp

REQUIRED_LIBRARIES = [
    "python-telegram-bot",
    "imageio-ffmpeg",
    "ffmpeg-python",
    "yt-dlp"
]

def check_libraries_and_exit_if_missing():
    installed_packages = {pkg.name.lower() for pkg in importlib.metadata.distributions()}
    missing = [lib for lib in REQUIRED_LIBRARIES if lib.lower() not in installed_packages]

    if missing:
        print("\n" + "!" * 60)
        print("ОШИБКА: ОБНАРУЖЕНЫ НЕДОСТАЮЩИЕ БИБЛИОТЕКИ")
        print(f"Не хватает: {', '.join(missing)}")
        install_cmd = f"pip install {' '.join(missing)}"
        print(f"\n ВЫПОЛНИТЕ ЭТУ КОМАНДУ:\n    {install_cmd}\n")
        print("!" * 60 + "\n")
        sys.exit(1)

check_libraries_and_exit_if_missing()

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

log_filename = 'logs/bot-latest.log'

file_handler = TimedRotatingFileHandler(
    filename=log_filename,
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8'
)
file_handler.suffix = "%Y-%m-%d-%H-%M-%S"

def log_namer(default_name):
    base_dir, filename = os.path.split(default_name)
    clean_date = filename.replace("bot-latest.log.", "")
    new_filename = f"bot-{clean_date}.log"
    return os.path.join(base_dir, new_filename)

file_handler.namer = log_namer
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

if logger.hasHandlers():
    logger.handlers.clear()

logger.addHandler(file_handler)
logger.addHandler(console_handler)

print(f"Активный лог: {log_filename}")

# Лимиты
MAX_INPUT_SIZE = 50 * 1024 * 1024  
MAX_NOTE_SIZE = 12 * 1024 * 1024   

# ТОКЕН
TOKEN = "" 

TIKTOK_URL_REGEX = r"https?://(?:[\w-]+\.)*tiktok\.com/.*"

def get_user_display_name(user: User):
    if user.username:
        return f"@{user.username}"
    return user.first_name

def run_ffmpeg_video_note(input_path, output_path):
    clean_input_path = f"{input_path}_clean.mp4"
    try:
        cmd_sanitize = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', input_path,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
            '-c:a', 'copy',
            clean_input_path
        ]
        subprocess.run(cmd_sanitize, check=True, capture_output=True, text=True)

        probe = ffmpeg.probe(clean_input_path)
        video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        width = int(video_stream['width'])
        height = int(video_stream['height'])
        
        min_dim = min(width, height)
        crop_x = (width - min_dim) // 2
        crop_y = (height - min_dim) // 2
        
        filter_str = (
            f"crop={min_dim}:{min_dim}:{crop_x}:{crop_y},"
            "scale=640:640,"
            "setsar=1"
        )

        logger.info("FFmpeg: Создание видеокружка...")
        cmd_circle = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', clean_input_path,
            '-vf', filter_str,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '64k', '-ar', '44100', '-ac', '1',
            '-t', '59',
            output_path
        ]
        subprocess.run(cmd_circle, check=True, capture_output=True, text=True)

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg Error: {e.stderr}")
        raise Exception(f"Ошибка конвертации: {e.stderr}")
    finally:
        if os.path.exists(clean_input_path):
            try: os.remove(clean_input_path)
            except: pass

def run_ffmpeg_voice(input_path, output_path):
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', input_path,
        '-vn', '-map', '0:a',
        '-c:a', 'libopus', '-b:a', '48k', '-ar', '48000',
        output_path
    ]
    logger.info(f"FFmpeg: Создание голосового...")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg Error: {result.stderr}")
        raise Exception("Ошибка конвертации аудио")

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} начал использовать бота.")
    await update.message.reply_text(
        "Привет! Я могу конвертировать видео с TikTok в видеокружки или голосовые сообщения. "
        "Просто отправь мне ссылку на видео с TikTok, и я всё сделаю за тебя!"
    )

async def handle_audio(update: Update, context: CallbackContext):
    user = update.effective_user
    name = get_user_display_name(user)

    if update.message.audio.file_size > MAX_INPUT_SIZE:
        await update.message.reply_text("❌ Аудиофайл слишком большой (>50МБ).")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_in.mp3"
    output_path = f"{unique_id}_voice.ogg"

    try:
        file_info = await context.bot.get_file(update.message.audio.file_id)
        logger.info(f"[{user.id}] {name} прислал АУДИО. Ссылка: {file_info.file_path}")
        
        await update.message.reply_text("⏳ Конвертирую...")
        await file_info.download_to_drive(input_path)

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_voice(input_path, output_path)
        )

        if os.path.exists(output_path):
            with open(output_path, "rb") as f:
                await update.message.reply_voice(f)
            logger.info(f"[{user.id}] {name} -> Голосовое отправлено.")
        else:
            await update.message.reply_text("❌ Ошибка создания файла.")

    except Exception as e:
        logger.error(f"Error audio: {e}")
        await update.message.reply_text("❌ Ошибка.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)

async def handle_video(update: Update, context: CallbackContext):
    user = update.effective_user
    name = get_user_display_name(user)
    
    if update.message.video.file_size > MAX_INPUT_SIZE:
        await update.message.reply_text("❌ Файл слишком большой (>50МБ).")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_in.mp4"
    output_path = f"{unique_id}_circle.mp4"

    try:
        file_info = await context.bot.get_file(update.message.video.file_id)
        logger.info(f"[{user.id}] {name} прислал ВИДЕО. Ссылка: {file_info.file_path}")

        await update.message.reply_text("⏳ Обрабатываю видео...")
        await file_info.download_to_drive(input_path)

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_video_note(input_path, output_path)
        )

        if os.path.exists(output_path) and os.path.getsize(output_path) < MAX_NOTE_SIZE:
            with open(output_path, "rb") as f:
                await update.message.reply_video_note(f)
            logger.info(f"[{user.id}] {name} -> Видеокружок отправлен.")
        else:
            await update.message.reply_text("❌ Файл слишком большой или поврежден.")

    except Exception as e:
        logger.error(f"Error video: {e}")
        await update.message.reply_text("❌ Ошибка.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)

async def handle_text(update: Update, context: CallbackContext):
    text = update.message.text
    user = update.effective_user
    name = get_user_display_name(user)

    logger.info(f"[{user.id}] {name} написал: {text}")

    if re.search(TIKTOK_URL_REGEX, text):
        match = re.search(TIKTOK_URL_REGEX, text)
        clean_url = match.group(0) 
        
        keyboard = [
            [InlineKeyboardButton("📹 Видеокружок", callback_data='video')],
            [InlineKeyboardButton("🎤 Голосовое", callback_data='voice')]
        ]
        context.user_data['tiktok_url'] = clean_url
        await update.message.reply_text("Что сделать с ссылкой?", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        pass

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} нажал кнопку: {query.data}")

    tiktok_url = context.user_data.get('tiktok_url')
    if not tiktok_url:
        await query.edit_message_text("❌ Ссылка устарела.")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_dl.mp4"
    output_path = f"{unique_id}_out.mp4"

    if query.data == 'video':
        await query.edit_message_text("⏳ Скачиваю и делаю кружок...")
        mode = 'video'
    else:
        await query.edit_message_text("⏳ Скачиваю и делаю голосовое...")
        mode = 'voice'
        output_path = f"{unique_id}_voice.ogg"

    try:
        ydl_opts = {'outtmpl': input_path, 'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'noplaylist': True, 'quiet': True}

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([tiktok_url])
        )

        if not os.path.exists(input_path):
            await context.bot.send_message(query.from_user.id, "❌ Не удалось скачать.")
            return

        if mode == 'video':
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_ffmpeg_video_note(input_path, output_path)
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) < MAX_NOTE_SIZE:
                with open(output_path, "rb") as f:
                    await context.bot.send_video_note(query.from_user.id, f)
            else:
                 await context.bot.send_message(query.from_user.id, "⚠️ Видео слишком большое.")

        elif mode == 'voice':
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_ffmpeg_voice(input_path, output_path)
            )
            if os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    await context.bot.send_voice(query.from_user.id, f)
            else:
                 await context.bot.send_message(query.from_user.id, "❌ Ошибка аудио.")

    except Exception as e:
        logger.error(f"Process error: {e}")
        await context.bot.send_message(query.from_user.id, "❌ Ошибка обработки.")
    
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): 
                try: os.remove(p) 
                except: pass

def run_bot():
    """Функция непосредственного запуска бота"""
    if not TOKEN or TOKEN == "":
        print("ОШИБКА: Вы забыли вставить TOKEN в файле bot.py!")
        return

    print("Бот запускается...")

    request_kwargs = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=30.0,
        pool_timeout=30.0
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .request(request_kwargs)
        .build()
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("Бот успешно запущен.")
    application.run_polling()

def main():
    """Главный цикл с обработкой ошибок сети и выхода"""
    while True:
        try:
            run_bot()
        
        except KeyboardInterrupt:
            print("\nБот остановлен пользователем.")
            sys.exit(0)

        except NetworkError as e:
            logger.error(f"Сетевая ошибка: {e}")
            print(f"\nСбой сети ({e}). Перезапуск через 3 секунды...")
            time.sleep(3)

        except Exception as e:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
            print(f"\nБОТ УПАЛ С ОШИБКОЙ: {e}")
            print("Перезапуск через 3 секунды...")
            time.sleep(3)

if __name__ == "__main__":
    main()
