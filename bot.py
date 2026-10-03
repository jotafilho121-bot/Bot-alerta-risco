import asyncio
import logging
import os
import sqlite3
import threading
from flask import Flask
from groq import Groq
from gtts import gTTS
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- SERVIDOR FLASK ---
app = Flask(__name__)


@app.route('/')
def home():
  return 'Bot de Mapeamento de Risco Ativo!'


# --- LOGS E BANCO DE DADOS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)


def iniciar_banco():
  try:
    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT NOT NULL,
                rua TEXT NOT NULL,
                risco INTEGER NOT NULL,
                horario_critico TEXT,
                detalhes TEXT
            )
        ''')
    conn.commit()
    conn.close()
    logging.info('Banco de dados verificado/criado com sucesso.')
  except Exception as e:
    logging.error(f'Erro ao inicializar banco: {e}')


# --- FUNÇÕES DE ÁUDIO ---
def gerar_audio_gtts(texto, caminho_arquivo):
  tts = gTTS(text=texto, lang='pt', tld='com.br')
  tts.save(caminho_arquivo)


async def enviar_resposta_em_audio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, texto_resposta: str
):
  caminho_mp3 = f'resposta_{update.effective_chat.id}.mp3'
  try:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, gerar_audio_gtts, texto_resposta, caminho_mp3
    )

    if os.path.exists(caminho_mp3):
      with open(caminho_mp3, 'rb') as audio_file:
        await context.bot.send_audio(
            chat_id=update.effective_chat.id,
            audio=audio_file,
            title='Alerta de Segurança',
            filename='alerta.mp3',
        )
  except Exception as e:
    logging.error(f'Erro ao gerar ou enviar audio: {e}')
  finally:
    if os.path.exists(caminho_mp3):
      try:
        os.remove(caminho_mp3)
      except Exception:
        pass


# --- INTEGRAÇÃO GROQ ---
def processar_relato_com_groq(caminho_audio):
  groq_api_key = os.environ.get('GROQ_API_KEY')
  if not groq_api_key:
    logging.error('GROQ_API_KEY nao encontrada nas variaveis de ambiente.')
    return None, None, None, None, None, None

  client = Groq(api_key=groq_api_key)

  try:
    with open(caminho_audio, 'rb') as file:
      transcription = client.audio.transcriptions.create(
          file=(caminho_audio, file.read()),
          model='whisper-large-v3-turbo',
          language='pt',
          response_format='text',
      )

    texto_transcrito = str(transcription).strip()

    prompt = f"""
        Voce e um assistente especializado em mapeamento de risco viario para entregadores em Nova Iguacu e Baixada Fluminense.
        Analise a transcricao da fala do entregador e extraia os dados.

        Texto falado: "{texto_transcrito}"

        Formato de resposta OBRIGATORIO (separado estritamente por |):
        BAIRRO | RUA | RISCO | HORARIO_CRITICO | DETALHES

        Regras:
        - BAIRRO: Nome do bairro citado (Ex: Jardim Geneciano, Grama, Austin, Ambai).
        - RUA: Nome da rua/avenida citada. Se o relato for sobre o bairro inteiro ou nao citar rua, escreva "Todo o Bairro / Vias de Acesso".
        - RISCO: Um numero simples (1, 2 ou 3). 1=Baixo, 2=Medio, 3=Alto/Critico.
        - HORARIO_CRITICO: Periodo citado (Ex: "Apos 17h", "A noite", "Dia e Noite").
        - DETALHES: Breve resumo neutro e direto sem usar girias nem nomes de faccoes.

        Retorne APENAS a linha no formato indicado.
        """

    resposta = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
    )

    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split('|')]

    if len(partes) == 5:
      return (
          partes[0],
          partes[1],
          partes[2],
          partes[3],
          partes[4],
          texto_transcrito,
      )

  except Exception as e:
    logging.error(f'Erro no processamento Groq: {e}')

  return None, None, None, None, None, None


# --- HANDLERS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco*\n\n'
      '🎙️ Envie um áudio informando o local e o nível de risco.\n\n'
      'Comandos:\n'
      '🔹 `/consultar [bairro ou rua]`\n'
      '🔹 `/listar` - Exibe os últimos alertas'
  )
  await update.message.reply_text(msg, parse_mode='Markdown')


async def consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not context.args:
    await update.message.reply_text(
        '⚠️ Use: `/consultar [Nome da Rua ou Bairro]`', parse_mode='Markdown'
    )
    return

  termo = ' '.join(context.args)
  conn = sqlite3.connect('banco_risco.db')
  cursor = conn.cursor()

  cursor.execute(
      'SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas WHERE'
      ' rua LIKE ? OR bairro LIKE ? ORDER BY id DESC',
      (f'%{termo}%', f'%{termo}%'),
  )
  resultados = cursor.fetchall()
  conn.close()

  if not resultados:
    msg_no = f'Nenhum alerta cadastrado para {termo}.'
    await update.message.reply_text(f'✅ {msg_no}')
    await enviar_resposta_em_audio(update, context, msg_no)
    return

  resposta = f'🔍 Alertas encontrados para {termo}:\n\n'
  texto_fala = f'Alertas encontrados para {termo}. '

  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    resposta += (
        f'⚠️ *Risco Nível {risco}*\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Local/Rua:* {rua}\n'
        f'⏰ *Horário Crítico:* {horario}\n'
        f'📝 *Detalhes:* {detalhes}\n'
        '------------------------\n'
    )
    texto_fala += f'No bairro {bairro}, local {rua}, risco nível {risco}. Horário crítico: {horario}. Detalhes: {detalhes}. '

  await update.message.reply_text(resposta, parse_mode='Markdown')
  await enviar_resposta_em_audio(update, context, texto_fala)


async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect('banco_risco.db')
  cursor = conn.cursor()
  cursor.execute(
      'SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas ORDER'
      ' BY id DESC LIMIT 10'
  )
  resultados = cursor.fetchall()
  conn.close()

  if not resultados:
    await update.message.reply_text('Nenhum registro no banco de dados ainda.')
    return

  resposta = '📋 *Últimos alertas cadastrados:*\n\n'
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    resposta += (
        f'• *{bairro}* ({rua})\n  ⏰ {horario} | Risco {risco}: {detalhes}\n\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      '🎧 *Ouvindo e analisando relato com IA...*', parse_mode='Markdown'
  )

  oga_path = f'voice_{update.effective_chat.id}.oga'
  try:
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    await voice_file.download_to_drive(oga_path)

    loop = asyncio.get_running_loop()
    (
        bairro,
        rua,
        risco_str,
        horario_critico,
        detalhes,
        texto_transcrito,
    ) = await loop.run_in_executor(None, processar_relato_com_groq, oga_path)

    if not bairro:
      bairro = 'Bairro Identificado no Relato'
    if not rua:
      rua = 'Todo o Bairro / Vias de Acesso'
    if not horario_critico:
      horario_critico = 'Dia e Noite'

    try:
      risco = int(risco_str)
    except (ValueError, TypeError):
      risco = 3

    if not detalhes:
      detalhes = (
          texto_transcrito if texto_transcrito else 'Relato de risco registrado'
      )

    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)'
        ' VALUES (?, ?, ?, ?, ?)',
        (bairro, rua, risco, horario_critico, detalhes),
    )
    conn.commit()
    conn.close()

    msg_sucesso = (
        f'🗣️ *Sua fala:* "_{texto_transcrito}_"\n\n'
        f'🤖 *Alerta Cadastrado com Sucesso!*\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣 *Local/Rua:* {rua}\n'
        f'⏰ *Horário Crítico:* {horario_critico}\n'
        f'⚠ *Nível de Risco:* {risco}\n'
        f'📝 *Detalhes:* {detalhes}'
    )

    fala_confirmacao = (
        f'Alerta salvo com sucesso. Bairro {bairro}, local {rua}. '
        f'Nível de risco {risco}. Horário crítico: {horario_critico}.'
    )

    await update.message.reply_text(msg_sucesso, parse_mode='Markdown')
    asyncio.create_task(
        enviar_resposta_em_audio(update, context, fala_confirmacao)
    )

  except Exception as e:
    logging.error(f'Erro no processamento de áudio: {e}')
    await update.message.reply_text(
        '❌ Não foi possível processar o áudio. Tente enviar novamente.'
    )
  finally:
    if os.path.exists(oga_path):
      try:
        os.remove(oga_path)
      except Exception:
        pass


# --- EXECUÇÃO DO BOT TELEGRAM ---
def iniciar_telegram():
  telegram_token = os.environ.get('TELEGRAM_TOKEN')
  if not telegram_token:
    logging.error('TELEGRAM_TOKEN ausente nas variaveis de ambiente.')
    return

  app_bot = ApplicationBuilder().token(telegram_token).build()
  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('consultar', consultar))
  app_bot.add_handler(CommandHandler('listar', listar))
  app_bot.add_handler(MessageHandler(filters.VOICE, processar_audio))

  logging.info('Polling do Telegram iniciado com sucesso!')
  app_bot.run_polling(drop_pending_updates=True)


# --- INICIALIZAÇÃO INCONDICIONAL DAS THREADS ---
iniciar_banco()

# Dispara a escuta do Telegram em thread separada
t_telegram = threading.Thread(target=iniciar_telegram, daemon=True)
t_telegram.start()

# Roda o Flask no processo principal se o script for chamado diretamente
if __name__ == '__main__':
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)
