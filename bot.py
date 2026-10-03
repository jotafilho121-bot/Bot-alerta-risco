import sqlite3
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Configuração de logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = "8590309661:AAGZ4YVRfdBFXuQ4qUuWPvGloTMXe_DLRcc"

# --- BANCO DE DADOS (SQLite) ---
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

# --- COMANDOS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚨 *Bot de Mapeamento de Risco Activo*\n\n"
        "Comandos disponíveis:\n"
        "🔹 `/consultar [nome da rua ou bairro]` - Busca alertas salvos\n"
        "🔹 `/cadastrar [Bairro] - [Rua] - [Risco 1 a 3] - [Detalhes]` - Adiciona nova rua\n"
        "🔹 `/listar` - Exibe os últimos 10 cadastros\n\n"
        "Exemplo de cadastro:\n"
        "`/cadastrar Miguel Couto - Rua Dagmar - 3 - Assalto a moto à noite`"
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
    partes = [p.strip() for p in texto.split("-")]

    if len(partes) < 4:
        await update.message.reply_text(
            "⚠️ Formato incorreto!\nUse: `/cadastrar [Bairro] - [Rua] - [Risco 1-3] - [Detalhes]`\n\n"
            "Exemplo:\n`/cadastrar Posse - Rua das Flores - 2 - Evitar parar no sinal à noite`",
            parse_mode="Markdown"
        )
        return

    bairro, rua, risco_str, detalhes = partes[0], partes[1], partes[2], partes[3]

    try:
        risco = int(risco_str)
    except ValueError:
        await update.message.reply_text("⚠️ O nível de risco precisa ser um número (1, 2 ou 3).")
        return

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO alertas (bairro, rua, risco, detalhes) VALUES (?, ?, ?, ?)",
        (bairro, rua, risco, detalhes)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ *Alerta salvo com sucesso!*\n📍 {rua} ({bairro}) - Risco {risco}", parse_mode="Markdown")

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

# --- EXECUÇÃO ---
if __name__ == '__main__':
    iniciar_banco()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("consultar", consultar))
    app.add_handler(CommandHandler("cadastrar", cadastrar))
    app.add_handler(CommandHandler("listar", listar))

    print("Bot rodando...")
    app.run_polling()
