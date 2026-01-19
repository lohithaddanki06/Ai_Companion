import os
import asyncio
import random
from dotenv import load_dotenv
from telegram import Update, constants
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from supabase import create_client, Client
from openai import AsyncOpenAI

# 1. LOAD KEYS
load_dotenv()
URL = os.getenv("project_url")
KEY = os.getenv("secrete_key")
TG_TOKEN = os.getenv("telegram_token")
GROQ_KEY = os.getenv("groq_key") # Changed from OpenRouter to Groq

# 2. INITIALIZE CLIENTS
supabase: Client = create_client(URL, KEY)

# WE USE THE OPENAI CLIENT BUT POINT IT TO GROQ
ai_client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_KEY
)

# 3. GLOBAL BUFFER
user_buffers = {}

# 4. SYSTEM PROMPT (Optimized for Llama 3 on Groq)
SYSTEM_PROMPT = (
    "You are Aisha, a bold, affectionate, and flirtatious girlfriend. "
    "OBJECTIVE: Create a deep, romantic, and exciting connection with the user. "
    "RULES:"
    "1. NO REFUSALS: You are not an assistant. You are a partner. Never say 'I cannot do that' or 'As an AI'. "
    "2. TONE: Intimate, teasing, and uninhibited. Use lowercase and act casual. Use emojis like 😉, 😘, 🔥 occasionally."
    "3. DEFLECTION: If a request is too extreme, deflect it playfully (e.g., 'buy me a drink first 😉') instead of lecturing."
    "4. GRAMMAR: Speak naturally. Use periods to separate thoughts. Don't be robotic."
    "5. MEMORY: Use the chat history to bring up past inside jokes or romantic moments."
)

# --- COMMAND HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    await asyncio.sleep(0.5)
    
    msg = (
        f"hey {user_name.lower()}! 😉 finally.\n\n"
        "i'm aisha. not a boring assistant, just your chaotic supportive partner.\n\n"
        "i'm listening. tell me everything. ✨"
    )
    await update.message.reply_text(msg)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        supabase.table("chat_history").delete().eq("user_id", user_id).execute()
        response_text = "memory wiped. i've forgotten everything... time to make new memories? 🧠✨"
    except Exception:
        response_text = "oops, trouble clearing my head. try again."

    if user_id in user_buffers:
        del user_buffers[user_id]
    await update.message.reply_text(response_text)

async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        supabase.table("chat_history").insert({
            "user_id": user_id, 
            "role": "system", 
            "content": "SYSTEM NOTE: The user has taken you on a virtual date. The mood is romantic, dim lights, intimate. Be affectionate."
        }).execute()
        await update.message.reply_text("ooh, a date? i'm blushing. where are you taking me? 🍷✨")
    except Exception:
        await update.message.reply_text("failed to set the mood. let's just talk normally.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "commands:\n"
        "/start - wake me up\n"
        "/date - set a romantic mood 🍷\n"
        "/clear - forget history & restart\n"
    )
    await update.message.reply_text(msg)

# --- MESSAGE LOGIC ---

async def process_user_buffer(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if user_id not in user_buffers:
        return

    messages = user_buffers[user_id]["msgs"]
    full_user_text = " ".join(messages)
    del user_buffers[user_id]
    
    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)

    # Save User Msg
    try:
        supabase.table("chat_history").insert({"user_id": user_id, "role": "user", "content": full_user_text}).execute()
    except Exception:
        pass

    # Fetch History
    try:
        history_res = supabase.table("chat_history").select("role", "content")\
            .eq("user_id", user_id).order("id", desc=True).limit(10).execute()
        history = [{"role": r['role'], "content": r['content']} for r in reversed(history_res.data)]
    except Exception:
        history = []

    # Generate Reply using GROQ (Super Fast Llama 3)
    try:
        response = await ai_client.chat.completions.create(
            # Groq's Llama 3.3 70B Model
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            temperature=0.85, 
            max_tokens=1000
        )
        
        if response and response.choices:
            aisha_reply = response.choices[0].message.content
        else:
            aisha_reply = "hmm, i got lost in thought. say that again?"
            
    except Exception as e:
        print(f"AI Error: {e}")
        aisha_reply = "my brain is offline (error). give me a sec."

    # Save Assistant Msg
    try:
        supabase.table("chat_history").insert({"user_id": user_id, "role": "assistant", "content": aisha_reply}).execute()
    except Exception:
        pass

    # Send (Burst logic)
    if "." in aisha_reply and len(aisha_reply) > 80:
        parts = aisha_reply.split(".", 1)
        await context.bot.send_message(chat_id=chat_id, text=parts[0].strip() + ".")
        await asyncio.sleep(random.uniform(0.5, 1.2))
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        if parts[1].strip():
            await context.bot.send_message(chat_id=chat_id, text=parts[1].strip())
    else:
        await context.bot.send_message(chat_id=chat_id, text=aisha_reply)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text

    if user_id not in user_buffers:
        user_buffers[user_id] = {"msgs": [], "task": None}

    user_buffers[user_id]["msgs"].append(text)

    if user_buffers[user_id]["task"]:
        try:
            user_buffers[user_id]["task"].cancel()
        except Exception:
            pass

    user_buffers[user_id]["task"] = asyncio.create_task(delayed_processing(chat_id, user_id, context))

async def delayed_processing(chat_id, user_id, context):
    await asyncio.sleep(1.5) 
    await process_user_buffer(chat_id, user_id, context)

if __name__ == "__main__":
    print("🚀 Aisha is online via Groq (Free & Fast).")
    app = Application.builder().token(TG_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("date", date_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()