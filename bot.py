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

# --- 1. MINI SERVIDOR WEB ---
app = Flask('')


@app.route('/')
def home():
  return 'Bot de Mapeamento de Risco (IA Groq) esta Online!'


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
    logging.error('ERRO: A variavel GROQ_API_KEY nao foi encontrada.')
    return None, None, None, None, None

  client_groq = Groq(api_key=groq_api_key)

  prompt = f"""
    Voce e um assistente especializado em mapeamento de risco viario para entregadores em Nova Iguacu e Baixada Fluminense.
    Analise a transcricao da fala do entregador e extraia os dados.

    Texto falado: "{texto_transcrito}"

    Formato de resposta OBRIGATORIO (separado estritamente por |):
    BAIRRO | RUA | RISCO | HORARIO_CRITICO | DETALHES

    Regras:
    - BAIRRO: Nome do bairro citado (Ex: Jardim Geneciano, Grama, Austin, Ambai). Se o usuario citou cidade ou bairro, extraia o bairro.
    - RUA: Nome da rua/avenida citada. Se o relato for sobre o bairro inteiro ou nao citar rua, escreva obrigatoriamente "Todo o Bairro / Vias de Acesso".
    - RISCO: Um numero simples (1, 2 ou 3). 1=Baixo, 2=Medio, 3=Alto/Critico.
    - HORARIO_CRITICO: Periodo citado (Ex: "Apos 17h", "A noite", "Dia e Noite").
    - DETALHES: Breve resumo neutro e direto sem usar gírias perigosas nem nomes de faccoes.

    Exemplo de Saida:
    Jardim Geneciano | Todo o Bairro / Vias de Acesso | 3 | Apos 17h | Atencao elevada nas vias de acesso no periodo noturno.

    Retorne APENAS a linha no formato indicado.
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
      '🚨 *Bot de Mapeamento de Risco*\n\n'
      '🎙️ *Como cadastrar por Áudio:* Envie um áudio falando o local e o'
      ' risco!\n'
      ' Exemplo: *"Atenção no Jardim Geneciano, área de risco 3 a partir das 5'
      ' da tarde."*\n\n'
      'Comandos por texto:\n'
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
    await update.message.reply_text(
        f'✅ Nenhum alerta cadastrado para: *{termo}*', parse_mode='Markdown'
    )
    return

  resposta = f"🔍 *Alertas encontrados para '{termo}':*\n\n"
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

  resposta = '📋 *Últimos alertas cadastrados:*\n\n'
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    resposta += (
        f'• *{bairro}* ({rua})\n  ⏰ {horario} | Risco {risco}: {detalhes}\n\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


# --- 5. PROCESSADOR DE ÁUDIO ---
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

    # Se a IA não conseguiu extrair em 5 partes, define padrão baseado no texto
    if not bairro or bairro.lower() in [
        'não informado',
        'nao informado',
        'none',
    ]:
      bairro = 'Bairro Identificado no Relato'

    if not rua or rua.lower() in ['não informado', 'nao informado', 'none']:
      rua = 'Todo o Bairro / Vias de Acesso'

    if not horario_critico:
      horario_critico = 'Dia e Noite'

    try:
      risco = int(risco_str)
    except (ValueError, TypeError):
      risco = 3 if '3' in texto_transcrito else 2

    if not detalhes:
      detalhes = texto_transcrito

    # Salva no Banco de Dados
    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()

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

    msg_sucesso = (
        f'🗣️ *Sua fala:* "_{texto_transcrito}_"\n\n'
        f'🤖 *Alerta Cadastrado com Sucesso!*\n'
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
        '❌ Erro ao processar o áudio. Tente novamente.'
    )


# --- 6. EXECUÇÃO DO BOT ---
if __name__ == '__main__':
  keep_alive()
  iniciar_banco()

  telegram_token = os.environ.get('TELEGRAM_TOKEN')
  if not telegram_token:
    raise ValueError('A variavel TELEGRAM_TOKEN nao foi configurada!')

  app_bot = ApplicationBuilder().token(telegram_token).build()

  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('consultar', consultar))
  app_bot.add_handler(CommandHandler('listar', listar))
  app_bot.add_handler(MessageHandler(filters.VOICE, processar_audio))

  print('Bot de Mapeamento Rodando...')
  app_bot.run_polling()
