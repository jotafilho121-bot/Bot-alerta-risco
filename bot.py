async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  eh_audio_entrada = bool(update.message.voice)

  if eh_audio_entrada:
    await update.message.reply_text(
        "🎧 *Ouvindo áudio...*", parse_mode="Markdown"
    )
    try:
      groq_api_key = os.environ.get("GROQ_API_KEY")
      client = Groq(api_key=groq_api_key)

      voice_file = await context.bot.get_file(update.message.voice.file_id)
      byte_array = await voice_file.download_as_bytearray()

      # Criação do buffer em memória configurado corretamente
      audio_bytes = io.BytesIO(byte_array)
      audio_bytes.name = "voice.oga"

      transcription = client.audio.transcriptions.create(
          file=audio_bytes,
          model="whisper-large-v3-turbo",
          language="pt",
          response_format="text",
      )

      # Trata o retorno caso venha como objeto ou string direta
      if hasattr(transcription, "text"):
        texto_entrada = transcription.text.strip()
      else:
        texto_entrada = str(transcription).strip()

      logging.info(f"Transcrição realizada com sucesso: {texto_entrada}")

    except Exception as e:
      logging.error(f"Erro detalhado na transcrição de áudio: {e}")
      await update.message.reply_text(
          "❌ Erro ao processar o áudio. Tente novamente."
      )
      return
  else:
    texto_entrada = update.message.text.strip()

  palavras_chave_audio = [
      "manda audio",
      "manda áudio",
      "responda em audio",
      "responda em áudio",
      "fala pra mim",
      "me fala",
      "em voz",
  ]
  pediu_audio = any(p in texto_entrada.lower() for p in palavras_chave_audio)
  modo_sempre_audio = context.user_data.get("sempre_audio", False)
  deve_enviar_audio = pediu_audio or modo_sempre_audio

  contexto_gps = ""
  if "bairro" in context.user_data and "rua" in context.user_data:
    contexto_gps = (
        f"O usuário compartilhou GPS em: Bairro"
        f" '{context.user_data['bairro']}', Rua '{context.user_data['rua']}'."
    )

  dados = analisar_mensagem_com_groq(texto_entrada, contexto_gps)
  tipo_acao = dados[0]

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

    resposta_texto = f"🔍 *Análise de Risco para {termo_busca}:*\n\n"
    texto_audio = f"Análise de risco para {termo_busca}. "

    if relatos:
      resposta_texto += "🚨 *Alertas em tempo real (Entregadores):*\n"
      for item in relatos:
        bairro, rua, risco, horario, detalhes = item
        resposta_texto += (
            f"• *Risco Nível {risco}* ({rua})\n  Horário: {horario} |"
            f" {detalhes}\n"
        )
        texto_audio += f"Alerta de risco nível {risco} na rua {rua}. "
    else:
      resposta_texto += (
          "✅ *Nenhum alerta recente relatado por entregadores.*\n\n"
      )
      texto_audio += "Sem alertas recentes de entregadores. "

    if estatistica:
      resposta_texto += (
          f"\n📊 *Mancha Criminal Histórica (Base Oficial):*\n• *Nível Base:*"
          f" {estatistica[0]}\n• *Resumo:* {estatistica[1]}\n"
      )
      texto_audio += (
          f"Histórico da região indica nível de risco base {estatistica[0]}."
      )

    await update.message.reply_text(resposta_texto, parse_mode="Markdown")
    if deve_enviar_audio:
      await enviar_resposta_em_audio(update, context, texto_audio)

  elif tipo_acao == "CADASTRO" and len(dados) >= 6:
    _, bairro, rua, risco_str, horario_critico, detalhes = dados[:6]

    try:
      risco = int(risco_str)
    except (ValueError, TypeError):
      risco = 3

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)"
        " VALUES (?, ?, ?, ?, ?)",
        (bairro, rua, risco, horario_critico, detalhes),
    )
    conn.commit()
    conn.close()

    msg_sucesso = (
        f"🤖 *Alerta Salvo no Banco de Dados!*\n\n"
        f"📍 *Bairro:* {bairro}\n"
        f"🛣️ *Rua/Local:* {rua}\n"
        f"⏰ *Horário:* {horario_critico}\n"
        f"⚠ *Nível:* {risco}\n"
        f"📝 *Detalhes:* {detalhes}"
    )

    await update.message.reply_text(msg_sucesso, parse_mode="Markdown")

    if deve_enviar_audio:
      fala_confirmacao = (
          f"Alerta gravado. Bairro {bairro}, local {rua}, risco nível {risco}."
      )
      await enviar_resposta_em_audio(update, context, fala_confirmacao)

  else:
    await update.message.reply_text(
        "⚠️ Não entendi com clareza. Digite ou fale informando o bairro ou rua"
        " para consultar ou cadastrar."
    )
