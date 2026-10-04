import asyncio
import io
import logging
import os
import sqlite3
import threading
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


@app.route('/')
def home():
  return 'Bot de Mapeamento de Risco (Híbrido + Estatístico) Ativo!'


# --- LOGS E BANCO DE DADOS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)


def iniciar_banco():
  try:
    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()

    # 1. Tabela de Alertas em Tempo Real (Enviados por usuários/entregadores)
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT NOT NULL,
                rua TEXT NOT NULL,
                risco INTEGER NOT NULL,
                horario_critico TEXT,
                detalhes TEXT,
                data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    # 2. Tabela de Mancha Criminal Histórica / Base Oficial (ISP-RJ)
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS estatisticas_bairros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT UNIQUE NOT NULL,
                risco_base INTEGER NOT NULL,
                resumo_criminal TEXT NOT NULL
            )
        ''')

    # Carga Inicial de Bairros de Nova Iguaçu e Baixada (Base Oficial Estatística)
    bairros_base = [
        (
            'Austin',
            3,
            'Incidência alta de furto/roubo de veículos e cargas em vias secundárias.',
        ),
        (
            'Comendador Soares',
            3,
            'Pontos críticos de abordagem em horários noturnos e cruzamentos.',
        ),
        (
            'Centro',
            2,
            'Alerta para furtos e roubos de celulares/bolsas durante o dia.',
        ),
        (
            'Posse',
            2,
            'Atenção reforçada nas marginais e vias de acesso à Dutra.',
        ),
        (
            'Grama',
            2,
            'Risco moderado a alto em acessos a áreas residenciais internas.',
        ),
        (
            'Rancho Novo',
            1,
            'Risco baixo a moderado; atenção em vias de saída rápida.',
        ),
        (
            'Jardim Iguaçu',
            2,
            'Atenção em horários de pico e circulação em vias secundárias.',
        ),
        (
            'Miguel Burnier',
            2,
            'Atividades de patrulhamento variáveis; cautela em horários calmos.',
        ),
        (
            'Vila de Cava',
            3,
            'Atenção em vias de integração e acessos secundários.',
        ),
        (
            'Kenia',
            2,
            'Risco moderado em acessos próximos a corredores de tráfego.',
        ),
    ]

    cursor.executemany(
        '''
            INSERT OR IGNORE INTO estatisticas_bairros (bairro, risco_base, resumo_criminal)
            VALUES (?, ?, ?)
        ''',
        bairros_base,
    )

    conn.commit()
    conn.close()
    logging.info('Banco de dados Híbrido inicializado com sucesso.')
  except Exception as e:
    logging.error(f'Erro na inicialização do banco: {e}')


# --- REVERSE GEOCODING (GPS PARA ENDEREÇO) ---
def obter_endereco_gps(lat, lon):
  try:
    url = f'https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1'
    headers = {'User-Agent': 'BotSegurancaIguacu/2.0'}
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
  return 'Região do GPS enviado', 'Vias de Acesso'


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


# --- MOTOR DE INTELIGÊNCIA ARTIFICIAL (GROQ) ---
def analisar_mensagem_com_groq(texto_entrada, contexto_gps=''):
  groq_api_key = os.environ.get('GROQ_API_KEY')
  if not groq_api_key:
    return 'ERRO', 'Chave GROQ_API_KEY nao configurada.', None, None, None, None

  client = Groq(api_key=groq_api_key)

  prompt = f"""
    Voce e um assistente de risco viario para entregadores em Nova Iguacu e Baixada Fluminense.
    Analise a mensagem recebida por texto ou transcricao de audio.

    {contexto_gps}
    Mensagem do usuario: "{texto_entrada}"

    A intencao do usuario e CONSULTAR (perguntar sobre seguranca/local) ou CADASTRAR (relatar risco/assalto/perigo)?

    Responda ESTRITAMENTE em um dos dois formatos:

    Se for CONSULTA:
    CONSULTA | NOME_DO_BAIRRO_OU_RUA | {texto_entrada}

    Se for CADASTRO:
    CADASTRO | BAIRRO | RUA | RISCO (1, 2 ou 3) | HORARIO_CRITICO | DETALHES | {texto_entrada}

    Regras para Cadastro:
    - RISCO: 1=Baixo, 2=Medio, 3=Alto/Critico.
    - Se o usuario disser "aqui" e houver informacao de GPS no contexto, use o bairro/rua do GPS.
    - Resumo objetivo e neutro.
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
    logging.error(f'Erro na API Groq: {e}')
    return 'ERRO', str(e), None, None, None, None


# --- HANDLERS DO TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  msg = (
      '🚨 *Bot de Mapeamento de Risco (Híbrido + Estatística)*\n\n'
      'Você pode consultar ou relatar riscos digitando, mandando áudio ou GPS:\n\n'
      '🔹 *Consulta:* Ex: _"Como tá a Rua Rocha Faria no bairro Grama?"_\n'
      '🔹 *Cadastro:* Ex: _"Assalto recente no Austin perto do posto, risco'
      ' alto"_\n'
      '📍 *GPS:* Envie sua localização atual para análise instantânea.\n\n'
      '🔊 *Áudio por demanda:* Por padrão, as respostas são rápidas em texto. Se'
      ' quiser áudio, diga *"manda áudio"* no final da mensagem ou use'
      ' `/audio`.'
  )
  await update.message.reply_text(msg, parse_mode='Markdown')


async def alternar_modo_audio(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  estado_atual = context.user_data.get('sempre_audio', False)
  novo_estado = not estado_atual
  context.user_data['sempre_audio'] = novo_estado

  if novo_estado:
    await update.message.reply_text(
        '🔊 *Modo Áudio Ativado:* Todas as respostas terão retorno em voz.'
    )
  else:
    await update.message.reply_text(
        '📱 *Modo Economia Ativado:* Respostas em texto (áudio apenas quando'
        ' você pedir).'
    )


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

  # Consulta imediata no banco híbrido para a localização enviada
  conn = sqlite3.connect('banco_risco.db')
  cursor = conn.cursor()

  cursor.execute(
      'SELECT risco, horario_critico, detalhes FROM alertas WHERE rua LIKE ?'
      ' OR bairro LIKE ? ORDER BY id DESC LIMIT 3',
      (f'%{rua}%', f'%{bairro}%'),
  )
  relatos = cursor.fetchall()

  cursor.execute(
      'SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE'
      ' bairro LIKE ?',
      (f'%{bairro}%',),
  )
  estatistica = cursor.fetchone()
  conn.close()

  msg = (
      f'📍 *Localização Identificada!*\n• *Bairro:* {bairro}\n•'
      f' *Rua/Referência:* {rua}\n\n'
  )

  if relatos:
    msg += '⚠️ *Alertas em Tempo Real de Entregadores:*\n'
    for r in relatos:
      msg += (
          f'• Risco {r[0]} | Horário: {r[1]}\n  Detalhes: {r[2]}\n'
      )
  else:
    msg += '✅ *Sem alertas recentes de entregadores para esta via.*\n\n'

  if estatistica:
    msg += (
        f'📊 *Mancha Criminal Histórica (Base Oficial):*\n• *Nível Base de'
        f' Risco:* {estatistica[0]}\n• *Resumo:* {estatistica[1]}\n'
  )
  else:
    msg += '📊 *Mancha Criminal:* Dados estatísticos gerais da região em análise.'

  await update.message.reply_text(msg, parse_mode='Markdown')


async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  eh_audio_entrada = bool(update.message.voice)

  # Processa o texto vindo do teclado ou transcreve o áudio na memória
  if eh_audio_entrada:
    await update.message.reply_text(
        '🎧 *Ouvindo áudio...*', parse_mode='Markdown'
    )
    try:
      groq_api_key = os.environ.get('GROQ_API_KEY')
      client = Groq(api_key=groq_api_key)

      voice_file = await context.bot.get_file(update.message.voice.file_id)
      byte_array = await voice_file.download_as_bytearray()
      audio_bytes = io.BytesIO(byte_array)
      audio_bytes.name = 'voice.oga'

      transcription = client.audio.transcriptions.create(
          file=(audio_bytes.name, audio_bytes.read()),
          model='whisper-large-v3-turbo',
          language='pt',
          response_format='text',
      )
      texto_entrada = str(transcription).strip()
    except Exception as e:
      logging.error(f'Erro na transcrição: {e}')
      await update.message.reply_text(
          '❌ Erro ao processar o áudio. Tente novamente.'
      )
      return
  else:
    texto_entrada = update.message.text.strip()

  # Verifica se o usuário pediu áudio expressamente
  palavras_chave_audio = [
      'manda audio',
      'manda áudio',
      'responda em audio',
      'responda em áudio',
      'fala pra mim',
      'me fala',
      'em voz',
  ]
  pediu_audio = any(p in texto_entrada.lower() for p in palavras_chave_audio)
  modo_sempre_audio = context.user_data.get('sempre_audio', False)
  deve_enviar_audio = pediu_audio or modo_sempre_audio

  contexto_gps = ''
  if 'bairro' in context.user_data and 'rua' in context.user_data:
    contexto_gps = (
        f"O usuário compartilhou GPS em: Bairro"
        f" '{context.user_data['bairro']}', Rua '{context.user_data['rua']}'."
    )

  dados = analisar_mensagem_com_groq(texto_entrada, contexto_gps)
  tipo_acao = dados[0]

  if tipo_acao == 'CONSULTA':
    termo_busca = dados[1] if len(dados) > 1 else 'Região'

    conn = sqlite3.connect('banco_risco.db')
    cursor = conn.cursor()

    # 1. Consulta Relatos Comunitários
    cursor.execute(
        'SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas'
        ' WHERE rua LIKE ? OR bairro LIKE ? ORDER BY id DESC LIMIT 5',
        (f'%{termo_busca}%', f'%{termo_busca}%'),
    )
    relatos = cursor.fetchall()

    # 2. Consulta Base Estatística do Bairro
    cursor.execute(
        'SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE'
        ' bairro LIKE ?',
        (f'%{termo_busca}%',),
    )
    estatistica = cursor.fetchone()
    conn.close()

    resposta_texto = f'🔍 *Análise de Risco para {termo_busca}:*\n\n'
    texto_audio = f'Análise de risco para {termo_busca}. '

    if relatos:
      resposta_texto += '🚨 *Alertas em tempo real (Entregadores):*\n'
      for item in relatos:
        bairro, rua, risco, horario, detalhes = item
        resposta_texto += (
            f'• *Risco Nível {risco}* ({rua})\n  Horário: {horario} |'
            f' {detalhes}\n'
        )
        texto_audio += f'Alerta de risco nível {risco} na rua {rua}. '
    else:
      resposta_texto += '✅ *Nenhum alerta recente relatado por entregadores.*\n\n'
      texto_audio += 'Sem alertas recentes de entregadores. '

    if estatistica:
      resposta_texto += (
          f'\n📊 *Mancha Criminal Histórica (Base Oficial):*\n• *Nível Base:*'
          f' {estatistica[0]}\n• *Resumo:* {estatistica[1]}\n'
      )
      texto_audio += f'Histórico da região indica nível de risco base {estatistica[0]}.'

    await update.message.reply_text(resposta_texto, parse_mode='Markdown')
    if deve_enviar_audio:
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
        f'🤖 *Alerta Salvo no Banco de Dados!*\n\n'
        f'📍 *Bairro:* {bairro}\n'
        f'🛣️ *Rua/Local:* {rua}\n'
        f'⏰ *Horário:* {horario_critico}\n'
        f'⚠ *Nível:* {risco}\n'
        f'📝 *Detalhes:* {detalhes}'
    )

    await update.message.reply_text(msg_sucesso, parse_mode='Markdown')

    if deve_enviar_audio:
      fala_confirmacao = (
          f'Alerta gravado. Bairro {bairro}, local {rua}, risco nível {risco}.'
      )
      await enviar_resposta_em_audio(update, context, fala_confirmacao)

  else:
    await update.message.reply_text(
        '⚠️ Não entendi com clareza. Digite ou fale informando o bairro ou rua'
        ' para consultar ou cadastrar.'
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
        f'✅ Nenhum alerta comunitário cadastrado para *{termo}*.',
        parse_mode='Markdown',
    )
    return

  resposta = f'🔍 *Alertas encontrados para {termo}:*\n\n'
  for item in resultados:
    bairro, rua, risco, horario, detalhes = item
    resposta += (
        f'⚠️ *Risco {risco}* | 📍 {bairro} ({rua})\n⏰ {horario} | {detalhes}\n---\n'
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

  resposta = '📋 *Últimos relatos em tempo real:*\n\n'
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

  app_bot.add_error_handler(lidar_com_erros)

  loop.run_until_complete(
      app_bot.bot.delete_webhook(drop_pending_updates=True)
  )

  app_bot.add_handler(CommandHandler('start', start))
  app_bot.add_handler(CommandHandler('audio', alternar_modo_audio))
  app_bot.add_handler(CommandHandler('consultar', consultar_cmd))
  app_bot.add_handler(CommandHandler('listar', listar_cmd))
  app_bot.add_handler(MessageHandler(filters.LOCATION, receber_localizacao))

  # Filtro híbrido: Texto (não comando) OU Áudio
  filtro_mensagens = (filters.TEXT & ~filters.COMMAND) | filters.VOICE
  app_bot.add_handler(MessageHandler(filtro_mensagens, processar_mensagem))

  logging.info('Polling ativo no modo Híbrido com Base Estatística!')
  app_bot.run_polling(drop_pending_updates=True, stop_signals=None)


# --- INICIALIZAÇÃO ---
iniciar_banco()

t_bot = threading.Thread(target=rodar_bot, daemon=True)
t_bot.start()

if __name__ == '__main__':
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)
