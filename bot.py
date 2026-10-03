import logging
import os
import sqlite3
from threading import Thread
from flask import Flask
from groq import Groq
from pydub import AudioSegment
import speech_recognition as sr
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- 1. MINI SERVIDOR WEB (Manter Render Ativo) ---
app = Flask('')


@app.route('/')
def home():
  return 'Bot de Mapeamento de Risco (IA Groq Ativa) está Online!'


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
    level=logging.INFO,
)


def iniciar_banco():
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


# --- 3. PROCESSAMENTO INTELIGENTE COM IA (GROQ / LLAMA 3) ---
def extrair_dados_com_ia(texto_transcrito):
  groq_api_key = os.environ.get('GROQ_API_KEY')

  if not groq_api_key:
    logging.error('ERRO: A variável GROQ_API_KEY não foi encontrada.')
    return None, None, None, None, None

  client_groq = Groq(api_key=groq_api_key)

  prompt = f"""
    Você é um assistente especialista em analisar relatos de segurança e logística de entregas na Baixada Fluminense.
    Analise o texto dito pelo entregador e extraia as informações de localização, nível de risco e restrições de horário.

    Texto falado: "{texto_transcrito}"

    Retorne APENAS no formato exato separado por barras verticais "|":
    BAIRRO | RUA | RISCO | HORARIO_CRITICO | DETALHES

    Regras de Interpretação:
    1. BAIRRO: Identifique o bairro ou região (Ex: Jardim Geneciano, Grama, Anbaí). Se não disser, use "Não informado".
    2. RUA:
       - Se o relato se referir ao bairro como um todo (Ex: "o bairro todo tá perigoso", "no Geneciano inteiro"), coloque "Todo o Bairro / Vias de Acesso".
       - Se citar uma rua/avenida específica (Ex: Av. Nazaré, Rua Rocha Farias), coloque o nome da rua.
       - Se não citar rua nem indicar o bairro todo, use "Não informado".
    3. RISCO:
       - Nível 1 (Baixo): Iluminação ruim, atenção leve.
       - Nível 2 (Médio): Furtos, histórico de assalto, atenção moderada.
       - Nível 3 (Alto): Área vermelha, presença de grupo armado, risco elevado de assalto a moto, restrição severa de circulação.
    4. HORARIO_CRITICO: Extraia qualquer menção a janelas de horário ou períodos do dia (Ex: "Após 17h", "À noite", "Qualquer horário", "Após 21h"). Se não citar, use "Dia e Noite".
    5. DETALHES: Resuma em uma frase curta os principais alertas (Ex: "Atenção reforçada após o pôr do sol", "Guerra de facções / risco de assalto a moto", "Exige atenção total nas vias de acesso").

    Exemplo 1 (Bairro Todo):
    Jardim Geneciano | Todo o Bairro / Vias de Acesso | 3 | Após 17h | Risco alto de assalto e movimentação armada nas vias internas à noite.

    Exemplo 2 (Rua Específica):
    Grama | Rua Rocha Farias | 3 | À noite | Ponto crítico de assalto a motociclistas.

    Responda APENAS a linha formatada com as barras verticais.
    """

  try:
    resposta = client_groq.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
    )

    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split('|')]

    if len(partes) == 5:
      return partes[0], partes[1], partes[2], partes[3], partes[4]
  except Exception as e:
    logging.error(f'Erro na chamada da API Groq: {e}')

  return None, None, None, None, None


