import logging
import gspread
from google.oauth2.service_account import Credentials
import requests
import json
import time
import re
import unicodedata
from datetime import datetime
from dateutil.relativedelta import relativedelta
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8303417832:AAG1RWaGUiSgIQ1cPhgdtInqHpraChUMaHU"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

PLANILHA_NOME = "Controle Financeiro"
SENHA = "2213"

usuarios_autenticados = set()

try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open(PLANILHA_NOME).sheet1
    logger.info("✅ Conectado ao Google Sheets com sucesso!")
    if not sheet.get_all_values():
        sheet.append_row(["Data", "Valor", "Meio", "Descrição", "Responsável"])
except Exception:
    logger.exception("❌ Erro ao conectar com Google Sheets:")
    sheet = None

def get_updates(offset=None):
    params = {"timeout": 10}
    if offset:
        params["offset"] = offset
    return requests.get(f"{BASE_URL}/getUpdates", params=params).json()

def send_message(chat_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    return requests.post(f"{BASE_URL}/sendMessage", params=params).json()

def normalize_text(s):
    if not s:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFD", s)
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    s = re.sub(r'[^0-9A-Za-z\s]', '', s)
    return s.strip().lower()

def float_to_br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def parse_valor(valor_str):
    v = valor_str.strip()
    if "," in v:
        partes = v.split(",")
        partes[0] = partes[0].replace(".", "")
        v = ".".join([partes[0]] + partes[1:])
    else:
        v = v.replace(".", "")
    try:
        return float(v)
    except:
        return 0.0

def limpar_conversa(chat_id):
    for _ in range(3):
        send_message(chat_id, "\u200B")

def registrar_despesa(text, chat_id, first_name, last_name=None):
    linhas = [linha.strip() for linha in text.split("\n") if linha.strip()]
    registros_ok = 0
    registros_fail = 0

    for linha in linhas:
        try:
            partes = [x.strip() for x in linha.split("-")]
            if len(partes) < 4:
                registros_fail += 1
                continue

            data_str = partes[0]
            valor_str = partes[1]
            meio = partes[2]

            parcelamento = 1
            descricao = ""
            responsavel = ""

            if len(partes) >= 5 and re.match(r"^\d+x$", partes[3].lower()):
                parcelamento = int(partes[3].lower().replace("x", ""))
                descricao = partes[4]
                responsavel = partes[5] if len(partes) >= 6 else first_name
            else:
                descricao = partes[3]
                responsavel = partes[4] if len(partes) >= 5 else first_name

            if last_name and responsavel == first_name:
                responsavel += f" {last_name}"

            try:
                dt = datetime.strptime(data_str, "%d/%m/%Y")
            except:
                registros_fail += 1
                continue

            try:
                valor_total = float(valor_str.replace(".", "").replace(",", "."))
            except:
                registros_fail += 1
                continue

            if parcelamento > 1:
                valor_parcela = round(valor_total / parcelamento, 2)
                for i in range(parcelamento):
                    dt_parcela = dt + relativedelta(months=i)
                    data_parcela_str = dt_parcela.strftime("%d/%m/%Y")
                    descricao_parcela = f"(Parcela {i+1}/{parcelamento}) {descricao}"
                    if sheet:
                        valor_parcela_str = float_to_br(valor_parcela)
                        sheet.append_row([data_parcela_str, valor_parcela_str, meio, descricao_parcela, responsavel])
                        registros_ok += 1
                        logger.info(f"Registrado parcela: {data_parcela_str} | {valor_parcela_str} | {meio} | {descricao_parcela} | {responsavel}")
            else:
                valor_planilha = float_to_br(valor_total)
                if sheet:
                    sheet.append_row([data_str, valor_planilha, meio, descricao, responsavel])
                    registros_ok += 1
                    logger.info(f"Registrado: {data_str} | {valor_planilha} | {meio} | {descricao} | {responsavel}")
                else:
                    registros_fail += 1
        except Exception:
            registros_fail += 1
            logger.exception("Erro ao registrar despesa:")

    if registros_ok and not registros_fail:
        send_message(chat_id, f"✅ {registros_ok} lançamento(s) registrado(s) com sucesso!")
    elif registros_ok and registros_fail:
        send_message(chat_id, f"⚠️ {registros_ok} registrado(s), {registros_fail} com erro(s) de formato.")
    else:
        send_message(chat_id, "❌ Nenhum lançamento válido encontrado.")

def list_months_from_sheet():
    if sheet is None:
        return []
    try:
        rows = sheet.get_all_values()[1:]
        meses_set = set()
        for r in rows:
            if not r:
                continue
            data_str = r[0]
            try:
                dt = datetime.strptime(data_str, "%d/%m/%Y")
                meses_set.add(datetime(dt.year, dt.month, 1))
            except:
                continue
        meses_sorted = sorted(meses_set, reverse=True)
        return [dt.strftime("%m/%Y") for dt in meses_sorted]
    except Exception:
        logger.exception("Erro ao listar meses:")
        return []

def teclado_inicial(chat_id):
    keyboard = {
        "keyboard": [
            [{"text": "📌 Enviar lançamento"}],
            [{"text": "📊 Resumo filtrado"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    send_message(chat_id, "👋 Escolha uma opção!", reply_markup=keyboard)

def show_months_keyboard(chat_id, months):
    rows = []
    for i in range(0, len(months), 3):
        row = [{"text": m} for m in months[i:i+3]]
        rows.append(row)
    reply_markup = {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}
    send_message(chat_id, "📅 Escolha o mês/ano:", reply_markup=reply_markup)

def show_responsavel_keyboard(chat_id, mes):
    if sheet is None:
        send_message(chat_id, "⚠️ Google Sheets não configurado.")
        return
    rows = sheet.get_all_values()[1:]
    responsaveis_set = set()
    for r in rows:
        try:
            data_str = r[0]
            dt = datetime.strptime(data_str, "%d/%m/%Y")
            row_month = dt.strftime("%m/%Y")
            if row_month == mes:
                responsavel = r[4].strip() if len(r) > 4 else ""
                if responsavel:
                    responsaveis_set.add(responsavel)
        except:
            continue
    responsaveis = sorted(list(responsaveis_set))
    if not responsaveis:
        send_message(chat_id, "⚠️ Nenhum responsável encontrado para esse mês.")
        teclado_inicial(chat_id)
        return
    rows = [[{"text": nome}] for nome in responsaveis]
    rows.append([{ "text": "👥 Todos"}])
    reply_markup = {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}
    send_message(chat_id, f"👤 Escolha o responsável para {mes}:", reply_markup=reply_markup)

def show_meio_keyboard(chat_id, mes):
    rows = [
        [{"text": "Débito"}, {"text": "Pix"}],
        [{"text": "Crédito"}, {"text": "Dinheiro"}],
        [{"text": "Todos"}]
    ]
    reply_markup = {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}
    send_message(chat_id, f"💳 Escolha o meio de pagamento para {mes}:", reply_markup=reply_markup)

def show_summary_grouped(chat_id, mes, responsavel_raw, meio_raw):
    if sheet is None:
        send_message(chat_id, "⚠️ Google Sheets não configurado.")
        teclado_inicial(chat_id)
        return

    rows = sheet.get_all_values()[1:]
    meio_norm = normalize_text(meio_raw)
    responsavel_norm = normalize_text(responsavel_raw)

    dados = {}

    for r in rows:
        try:
            data_str = r[0]
            dt = datetime.strptime(data_str, "%d/%m/%Y")
            row_month = dt.strftime("%m/%Y")
        except:
            continue
        meio_cel = r[2] if len(r) > 2 else ""
        resp_cel = r[4] if len(r) > 4 else ""

        if row_month != mes:
            continue

        if responsavel_raw != "👥 Todos" and normalize_text(resp_cel) != responsavel_norm:
            continue
        if meio_raw != "Todos" and normalize_text(meio_cel) != meio_norm:
            continue

        valor = parse_valor(str(r[1]))

        if resp_cel not in dados:
            dados[resp_cel] = {"Débito": 0.0, "Pix": 0.0, "Crédito": 0.0, "Dinheiro": 0.0}
        meio_key = meio_cel.capitalize()
        if meio_key not in dados[resp_cel]:
            dados[resp_cel][meio_key] = 0.0
        dados[resp_cel][meio_key] += valor

    texto = f"📊 *Resumo {mes}* (agrupado por responsável e meio)\n\n"
    total_geral = 0.0

    for resp, meios in dados.items():
        texto += f"*{resp}*\n"
        subtotal_resp = 0.0
        for meio in ["Débito", "Pix", "Crédito", "Dinheiro"]:
            valor = meios.get(meio, 0.0)
            texto += f"  {meio}: R$ {float_to_br(valor)}\n"
            subtotal_resp += valor
        texto += f"  *Subtotal:* R$ {float_to_br(subtotal_resp)}\n\n"
        total_geral += subtotal_resp

    texto += f"*Total Geral:* R$ {float_to_br(total_geral)}"
    send_message(chat_id, texto)
    teclado_inicial(chat_id)

estado_usuario = {}

def handle_message(update):
    if "message" not in update or "text" not in update["message"]:
        return
    chat_id = update["message"]["chat"]["id"]
    text = update["message"]["text"].strip()
    first_name = update["message"]["from"].get("first_name", "")
    last_name = update["message"]["from"].get("last_name", "")

    if chat_id not in usuarios_autenticados:
        estado = estado_usuario.get(chat_id)
        if not estado:
            send_message(chat_id, "Por favor, envie a senha para acessar o bot.")
            estado_usuario[chat_id] = {"etapa": "aguardando_senha"}
            return

        if estado.get("etapa") == "aguardando_senha":
            if text == SENHA:
                usuarios_autenticados.add(chat_id)
                send_message(chat_id, "✅ Senha correta! Você agora tem acesso ao bot.")
                teclado_inicial(chat_id)
                estado_usuario.pop(chat_id, None)
            else:
                send_message(chat_id, "❌ Senha incorreta. Tente novamente.")
            return

    estado = estado_usuario.get(chat_id)
    if estado:
        etapa = estado.get("etapa")
        if etapa == "mes":
            if not re.match(r"^\d{2}/\d{4}$", text):
                send_message(chat_id, "❌ Formato inválido. Escolha um dos meses apresentados (MM/YYYY).")
                return
            estado_usuario[chat_id] = {"etapa": "responsavel", "mes": text}
            show_responsavel_keyboard(chat_id, text)
            return
        elif etapa == "responsavel":
            mes = estado.get("mes")
            estado_usuario[chat_id]["responsavel"] = text
            estado_usuario[chat_id]["etapa"] = "meio"
            show_meio_keyboard(chat_id, mes)
            return
        elif etapa == "meio":
            mes = estado.get("mes")
            responsavel = estado.get("responsavel")
            estado_usuario.pop(chat_id, None)
            show_summary_grouped(chat_id, mes, responsavel, text)
            return

    if text == "📌 Enviar lançamento":
        send_message(chat_id, "✏️ Digite no formato:\n`07/01/2025 - 45,50 - crédito - descrição - Mateus`\n\nPode enviar várias linhas.")
        return

    if text == "📊 Resumo filtrado":
        months = list_months_from_sheet()
        if not months:
            send_message(chat_id, "📭 Nenhum mês encontrado na planilha.")
            return
        estado_usuario[chat_id] = {"etapa": "mes"}
        show_months_keyboard(chat_id, months)
        return

    registrar_despesa(text, chat_id, first_name, last_name)

# === FLASK PARA KEEP-ALIVE ===
app = Flask('')

@app.route('/')
def home():
    return "Bot online ✅"

@app.route('/ping')
def ping():
    return "pong 🏓"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def main():
    Thread(target=run_flask).start()
    logger.info("🤖 Bot iniciado...")
    offset = None
    while True:
        try:
            result = get_updates(offset)
            if result.get("ok"):
                updates = result.get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    try:
                        handle_message(update)
                    except Exception:
                        logger.exception("Erro ao processar mensagem")
            else:
                logger.error("Resposta do Telegram não OK: %s", result)
                time.sleep(5)
        except Exception:
            logger.exception("Erro no loop principal")
            time.sleep(5)

if __name__ == "__main__":
    main()
