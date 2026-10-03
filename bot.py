# --- PROCESSAMENTO GROQ INTELIGENTE ---
def processar_relato_com_groq(caminho_audio):
  groq_api_key = os.environ.get('GROQ_API_KEY')
  if not groq_api_key:
    logging.error('GROQ_API_KEY ausente.')
    return 'ERRO', 'Chave de API não configurada.', None, None, None, None

  client = Groq(api_key=groq_api_key)

  try:
    # 1. Transcrição com Whisper
    with open(caminho_audio, 'rb') as file:
      transcription = client.audio.transcriptions.create(
          file=(caminho_audio, file.read()),
          model='whisper-large-v3-turbo',
          language='pt',
          response_format='text',
      )

    texto_transcrito = str(transcription).strip()

    # 2. Prompt com detecção de intenção (Cadastrar x Consultar)
    prompt = f"""
        Você é um assistente de risco viário para entregadores em Nova Iguaçu e Baixada Fluminense.
        Analise a transcrição abaixo e identifique a INTENÇÃO do usuário.

        Texto falado: "{texto_transcrito}"

        A intenção é CONSULTAR (perguntar sobre a segurança de um local) ou CADASTRAR (relatar um perigo/assalto/risco que ocorreu)?

        Responda ESTRITAMENTE em um dos dois formatos abaixo:

        Se for CONSULTA:
        CONSULTA | NOME_DO_BAIRRO_OU_RUA | {texto_transcrito}

        Se for CADASTRO de risco:
        CADASTRO | BAIRRO | RUA | RISCO (1, 2 ou 3) | HORARIO_CRITICO | DETALHES | {texto_transcrito}

        Regras para Cadastro:
        - RISCO: 1=Baixo, 2=Médio, 3=Alto/Crítico.
        - Se não citar rua no cadastro, use "Todo o Bairro / Vias de Acesso".
        - Mantenha o resumo neutro e direto sem gírias.
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


# --- HANDLER DE ÁUDIO ATUALIZADO ---
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

    # --- CASO 1: O USUÁRIO QUER CONSULTAR UM LOCAL POR ÁUDIO ---
    if tipo_acao == 'CONSULTA':
      termo_busca = dados[1]
      texto_fala_original = dados[2]

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
            f'✅ Nenhum alerta de risco cadastrado para *{termo_busca}* até o'
            ' momento.'
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
        await enviar_resposta_em_audio(
            update,
            context,
            f'Nenhum alerta cadastrado para {termo_busca}. Local aparentemente sem registros.',
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
            f'⚠️ *Risco Nível {risco}*\n📍 *Bairro:* {bairro}\n🛣️ *Rua:*'
            f' {rua}\n⏰ *Horário:* {horario}\n📝 *Detalhes:* {detalhes}\n---\n'
        )
        texto_audio += f'No bairro {bairro}, local {rua}, risco nível {risco}. Detalhes: {detalhes}. '

      await update.message.reply_text(resposta_texto, parse_mode='Markdown')
      await enviar_resposta_em_audio(update, context, texto_audio)

    # --- CASO 2: O USUÁRIO ESTÁ CADASTRANDO UM RISCO NOVO ---
    elif tipo_acao == 'CADASTRO' and len(dados) >= 6:
      _, bairro, rua, risco_str, horario_critico, detalhes, texto_transcrito = (
          dados[:7]
      )

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