# --- 4. COMANDOS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco (Baixada Fluminense)*\n\n'
      '🎙️ *Como cadastrar por Áudio:* Grave um áudio falando naturalmente!\n'
      ' Exemplo 1: *"Atenção rapaziada, o bairro do Geneciano todo tá'
      ' perigoso, depois das 5 da tarde o risco de assalto é alto."*\n'
      ' Exemplo 2: *"Cuidado na Av. Nazaré à noite, ponto crítico de assalto'
      ' a moto."*\n\n'
      'Comandos por texto:\n'
      '🔹 `/consultar [nome da rua ou bairro]`\n'
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

  # Busca por bairro ou rua
  cursor.execute(
      'SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas WHERE'
      ' rua LIKE ? OR bairro LIKE ? ORDER BY id DESC',
      (f'%{termo}%', f'%{termo}%'),
  )
  resultados = cursor.fetchall()
  conn.close()

  if not resultados:
    await update.message.reply_text(
        f'✅ Nenhum alerta cadastrado para: *{termo}*', parse_mode='Markdown'
    )
    return

  resposta = f"🔍 *Alertas encontrados para '{termo}':*\n\n"
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    alerta_emoji = '🟡' if risco == 1 else '🟧' if risco == 2 else '🔴'
    resposta += (
        f'{alerta_emoji} *Risco Nível {risco}*\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Local/Rua:* {rua}\n'
        f'⏰ *Horário Crítico:* {horario}\n'
        f'📝 *Detalhes:* {detalhes}\n'
        '------------------------\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


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

  resposta = '📋 *Últimos 10 alertas cadastrados:*\n\n'
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    alerta_emoji = '🟡' if risco == 1 else '🟧' if risco == 2 else '🔴'
    resposta += (
        f'{alerta_emoji} *{bairro}* ({rua})\n'
        f'⏰ {horario} | ⚠️ Risco {risco}: {detalhes}\n\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


# --- 5. PROCESSADOR DE ÁUDIO COM INTELIGÊNCIA ARTIFICIAL ---
async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg_espera = await update.message.reply_text(
      '🎧 *Ouvindo e analisando relato com IA...*', parse_mode='Markdown'
  )

  try:
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    oga_path = 'voice.oga'
    wav_path = 'voice.wav'

    await voice_file.download_to_drive(oga_path)

    sound = AudioSegment.from_file(oga_path)
    sound.export(wav_path, format='wav')

    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
      audio_data = recognizer.record(source)
      texto_transcrito = recognizer.recognize_google(
          audio_data, language='pt-BR'
      )

    if os.path.exists(oga_path):
      os.remove(oga_path)
    if os.path.exists(wav_path):
      os.remove(wav_path)

    bairro, rua, risco_str, horario_critico, detalhes = extrair_dados_com_ia(
        texto_transcrito
    )

    if not bairro or bairro == 'Não informado':
      await update.message.reply_text(
          f'🗣️ *Você falou:* "_{texto_transcrito}_"\n\n'
          '⚠️ Não consegui identificar o nome do bairro ou região com clareza.'
          ' Tente mencionar o bairro no áudio!',
          parse_mode='Markdown',
      )
      return

    try:
      risco = int(risco_str)
    except (ValueError, TypeError):
      risco = 2

    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()

    # Garantir que a coluna 'horario_critico' exista no banco existente
    cursor.execute('PRAGMA table_info(alertas)')
    colunas = [coluna[1] for coluna in cursor.fetchall()]
    if 'horario_critico' not in colunas:
      cursor.execute('ALTER TABLE alertas ADD COLUMN horario_critico TEXT')

    cursor.execute(
        'INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)'
        ' VALUES (?, ?, ?, ?, ?)',
        (bairro, rua, risco, horario_critico, detalhes),
    )
    conn.commit()
    conn.close()

    alerta_emoji = '🟡' if risco == 1 else '🟧' if risco == 2 else '🔴'
    msg_sucesso = (
        f'🗣️ *Sua fala:* "_{texto_transcrito}_"\n\n'
        f'🤖 *Interpretação Inteligente:*\n'
        f'{alerta_emoji} *Alerta Salvo com Sucesso!*\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Local/Rua:* {rua}\n'
        f'⏰ *Horário Crítico:* {horario_critico}\n'
        f'⚠️ *Nível de Risco:* {risco}\n'
        f'📝 *Detalhes:* {detalhes}'
    )
    await update.message.reply_text(msg_sucesso, parse_mode='Markdown')

  except Exception as e:
    logging.error(f'Erro no processamento de áudio: {e}')
    await update.message.reply_text(
        '❌ Não consegui processar o áudio. Tente falar novamente com mais'
        ' clareza.'
    )


# --- 6. EXECUÇÃO DO BOT ---
if __name__ == '__main__':
  keep_alive()
  iniciar_banco()

  telegram_token = os.environ.get('TELEGRAM_TOKEN')
  if not telegram_token:
    raise ValueError('A variável TELEGRAM_TOKEN não foi configurada no Render!')

  app_bot = ApplicationBuilder().token(telegram_token).build()

  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('consultar', consultar))
  app_bot.add_handler(CommandHandler('listar', listar))
  app_bot.add_handler(MessageHandler(filters.VOICE, processar_audio))

  print('Bot com IA Groq e Mapeamento por Horário/Bairro rodando...')
  app_bot.run_polling()
