import asyncio
import logging
import os
import sqlite3
import threading
import time
import requests
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- SERVIDOR FLASK (KEEP-ALIVE) ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Bot de Mapeamento de Risco Ativo!"


@app.route("/ping")
def ping():
  return "pong", 200


# --- LOGS E BANCO DE DADOS ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def iniciar_banco():
  try:
    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT NOT NULL,
                rua TEXT NOT NULL,
                risco INTEGER NOT NULL,
                horario_critico TEXT,
                detalhes TEXT,
                data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS estatisticas_bairros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT UNIQUE NOT NULL,
                risco_base INTEGER NOT NULL,
                resumo_criminal TEXT NOT NULL
            )
        """)

    bairros_base = [
        (
            "Austin",
            3,
            "Incidência alta de furto/roubo de veículos e cargas em vias"
            " secundárias.",
        ),
        (
            "Comendador Soares",
            3,
            "Pontos críticos de abordagem em horários noturnos e cruzamentos.",
        ),
        (
            "Centro",
            2,
            "Alerta para furtos e roubos de celulares/bolsas durante o dia.",
        ),
        (
            "Posse",
            2,
            "Atenção reforçada nas marginais e vias de acesso à Dutra.",
        ),
        (
            "Grama",
            2,
            "Risco moderado a alto em acessos a áreas residenciais internas.",
        ),
        (
            "Rancho Novo",
            1,
            "Risco baixo a moderado; atenção em vias de saída rápida.",
        ),
        (
            "Jardim Iguaçu",
            2,
            "Atenção em horários de pico e circulação em vias secundárias.",
        ),
        (
            "Miguel Burnier",
            2,
            "Atividades de patrulhamento variáveis; cautela em horários calmos.",
        ),
        (
            "Vila de Cava",
            3,
            "Atenção em vias de integração e acessos secundários.",
        ),
        (
            "Kenia",
            2,
            "Risco moderado em acessos próximos a corredores de tráfego.",
        ),
        (
            "Miguel Couto",
            2,
            "Atenção em cruzamentos principais e acessos ao Ambaí / Tinguá.",
        ),
        (
            "Ambaí",
            3,
            "Risco elevado em acessos secundários e travessas sentido"
            " comunidades.",
        ),
    ]

    cursor.executemany(
        """
            INSERT OR IGNORE INTO estatisticas_bairros (bairro, risco_base, resumo_criminal)
            VALUES (?, ?, ?)
        """,
        bairros_base,
    )

    conn.commit()
    conn.close()
    logging.info("Banco de dados inicializado.")
  except Exception as e:
    logging.error(f"Erro no banco: {e}")


# --- GEOLOCALIZAÇÃO ROBUSTA ---
def obter_endereco_gps(lat, lon):
  try:
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    headers = {"User-Agent": "BotSegurancaIguacu/2.0"}
    res = requests.get(url, headers=headers, timeout=5)

    if res.status_code == 200:
      dados = res.json().get("address", {})
      rua = (
          dados.get("road")
          or dados.get("pedestrian")
          or dados.get("footway")
          or "Via Próxima"
      )
      bairro = (
          dados.get("suburb")
          or dados.get("neighbourhood")
          or dados.get("quarter")
          or dados.get("city_district")
          or dados.get("town")
          or "Nova Iguaçu"
      )
      return bairro, rua
  except Exception as e:
    logging.error(f"Erro no geocoding: {e}")
  return "Nova Iguaçu", "Via Próxima"


# --- IA PARA EXTRAÇÃO DE CADASTRO ---
def extrair_dados_cadastro_com_groq(texto_entrada):
  groq_api_key = os.environ.get("GROQ_API_KEY")
  if not groq_api_key:
    return "Grama", "Rua Não Informada", 2, "Recente", texto_entrada

  try:
    client = Groq(api_key=groq_api_key)
    prompt = f"""
        Extraia as informações desta frase de cadastro de segurança de entregador em Nova Iguaçu.
        Frase: "{texto_entrada}"

        Responda ESTRITAMENTE em uma única linha no formato exato:
        BAIRRO | RUA | RISCO (apenas número 1, 2 ou 3) | HORARIO | DETALHES
        """

    resposta = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.1,
    )
    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split("|")]
    if len(partes) >= 5:
      bairro = partes[0]
      rua = partes[1]
      try:
        risco = int("".join(filter(str.isdigit, partes[2])))
      except ValueError:
        risco = 2
      horario = partes[3]
      detalhes = partes[4]
      return bairro, rua, risco, horario, detalhes
  except Exception as e:
    logging.error(f"Erro IA Cadastro: {e}")

  return "Grama", "Via Informada", 2, "Recente", texto_entrada


# --- HANDLERS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      "🚨 *Bot Mapeador de Entregas Ativo*\n\n"
      "📍 *Enviar GPS:* Mande sua localização para consultar a via instantaneamente.\n\n"
      "📝 *Para Cadastrar:* Comece com a palavra *Cadastrar*\n"
      'Exemplo: _"Cadastrar Rua Cajueiro no bairro Grama risco 2 a partir das 17h"_\n\n'
      "🔍 *Para Consultar:* Comece com a palavra *Consultar* ou digite o local.\n"
      'Exemplo: _"Consultar Rua Cajueiro"_'
  )
  await update.message.reply_text(msg, parse_mode="Markdown")


async def receber_localizacao(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  try:
    lat = update.message.location.latitude
    lon = update.message.location.longitude

    bairro, rua = obter_endereco_gps(lat, lon)

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()

    cursor.execute(
        "SELECT risco, horario_critico, detalhes FROM alertas WHERE rua LIKE ?"
        " OR bairro LIKE ? ORDER BY id DESC LIMIT 3",
        (f"%{rua}%", f"%{bairro}%"),
    )
    relatos = cursor.fetchall()

    cursor.execute(
        "SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE"
        " bairro LIKE ?",
        (f"%{bairro}%",),
    )
    estatistica = cursor.fetchone()
    conn.close()

    msg = (
        f"📍 *Localização Identificada via GPS!*\n• *Bairro:* {bairro}\n• *Rua:* {rua}\n\n"
    )

    if relatos:
      msg += "⚠️ *Alertas em Tempo Real:*\n"
      for r in relatos:
        msg += f"• Risco {r[0]} | {r[1]}: {r[2]}\n"
    else:
      msg += "✅ *Nenhum alerta recente relatado nesta via.*\n\n"

    if estatistica:
      msg += (
          f"📊 *Mancha Criminal:* Risco Nível {estatistica[0]} |"
          f" {estatistica[1]}\n"
      )

    await update.message.reply_text(msg, parse_mode="Markdown")
  except Exception as e:
    logging.error(f"Erro no handler de localização: {e}")
    await update.message.reply_text("❌ Erro ao ler localização.")


async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  try:
    if update.message.voice:
      await update.message.reply_text(
          "⚠️ Processamento de áudio temporariamente desativado. Use texto ou GPS."
      )
      return

    texto_entrada = update.message.text.strip()
    texto_lower = texto_entrada.lower()

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()

    # --- 1. SE FOR CADASTRO ---
    if texto_lower.startswith("cadastrar"):
      bairro, rua, risco, horario, detalhes = extrair_dados_cadastro_com_groq(
          texto_entrada
      )

      cursor.execute(
          "INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)"
          " VALUES (?, ?, ?, ?, ?)",
          (bairro, rua, risco, horario, detalhes),
      )
      conn.commit()
      conn.close()

      await update.message.reply_text(
          f"🤖 *Alerta Cadastrado com Sucesso!*\n\n"
          f"📍 *Bairro:* {bairro}\n"
          f"🛣️ *Rua:* {rua}\n"
          f"⚠ *Risco:* Nível {risco}\n"
          f"⏰ *Horário:* {horario}\n"
          f"📝 *Detalhes:* {detalhes}",
          parse_mode="Markdown",
      )

    # --- 2. SE FOR CONSULTA ---
    else:
      termo_busca = (
          texto_lower.replace("consultar", "")
          .replace("pesquisar", "")
          .strip()
      )

      if not termo_busca:
        termo_busca = texto_entrada

      cursor.execute(
          "SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas"
          " WHERE rua LIKE ? OR bairro LIKE ? ORDER BY id DESC LIMIT 5",
          (f"%{termo_busca}%", f"%{termo_busca}%"),
      )
      relatos = cursor.fetchall()

      cursor.execute(
          "SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE"
          " bairro LIKE ? OR resumo_criminal LIKE ?",
          (f"%{termo_busca}%", f"%{termo_busca}%"),
      )
      estatistica = cursor.fetchone()
      conn.close()

      resposta = f"🔍 *Resultado da Consulta para '{termo_busca}':*\n\n"
      if relatos:
        resposta += "🚨 *Alertas Recentes (Entregadores):*\n"
        for item in relatos:
          resposta += (
              f"• *Risco {item[2]}* em {item[1]} ({item[0]})\n  ⏰ {item[3]} |"
              f" {item[4]}\n\n"
          )
      else:
        resposta += "✅ *Nenhum alerta recente cadastrado para este termo.*\n\n"

      if estatistica:
        resposta += (
            f"📊 *Mancha Criminal (Oficial):* Risco Base {estatistica[0]} -"
            f" {estatistica[1]}\n"
        )
      else:
        resposta += "📊 *Mancha Criminal:* Sem ocorrências oficiais registradas para este termo."

      await update.message.reply_text(resposta, parse_mode="Markdown")

  except Exception as e:
    logging.error(f"Erro em processar_mensagem: {e}")
    await update.message.reply_text(
        "❌ Ocorreu um erro ao processar sua mensagem."
    )


def rodar_bot():
  telegram_token = os.environ.get("TELEGRAM_TOKEN")
  if not telegram_token:
    return

  while True:
    try:
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      app_bot = ApplicationBuilder().token(telegram_token).build()
      loop.run_until_complete(
          app_bot.bot.delete_webhook(drop_pending_updates=True)
      )

      app_bot.add_handler(CommandHandler("start", start))
      app_bot.add_handler(
          MessageHandler(filters.LOCATION, receber_localizacao)
      )
      app_bot.add_handler(
          MessageHandler(
              (filters.TEXT & ~filters.COMMAND) | filters.VOICE,
              processar_mensagem,
          )
      )

      app_bot.run_polling(drop_polling_updates=True, stop_signals=None)
    except Exception as e:
      logging.error(f"Erro no polling: {e}")
      time.sleep(5)


iniciar_banco()
t_bot = threading.Thread(target=rodar_bot, daemon=True)
t_bot.start()

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 8080))
  app.run(host="0.0.0.0", port=port)
