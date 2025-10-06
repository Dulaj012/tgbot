from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from openai import AsyncOpenAI
import logging, os
import httpx
from datetime import datetime, timezone
from io import BytesIO
import json
import asyncio

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()  # This loads .env file
    load_dotenv('config.env')  # Also try config.env as fallback
except ImportError:
    # If python-dotenv is not installed, try to load manually
    try:
        with open('.env', 'r') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    os.environ[key] = value
    except FileNotFoundError:
        try:
            with open('config.env', 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith('#'):
                        key, value = line.strip().split('=', 1)
                        os.environ[key] = value
        except FileNotFoundError:
            pass  # No .env file found

# API credentials (use environment variables in production)
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if required environment variables are set
if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, GROQ_API_KEY]):
    logger.error("Missing required environment variables!")
    logger.error("Please set: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, GROQ_API_KEY")
    logger.error("You can set them in a .env file or as environment variables")
    exit(1)

# Setup Groq client
groq_client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# Conversation history
user_histories = {}

def get_user_history(user_id: int):
    return user_histories.get(user_id, [])

def append_to_history(user_id: int, role: str, content: str):
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": role, "content": content})
    # Keep only last 10 messages
    if len(user_histories[user_id]) > 10:
        user_histories[user_id] = user_histories[user_id][-10:]

async def handle_sentiment(event):
    """Handle /sentiment command"""
    try:
        print("📊 Handling /sentiment command")
        # Send typing action
        event.client.action(event.chat_id, 'typing')
        
        # Fetch Fear & Greed Index
        url = "https://api.alternative.me/fng/?limit=1"
        print(f"🌐 Fetching from: {url}")
        
        async with httpx.AsyncClient(timeout=20) as client_http:
            resp = await client_http.get(url)
            print(f"📡 Response status: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            print(f"📊 API data: {data}")
        
        if "data" not in data or not data["data"]:
            print("❌ No data in API response")
            await event.reply("No sentiment data available right now.")
            return
            
        item = data["data"][0]
        value = item["value"]
        classification = item["value_classification"]
        timestamp = item["timestamp"]
        
        print(f"📈 Value: {value}, Classification: {classification}")
        
        # Format timestamp
        ts = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        when = ts.strftime("%Y-%m-%d %H:%M UTC")
        
        msg = f"Fear & Greed Index: {value} ({classification})\nAs of: {when}"
        
        # Use the official alternative.me gauge image
        try:
            print("🎨 Fetching official gauge image from alternative.me")
            
            # Get the official PNG image from alternative.me
            image_url = "https://alternative.me/crypto/fear-and-greed-index.png"
            # Add cache buster to ensure fresh image
            cache_buster = int(datetime.now(tz=timezone.utc).timestamp())
            
            async with httpx.AsyncClient(timeout=20) as client_http:
                img_resp = await client_http.get(
                    image_url, 
                    params={"t": cache_buster},
                    headers={"Cache-Control": "no-cache"}
                )
                print(f"📡 Image response status: {img_resp.status_code}")
                img_resp.raise_for_status()
                img_bytes = img_resp.content
                print(f"📊 Image size: {len(img_bytes)} bytes")
            
            bio = BytesIO(img_bytes)
            bio.name = "fear_and_greed_index.png"
            bio.seek(0)
            print("📤 Sending official gauge image with caption...")
            # Send image with text as caption in a single message
            await event.reply(msg, file=bio)
            print("✅ Official gauge image with caption sent successfully!")
            
        except Exception as img_err:
            print(f"❌ Image fetch failed: {type(img_err).__name__}: {img_err}")
            import traceback
            traceback.print_exc()
            print("📤 Sending text only...")
            await event.reply(msg)
            
    except Exception as e:
        print(f"❌ Sentiment error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        await event.reply(f"Failed to fetch sentiment: {type(e).__name__}: {str(e)}")

async def handle_chat(event):
    """Handle regular chat messages"""
    try:
        user_message = event.message.text or ""
        print(f"💬 Processing message: {user_message}")
        
        # Send typing action
        event.client.action(event.chat_id, 'typing')
        
        user_id = event.sender_id
        history = get_user_history(user_id)
        messages = [{"role": "system", "content": "You are a helpful Telegram assistant. Keep replies concise and friendly."}] + history + [{"role": "user", "content": user_message}]
        
        completion = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.7,
            max_tokens=512,
        )
        
        ai_reply = completion.choices[0].message.content
        await event.reply(ai_reply)
        
        append_to_history(user_id, "user", user_message)
        append_to_history(user_id, "assistant", ai_reply)
        
    except Exception as e:
        print(f"Chat error: {e}")
        await event.reply(f"Error: {type(e).__name__}: {str(e)}")

# Create client with unique session name
client = TelegramClient('userbot_simple_final', TELEGRAM_API_ID, TELEGRAM_API_HASH)

@client.on(events.NewMessage(incoming=True))
async def message_handler(event):
    print(f"🔔 NEW MESSAGE RECEIVED!")
    print(f"   From: {event.sender_id}")
    print(f"   Text: {event.message.text}")
    print(f"   Chat ID: {event.chat_id}")
    
    # Skip messages from yourself
    me = await client.get_me()
    print(f"   My ID: {me.id}")
    if event.sender_id == me.id:
        print("⏭️ Skipping self message")
        return
        
    # Skip bot messages
    if isinstance(event.sender, User) and event.sender.bot:
        print("⏭️ Skipping bot message")
        return
        
    # Skip forwarded messages
    if event.message.fwd_from:
        print("⏭️ Skipping forwarded message")
        return
        
    message_text = event.message.text or ""
    print(f"📝 Processing message: '{message_text}'")
    
    # Handle commands FIRST (work in both DMs and groups)
    if message_text.startswith('/sentiment') or message_text.startswith('/fear'):
        print("📊 Command detected: /sentiment - processing in any chat type")
        await handle_sentiment(event)
        return
    elif message_text.startswith('/reset'):
        print("🔄 Command detected: /reset")
        user_histories[event.sender_id] = []
        await event.reply("Conversation reset!")
        return
    elif message_text.startswith('/start'):
        print("🚀 Command detected: /start")
        user_histories[event.sender_id] = []
        await event.reply("Hi! I'm your assistant. Ask me anything.")
        return
    
    # Only reply to regular messages in DMs or when mentioned in groups
    chat = await event.get_chat()
    should_reply = False
    
    if isinstance(chat, User):  # DM
        print("💬 DM - will reply")
        should_reply = True
    elif isinstance(chat, (Chat, Channel)):  # Group/Channel
        if f"@{me.username}" in message_text or me.first_name in message_text:
            print("📢 Mentioned in group - will reply")
            should_reply = True
        else:
            print("⏭️ Not mentioned in group - skipping")
    
    if should_reply:
        await handle_chat(event)

async def main():
    """Main function to run the userbot"""
    print("🤖 Starting userbot...")
    try:
        await client.start(phone=TELEGRAM_PHONE)
        print("✅ Userbot started!")
        print("Commands: /start, /reset, /sentiment, /fear")
        print("Commands work in DMs and groups")
        print("Regular messages: DMs only, or when mentioned in groups")
        print("Press Ctrl+C to stop")
        
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n👋 Userbot stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
