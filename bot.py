import asyncio
import logging
import os
import sqlite3
import threading
import edge_tts
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
    logging.info('Banco de dados inicializado.')
  except Exception as e:
    logging.error(f'Erro no banco: {e}')


# --- REVERSE GEOCODING (GPS PARA ENDEREÇO) ---
def obter_endereco_gps(lat, lon):
  try:
    url = f'https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1'
    headers = {'User-Agent': 'BotSeguranca/1.0'}
    res = requests.get(url, headers=headers, timeout=5)
    if res.status_code == 200:
      dados = res.json().get('address', {})
      rua = (
          dados.get('road')
          or dados.get('pedestrian')
          or dados.get('suburb')
          or 'Via Próxima'
      )
      bairro = (
          dados.get('suburb')
          or dados.get('neighbourhood')
          or dados.get('city_district')
          or dados.get('city')
          or 'Região Atual'
      )
      return bairro, rua
  except Exception as e:
    logging.error(f'Erro no geocoding: {e}')
  return 'Região da Localização Enviada', 'Vias de Acesso'


# --- VOZ NEURAL HUMANA ---
async def gerar_audio_neural(texto, caminho_arquivo):
  communicate = edge_tts.Communicate(
      texto, voice='pt-BR-FranciscaNeural', rate='+0%'
  )
  await communicate.save(caminho_arquivo)


async def enviar_resposta_em_audio(
    update: Update, context: ContextTypes.DEFAULT_TYPE, texto_resposta: str
):
  caminho_mp3 = f'resposta_{update.effective_chat.id}.mp3'
  try:
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
    logging.error(f'Erro ao gerar/enviar audio: {e}')
  finally:
    if os.path.exists(caminho_mp3):
      try:
        os.remove(caminho_mp3)
      except Exception:
        pass


# --- MOTOR DE INTELIGÊNCIA ARTIFICIAL (TEXTO/ÁUDIO) ---
def analisar_mensagem_com_groq(texto_entrada, contexto_gps=''):
  groq_api_key = os.environ.get('GROQ_API_KEY')
  if not groq_api_key:
    return 'ERRO', 'Chave GROQ_API_KEY nao configurada.', None, None, None, None

  client = Groq(api_key=groq_api_key)

  prompt = f"""
    Voce e um assistente de risco viario para entregadores em Nova Iguacu e Baixada Fluminense.
    Analise a mensagem abaixo recebida por texto ou transcricao de audio.

    {contexto_gps}
    Mensagem recebida: "{texto_entrada}"

    A intencao do usuario e CONSULTAR (perguntar sobre seguranca) ou CADASTRAR (relatar risco/assalto/perigo)?

    Responda ESTRITAMENTE em um dos dois formatos:

    Se for CONSULTA:
    CONSULTA | NOME_DO_BAIRRO_OU_RUA | {texto_entrada}

    Se for CADASTRO:
    CADASTRO | BAIRRO | RUA | RISCO (1, 2 ou 3) | HORARIO_CRITICO | DETALHES | {texto_entrada}

    Regras para Cadastro:
    - RISCO: 1=Baixo, 2=Medio, 3=Alto/Critico.
    - Se o usuario disser "aqui", "nesta rua" ou "este local" e houver informacao de GPS no contexto, USE o bairro e rua do GPS.
    - Se nao souber a rua exata, coloque "Vias do Bairro".
    - Resumo claro, objetivo e neutro.
    """

  try:
    resposta = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
    )
    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split('|')]
    return partes
  except Exception as e:
    logging.error(f'Erro na chamada Groq: {e}')
    return 'ERRO', str(e), None, None, None, None


# --- HANDLERS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco (Híbrido)*\n\n'
      'Você pode interagir enviando *Texto*, *Áudio* ou *Localização GPS*:\n\n'
      '🔹 *Consulta:* Digite ou fale ex: _"Como tá a Rua Menezes de Avellar?"_\n'
      '🔹 *Cadastro:* Digite ou fale ex: _"Assalto recente no Austin perto do'
      ' posto, risco alto"_\n'
      '📍 *GPS:* Envie sua localização atual para vincular aos relatos ou'
      ' consultas.\n\n'
      'Comandos diretos:\n'
      '• `/consultar [bairro ou rua]`\n'
      '• `/listar` - Exibe últimos alertas'
  )
  await update.message.reply_text(msg, parse_mode='Markdown')


