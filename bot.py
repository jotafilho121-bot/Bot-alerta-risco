# --- GEOLOCALIZAÇÃO ROBUSTA COM LEITURA DE DISPLAY_NAME ---
def obter_endereco_gps(lat, lon):
  try:
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
    headers = {"User-Agent": "BotSegurancaIguacu/2.0"}
    res = requests.get(url, headers=headers, timeout=5)

    if res.status_code == 200:
      dados_json = res.json()
      dados_addr = dados_json.get("address", {})
      display_name = dados_json.get("display_name", "")
      
      # Tenta pegar pelas chaves estruturadas padrão
      rua = (
          dados_addr.get("road")
          or dados_addr.get("pedestrian")
          or dados_addr.get("footway")
          or dados_addr.get("path")
          or dados_addr.get("residential")
          or dados_addr.get("hamlet")
      )
      
      bairro = (
          dados_addr.get("suburb")
          or dados_addr.get("neighbourhood")
          or dados_addr.get("quarter")
          or dados_addr.get("city_district")
          or dados_addr.get("town")
      )

      # SALVA-VIDAS: Se o campo estruturado da rua veio vazio, pega a 1ª parte do texto completo do mapa
      if (not rua or rua == "Via Próxima") and display_name:
        partes = display_name.split(",")
        if len(partes) > 0:
          rua = partes[0].strip()
        if len(partes) > 1 and not bairro:
          bairro = partes[1].strip()

      return bairro or "Nova Iguaçu", rua or "Via Próxima"
  except Exception as e:
    logging.error(f"Erro no geocoding: {e}")
  return "Nova Iguaçu", "Via Próxima"
