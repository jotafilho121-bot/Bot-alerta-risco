import os
import sqlite3
import logging
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import speech_recognition as sr
from pydub import AudioSegment

# --- 1. MINI SERVIDOR WEB (Manter Render Ativo de Graça) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot de Mapeamento de Risco (Voz Ativa) está Online!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 2. CONFIGURAÇÕES E BANCO DE DADOS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = "8590309661:AAGZ4YVRfdBFXuQ4qUuWPvGloTMXe_DLRcc"

def iniciar_banco():
    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bairro TEXT NOT NULL,
            rua TEXT NOT NULL,
            risco INTEGER NOT NULL,
            detalhes TEXT
        )
    ''')
    conn.commit()
    conn.close()

# --- 3. COMANDOS POR TEXTO ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚨 *Bot de Mapeamento de Risco Ativo*\n\n"
        "🎙️ *NOVO:* Agora você pode me enviar um **ÁUDIO DE VOZ** para cadastrar!\n"
        "Exemplo de áudio: *'Cadastrar Miguel Couto - Rua Dagmar - Risco 3 - Assalto à noite'*\n\n"
        "Comandos por texto:\n"
        "🔹 `/consultar [nome da rua ou bairro]`\n"
        "🔹 `/cadastrar [Bairro] - [Rua] - [Risco 1 a 3] - [Detalhes]`\n"
        "🔹 `/listar` - Exibe os últimos cadastros"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Use: `/consultar [Nome da Rua ou Bairro]`", parse_mode="Markdown")
        return

    termo = " ".join(context.args)
    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT bairro, rua, risco, detalhes FROM alertas WHERE rua LIKE ? OR bairro LIKE ?",
        (f"%{termo}%", f"%{termo}%")
    )
    resultados = cursor.fetchall()
    conn.close()

    if not resultados:
        await update.message.reply_text(f"✅ Nenhum alerta cadastrado para: *{termo}*", parse_mode="Markdown")
        return

    resposta = f"🔍 *Resultados para '{termo}':*\n\n"
    for item in resultados:
        bairro, rua, risco, detalhes = item
        alerta_emoji = "🟡" if risco == 1 else "🟧" if risco == 2 else "🔴"
        resposta += (
            f"{alerta_emoji} *Risco Nível {risco}*\n"
            f"📍 *Bairro:* {bairro} | *Rua:* {rua}\n"
            f"📝 *Detalhes:* {detalhes}\n"
            "------------------------\n"
        )

    await update.message.reply_text(resposta, parse_mode="Markdown")

async def cadastrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    processar_e_salvar(texto, update)

def processar_e_salvar(texto_bruto, update_or_msg):
    partes = [p.strip() for p in texto_bruto.split("-")]

    if len(partes) < 4:
        return False, "⚠️ Formato incorreto!\nUse: `Bairro - Rua - Risco - Detalhes`"

    bairro, rua, risco_str, detalhes = partes[0], partes[1], partes[2], partes[3]

    try:
        risco = int(risco_str)
    except ValueError:
        return False, "⚠️ O nível de risco precisa ser um número (1, 2 ou 3)."

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO alertas (bairro, rua, risco, detalhes) VALUES (?, ?, ?, ?)",
        (bairro, rua, risco, detalhes)
    )
    conn.commit()
    conn.close()

    return True, f"✅ *Alerta salvo com sucesso!*\n📍 {rua} ({bairro}) - Risco {risco}\n📝 {detalhes}"

async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute("SELECT bairro, rua, risco, detalhes FROM alertas ORDER BY id DESC LIMIT 10")
    resultados = cursor.fetchall()
    conn.close()

    if not resultados:
        await update.message.reply_text("Nenhum registro no banco de dados ainda.")
        return

    resposta = "📋 *Últimos 10 alertas cadastrados:*\n\n"
    for item in resultados:
        bairro, rua, risco, detalhes = item
        resposta += f"• *{rua}* ({bairro}) - Risco {risco}: {detalhes}\n"

    await update.message.reply_text(resposta, parse_mode="Markdown")

# --- 4. PROCESSADOR DE MENSAGEM DE VOZ ---
async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_espera = await update.message.reply_text("🎧 *Processando áudio de voz... Aguarde!*", parse_mode="Markdown")
    
    try:
        # Baixa o arquivo de áudio do Telegram
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        oga_path = "voice.oga"
        wav_path = "voice.wav"
        
        await voice_file.download_to_drive(oga_path)

        # Converte OGA para WAV
        sound = AudioSegment.from_file(oga_path)
        sound.export(wav_path, format="wav")

        # Reconhecimento de Fala
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            texto_transcrito = recognizer.recognize_google(audio_data, language="pt-BR")

        # Limpa arquivos temporários
        if os.path.exists(oga_path): os.remove(oga_path)
        if os.path.exists(wav_path): os.remove(wav_path)

        # Envia transcrição para o usuário
        await update.message.reply_text(f"🗣️ *Você falou:* \"_{texto_transcrito}_\"", parse_mode="Markdown")

        # Tenta cadastrar se contiver o padrão com separadores
        if "-" in texto_transcrito:
            sucesso, resposta = processar_e_salvar(texto_transcrito, update)
            await update.message.reply_text(resposta, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "💡 Para cadastrar por voz, fale com pausas ou diga a palavra hífen/traço entre as informações.\n"
                "Exemplo: *Posse traço Rua das Flores traço 2 traço Cuidado com o sinal*",
                parse_mode="Markdown"
            )

    except Exception as e:
        await update.message.reply_text(f"❌ Não consegui entender o áudio com clareza. Tente falar novamente em um ambiente mais silencioso.")

# --- 5. EXECUÇÃO DO BOT ---
if __name__ == '__main__':
    keep_alive()
    iniciar_banco()
    
    app_bot = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("consultar", consultar))
    app_bot.add_handler(CommandHandler("cadastrar", cadastrar))
    app_bot.add_handler(CommandHandler("listar", listar))
    
    # Escuta mensagens de voz
    app_bot.add_handler(MessageHandler(filters.VOICE, processar_audio))

    print("Bot com suporte à voz rodando...")
    app_bot.run_polling()
