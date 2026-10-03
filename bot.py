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

# --- 1. MINI SERVIDOR WEB (Manter Render Ativo de Graça) ---
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
            detalhes TEXT
        )
    ''')
  conn.commit()
  conn.close()


# --- 3. PROCESSAMENTO INTELIGENTE COM IA (GROQ / LLAMA 3) ---
def extrair_dados_com_ia(texto_transcrito):
  # Instanciação Lazy: Lê a variável de ambiente somente no momento da chamada da função
  groq_api_key = os.environ.get('GROQ_API_KEY')

  if not groq_api_key:
    logging.error('ERRO: A variável GROQ_API_KEY não foi encontrada.')
    return None, None, None, None

  client_groq = Groq(api_key=groq_api_key)

  prompt = f"""
    Você é um assistente especialista em analisar relatos de segurança pública e entregas na Baixada Fluminense.
    Analise o texto abaixo dito por um entregador e extraia as informações de local e risco.

    Texto falado: "{texto_transcrito}"

    Retorne Apenas no formato exato separado por barras verticais "|":
    BAIRRO | RUA | RISCO | DETALHES

    Regras de Risco:
    - Risco 1: Baixo (iluminação ruim, movimento suspeito leve)
    - Risco 2: Médio (histórico de assaltos, furtos, atenção)
    - Risco 3: Alto (área de risco máximo, grupo armado, troca de tiros, assalto a moto frequente)

    Exemplo de Saída:
    Grama | Rua Rocha Farias | 3 | Atuação de grupo armado e risco de assalto
    
    Se não identificar o bairro ou rua no texto, use "Não informado".
    Responda APENAS a linha formatada com as barras verticais.
    """

  try:
    resposta = client_groq.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
    )

    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split('|')]

    if len(partes) == 4:
      return partes[0], partes[1], partes[2], partes[3]
  except Exception as e:
    logging.error(f'Erro na chamada da API Groq: {e}')

  return None, None, None, None


# --- 4. COMANDOS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco (Com IA Ativa)*\n\n'
      '🎙️ *Como cadastrar por Áudio:* Mandar um áudio falando normalmente!\n'
      ' Exemplo: *"Atenção aqui na Rua Rocha Farias no Bairro da Grama, área'
      ' com os cara armado, risco alto de assalto."*\n\n'
      'Comandos por texto:\n'
      '🔹 `/consultar [nome da rua ou bairro]`\n'
      '🔹 `/listar` - Exibe os últimos cadastros'
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
      'SELECT bairro, rua, risco, detalhes FROM alertas WHERE rua LIKE ? OR'
      ' bairro LIKE ?',
      (f'%{termo}%', f'%{termo}%'),
  )
  resultados = cursor.fetchall()
  conn.close()

  if not resultados:
    await update.message.reply_text(
        f'✅ Nenhum alerta cadastrado para: *{termo}*', parse_mode='Markdown'
    )
    return

  resposta = f"🔍 *Resultados para '{termo}':*\n\n"
  for item in resultados:
    bairro, rua, risco, detalhes = item
    alerta_emoji = '🟡' if risco == 1 else '🟧' if risco == 2 else '🔴'
    resposta += (
        f'{alerta_emoji} *Risco Nível {risco}*\n'
        f'📍 *Bairro:* {bairro} | *Rua:* {rua}\n'
        f'📝 *Detalhes:* {detalhes}\n'
        '------------------------\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


async def listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
  conn = sqlite3.connect('banco_risco.db')
  cursor = conn.cursor()
  cursor.execute(
      'SELECT bairro, rua, risco, detalhes FROM alertas ORDER BY id DESC LIMIT'
      ' 10'
  )
  resultados = cursor.fetchall()
  conn.close()

  if not resultados:
    await update.message.reply_text('Nenhum registro no banco de dados ainda.')
    return

  resposta = '📋 *Últimos 10 alertas cadastrados:*\n\n'
  for item in resultados:
    bairro, rua, risco, detalhes = item
    resposta += f'• *{rua}* ({bairro}) - Risco {risco}: {detalhes}\n'

  await update.message.reply_text(resposta, parse_mode='Markdown')


# --- 5. PROCESSADOR DE ÁUDIO COM INTELIGÊNCIA ARTIFICIAL ---
async def processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg_espera = await update.message.reply_text(
      '🎧 *Ouvindo e analisando relato com IA...*', parse_mode='Markdown'
  )

  try:
    # Baixa áudio do Telegram
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    oga_path = 'voice.oga'
    wav_path = 'voice.wav'

    await voice_file.download_to_drive(oga_path)

    # Converte para WAV
    sound = AudioSegment.from_file(oga_path)
    sound.export(wav_path, format='wav')

    # Transcreve Voz em Texto
    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
      audio_data = recognizer.record(source)
      texto_transcrito = recognizer.recognize_google(
          audio_data, language='pt-BR'
      )

    # Limpa arquivos temporários
    if os.path.exists(oga_path):
      os.remove(oga_path)
    if os.path.exists(wav_path):
      os.remove(wav_path)

    # IA do Groq analisa o texto e extrai os campos
    bairro, rua, risco_str, detalhes = extrair_dados_com_ia(texto_transcrito)

    if not bairro or bairro == 'Não informado':
      await update.message.reply_text(
          f'🗣️ *Você falou:* "_{texto_transcrito}_"\n\n'
          '⚠️ Não consegui identificar o nome da rua ou bairro com clareza.'
          ' Tente mencionar a rua e o bairro no áudio!',
          parse_mode='Markdown',
      )
      return

    try:
      risco = int(risco_str)
    except (ValueError, TypeError):
      risco = 2

    # Salva no banco de dados
    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO alertas (bairro, rua, risco, detalhes) VALUES (?, ?, ?,'
        ' ?)',
        (bairro, rua, risco, detalhes),
    )
    conn.commit()
    conn.close()

    alerta_emoji = '🟡' if risco == 1 else '🟧' if risco == 2 else '🔴'
    msg_sucesso = (
        f'🗣️ *Sua fala:* "_{texto_transcrito}_"\n\n'
        f'🤖 *Interpretação da IA:*\n'
        f'{alerta_emoji} *Alerta Salvo com Sucesso!*\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Rua:* {rua}\n'
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

  print('Bot com IA Groq rodando...')
  app_bot.run_polling()
