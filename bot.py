async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  try:
    if update.message.voice:
      await update.message.reply_text(
          "⚠️ Processamento de áudio temporariamente desativado. Por favor, digite ou envie o GPS."
      )
      return

    texto_entrada = update.message.text.strip()
    texto_lower = texto_entrada.lower()

    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()

    # --- SE FOR CADASTRO ---
    if "cadastrar" in texto_lower or "registrar" in texto_lower:
      # Valores padrão robustos para a Estrada da Grama
      bairro = "Grama"
      rua = "Estrada da Grama"
      risco = 2
      horario = "Após as 17h"
      detalhes = texto_entrada

      # Extração simples de risco se houver
      if "risco 3" in texto_lower or "nível 3" in texto_lower:
        risco = 3
      elif "risco 1" in texto_lower or "nível 1" in texto_lower:
        risco = 1

      cursor.execute(
          "INSERT INTO alertas (bairro, rua, risco, horario_critico, detalhes)"
          " VALUES (?, ?, ?, ?, ?)",
          (bairro, rua, risco, horario, detalhes),
      )
      conn.commit()
      conn.close()

      await update.message.reply_text(
          f"🤖 *Alerta Cadastrado com Sucesso!*\n\n"
          f"📍 *Bairro:* {bairro}\n"
          f"🛣️ *Rua:* {rua}\n"
          f"⚠ *Risco:* Nível {risco}\n"
          f"⏰ *Horário:* {horario}\n"
          f"📝 *Detalhes:* {detalhes}",
          parse_mode="Markdown",
      )

    # --- SE FOR CONSULTA ---
    else:
      # Limpa palavras comuns de comando para buscar apenas o local
      termo_busca = (
          texto_lower.replace("consultar", "")
          .replace("estrada do", "")
          .replace("estrada da", "")
          .replace("rua", "")
          .replace("bairro", "")
          .strip()
      )

      if not termo_busca:
        termo_busca = texto_entrada

      cursor.execute(
          "SELECT bairro, rua, risco, horario_critico, detalhes FROM alertas"
          " WHERE rua LIKE ? OR bairro LIKE ? ORDER BY id DESC LIMIT 5",
          (f"%{termo_busca}%", f"%{termo_busca}%"),
      )
      relatos = cursor.fetchall()

      cursor.execute(
          "SELECT risco_base, resumo_criminal FROM estatisticas_bairros WHERE"
          " bairro LIKE ? OR resumo_criminal LIKE ?",
          (f"%{termo_busca}%", f"%{termo_busca}%"),
      )
      estatistica = cursor.fetchone()
      conn.close()

      resposta = f"🔍 *Análise para '{texto_entrada}':*\n\n"
      if relatos:
        resposta += "🚨 *Alertas Recentes (Entregadores):*\n"
        for item in relatos:
          resposta += (
              f"• *Risco {item[2]}* em {item[1]} ({item[0]})\n  ⏰ {item[3]} |"
              f" {item[4]}\n"
          )
      else:
        resposta += "✅ *Nenhum alerta recente cadastrado para este termo.*\n\n"

      if estatistica:
        resposta += (
            f"📊 *Mancha Criminal (Oficial):* Risco Base {estatistica[0]} -"
            f" {estatistica[1]}\n"
        )
      else:
        resposta += "📊 *Mancha Criminal:* Sem ocorrências oficiais registradas para este termo exato."

      await update.message.reply_text(resposta, parse_mode="Markdown")

  except Exception as e:
    logging.error(f"Erro em processar_mensagem: {e}")
    await update.message.reply_text(
        "❌ Ocorreu um erro ao processar sua mensagem."
    )
