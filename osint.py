import asyncio
import logging
import sqlite3
import aiohttp
import socket
import ssl
from datetime import date
from urllib.parse import urlparse
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = "8774132269:AAG-1aHseo4MFZmSjOYiTDm1JwkjxuCKP0c"  # Вставьте токен вашего бота

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect("osint_bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            requests_left INTEGER DEFAULT 5,
            last_reset DATE,
            invited_by INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user(user_id):
    conn = sqlite3.connect("osint_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT requests_left, last_reset, invited_by FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    today = str(date.today())
    
    if not row:
        cursor.execute("INSERT INTO users (user_id, requests_left, last_reset) VALUES (?, 5, ?)", (user_id, today))
        conn.commit()
        requests_left = 5
    else:
        requests_left, last_reset, invited_by = row
        if last_reset != today:
            requests_left = 5
            cursor.execute("UPDATE users SET requests_left = 5, last_reset = ? WHERE user_id = ?", (today, user_id))
            conn.commit()
    conn.close()
    return requests_left

def decrease_limit(user_id):
    conn = sqlite3.connect("osint_bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET requests_left = requests_left - 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_bonus(user_id, amount=3):
    conn = sqlite3.connect("osint_bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET requests_left = requests_left + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

# FSM Состояния для новых функций
class OsintStates(StatesGroup):
    waiting_for_ssl = State()
    waiting_for_fuzz = State()
    waiting_for_ports = State()
    waiting_for_mac = State()
    waiting_for_doc = State()

# --- МЕНЮ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args
    
    conn = sqlite3.connect("osint_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT invited_by FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if not row:
        today = str(date.today())
        inviter = int(args) if args and args.isdigit() and int(args) != user_id else None
        cursor.execute("INSERT INTO users (user_id, requests_left, last_reset, invited_by) VALUES (?, 5, ?, ?)", (user_id, today, inviter))
        conn.commit()
        if inviter:
            add_bonus(inviter, 3)
    conn.close()

    left = get_user(user_id)
    
    kb = [
        [types.KeyboardButton(text="🔒 SSL Сертификат"), types.KeyboardButton(text="📂 Fuzzing Директорий")],
        [types.KeyboardButton(text="⚡ Сканер портов"), types.KeyboardButton(text="🔍 MAC Vendor Lookup")],
        [types.KeyboardButton(text="📄 Анализ документов"), types.KeyboardButton(text="👥 Моя ссылка (Лимиты)")]
    ]
    keyboard = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! Это расширенный OSINT бот.\n"
        f"Осталось запросов на сегодня: <b>{left}</b>\n\n"
        f"Выберите инструмент в меню ниже:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.message(F.text == "👥 Моя ссылка (Лимиты)")
async def referral_link(message: types.Message):
    user_id = message.from_user.id
    left = get_user(user_id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    await message.answer(
        f"📊 **Ваша статистика:**\n"
        f"🔹 Осталось запросов: **{left}**\n\n"
        f"🔗 **Реферальная ссылка:**\n`{ref_link}`\n\n"
        f"💡 *Приглашайте друзей, чтобы увеличивать лимит!*",
        parse_mode="Markdown"
    )

# --- 1. SSL/TLS СЕРТИФИКАТ ---
@dp.message(F.text == "🔒 SSL Сертификат")
async def start_ssl(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Лимит исчерпан.")
        return
    await message.answer("Введите домен для проверки SSL (например: `google.com`):")
    await state.set_state(OsintStates.waiting_for_ssl)

@dp.message(OsintStates.waiting_for_ssl)
async def process_ssl(message: types.Message, state: FSMContext):
    domain = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    decrease_limit(message.from_user.id)
    
    context = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                
                subject = dict(x[0] for x in cert.get('subject', []))
                issuer = dict(x[0] for x in cert.get('issuer', []))
                not_before = cert.get('notBefore')
                not_after = cert.get('notAfter')
                
                info = (
                    f"🔒 **SSL Сертификат `{domain}`:**\n\n"
                    f"🏢 **Издатель:** {issuer.get('organizationName', 'Неизвестно')}\n"
                    f"👤 **Владелец (CN):** {subject.get('commonName', 'Неизвестно')}\n"
                    f"📅 **Действителен с:** {not_before}\n"
                    f"⏳ **Действителен до:** {not_after}"
                )
    except Exception as e:
        info = f"⚠️ Ошибка получения SSL сертификата: {e}"

    await message.answer(info, parse_mode="Markdown")
    await state.clear()

# --- 2. FUZZING ДИРЕКТОРИЙ (Поиск скрытых путей) ---
@dp.message(F.text == "📂 Fuzzing Директорий")
async def start_fuzz(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Лимит исчерпан.")
        return
    await message.answer("Введите URL сайта (например: `https://example.com`):")
    await state.set_state(OsintStates.waiting_for_fuzz)

@dp.message(OsintStates.waiting_for_fuzz)
async def process_fuzz(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        url = "https://" + url
    decrease_limit(message.from_user.id)
    
    # Базовый список популярных путей для проверки
    common_paths = [
        "robots.txt", "sitemap.xml", "admin", "login", "api", 
        "config.json", ".env", "backup.zip", "wp-login.php", "dashboard"
    ]
    
    result = f"📂 **Результаты Fuzzing для `{url}`:**\n\n"
    async with aiohttp.ClientSession() as session:
        for path in common_paths:
            target_url = f"{url.rstrip('/')}/{path}"
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                async with session.get(target_url, headers=headers, timeout=3, allow_redirects=False) as resp:
                    if resp.status in [200, 301, 302, 403]:
                        result += f"🔹 `/{path}` — Статус: **{resp.status}**\n"
            except:
                pass

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 3. СКАНИРОВАНИЕ ПОРТОВ ---
@dp.message(F.text == "⚡ Сканер портов")
async def start_ports(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Лимит исчерпан.")
        return
    await message.answer("Введите домен или IP для сканирования базовых портов (например: `scanme.nmap.org`):")
    await state.set_state(OsintStates.waiting_for_ports)

@dp.message(OsintStates.waiting_for_ports)
async def process_ports(message: types.Message, state: FSMContext):
    host = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    decrease_limit(message.from_user.id)
    
    ports_to_check = {
        21: "FTP", 22: "SSH", 80: "HTTP", 443: "HTTPS", 
        3306: "MySQL", 3389: "RDP", 8080: "HTTP-Proxy"
    }
    
    result = f"⚡ **Сканирование портов для `{host}`:**\n\n"
    try:
        ip = socket.gethostbyname(host)
        result += f"📌 IP: `{ip}`\n\n"
        
        for port, service in ports_to_check.items():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            try:
                conn = s.connect_ex((ip, port))
                if conn == 0:
                    result += f"🟢 Порт `{port}` ({service}): **Открыт**\n"
                else:
                    result += f"🔴 Порт `{port}` ({service}): Закрыт\n"
            except:
                pass
            finally:
                s.close()
    except Exception as e:
        result += f"⚠️ Ошибка резолва хоста: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 4. MAC VENDOR LOOKUP ---
@dp.message(F.text == "🔍 MAC Vendor Lookup")
async def start_mac(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Лимит исчерпан.")
        return
    await message.answer("Введите MAC-адрес (например: `00:1A:2B:3C:4D:5E`):")
    await state.set_state(OsintStates.waiting_for_mac)

@dp.message(OsintStates.waiting_for_mac)
async def process_mac(message: types.Message, state: FSMContext):
    mac = message.text.strip()
    decrease_limit(message.from_user.id)
    
    api_url = f"https://api.macvendors.com/{mac}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(api_url, timeout=5) as resp:
                if resp.status == 200:
                    vendor = await resp.text()
                    result = f"🔍 **MAC-адрес:** `{mac}`\n🏢 **Производитель:** `{vendor}`"
                else:
                    result = "❌ Производитель не найден или неверный формат MAC-адреса."
        except Exception as e:
            result = f"⚠️ Ошибка запроса: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 5. АНАЛИЗ ДОКУМЕНТОВ (Метаданные PDF / DOCX) ---
@dp.message(F.text == "📄 Анализ документов")
async def start_doc(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Лимит исчерпан.")
        return
    await message.answer("Отправьте документ (`.pdf`) в виде файла (документа), чтобы проанализировать его метаданные.")
    await state.set_state(OsintStates.waiting_for_doc)

@dp.message(OsintStates.waiting_for_doc, F.document)
async def process_doc(message: types.Message, state: FSMContext):
    decrease_limit(message.from_user.id)
    doc = message.document
    
    if not doc.file_name.endswith('.pdf'):
        await message.answer("❌ Пока поддерживается детальный анализ только PDF файлов.")
        await state.clear()
        return
        
    file_info = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file_info.file_path)
    
    try:
        from pypdf import PdfReader
        import io
        
        reader = PdfReader(io.BytesIO(file_bytes.read()))
        meta = reader.metadata
        
        result = f"📄 **Метаданные PDF (`{doc.file_name}`):**\n\n"
        if meta:
            result += f"🔹 **Автор:** `{meta.author}`\n"
            result += f"🔹 **Создатель:** `{meta.creator}`\n"
            result += f"🔹 **Программа:** `{meta.producer}`\n"
            result += f"🔹 **Дата создания:** `{meta.creation_date}`\n"
            result += f"🔹 **Количество страниц:** `{len(reader.pages)}`\n"
        else:
            result += "❌ В документе отсутствуют метаданные."
            
        await message.answer(result, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка чтения PDF: {e}")
        
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