async def receber_localizacao(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  lat = update.message.location.latitude
  lon = update.message.location.longitude

  bairro, rua = obter_endereco_gps(lat, lon)

  context.user_data['lat'] = lat
  context.user_data['lon'] = lon
  context.user_data['bairro'] = bairro
  context.user_data['rua'] = rua

  msg = (
      f'📍 *Localização Identificada!*\n\n'
      f'• *Bairro:* {bairro}\n'
      f'• *Rua/Referência:* {rua}\n\n'
      'Agora escreva uma mensagem ou mande um áudio relatando o risco ou'
      ' fazendo uma consulta sobre este local.'
  )
  await update.message.reply_text(msg, parse_mode='Markdown')


async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  # Identifica se é áudio ou texto digitado
  if update.message.voice:
    await update.message.reply_text(
        '🎧 *Ouvindo áudio...*', parse_mode='Markdown'
    )
    oga_path = f'voice_{update.effective_chat.id}.oga'
    try:
      voice_file = await context.bot.get_file(update.message.voice.file_id)
      await voice_file.download_to_drive(oga_path)

      # Transcrição Whisper Groq
      groq_api_key = os.environ.get('GROQ_API_KEY')
      client = Groq(api_key=groq_api_key)
      with open(oga_path, 'rb') as file:
        transcription = client.audio.transcriptions.create(
            file=(oga_path, file.read()),
            model='whisper-large-v3-turbo',
            language='pt',
            response_format='text',
        )
      texto_entrada = str(transcription).strip()
    except Exception as e:
      logging.error(f'Erro na transcrição de áudio: {e}')
      await update.message.reply_text(
          '❌ Erro ao processar o áudio. Tente novamente.'
      )
      return
    finally:
      if os.path.exists(oga_path):
        try:
          os.remove(oga_path)
        except Exception:
          pass
  else:
    texto_entrada = update.message.text.strip()

  # Contexto do GPS
  contexto_gps = ''
  if 'bairro' in context.user_data and 'rua' in context.user_data:
    contexto_gps = (
        f"O usuário compartilhou GPS localizad em: Bairro"
        f" '{context.user_data['bairro']}', Rua '{context.user_data['rua']}'."
    )

  # Chama o motor da Groq
  dados = analisar_mensagem_com_groq(texto_entrada, contexto_gps)
  tipo_acao = dados[0]

  if tipo_acao == 'CONSULTA':
    termo_busca = dados[1] if len(dados) > 1 else 'Região'

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
      msg = f'✅ Nenhum alerta de risco registrado para *{termo_busca}*.'
      await update.message.reply_text(msg, parse_mode='Markdown')
      if update.message.voice:
        await enviar_resposta_em_audio(
            update, context, f'Nenhum alerta cadastrado para {termo_busca}.'
        )
      return

    resposta_texto = f'🔍 *Alertas encontrados para {termo_busca}:*\n\n'
    texto_audio = f'Alertas encontrados para {termo_busca}. '

    for item in resultados:
      bairro, rua, risco, horario, detalhes = item
      resposta_texto += (
          f'⚠️ *Risco Nível {risco}*\n📍 *Bairro:* {bairro}\n🛣️ *Rua:*'
          f' {rua}\n⏰ *Horário:* {horario}\n📝 *Detalhes:* {detalhes}\n---\n'
      )
      texto_audio += f'No bairro {bairro}, rua {rua}, risco nível {risco}. Detalhes: {detalhes}. '

    await update.message.reply_text(resposta_texto, parse_mode='Markdown')

    # Envia áudio se a pergunta tiver sido por áudio
    if update.message.voice:
      await enviar_resposta_em_audio(update, context, texto_audio)

  elif tipo_acao == 'CADASTRO' and len(dados) >= 6:
    _, bairro, rua, risco_str, horario_critico, detalhes = dados[:6]

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
        f'🤖 *Alerta Cadastrado com Sucesso!*\n\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Rua/Local:* {rua}\n'
        f'⏰ *Horário Crítico:* {horario_critico}\n'
        f'⚠️️ *Nível de Risco:* {risco}\n'
        f'📝 *Detalhes:* {detalhes}'
    )

    await update.message.reply_text(msg_sucesso, parse_mode='Markdown')

    if update.message.voice:
      fala_confirmacao = (
          f'Alerta salvo com sucesso. Bairro {bairro}, local {rua}. Nível de'
          f' risco {risco}.'
      )
      await enviar_resposta_em_audio(update, context, fala_confirmacao)

  else:
    await update.message.reply_text(
        '⚠️ Não entendi a mensagem. Você pode enviar uma pergunta ou um relato'
        ' citando o bairro/rua por texto ou áudio.'
    )


async def consultar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f'✅ Nenhum alerta cadastrado para *{termo}*.', parse_mode='Markdown'
    )
    return

  resposta = f'🔍 *Alertas encontrados para {termo}:*\n\n'
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    resposta += (
        f'⚠️ *Risco Nível {risco}*\n📍 *Bairro:* {bairro}\n🛣️ *Local:*'
        f' {rua}\n⏰ *Horário:* {horario}\n📝 *Detalhes:* {detalhes}\n---\n'
    )

  await update.message.reply_text(resposta, parse_mode='Markdown')


async def listar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def lidar_com_erros(update: object, context: ContextTypes.DEFAULT_TYPE):
  logging.error(f'Exceção no bot: {context.error}')


# --- EXECUÇÃO PRINCIPAL ---
def rodar_bot():
  telegram_token = os.environ.get('TELEGRAM_TOKEN')
  if not telegram_token:
    logging.error('TELEGRAM_TOKEN ausente.')
    return

  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)

  app_bot = ApplicationBuilder().token(telegram_token).build()

  # Evita travamento por exceções
  app_bot.add_error_handler(lidar_com_erros)

  loop.run_until_complete(
      app_bot.bot.delete_webhook(drop_pending_updates=True)
  )

  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('consultar', consultar_cmd))
  app_bot.add_handler(CommandHandler('listar', listar_cmd))
  app_bot.add_handler(MessageHandler(filters.LOCATION, receber_localizacao))

  # Handler unificado para Texto e Áudio
  app_bot.add_handler(
      MessageHandler(
          filters.TEXT | filters.VOICE & ~filters.COMMAND, processar_mensagem
      )
  )

  logging.info('Polling ativo no modo Híbrido!')
  app_bot.run_polling(drop_pending_updates=True, stop_signals=None)


# --- INICIALIZAÇÃO ---
iniciar_banco()

t_bot = threading.Thread(target=rodar_bot, daemon=True)
t_bot.start()

if __name__ == '__main__':
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)
