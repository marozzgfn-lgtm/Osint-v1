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

TOKEN = "8774132269:AAG-1aHseo4MFZmSjOYiTDm1JwkjxuCKP0c"  # Вставьте ваш токен

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
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

# FSM Состояния
class OsintStates(StatesGroup):
    waiting_for_ip = State()
    waiting_for_username = State()
    waiting_for_ssl = State()
    waiting_for_fuzz = State()
    waiting_for_ports = State()
    waiting_for_mac = State()
    waiting_for_doc = State()
    waiting_for_photo = State()
    waiting_for_headers = State()
    waiting_for_leak = State()
    waiting_for_dns = State()
    waiting_for_wayback = State()
    waiting_for_phone = State()

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
        [types.KeyboardButton(text="🌐 IP / Домен"), types.KeyboardButton(text="👤 Username (Соцсети)")],
        [types.KeyboardButton(text="🔒 SSL Сертификат"), types.KeyboardButton(text="📂 Fuzzing Директорий")],
        [types.KeyboardButton(text="⚡ Сканер портов"), types.KeyboardButton(text="🔍 MAC Vendor Lookup")],
        [types.KeyboardButton(text="📄 Анализ PDF"), types.KeyboardButton(text="🖼️ EXIF Фото")],
        [types.KeyboardButton(text="🛡️ HTTP Headers"), types.KeyboardButton(text="⚠️ Проверка утечек (Email)")],
        [types.KeyboardButton(text="🔤 DNS Записи"), types.KeyboardButton(text="🏛️ Wayback Archive")],
        [types.KeyboardButton(text="📱 Проверка телефона"), types.KeyboardButton(text="👥 Моя ссылка (Лимиты)")]
    ]
    keyboard = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    await message.answer(
        f"Բարև, {message.from_user.first_name}! Սա հզոր և ընդլայնված OSINT բոտ է։\n"
        f"Այսօր մնացած հարցումներ: <b>{left}</b>\n\n"
        f"Ընտրիր գործիքը ներքևի ընտրացանկից:",
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
        f"📊 **Վիճակագրություն:**\n"
        f"🔹 Մնացած հարցումներ: **{left}**\n\n"
        f"🔗 **Ռեֆերալ հղում:**\n`{ref_link}`\n\n"
        f"💡 *Հրավիրիր ընկերներին լիմիտը մեծացնելու համար!*",
        parse_mode="Markdown"
    )

