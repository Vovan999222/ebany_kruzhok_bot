import os
import logging
import subprocess
import sys
import importlib.metadata
import re
import uuid
import asyncio
from config import TOKEN
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import ffmpeg
import yt_dlp

REQUIRED_LIBRARIES = [
    "aiogram",
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

# лимиты
MAX_INPUT_SIZE = 50 * 1024 * 1024  
MAX_NOTE_SIZE = 12 * 1024 * 1024

TIKTOK_URL_REGEX = r"https?://(?:[\w-]+\.)*tiktok\.com/.*"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class DownloadState(StatesGroup):
    waiting_for_action = State()

def get_user_display_name(user: types.User):
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

@dp.message(CommandStart())
async def start(message: types.Message):
    user = message.from_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} начал использовать бота.")
    await message.answer(
        "Привет! Я могу конвертировать видео с TikTok в видеокружки или голосовые сообщения. "
        "Просто отправь мне ссылку на видео с TikTok, и я всё сделаю за тебя!"
    )

@dp.message(F.audio | (F.document & F.document.mime_type.startswith('audio/')))
async def handle_audio(message: types.Message):
    user = message.from_user
    name = get_user_display_name(user)
    file_obj = message.audio or message.document

    if file_obj.file_size > MAX_INPUT_SIZE:
        await message.answer("❌ Аудиофайл слишком большой (>50МБ).")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_in.mp3"
    output_path = f"{unique_id}_voice.ogg"

    try:
        file_info = await bot.get_file(file_obj.file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"
        logger.info(f"[{user.id}] {name} прислал аудио. Ссылка: {file_url}")
        await message.answer("⏳ Конвертирую...")
        await bot.download_file(file_info.file_path, destination=input_path)
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_voice(input_path, output_path)
        )
        if os.path.exists(output_path):
            await message.answer_voice(FSInputFile(output_path))
            logger.info(f"[{user.id}] {name} -> Голосовое отправлено.")
        else:
            await message.answer("❌ Ошибка создания файла.")

    except Exception as e:
        logger.error(f"Error audio: {e}")
        await message.answer("❌ Ошибка.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)


@dp.message(F.video | (F.document & F.document.mime_type.startswith('video/')))
async def handle_video(message: types.Message):
    user = message.from_user
    name = get_user_display_name(user)
    file_obj = message.video or message.document

    if file_obj.file_size > MAX_INPUT_SIZE:
        await message.answer("❌ Файл слишком большой (>50МБ).")
        return

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_in.mp4"
    output_path = f"{unique_id}_circle.mp4"

    try:
        file_info = await bot.get_file(file_obj.file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"
        logger.info(f"[{user.id}] {name} прислал видео. Ссылка: {file_url}")
        await message.answer("⏳ Обрабатываю видео...")
        await bot.download_file(file_info.file_path, destination=input_path)
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: run_ffmpeg_video_note(input_path, output_path)
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) < MAX_NOTE_SIZE:
            await message.answer_video_note(FSInputFile(output_path))
            logger.info(f"[{user.id}] {name} -> Видеокружок отправлен.")
        else:
            await message.answer("❌ Файл слишком большой или поврежден.")

    except Exception as e:
        logger.error(f"Error video: {e}")
        await message.answer("❌ Ошибка.")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    text = message.text
    user = message.from_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} написал: {text}")
    if re.search(TIKTOK_URL_REGEX, text):
        match = re.search(TIKTOK_URL_REGEX, text)
        clean_url = match.group(0)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📹 Видеокружок", callback_data='video')],
            [InlineKeyboardButton(text="🎤 Голосовое", callback_data='voice')]
        ])
        await state.update_data(tiktok_url=clean_url)
        await message.answer("Что сделать с ссылкой?", reply_markup=keyboard)

@dp.callback_query(F.data.in_({"video", "voice"}))
async def button_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    user = callback.from_user
    name = get_user_display_name(user)
    logger.info(f"[{user.id}] {name} нажал кнопку: {callback.data}")

    data = await state.get_data()
    tiktok_url = data.get('tiktok_url')

    if not tiktok_url:
        await callback.message.edit_text("❌ Ссылка устарела.")
        return
    await state.clear()

    unique_id = uuid.uuid4()
    input_path = f"{unique_id}_dl.mp4"
    output_path = f"{unique_id}_out.mp4"

    if callback.data == 'video':
        await callback.message.edit_text("⏳ Скачиваю и делаю кружок...")
        mode = 'video'
    else:
        await callback.message.edit_text("⏳ Скачиваю и делаю голосовое...")
        mode = 'voice'
        output_path = f"{unique_id}_voice.ogg"

    try:
        ydl_opts = {'outtmpl': input_path, 'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'noplaylist': True, 'quiet': True}

        await asyncio.get_running_loop().run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([tiktok_url])
        )

        if not os.path.exists(input_path):
            await callback.message.answer("❌ Не удалось скачать.")
            return

        if mode == 'video':
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_ffmpeg_video_note(input_path, output_path)
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) < MAX_NOTE_SIZE:
                await callback.message.answer_video_note(FSInputFile(output_path))
            else:
                 await callback.message.answer("⚠️ Видео слишком большое.")

        elif mode == 'voice':
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_ffmpeg_voice(input_path, output_path)
            )
            if os.path.exists(output_path):
                await callback.message.answer_voice(FSInputFile(output_path))
            else:
                 await callback.message.answer("❌ Ошибка аудио.")

    except Exception as e:
        logger.error(f"Process error: {e}")
        await callback.message.answer("❌ Ошибка обработки.")

    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): 
                try: os.remove(p) 
                except: pass

async def main():
    if not TOKEN or TOKEN == "":
        print("ОШИБКА: Вы забыли вставить TOKEN в файле config.py!")
        return
    print("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен пользователем.")
