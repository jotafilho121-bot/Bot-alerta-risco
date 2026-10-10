import asyncio
import logging
import os
import sqlite3
import threading
import time
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


@app.route("/")
def home():
  return "Bot de Mapeamento de Risco Ativo!"


@app.route("/ping")
def ping():
  return "pong", 200


# --- LOGS E BANCO DE DADOS ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def iniciar_banco():
  try:
    conn = sqlite3.connect("banco_risco.db")
    cursor = conn.cursor()

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS alertas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT NOT NULL,
                rua TEXT NOT NULL,
                risco INTEGER NOT NULL,
                horario_critico TEXT,
                detalhes TEXT,
                data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    cursor.execute("""
            CREATE TABLE IF NOT EXISTS estatisticas_bairros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bairro TEXT UNIQUE NOT NULL,
                risco_base INTEGER NOT NULL,
                resumo_criminal TEXT NOT NULL
            )
        """)

    bairros_base = [
        (
            "Austin",
            3,
            "Incidência alta de furto/roubo de veículos e cargas em vias"
            " secundárias.",
        ),
        (
            "Comendador Soares",
            3,
            "Pontos críticos de abordagem em horários noturnos e cruzamentos.",
        ),
        (
            "Centro",
            2,
            "Alerta para furtos e roubos de celulares/bolsas durante o dia.",
        ),
        (
            "Posse",
            2,
            "Atenção reforçada nas marginais e vias de acesso à Dutra.",
        ),
        (
            "Grama",
            2,
            "Risco moderado a alto em acessos a áreas residenciais internas.",
        ),
        (
            "Rancho Novo",
            1,
            "Risco baixo a moderado; atenção em vias de saída rápida.",
        ),
        (
            "Jardim Iguaçu",
            2,
            "Atenção em horários de pico e circulação em vias secundárias.",
        ),
        (
            "Miguel Burnier",
            2,
            "Atividades de patrulhamento variáveis; cautela em horários calmos.",
        ),
        (
            "Vila de Cava",
            3,
            "Atenção em vias de integração e acessos secundários.",
        ),
        (
            "Kenia",
            2,
            "Risco moderado em acessos próximos a corredores de tráfego.",
        ),
        (
            "Miguel Couto",
            2,
            "Atenção em cruzamentos principais e acessos ao Ambaí / Tinguá.",
        ),
        (
            "Ambaí",
            3,
            "Risco elevado em acessos secundários e travessas sentido"
            " comunidades.",
        ),
    ]

    cursor.executemany(
        """
            INSERT OR IGNORE INTO estatisticas_bairros (bairro, risco_base, resumo_criminal)
            VALUES (?, ?, ?)
        """,
        bairros_base,
    )

    conn.commit()
    conn.close()
    logging.info("Banco de dados inicializado.")
  except Exception as e:
    logging.error(f"Erro no banco: {e}")


# --- GEOLOCALIZAÇÃO ROBUSTA ---
def obter_endereco_gps(lat, lon):
  try:
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    headers = {"User-Agent": "BotSegurancaIguacu/2.0"}
    res = requests.get(url, headers=headers, timeout=5)

    if res.status_code == 200:
      dados = res.json().get("address", {})
      rua = (
          dados.get("road")
          or dados.get("pedestrian")
          or dados.get("footway")
          or "Via Próxima"
      )
      bairro = (
          dados.get("suburb")
          or dados.get("neighbourhood")
          or dados.get("quarter")
          or dados.get("city_district")
          or dados.get("town")
          or "Nova Iguaçu"
      )
      return bairro, rua
  except Exception as e:
    logging.error(f"Erro no geocoding: {e}")
  return "Nova Iguaçu", "Via Próxima"


# --- IA PARA EXTRAÇÃO DE CADASTRO ---
def extrair_dados_cadastro_com_groq(texto_entrada):
  groq_api_key = os.environ.get("GROQ_API_KEY")
  if not groq_api_key:
    return "Grama", "Rua Não Informada", 2, "Recente", texto_entrada

  try:
    client = Groq(api_key=groq_api_key)
    prompt = f"""
        Extraia as informações desta frase de cadastro de segurança de entregador em Nova Iguaçu.
        Frase: "{texto_entrada}"

        Responda ESTRITAMENTE em uma única linha no formato exato:
        BAIRRO | RUA | RISCO (apenas número 1, 2 ou 3) | HORARIO | DETALHES

        Exemplo:
        Grama | Rua Cajueiro | 2 | A partir das 17h | Via escura e movimentada
        """

    resposta = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        temperature=0.1,
    )
    resultado = resposta.choices[0].message.content.strip()
    
