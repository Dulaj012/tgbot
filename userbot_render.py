#!/usr/bin/env python3
"""
Render.com optimized userbot
This version includes a simple HTTP server to satisfy Render's port requirements
"""

from telethon import TelegramClient, events
from telethon.tl.types import User, Chat, Channel
from openai import AsyncOpenAI
import logging, os
import httpx
from datetime import datetime, timezone
from io import BytesIO
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv('config.env')
except ImportError:
    pass

# API credentials
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Check credentials
if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, GROQ_API_KEY]):
    print("❌ Missing environment variables!")
    exit(1)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Setup clients
groq_client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# Create client
client = TelegramClient('userbot_render', TELEGRAM_API_ID, TELEGRAM_API_HASH)

# Conversation history
user_histories = {}

def get_user_history(user_id: int):
    return user_histories.get(user_id, [])

def append_to_history(user_id: int, role: str, content: str):
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": role, "content": content})
    if len(user_histories[user_id]) > 10:
        user_histories[user_id] = user_histories[user_id][-10:]

async def handle_sentiment(event):
    """Handle /sentiment command"""
    try:
        print("📊 Fetching Fear & Greed Index...")
        
        async with httpx.AsyncClient(timeout=20) as http_client:
            response = await http_client.get("https://api.alternative.me/fng/?limit=1")
            data = response.json()
            
        if data and "data" in data and len(data["data"]) > 0:
            fng_data = data["data"][0]
            value = int(fng_data["value"])
            classification = fng_data["value_classification"]
            timestamp = fng_data["timestamp"]
            
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            formatted_time = dt.strftime("%Y-%m-%d %H:%M UTC")
            
            msg = f"📊 **Fear & Greed Index: {value} ({classification})**\n"
            msg += f"🕐 As of: {formatted_time}\n\n"
            
            if classification == "Extreme Fear":
                msg += "😱 Market shows extreme fear. Could be a buying opportunity for brave investors!"
            elif classification == "Fear":
                msg += "😰 Market sentiment is fearful. Consider cautious investing."
            elif classification == "Neutral":
                msg += "😐 Market sentiment is neutral. Mixed signals from investors."
            elif classification == "Greed":
                msg += "😏 Market shows greed. Be cautious of potential overvaluation."
            elif classification == "Extreme Greed":
                msg += "🚨 Extreme greed detected! Consider taking profits or being very cautious."
            
            # Get the official alternative.me gauge image
            try:
                print("🎨 Fetching official gauge image...")
                image_url = "https://alternative.me/crypto/fear-and-greed-index.png"
                cache_buster = int(datetime.now(tz=timezone.utc).timestamp())
                
                async with httpx.AsyncClient(timeout=20) as http_client:
                    img_resp = await http_client.get(
                        image_url, 
                        params={"t": cache_buster},
                        headers={"Cache-Control": "no-cache"}
                    )
                    img_resp.raise_for_status()
                    img_bytes = img_resp.content
                
                bio = BytesIO(img_bytes)
                bio.name = "fear_and_greed_index.png"
                bio.seek(0)
                
                print("📤 Sending image with caption...")
                await event.reply(msg, file=bio)
                print("✅ Sent successfully!")
                
            except Exception as img_err:
                print(f"❌ Image error: {img_err}")
                await event.reply(msg)
        else:
            await event.reply("❌ Failed to fetch sentiment data. Please try again later.")
            
    except Exception as e:
        print(f"❌ Sentiment error: {e}")
        await event.reply("❌ Error fetching sentiment data. Please try again later.")

async def handle_chat(event):
    """Handle regular chat messages"""
    try:
        user_message = event.message.text
        if not user_message:
            return
            
        print(f"💬 Processing chat: {user_message[:50]}...")
        
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
        append_to_history(user_id, "user", user_message)
        append_to_history(user_id, "assistant", ai_reply)
        
        await event.reply(ai_reply)
        print(f"✅ Replied: {ai_reply[:50]}...")
        
    except Exception as e:
        print(f"Chat error: {e}")
        await event.reply(f"Error: {type(e).__name__}: {str(e)}")

@client.on(events.NewMessage(incoming=True))
async def message_handler(event):
    print(f"🔔 NEW MESSAGE: {event.message.text}")
    
    # Skip self messages
    me = await client.get_me()
    if event.sender_id == me.id:
        return
        
    # Skip bot messages
    if isinstance(event.sender, User) and event.sender.bot:
        return
        
    # Skip forwarded messages
    if event.message.fwd_from:
        return
        
    message_text = event.message.text or ""
    
    # Handle commands
    if message_text.startswith('/sentiment') or message_text.startswith('/fear'):
        await handle_sentiment(event)
        return
    elif message_text.startswith('/reset'):
        user_histories[event.sender_id] = []
        await event.reply("Conversation reset!")
        return
    elif message_text.startswith('/start'):
        user_histories[event.sender_id] = []
        await event.reply("Hi! I'm your assistant. Ask me anything.")
        return
    
    # Only reply to DMs or when mentioned in groups
    chat = await event.get_chat()
    should_reply = False
    
    if isinstance(chat, User):  # DM
        should_reply = True
    elif isinstance(chat, (Chat, Channel)):  # Group/Channel
        if f"@{me.username}" in message_text or me.first_name in message_text:
            should_reply = True
    
    if should_reply:
        await handle_chat(event)

# Simple HTTP server for Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<h1>Telegram Userbot is running!</h1><p>Status: Active</p>')
    
    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

def start_http_server():
    """Start HTTP server for Render port detection"""
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"🌐 HTTP server started on port {port}")
    server.serve_forever()

async def main():
    """Main function"""
    print("🤖 Starting userbot on Render...")
    
    # Start HTTP server in background thread
    http_thread = threading.Thread(target=start_http_server)
    http_thread.daemon = True
    http_thread.start()
    
    try:
        await client.start(phone=TELEGRAM_PHONE)
        print("✅ Userbot started successfully!")
        print("📱 Send me a DM or mention me in a group to test!")
        print("🔧 Commands: /start, /reset, /sentiment, /fear")
        
        await client.run_until_disconnected()
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Userbot stopped")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
