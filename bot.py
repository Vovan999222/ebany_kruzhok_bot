import os
import ffmpeg
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from moviepy import AudioFileClip

TOKEN = ""

# Максимальный размер файла в байтах (20 МБ)
MAX_FILE_SIZE = 20 * 1024 * 1024

async def start(update: Update, context: CallbackContext):
    """Отправляет приветственное сообщение в ответ на команду /start."""
    await update.message.reply_text("Привет! Отправь мне песню или видео, и я превращу её в голосовое сообщение или видеокружок.")

async def handle_audio(update: Update, context: CallbackContext):
    """Скачивает аудио, конвертирует в голосовое сообщение и отправляет пользователю."""
    if update.message.audio.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ Этот аудиофайл слишком большой! Я могу обрабатывать файлы размером до 20 МБ.")
        return

    input_path = "audio.mp3"
    output_path = "voice.ogg"
    
    await update.message.reply_text("Получил аудио, начинаю обработку... ⏳")
    
    try:
        file = await update.message.audio.get_file()
        await file.download_to_drive(input_path)
        
        with AudioFileClip(input_path) as clip:
            clip.write_audiofile(output_path, codec="libopus", bitrate="64k", fps=48000)

        if os.path.getsize(output_path) > 0:
            with open(output_path, "rb") as voice_file:
                await update.message.reply_voice(voice_file)
        else:
            await update.message.reply_text("❌ Не удалось сконвертировать аудио. Пожалуйста, попробуйте другой файл.")
            
    except Exception as e:
        print(f"❌ Произошла ошибка при обработке аудио: {e}")
        await update.message.reply_text("❌ Произошла непредвиденная ошибка при обработке вашего аудиофайла.")
        
    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

async def handle_video(update: Update, context: CallbackContext):
    """Скачивает видео, конвертирует в видеокружок и отправляет пользователю."""
    if update.message.video.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ Этот видеофайл слишком большой! Я могу обрабатывать файлы размером до 20 МБ.")
        return
        
    input_path = "input_video.mp4"
    output_path = "output_video.mp4"

    await update.message.reply_text("Получил видео, конвертирую в кружок... 📹")
    
    try:
        file = await update.message.video.get_file()
        await file.download_to_drive(input_path)
        
        probe = ffmpeg.probe(input_path)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])
        
        min_dim = min(width, height)
        crop_x = (width - min_dim) // 2
        crop_y = (height - min_dim) // 2
        
        input_stream = ffmpeg.input(input_path)
        video_stream = (
            input_stream.video
            .filter('crop', min_dim, min_dim, crop_x, crop_y)
            .filter('scale', 640, 640)
        )
        audio_stream = input_stream.audio
        
        (
            ffmpeg
            .output(video_stream, audio_stream, output_path, vcodec='libx264', acodec='aac')
            .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
        )
        
        with open(output_path, "rb") as video_note_file:
            await update.message.reply_video_note(video_note_file)
            
    except Exception as e:
        print(f"❌ Произошла ошибка при обработке видео: {e}")
        if isinstance(e, ffmpeg.Error):
            print('stdout:', e.stdout.decode('utf8'))
            print('stderr:', e.stderr.decode('utf8'))
        await update.message.reply_text("❌ Произошла непредвиденная ошибка при обработке вашего видеофайла.")

    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(output_path): os.remove(output_path)

def main():
    """Основная функция для запуска бота."""
    print("Бот запускается...")
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    
    print("Бот успешно запущен и готов к работе.")
    application.run_polling()
    print("Бот остановлен.")

if __name__ == "__main__":
    main()