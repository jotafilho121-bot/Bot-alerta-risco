import asyncio
import logging
import os
import sqlite3
import threading
import edge_tts
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

# --- SERVIDOR FLASK PARA O RENDER ---
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
    logging.info('Banco de dados carregado com sucesso.')
  except Exception as e:
    logging.error(f'Erro no banco: {e}')


# --- SÍNTESE DE VOZ NEURAL HUMANA (EDGE-TTS) ---
async def gerar_audio_neural(texto, caminho_arquivo):
  # Voz Francisca Neural: tom natural, claro e sem ruído robótico
  communicate = edge_tts.Communicate(
      texto, voice='pt-BR-FranciscaNeural', rate='+0%'
  )
  await communicate.save(caminho_arquivo)


async def enviar_resposta_em_audio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, texto_resposta: str
):
  caminho_mp3 = f'resposta_{update.effective_chat.id}.mp3'
  try:
    # Gera o áudio com qualidade humana
    await gerar_audio_neural(texto_resposta, caminho_mp3)

    if os.path.exists(caminho_mp3):
      with open(caminho_mp3, 'rb') as audio_file:
        await context.bot.send_audio(
            chat_id=update.effective_chat.id,
            audio=audio_file,
            title='Alerta de Segurança',
            filename='alerta.mp3',
        )
  except Exception as e:
    logging.error(f'Erro ao gerar/enviar audio neural: {e}')
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
    logging.error('GROQ_API_KEY ausente.')
    return 'ERRO', 'Chave de API nao configurada.', None, None, None, None

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
        Voce e um assistente de risco viario para entregadores em Nova Iguacu e Baixada Fluminense.
        Analise a transcricao abaixo e identifique a INTENCAO do usuario.

        Texto falado: "{texto_transcrito}"

        A intencao e CONSULTAR (perguntar sobre a seguranca de um local) ou CADASTRAR (relatar um perigo/assalto/risco)?

        Responda ESTRITAMENTE em um dos dois formatos abaixo:

        Se for CONSULTA:
        CONSULTA | NOME_DO_BAIRRO_OU_RUA | {texto_transcrito}

        Se for CADASTRO:
        CADASTRO | BAIRRO | RUA | RISCO (1, 2 ou 3) | HORARIO_CRITICO | DETALHES | {texto_transcrito}

        Regras para Cadastro:
        - RISCO: 1=Baixo, 2=Medio, 3=Alto/Critico.
        - Se nao citar rua, use "Todo o Bairro / Vias de Acesso".
        - Resumo neutro e direto sem girias.
        """

    resposta = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
    )

    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split('|')]
    return partes

  except Exception as e:
    logging.error(f'Erro no Groq: {e}')
    return 'ERRO', str(e), None, None, None, None


# --- HANDLERS TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco*\n\n'
      '🎙️ Envie um áudio informando um relato de risco ou consultando um'
      ' local.\n\n'
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
        f'⏰ *Horário:* {horario}\n'
        f'📝 *Detalhes:* {detalhes}\n'
        '------------------------\n'
    )
    texto_fala += f'No bairro {bairro}, local {rua}, risco nível {risco}. Detalhes: {detalhes}. '

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
    dados = await loop.run_in_executor(
        None, processar_relato_com_groq, oga_path
    )

    tipo_acao = dados[0]

    if tipo_acao == 'CONSULTA':
      termo_busca = dados[1] if len(dados) > 1 else 'Região'
      texto_fala_original = dados[2] if len(dados) > 2 else ''

      conn = sqlite3.connect('banco_risco.db')
      cursor = conn.cursor()
      cursor.execute(
          'SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas'
          ' WHERE rua LIKE ? OR bairro LIKE ? ORDER BY id DESC',
          (f'%{termo_busca}%', f'%{termo_busca}%'),
      )
      resultados = cursor.fetchall()
      conn.close()

      if not resultados:
        msg = (
            f'🗣️ *Você perguntou:* "_{texto_fala_original}_"\n\n'
            f'✅ Nenhum alerta cadastrado para *{termo_busca}*.'
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
        await enviar_resposta_em_audio(
            update,
            context,
            f'Nenhum alerta cadastrado para {termo_busca}. Região sem registros.',
        )
        return

      resposta_texto = (
          f'🗣️ *Você perguntou:* "_{texto_fala_original}_"\n\n'
          f'🔍 *Alertas para {termo_busca}:*\n\n'
      )
      texto_audio = f'Alertas encontrados para {termo_busca}. '

      for item in resultados:
        bairro, rua, risco, horario, detalhes = item
        resposta_texto += (
            f'⚠️ *Risco Nível {risco}*\n📍 *Bairro:* {bairro}\n🛣️️ *Rua:*'
            f' {rua}\n⏰ *Horário:* {horario}\n📝 *Detalhes:* {detalhes}\n---\n'
        )
        texto_audio += f'No bairro {bairro}, local {rua}, risco nível {risco}. Detalhes: {detalhes}. '

      await update.message.reply_text(resposta_texto, parse_mode='Markdown')
      await enviar_resposta_em_audio(update, context, texto_audio)

    elif tipo_acao == 'CADASTRO' and len(dados) >= 6:
      _, bairro, rua, risco_str, horario_critico, detalhes = dados[:6]
      texto_transcrito = dados[6] if len(dados) > 6 else ''

      try:
        risco = int(risco_str)
      except (ValueError, TypeError):
        risco = 3

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
          f'Alerta salvo com sucesso. Bairro {bairro}, local {rua}. Nível de'
          f' risco {risco}.'
      )

      await update.message.reply_text(msg_sucesso, parse_mode='Markdown')
      await enviar_resposta_em_audio(update, context, fala_confirmacao)

    else:
      await update.message.reply_text(
          '⚠️ Não entendi se você queria cadastrar ou consultar. Pode repetir?'
      )

  except Exception as e:
    logging.error(f'Erro no processamento de áudio: {e}')
    await update.message.reply_text(
        '❌ Erro ao processar o áudio. Tente novamente.'
    )
  finally:
    if os.path.exists(oga_path):
      try:
        os.remove(oga_path)
      except Exception:
        pass


# --- EXECUÇÃO DO BOT ---
def rodar_bot():
  telegram_token = os.environ.get('TELEGRAM_TOKEN')
  if not telegram_token:
    logging.error('TELEGRAM_TOKEN ausente.')
    return

  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)

  app_bot = ApplicationBuilder().token(telegram_token).build()

  # Remove webhooks antigos antes do polling
  loop.run_until_complete(
      app_bot.bot.delete_webhook(drop_pending_updates=True)
  )

  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('consultar', consultar))
  app_bot.add_handler(CommandHandler('listar', listar))
  app_bot.add_handler(MessageHandler(filters.VOICE, processar_audio))

  logging.info('Polling ativado com sucesso!')
  app_bot.run_polling(drop_pending_updates=True, stop_signals=None)


# --- INICIALIZAÇÃO DE THREADS ---
iniciar_banco()

t_bot = threading.Thread(target=rodar_bot, daemon=True)
t_bot.start()

if __name__ == '__main__':
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)
