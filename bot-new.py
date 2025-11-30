import os
import logging
import subprocess
import sys
import importlib.metadata
import re
import uuid
import asyncio
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import ffmpeg
import yt_dlp

required_libraries = [
    "python-telegram-bot",
    "moviepy",
    "imageio-ffmpeg",
    "ffmpeg-python",
    "yt-dlp"
]

def check_libraries():
    installed_libraries = {pkg.name for pkg in importlib.metadata.distributions()}
    missing_libraries = [lib for lib in required_libraries if lib not in installed_libraries]
    return missing_libraries

def install_missing_libraries(missing_libraries):
    for library in missing_libraries:
        subprocess.check_call([sys.executable, "-m", "pip", "install", library])

missing_libraries = check_libraries()
if missing_libraries:
    print(f"Недостающие библиотеки: {', '.join(missing_libraries)}. Устанавливаю...")
    install_missing_libraries(missing_libraries)
else:
    print("Все необходимые библиотеки уже установлены.")

os.makedirs('logs', exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_filename = datetime.now().strftime('logs/bot-%Y-%m-%d-%H-%M-%S.log')

file_handler = TimedRotatingFileHandler(
    filename=log_filename,
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

print(f"Логи будут записываться в файл: {log_filename}")

# Лимиты
MAX_INPUT_SIZE = 50 * 1024 * 1024  
MAX_NOTE_SIZE = 12 * 1024 * 1024   
# Токен бота
TOKEN = ""

TIKTOK_URL_REGEX = r"https?://(?:[\w-]+\.)*tiktok\.com/.*"

def get_user_display_name(user: User):
    """Возвращает @username или Имя, если юзернейма нет."""
    if user.username:
        return f"@{user.username}"
    return user.first_name

def run_ffmpeg_video_note(input_path, output_path):
    """
    Создание видеокружка с жесткой очисткой метаданных.
    """
    clean_input_path = f"{input_path}_clean.mp4"
    
    try:
        cmd_sanitize = [
            'ffmpeg',
            '-y', '-hide_banner', '-loglevel', 'error',
            '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-colorspace', 'bt709',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '22',
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

        logger.info("Запуск Этапа 2: Создание кружка...")
        cmd_circle = [
            'ffmpeg',
            '-y', '-hide_banner', '-loglevel', 'error',
            '-i', clean_input_path,
            '-vf', filter_str,
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '28',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '64k',
            '-ar', '44100',
            '-ac', '1',
            '-t', '59',
            output_path
        ]
        
        subprocess.run(cmd_circle, check=True, capture_output=True, text=True)

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr}")
        raise Exception(f"Ошибка конвертации: {e.stderr}")
    finally:
        if os.path.exists(clean_input_path):
            try: os.remove(clean_input_path)
            except: pass

def run_ffmpeg_voice(input_path, output_path):
    """
    Конвертация любого аудио/видео в голосовое (OGG Opus).
    """
    try:
        cmd = [
            'ffmpeg',
            '-y', '-hide_banner', '-loglevel', 'error',
            '-i', input_path,
            '-vn',
            '-c:a', 'libopus',
            '-b:a', '48k',
            '-ar', '48000',
            output_path
        ]
        logger.info(f"Запуск FFmpeg аудио: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg audio error: {e.stderr}")
        raise Exception("Ошибка конвертации аудио")

async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} запустил бота.")
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
        logger.info(f"[{user.id}] {name} отправил аудиофайл. Ссылка: {file_info.file_path}")

        await update.message.reply_text("⏳ Конвертирую аудио в голосовое...")
        await file_info.download_to_drive(input_path)

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_voice(input_path, output_path)
        )

        if os.path.exists(output_path):
            with open(output_path, "rb") as f:
                await update.message.reply_voice(f)
        else:
            await update.message.reply_text("❌ Ошибка конвертации аудио.")

    except Exception as e:
        logger.error(f"[{user.id}] {name} Ошибка аудио: {e}")
        await update.message.reply_text("❌ Произошла ошибка.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)

async def handle_video(update: Update, context: CallbackContext):
    user = update.effective_user
    name = get_user_display_name(user)
    
    if update.message.video.file_size > MAX_INPUT_SIZE:
        logger.info(f"[{user.id}] {name} попытался отправить большой файл (>50МБ).")
        await update.message.reply_text("❌ Файл слишком большой (>50МБ).")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_in.mp4"
    output_path = f"{unique_id}_circle.mp4"

    try:
        file_info = await context.bot.get_file(update.message.video.file_id)
        
        logger.info(f"[{user.id}] {name} отправил видеофайл. Ссылка: {file_info.file_path}")
        
        await update.message.reply_text("⏳ Обрабатываю видео...")

        await file_info.download_to_drive(input_path)

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_video_note(input_path, output_path)
        )

        if os.path.exists(output_path) and os.path.getsize(output_path) < MAX_NOTE_SIZE:
            with open(output_path, "rb") as f:
                await update.message.reply_video_note(f)
        else:
            await update.message.reply_text("❌ Ошибка: файл слишком большой или поврежден.")

    except Exception as e:
        logger.error(f"[{user.id}] {name} Ошибка: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)

async def handle_text(update: Update, context: CallbackContext):
    text = update.message.text
    user = update.effective_user
    name = get_user_display_name(user)
    
    logger.info(f"[{user.id}] {name} отправил текст: {text}")

    if re.match(TIKTOK_URL_REGEX, text):
        keyboard = [
            [InlineKeyboardButton("Сделать видеокружок", callback_data='video')],
            [InlineKeyboardButton("Сделать голосовое сообщение", callback_data='voice')]
        ]
        context.user_data['tiktok_url'] = text
        await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Это не похоже на ссылку TikTok.")

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} нажал кнопку: {query.data}")

    tiktok_url = context.user_data.get('tiktok_url')
    if not tiktok_url:
        await query.edit_message_text("Ссылка устарела.")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_dl.mp4"
    output_path = f"{unique_id}_out.mp4" 

    if query.data == 'video':
        await query.edit_message_text("⏳ Скачиваю и конвертирую...")
        mode = 'video'
    else:
        await query.edit_message_text("⏳ Скачиваю и делаю голосовое...")
        mode = 'voice'
        output_path = f"{unique_id}_voice.ogg"

    try:
        ydl_opts = {
            'outtmpl': input_path,
            'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'noplaylist': True
        }

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([tiktok_url])
        )

        if not os.path.exists(input_path):
            await context.bot.send_message(query.from_user.id, "❌ Ошибка скачивания.")
            return

        if mode == 'video':
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_ffmpeg_video_note(input_path, output_path)
            )
            
            if os.path.exists(output_path):
                if os.path.getsize(output_path) > MAX_NOTE_SIZE:
                     await context.bot.send_message(query.from_user.id, "⚠️ Видео слишком тяжелое (>12MB).")
                else:
                    with open(output_path, "rb") as f:
                        await context.bot.send_video_note(query.from_user.id, f)
            else:
                 await context.bot.send_message(query.from_user.id, "❌ Ошибка конвертации.")

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
        logger.error(f"[{user.id}] {name} Error process: {e}")
        await context.bot.send_message(query.from_user.id, "❌ Произошла ошибка.")
    
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): 
                try: os.remove(p) 
                except: pass

def main():
    print("Бот запускается...")
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT, handle_text))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("Бот успешно запущен.")
    application.run_polling()

if __name__ == "__main__":
    main()
