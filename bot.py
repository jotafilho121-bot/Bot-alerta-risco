# --- INTELIGÊNCIA ARTIFICIAL (GROQ) COM DETECÇÃO DIRETA ---
def analisar_mensagem_com_groq(texto_entrada):
  groq_api_key = os.environ.get("GROQ_API_KEY")
  
  # Verificação rápida por texto (se começar com cadastrar, já força o cadastro)
  texto_lower = texto_entrada.lower()
  if "cadastrar" in texto_lower or "registrar" in texto_lower or "alerta" in texto_lower:
    # Tenta extrair o básico de forma inteligente se a IA falhar
    pass

  if not groq_api_key:
    return ["CONSULTA", texto_entrada]

  try:
    client = Groq(api_key=groq_api_key)
    prompt = f"""
        Analise a frase do entregador e decida se é CONSULTA ou CADASTRO.
        Frase: "{texto_entrada}"

        Se o usuário quiser salvar ou relatar um perigo, o formato OBRIGATÓRIO de resposta é:
        CADASTRO | bairro | rua | risco_1_a_3 | horario | detalhes

        Se for apenas uma pergunta ou busca de local, o formato é:
        CONSULTA | termo_de_busca

        Responda ESTRITAMENTE em uma linha usando '|', sem textos adicionais.
        """

    resposta = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.1,
    )
    resultado = resposta.choices[0].message.content.strip()
    partes = [p.strip() for p in resultado.split("|")]
    return partes
  except Exception as e:
    logging.error(f"Erro Groq: {e}")
    if "cadastrar" in texto_lower or "registrar" in texto_lower:
      return ["CADASTRO", "Grama", "Estrada da Grama", "2", "Após 17h", texto_entrada]
    return ["CONSULTA", texto_entrada]


async def processar_mensagem(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
  try:
    if update.message.voice:
      await update.message.reply_text(
          "⚠️ Processamento de áudio temporariamente desativado. Por favor, digite sua consulta ou envie o GPS."
      )
      return
    
    texto_entrada = update.message.text.strip()
    dados = analisar_mensagem_com_groq(texto_entrada)
    
    tipo_acao = dados[0].upper() if len(dados) > 0 else "CONSULTA"

    if tipo_acao == "CADASTRO":
      # Se a IA retornou o formato de cadastro mas faltaram pedaços, preenchemos com padrões
      bairro = dados[1] if len(dados) > 1 and dados[1] else "Grama"
      rua = dados[2] if len(dados) > 2 and dados[2] else "Estrada da Grama"
      
      risco_raw = dados[3] if len(dados) > 3 and dados[3] else "2"
      try:
        risco = int("".join(filter(str.isdigit, risco_raw)))
      except ValueError:
        risco = 2

      horario = dados[4] if len(dados) > 4 and dados[4] else "Após 17h"
      detalhes = dados[5] if len(dados) > 5 and dados[5] else texto_entrada

      conn = sqlite3.connect("banco_risco.db")
      cursor = conn.cursor()
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
          parse_output="Markdown" if hasattr(Update, 'Markdown') else "Markdown"
      )

    else:
      termo_busca = dados[1] if len(dados) > 1 else texto_entrada
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

      resposta = f"🔍 *Análise para '{termo_busca}':*\n\n"
      if relatos:
        resposta += "🚨 *Alertas Recentes (Entregadores):*\n"
        for item in relatos:
          resposta += (
              f"• *Risco {item[2]}* em {item[1]} ({item[0]})\n  ⏰ {item[3]} | {item[4]}\n"
          )
      else:
        resposta += "✅ *Nenhum alerta recente registrado nesta área.*\n\n"

      if estatistica:
        resposta += (
            f"📊 *Mancha Criminal (Oficial):* Risco Base {estatistica[0]} -"
            f" {estatistica[1]}\n"
        )

      await update.message.reply_text(resposta, parse_mode="Markdown")

  except Exception as e:
    logging.error(f"Erro em processar_mensagem: {e}")
    await update.message.reply_text("❌ Ocorreu um erro ao processar sua mensagem.")
