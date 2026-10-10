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

        # Download do áudio do Telegram
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        await voice_file.download_to_drive(caminho_temp)

        # Envio estruturado com nome de arquivo explícito para a Groq
        with open(caminho_temp, "rb") as file_to_transcribe:
          transcription = client.audio.transcriptions.create(
              file=("voice.ogg", file_to_transcribe, "audio/ogg"),
              model="whisper-large-v3-turbo",
              language="pt",
              response_format="text",
          )

        # Conversão limpa para texto
        if hasattr(transcription, "text"):
          texto_entrada = transcription.text.strip()
        else:
          texto_entrada = str(transcription).strip()

        logging.info(f"Áudio transcrito: {texto_entrada}")

      except Exception as e:
        logging.error(f"Erro no Whisper/Groq: {e}")
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
