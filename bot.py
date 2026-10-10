import asyncio
import io
import logging
import os
import sqlite3
import threading
import time
import edge_tts
from flask import Flask
from groq import Groq
import requests
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
          or dados.get("path")
          or "Via Próxima"
      )

      # Tenta pegar campos específicos de bairro antes do município
      bairro = (
          dados.get("suburb")
          or dados.get("neighbourhood")
          or dados.get("quarter")
          or dados.get("hamlet")
          or dados.get("residential")
          or dados.get("city_district")
      )

      # Se o Nominatim retornar apenas a cidade genérica, ajustamos para a região atual (Grama)
      if not bairro or bairro.lower() in [
          "nova iguaçu",
          "nova iguacu",
          "rio de janeiro",
      ]:
        bairro = "Grama"

      return bairro, rua
  except Exception as e:
    logging.error(f"Erro no geocoding: {e}")

  return "Grama", "Via Próxima"


# --- ÁUDIO DE RESPOSTA ---
async def gerar_audio_neural(texto, caminho_arquivo):
  communicate = edge_tts.Communicate(
      texto, voice="pt-BR-FranciscaNeural", rate="+0%"
  )
  await communicate.save(caminho_arquivo)


async def enviar_resposta_em_audio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, texto_resposta: str
):
  caminho_mp3 = f"resposta_{update.effective_chat.id}.mp3"
  try:
    await gerar_audio_neural(texto_resposta, caminho_mp3)
    if os.path.exists(caminho_mp3):
      with open(caminho_mp3, "rb") as audio_file:
        await context.bot.send_audio(
            chat_id=update.effective_chat.id,
            audio=audio_file,
            title="Alerta de Segurança",
            filename="alerta.mp3",
        )
  except Exception as e:
    logging.error(f"Erro envio audio: {e}")
  finally:
    if os.path.exists(caminho_mp3):
      try:
        os.remove(caminho_mp3)
      except Exception:
        pass


# --- INTELIGÊNCIA ARTIFICIAL (GROQ) ---
def analisar_mensagem_com_groq(texto_entrada, contexto_gps=""):
  groq_api_key = os.environ.get("GROQ_API_KEY")
  if not groq_api_key:
    return ["ERRO", "Sem API Key"]

  client = Groq(api_key=groq_api_key)

  prompt = f"""
    Sua tarefa é analisar a mensagem de um entregador em Nova Iguaçu e classificar em CADASTRO ou CONSULTA.

    {contexto_gps}
    Mensagem: "{texto_entrada}"

    Formato OBRIGATÓRIO de resposta (apenas uma linha separada por |):

    Se for CADASTRO de alerta/risco/assalto:
    CADASTRO | BAIRRO | RUA | RISCO_NUMERO | HORARIO | DETALHES

    Se for CONSULTA sobre segurança de local:
    CONSULTA | BAIRRO_OU_RUA

    Exemplos:
    CADASTRO | Grama | Rua Rocha Faria | 3 | Recente | Assalto a entregador recente
    CONSULTA | Austin

    Responda apenas na estrutura solicitada, sem textos adicionais.
    """

  try:
    resposta = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.1,
    )
    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split("|")]
    return partes
  except Exception as e:
    logging.error(f"Erro Groq Text: {e}")
    return ["ERRO", str(e)]


# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      "🚨 *Bot Mapeador de Entregas Ativo*\n\n"
      "• Mande texto, áudio ou sua localização (GPS).\n"
      '• Para consultar: _"Como tá a Rua Rocha Faria no bairro Grama?"_\n'
      '• Para cadastrar: _"Assalto na Rua Rocha Faria no Grama, risco 3"_'
  )
  await update.message.reply_text(msg, parse_mode="Markdown")