# --- 1. IP / DOMAIN GEOLOCATION ---
@dp.message(F.text == "🌐 IP / Домен")
async def start_ip(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր IP հասցեն կամ դոմեյնը (օրինակ՝ `8.8.8.8` կամ `google.com`):")
    await state.set_state(OsintStates.waiting_for_ip)

@dp.message(OsintStates.waiting_for_ip)
async def process_ip(message: types.Message, state: FSMContext):
    query = message.text.strip()
    decrease_limit(message.from_user.id)
    
    url = f"http://ip-api.com/json/{query}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                data = await resp.json()
                if data.get("status") == "success":
                    res = (
                        f"🌐 **IP/Domain Տեղեկություն (`{query}`):**\n\n"
                        f"🌍 Երկիր: `{data.get('country')}` ({data.get('countryCode')})\n"
                        f"🏙️ Քաղաք: `{data.get('city')}`\n"
                        f"📍 Մարզ: `{data.get('regionName')}`\n"
                        f"🏢 Պրովայդեր (ISP): `{data.get('isp')}`\n"
                        f"📡 Կազմակերպություն: `{data.get('org')}`\n"
                        f"📌 Կոորդինատներ: `{data.get('lat')}, {data.get('lon')}`"
                    )
                else:
                    res = "❌ Տվյալներ չհաջողվեց գտնել։"
        except Exception as e:
            res = f"⚠️ Սխալ: {e}"

    await message.answer(res, parse_mode="Markdown")
    await state.clear()

# --- 2. USERNAME LOOKUP ---
@dp.message(F.text == "👤 Username (Соцсети)")
async def start_username(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր օգտանունը (username) սոցցանցերում փնտրելու համար:")
    await state.set_state(OsintStates.waiting_for_username)

@dp.message(OsintStates.waiting_for_username)
async def process_username(message: types.Message, state: FSMContext):
    username = message.text.strip().replace("@", "")
    decrease_limit(message.from_user.id)
    
    platforms = {
        "Telegram": f"https://t.me/{username}",
        "GitHub": f"https://github.com/{username}",
        "Instagram": f"https://instagram.com/{username}",
        "TikTok": f"https://tiktok.com/@{username}",
        "Twitter/X": f"https://twitter.com/{username}",
        "Pinterest": f"https://pinterest.com/{username}"
    }
    
    result = f"👤 **Username-ի որոնում (`{username}`):**\n\n"
    async with aiohttp.ClientSession() as session:
        for name, link in platforms.items():
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                async with session.get(link, headers=headers, timeout=4) as resp:
                    if resp.status == 200:
                        result += f"🟢 [{name}]({link}) — **Գտնվել է**\n"
                    else:
                        result += f"🔴 {name} — Չկա\n"
            except:
                result += f"⚪ {name} — Ստուգման սխալ\n"

    await message.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await state.clear()

# --- 3. SSL CERTIFICATE ---
@dp.message(F.text == "🔒 SSL Сертификат")
async def start_ssl(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր դոմեյնը SSL ստուգելու համար (օրինակ՝ `google.com`):")
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
                
                info = (
                    f"🔒 **SSL Սերտիֆիկատ `{domain}`:**\n\n"
                    f"🏢 Թողարկող: `{issuer.get('organizationName', 'Անհայտ')}`\n"
                    f"👤 Սեփականատեր (CN): `{subject.get('commonName', 'Անհայտ')}`\n"
                    f"📅 Գործում է սկսած՝ `{cert.get('notBefore')}`\n"
                    f"⏳ Վավեր է մինչև՝ `{cert.get('notAfter')}`"
                )
    except Exception as e:
        info = f"⚠️ Սխալ: {e}"

    await message.answer(info, parse_mode="Markdown")
    await state.clear()

# --- 4. FUZZING ---
@dp.message(F.text == "📂 Fuzzing Директорий")
async def start_fuzz(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր կայքի URL-ը (օրինակ՝ `https://example.com`):")
    await state.set_state(OsintStates.waiting_for_fuzz)

@dp.message(OsintStates.waiting_for_fuzz)
async def process_fuzz(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        url = "https://" + url
    decrease_limit(message.from_user.id)
    
    common_paths = ["robots.txt", "sitemap.xml", "admin", "login", "api", "config.json", ".env", "backup.zip"]
    result = f"📂 **Fuzzing արդյունքներ `{url}`:**\n\n"
    
    async with aiohttp.ClientSession() as session:
        for path in common_paths:
            target_url = f"{url.rstrip('/')}/{path}"
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                async with session.get(target_url, headers=headers, timeout=3, allow_redirects=False) as resp:
                    if resp.status in [200, 301, 302, 403]:
                        result += f"🔹 `/{path}` — Ստատուս: **{resp.status}**\n"
            except:
                pass

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 5. PORT SCANNER ---
@dp.message(F.text == "⚡ Сканер портов")
async def start_ports(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր դոմեյնը կամ IP-ն պորտերը ստուգելու համար:")
    await state.set_state(OsintStates.waiting_for_ports)

@dp.message(OsintStates.waiting_for_ports)
async def process_ports(message: types.Message, state: FSMContext):
    host = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    decrease_limit(message.from_user.id)
    
    ports_to_check = {21: "FTP", 22: "SSH", 80: "HTTP", 443: "HTTPS", 3306: "MySQL", 3389: "RDP"}
    result = f"⚡ **Պորտերի սկանավորում `{host}`:**\n\n"
    
    try:
        ip = socket.gethostbyname(host)
        result += f"📌 IP: `{ip}`\n\n"
        for port, service in ports_to_check.items():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            try:
                if s.connect_ex((ip, port)) == 0:
                    result += f"🟢 `{port}` ({service}): **Բաց է**\n"
                else:
                    result += f"🔴 `{port}` ({service}): Փակ է\n"
            except:
                pass
            finally:
                s.close()
    except Exception as e:
        result += f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 6. MAC VENDOR ---
@dp.message(F.text == "🔍 MAC Vendor Lookup")
async def start_mac(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր MAC հասցեն (օրինակ՝ `00:1A:2B:3C:4D:5E`):")
    await state.set_state(OsintStates.waiting_for_mac)

@dp.message(OsintStates.waiting_for_mac)
async def process_mac(message: types.Message, state: FSMContext):
    mac = message.text.strip()
    decrease_limit(message.from_user.id)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.macvendors.com/{mac}", timeout=5) as resp:
                if resp.status == 200:
                    vendor = await resp.text()
                    result = f"🔍 MAC: `{mac}`\n🏢 Արտադրող: `{vendor}`"
                else:
                    result = "❌ Արտադրողը չի գտնվել։"
        except Exception as e:
            result = f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 7. PDF METADATA ---
@dp.message(F.text == "📄 Анализ PDF")
async def start_doc(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Ուղարկիր PDF ֆայլը (document ֆորմատով) մետատվյալները կարդալու համար:")
    await state.set_state(OsintStates.waiting_for_doc)

@dp.message(OsintStates.waiting_for_doc, F.document)
async def process_doc(message: types.Message, state: FSMContext):
    decrease_limit(message.from_user.id)
    doc = message.document
    
    if not doc.file_name.lower().endswith('.pdf'):
        await message.answer("❌ Խնդրում եմ ուղարկել պահանջվող `.pdf` ֆայլը:")
        await state.clear()
        return
        
    file_info = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file_info.file_path)
    
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(file_bytes.read()))
        meta = reader.metadata
        
        result = f"📄 **PDF Մետատվյալներ (`{doc.file_name}`):**\n\n"
        if meta:
            result += f"🔹 Հեղինակ: `{meta.author}`\n"
            result += f"🔹 Ստեղծող: `{meta.creator}`\n"
            result += f"🔹 Ծրագիր: `{meta.producer}`\n"
            result += f"🔹 Էջերի քանակ: `{len(reader.pages)}`\n"
        else:
            result += "❌ Մետատվյալներ չկան:"
        await message.answer(result, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"⚠️ Սխալ: {e}")
    await state.clear()

# --- 8. EXIF PHOTO ---
@dp.message(F.text == "🖼️ EXIF Фото")
async def start_photo(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Ուղարկիր նկարը (photo ֆորմատով), որպեսզի ստուգեմ նրա EXIF մետատվյալները:")
    await state.set_state(OsintStates.waiting_for_photo)

@dp.message(OsintStates.waiting_for_photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    decrease_limit(message.from_user.id)
    photo = message.photo[-1]
    
    file_info = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file_info.file_path)
    
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        import io
        
        image = Image.open(io.BytesIO(file_bytes.read()))
        exifdata = image.getexif()
        
        result = "🖼️ **Նկարի EXIF Մետատվյալներ:**\n\n"
        if exifdata:
            found = False
            for tag_id, value in exifdata.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ['Make', 'Model', 'DateTime', 'ExposureTime', 'FNumber', 'ISOSpeedRatings']:
                    result += f"🔹 {tag}: `{value}`\n"
                    found = True
            if not found:
                result += "❌ Հիմնական EXIF տվյալներ չեն հայտնաբերվել (հնարավոր է սոցցանցերն են հեռացրել):"
        else:
            result += "❌ EXIF տվյալներ բացակայում են:"
            
        await message.answer(result, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"⚠️ Սխալ: {e}")
    await state.clear()

# --- 9. HTTP HEADERS ---
@dp.message(F.text == "🛡️ HTTP Headers")
async def start_headers(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր կայքի URL-ը HTTP գլխագրերը (Headers) ստուգելու համար:")
    await state.set_state(OsintStates.waiting_for_headers)

@dp.message(OsintStates.waiting_for_headers)
async def process_headers(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        url = "https://" + url
    decrease_limit(message.from_user.id)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                headers = resp.headers
                result = f"🛡️ **HTTP Headers (`{url}`):**\n\n"
                result += f"🔹 **Server:** `{headers.get('Server', 'Անհայտ')}`\n"
                result += f"🔹 **Content-Type:** `{headers.get('Content-Type', 'Անհայտ')}`\n"
                result += f"🔹 **Strict-Transport-Security:** `{headers.get('Strict-Transport-Security', 'Չկա')}`\n"
                result += f"🔹 **X-Frame-Options:** `{headers.get('X-Frame-Options', 'Չկա')}`\n"
                result += f"🔹 **X-XSS-Protection:** `{headers.get('X-XSS-Protection', 'Չկա')}`\n"
        except Exception as e:
            result = f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 10. EMAIL LEAK CHECK (Public API simulation / HIBP style) ---
@dp.message(F.text == "⚠️ Проверка утечек (Email)")
async def start_leak(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր էլ․ հասցեն (Email) արտահոսքերի բազաներում ստուգելու համար:")
    await state.set_state(OsintStates.waiting_for_leak)

@dp.message(OsintStates.waiting_for_leak)
async def process_leak(message: types.Message, state: FSMContext):
    email = message.text.strip()
    decrease_limit(message.from_user.id)
    
    # Օգտագործում ենք ազատ հանրային API (leaks lookup) կամ պարզ ստուգում
    url = f"https://api.xposedornot.com/v1/check-email/{email}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    breaches = data.get("Breaches", [])
                    if breaches:
                        result = f"⚠️ **Զգուշացում! Email `{email}` գտնվել է արտահոսքերում:**\n\n"
                        result += f"🔹 Հայտնաբերված բազաներ: {', '.join(breaches[:5])}"
                    else:
                        result = f"✅ **Email `{email}` տվյալների արտահոսքերում չի հայտնաբերվել:**"
                else:
                    result = f"✅ **Տվյալներ չեն գտնվել կամ հասցեն մաքուր է:**"
        except Exception as e:
            result = f"⚠️ Սխալ կամ տվյալներ չկան: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 11. DNS LOOKUP ---
@dp.message(F.text == "🔤 DNS Записи")
async def start_dns(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր դոմեյնը DNS գրառումները ստուգելու համար (օրինակ՝ `google.com`):")
    await state.set_state(OsintStates.waiting_for_dns)

@dp.message(OsintStates.waiting_for_dns)
async def process_dns(message: types.Message, state: FSMContext):
    domain = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    decrease_limit(message.from_user.id)
    
    result = f"🔤 **DNS Գրառումներ `{domain}`:**\n\n"
    try:
        # A record (IP)
        ip = socket.gethostbyname(domain)
        result += f"🔹 **A Record (IP):** `{ip}`\n"
        
        # Օգտագործում ենք Cloudflare DNS over HTTPS API-ն լրիվ DNS-ների համար
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://cloudflare-dns.com/dns-query?name={domain}&type=TXT", headers={"accept": "application/dns-json"}) as resp:
                data = await resp.json()
                answer = data.get("Answer", [])
                txt_records = [ans.get("data") for ans in answer if ans.get("type") == 16]
                if txt_records:
                    result += f"🔹 **TXT Records:**\n"
                    for txt in txt_records[:3]:
                        result += f"  `{txt}`\n"
    except Exception as e:
        result += f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

# --- 12. WAYBACK MACHINE ARCHIVE CHECK ---
@dp.message(F.text == "🏛️ Wayback Archive")
async def start_wayback(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր դոմեյնը արխիվային պատմությունը ստուգելու համար:")
    await state.set_state(OsintStates.waiting_for_wayback)

@dp.message(OsintStates.waiting_for_wayback)
async def process_wayback(message: types.Message, state: FSMContext):
    domain = message.text.strip().replace("https://", "").replace("http://", "").split("/")[0]
    decrease_limit(message.from_user.id)
    
    wb_url = f"http://archive.org/wayback/available?url={domain}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(wb_url, timeout=5) as resp:
                data = await resp.json()
                archived_snapshot = data.get("archived_snapshots", {}).get("closest", {})
                if archived_snapshot.get("available"):
                    snapshot_url = archived_snapshot.get("url")
                    snapshot_date = archived_snapshot.get("timestamp")
                    result = (
                        f"🏛️ **Wayback Machine Արխիվ (`{domain}`):**\n\n"
                        f"🟢 Կայքը գտնվել է արխիվում!\n"
                        f"📅 Մոտակա պատճենի ամսաթիվը: `{snapshot_date}`\n"
                        f"🔗 [Առցանց դիտել հղումով]({snapshot_url})"
                    )
                else:
                    result = f"❌ Արխիվային տվյալներ `{domain}`-ի համար չեն գտնվել:"
        except Exception as e:
            result = f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await state.clear()

# --- 13. PHONE CARRIER LOOKUP ---
@dp.message(F.text == "📱 Проверка телефона")
async def start_phone(message: types.Message, state: FSMContext):
    if get_user(message.from_user.id) <= 0:
        await message.answer("❌ Լիմիտն սպառվել է։")
        return
    await message.answer("Մուտքագրիր հեռախոսահամարը միջազգային ձևաչափով (օրինակ՝ `+37491XXXXXX`):")
    await state.set_state(OsintStates.waiting_for_phone)

@dp.message(OsintStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace("+", "")
    decrease_limit(message.from_user.id)
    
    # Օգտագործում ենք բաց հանրային համարների ստուգման ապի
    url = f"https://htmlweb.ru/json/geo/phone?json&tel={phone}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                data = await resp.json()
                if data.get("country"):
                    country = data.get("country", {}).get("name", "Անհայտ")
                    operator = data.get("operator", {}).get("name", "Անհայտ")
                    region = data.get("region", {}).get("name", "Անհայտ")
                    result = (
                        f"📱 **Հեռախոսահամարի տեղեկություն (`+{phone}`):**\n\n"
                        f"🌍 Երկիր: `{country}`\n"
                        f"📍 Մարզ / Քաղաք: `{region}`\n"
                        f"📡 Օպերատոր: `{operator}`"
                    )
                else:
                    result = "❌ Համարի մասին տվյալներ չեն հաջողվել գտնել։"
        except Exception as e:
                    result = f"⚠️ Սխալ: {e}"

    await message.answer(result, parse_mode="Markdown")
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