async def receber_localizacao(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  try:
    lat = update.message.location.latitude
    lon = update.message.location.longitude

    bairro, rua = obter_endereco_gps(lat, lon)

    context.user_data["bairro"] = bairro
    context.user_data["rua"] = rua

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
        f"📍 *Localização Identificada!*\n• *Bairro:* {bairro}\n• *Rua:* {rua}\n\n"
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
    eh_audio = bool(update.message.voice)
    texto_entrada = ""

    if eh_audio:
      await update.message.reply_text(
          "🎧 *Ouvindo áudio...*", parse_mode="Markdown"
      )
      caminho_temp = f"audio_{update.effective_chat.id}.ogg"
      try:
        groq_api_key = os.environ.get("GROQ_API_KEY")
        client = Groq(api_key=groq_api_key)

        voice_file = await context.bot.get_file(update.message.voice.file_id)
        await voice_file.download_to_drive(caminho_temp)

        # Envio corrigido e otimizado para o Whisper da Groq
        with open(caminho_temp, "rb") as audio_file_obj:
          transcription = client.audio.transcriptions.create(
              file=("audio.ogg", audio_file_obj.read()),
              model="whisper-large-v3-turbo",
              language="pt",
              response_format="text",
          )

        texto_entrada = str(transcription).strip()
        logging.info(f"Áudio transcrito com sucesso: {texto_entrada}")

      except Exception as e:
        logging.error(f"Erro crítico no Whisper/Groq: {e}")
        await update.message.reply_text(
            "❌ Não consegui processar o áudio. Tente enviar novamente ou digite"
            " em texto."
        )
        return
      finally:
        if os.path.exists(caminho_temp):
          try:
            os.remove(caminho_temp)
          except Exception:
            pass
    else:
      texto_entrada = update.message.text.strip()

    contexto_gps = ""
    if "bairro" in context.user_data and "rua" in context.user_data:
      contexto_gps = (
          f"Localização GPS recente: Bairro '{context.user_data['bairro']}', Rua"
          f" '{context.user_data['rua']}'."
      )

    dados = analisar_mensagem_com_groq(texto_entrada, contexto_gps)

    if not dados or len(dados) == 0:
      await update.message.reply_text(
          "⚠️ Não entendi a mensagem. Informe o bairro ou rua."
      )
      return

    tipo_acao = dados[0].upper()

    if tipo_acao == "CONSULTA":
      termo_busca = dados[1] if len(dados) > 1 else "Região"

      conn = sqlite3.connect("banco_risco.db")
      cursor = conn.cursor()

      cursor.execute(
          "SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas"
          " WHERE rua LIKE ? OR bairro LIKE ? ORDER BY id DESC LIMIT 5",
          (f"%{termo_busca}%", f"%{termo_busca}%"),
      )
      relatos = cursor.fetchall()

      cursor.execute(
          "SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE"
          " bairro LIKE ?",
          (f"%{termo_busca}%",),
      )
      estatistica = cursor.fetchone()
      conn.close()

      resposta = f"🔍 *Análise para {termo_busca}:*\n\n"
      if relatos:
        resposta += "🚨 *Alertas Recentes:*\n"
        for item in relatos:
          resposta += (
              f"• *Risco {item[2]}* em {item[1]} ({item[0]})\n  {item[3]} |"
              f" {item[4]}\n"
          )
      else:
        resposta += "✅ *Nenhum alerta recente registrado nesta área.*\n\n"

      if estatistica:
        resposta += (
            f"📊 *Mancha Criminal:* Risco Base {estatistica[0]} -"
            f" {estatistica[1]}\n"
        )

      await update.message.reply_text(resposta, parse_mode="Markdown")

    elif tipo_acao == "CADASTRO" and len(dados) >= 4:
      bairro = dados[1] if len(dados) > 1 else "Grama"
      rua = dados[2] if len(dados) > 2 else "Via Não Informada"
      risco_raw = dados[3] if len(dados) > 3 else "3"
      horario = dados[4] if len(dados) > 4 else "Recente"
      detalhes = dados[5] if len(dados) > 5 else texto_entrada

      try:
        risco = int("".join(filter(str.isdigit, risco_raw)))
      except ValueError:
        risco = 3

      conn = sqlite3.connect("banco_risco.db")
      cursor = conn.cursor()
      cursor.execute(
          "INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)"
          " VALUES (?, ?, ?, ?, ?)",
          (bairro, rua, risco, horario, detalhes),
      )
      conn.commit()
      conn.close()

      msg_sucesso = (
          f"🤖 *Alerta Cadastrado com Sucesso!*\n\n"
          f"📍 *Bairro:* {bairro}\n"
          f"🛣️ *Rua:* {rua}\n"
          f"⚠ *Risco:* Nível {risco}\n"
          f"⏰ *Horário:* {horario}\n"
          f"📝 *Detalhes:* {detalhes}"
      )
      await update.message.reply_text(msg_sucesso, parse_mode="Markdown")

    else:
      await update.message.reply_text(
          "⚠️ Não entendi com clareza. Digite ou fale informando o bairro ou rua"
          " para consultar ou cadastrar."
      )

  except Exception as e:
    logging.error(f"Erro geral em processar_mensagem: {e}")
    await update.message.reply_text(
        "❌ Ocorreu um erro ao processar sua mensagem."
    )


def rodar_bot():
  telegram_token = os.environ.get("TELEGRAM_TOKEN")
  if not telegram_token:
    logging.error("TELEGRAM_TOKEN ausente.")
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

      filtro_mensagens = (filters.TEXT & ~filters.COMMAND) | filters.VOICE
      app_bot.add_handler(MessageHandler(filtro_mensagens, processar_mensagem))

      logging.info("Bot rodando!")
      app_bot.run_polling(drop_pending_updates=True, stop_signals=None)

    except Exception as e:
      logging.error(f"Erro no polling: {e}")
      time.sleep(5)


iniciar_banco()

t_bot = threading.Thread(target=rodar_bot, daemon=True)
t_bot.start()

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 8080))
  app.run(host="0.0.0.0", port=port)
