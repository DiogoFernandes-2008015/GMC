import base64
import secrets
import threading
import time
import uuid
import csv
import http.server
import io
import json
import os
import re
import socketserver
import sqlite3
import urllib.parse
import webbrowser
from datetime import date

import pypdf

PORT = 8000
DB_NAME = "patrimonio_se4.db"
UPLOAD_DIR = "fotos_patrimonio"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# =============================================================================
# 🔒 AUTENTICAÇÃO (HTTP Basic Auth)
# =============================================================================
# Usuário e senha exigidos sempre que alguém acessar o IP do servidor.
# É POSSÍVEL sobrescrever via variáveis de ambiente, sem editar o código:
#   Windows (cmd):  set GMC_USER=fulano & set GMC_SENHA=minha_senha & python GMC_html_v4.py
#   Linux/Mac:      GMC_USER=fulano GMC_SENHA=minha_senha python GMC_html_v4.py
# ALTERE os valores padrão abaixo antes de colocar o sistema em uso real.
AUTH_USER = os.environ.get("GMC_USER", "se4")
AUTH_SENHA = os.environ.get("GMC_SENHA", "ime@2026")

if AUTH_SENHA == "ime@2026":
    print(
        "⚠️  Aviso: usando a senha padrão de fábrica. Defina GMC_USER/GMC_SENHA "
        "(variáveis de ambiente) ou altere AUTH_USER/AUTH_SENHA no código."
    )


def _requisicao_autenticada(handler) -> bool:
    """Verifica o cabeçalho 'Authorization: Basic ...' da requisição."""
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        credenciais = base64.b64decode(auth_header[len("Basic "):]).decode("utf-8")
        usuario, _, senha = credenciais.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(usuario, AUTH_USER) and secrets.compare_digest(senha, AUTH_SENHA)


def _solicitar_login(handler):
    """Responde 401 pedindo usuário/senha (o navegador exibe um pop-up nativo)."""
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Patrimonio SE4 - Acesso Restrito"')
    handler.send_header("Content-type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(
        b"<h2>Autenticacao necessaria</h2>"
        b"<p>Informe usuario e senha para acessar o sistema de patrimonio.</p>"
    )

# Importações pesadas são executadas em segundo plano para não bloquear o servidor.
IMPORT_JOBS = {}
IMPORT_JOBS_LOCK = threading.Lock()



def parse_float(val) -> float:
    """Converte valores numéricos (com vírgula ou ponto) para float com segurança."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return 0.0
    if "," in val_str:
        val_str = val_str.replace(".", "").replace(",", ".")
    try:
        return float(val_str)
    except ValueError:
        return 0.0


# =============================================================================
# ⚙️ CONFIGURAÇÃO DE OPÇÕES DOS MENUS SUSPENSOS
# =============================================================================
LOCAIS_DISPONIVEIS = [
    "Lab Motores",
    "Lab Projetos Mecânicos",
    "Lab Aerodinâmica",
    "Lab Canhoneio",
    "Lab IDR",
    "LAN",
    "Lab Armamento",
    "Lab Construção Mecânica",
    "Sala dos Professores",
    "Sala 4018",
    "Sala 4026",
    "Sala 4028",
    "Sala 4029",
    "Sala PGMEC",
    "Material de TI",
]

STATUS_CONFERENCIA_OPTS = [
    "Não conferido",
    "Conferido",
    "Não Encontrado",
    "Fora da SE",
]

SITUACAO_OPTS = [
    "Em carga",
    "Fora de carga",
    "Aguardando transferência",
    "Aguardando alienação",
    "Aguardando recolhimento",
]


# =============================================================================
# 📄 PROCESSAMENTO E EXTRAÇÃO DE PDF (SISCOFIS)
# =============================================================================
def extrair_texto_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extrai texto de um PDF recebido em bytes preservando o layout visual."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    texto_completo = []
    for page in reader.pages:
        texto_pagina = page.extract_text(extraction_mode="layout")
        if texto_pagina:
            texto_completo.append(texto_pagina)
    return "\n".join(texto_completo)


def filtrar_cabecalhos_e_rodapes(texto_bruto: str) -> list[str]:
    """Remove cabeçalhos e rodapés do SISCOFIS.

    Compatível com o layout antigo (colunas: Nome material / Nr Ficha / NEE Mat. /
    Conta Contábil / Acervo / Quantidade / Valor Unitário / Valor Total) e com o
    layout novo (colunas: Nome material / Quantidade / Valor Unitário / Valor Total /
    Nr Ficha / Cod Mat / Conta Contábil / Acervo).
    """
    linhas = texto_bruto.split("\n")
    linhas_limpas = []

    termos_ignorar = [
        "MINISTÉRIO DA DEFESA",
        "EXÉRCITO BRASILEIRO",
        "DEPARTAMENTO DE CIÊNCIA E TECNOLOGIA",
        "INSTITUTO MILITAR DE ENGENHARIA",
        "(REAL ACADEMIA",
        "Página ",
        "RELAÇÃO DE MATERIAL CARGA DA DEPENDÊNCIA",
        "1. MATERIAL PERMANENTE",
        "Nr Ficha NEE Mat.",
        "Nr Ficha Cod Mat",
        "Relação dos patrimônios",
        "Relação emitida pelo SISCOFIS OM",
    ]

    for linha in linhas:
        l_strip = linha.strip()
        if not l_strip:
            continue
        # Linhas de cabeçalho de coluna (repetidas a cada página), em ambos os
        # layouts, sempre começam com "Nome material".
        if l_strip.startswith("Nome material"):
            continue
        if any(termo in l_strip for termo in termos_ignorar):
            continue
        linhas_limpas.append(l_strip)

    return linhas_limpas


def processar_relacao_siscofis(texto_bruto: str) -> list[dict]:
    """Extrai estrutura de dados a partir do texto do PDF, incluindo patrimônios e valores individuais.

    Suporta dois layouts de exportação do SISCOFIS, detectados automaticamente
    linha a linha:

    - Layout ANTIGO (ex.: teste.pdf): colunas na ordem
      Nome | Nr Ficha | NEE Mat. | Conta Contábil | Acervo | Quantidade | Valor Unitário | Valor Total
      e o marcador "Nr Patrimônios:" aparece seguido dos números na mesma linha.

    - Layout NOVO (ex.: teste3.pdf): colunas na ordem
      Nome | Nr Ficha | Cod Mat | Conta Contábil | Quantidade | Valor Unitário | Valor Total | Acervo
      e o marcador "Nr Patrimoniais:" aparece sozinho em uma linha, com os
      números dos patrimônios em linha(s) separada(s) logo abaixo.
    """
    linhas = filtrar_cabecalhos_e_rodapes(texto_bruto)
    itens_extraidos = []

    # Número de ficha: aceita o formato clássico só com dígitos (ex.: "1234"),
    # mas também variações usadas em algumas fichas do SISCOFIS, como
    # "1234A" (dígitos + uma letra) ou "1234/5" (dígitos + "/" + dígitos).
    FICHA_REGEX = r"\d{1,6}(?:[A-Z]|/\d{1,4})?"

    # Layout novo: ...nome... ficha  codmat  conta  qtd  unit  total  acervo
    pattern_linha_nova = re.compile(
        r"^(?P<nome>.*?)\s+(?P<ficha>" + FICHA_REGEX + r")\s+(?P<codmat>[A-Z0-9\.-]+)\s+"
        r"(?P<conta>\d{6,12})\s+(?P<qtd>\d+)\s+(?P<unit>[\d\.,]+)\s+"
        r"(?P<total>[\d\.,]+)\s+(?P<acervo>[A-Z])$"
    )
    # Layout antigo: ...nome... ficha  codmat  conta  acervo  qtd  unit  total
    pattern_linha_antiga = re.compile(
        r"^(?P<nome>.*?)\s+(?P<ficha>" + FICHA_REGEX + r")\s+(?P<codmat>[A-Z0-9\.-]+)\s+"
        r"(?P<conta>\d{6,12})\s+(?P<acervo>[A-Z])\s+(?P<qtd>\d+)\s+"
        r"(?P<unit>[\d\.,]+)\s+(?P<total>[\d\.,]+)$"
    )
    # Marcador de início da lista de patrimônios (aceita ambas as grafias)
    pattern_marcador = re.compile(r"Nr Patrim(?:ônios|oniais):")
    pattern_patrimonio_valor = re.compile(
        r"(\b\d{6,18}\b)\s+(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2})"
    )

    def match_linha_tabela(linha: str):
        """Tenta casar a linha com o layout novo e, em seguida, com o antigo."""
        m = pattern_linha_nova.match(linha)
        if m:
            return m.groupdict()
        m = pattern_linha_antiga.match(linha)
        if m:
            return m.groupdict()
        return None

    i = 0
    total_linhas = len(linhas)

    while i < total_linhas:
        linha = linhas[i]
        dados = match_linha_tabela(linha)

        if dados:
            nome_inicio = dados["nome"].strip()
            ficha = dados["ficha"]
            nee = dados["codmat"]
            conta = dados["conta"]
            acervo = dados["acervo"]
            qtd = int(dados["qtd"])
            val_unit = parse_float(dados["unit"])

            partes_nome = [nome_inicio]
            patrimonios_detalhados = []

            i += 1
            while i < total_linhas:
                curr_line = linhas[i]
                if match_linha_tabela(curr_line):
                    break

                m_marcador = pattern_marcador.search(curr_line)
                if m_marcador:
                    texto_antes = curr_line[: m_marcador.start()].strip()
                    texto_depois = curr_line[m_marcador.end():].strip()

                    if texto_antes:
                        partes_nome.append(texto_antes)

                    sub_linhas = [texto_depois] if texto_depois else []
                    i += 1
                    while i < total_linhas:
                        prox = linhas[i]
                        if match_linha_tabela(prox) or pattern_marcador.search(prox):
                            break
                        if re.search(r"\b\d{6,18}\b", prox):
                            sub_linhas.append(prox.strip())
                            i += 1
                        else:
                            break

                    texto_pat = " ".join(sub_linhas)
                    matches_pv = pattern_patrimonio_valor.findall(texto_pat)

                    if matches_pv:
                        for pat_num, val_str in matches_pv:
                            patrimonios_detalhados.append({
                                "numero": pat_num,
                                "valor": parse_float(val_str),
                            })
                    else:
                        pats_simples = re.findall(r"\b\d{6,18}\b", texto_pat)
                        for p_num in pats_simples:
                            patrimonios_detalhados.append({
                                "numero": p_num,
                                "valor": val_unit,
                            })
                    break
                else:
                    partes_nome.append(curr_line)
                    i += 1

            nome_completo = re.sub(r"\s+", " ", " ".join(partes_nome)).strip()

            itens_extraidos.append({
                "nome_material": nome_completo,
                "nr_ficha": ficha,
                "nee_mat": nee,
                "conta_contabil": conta,
                "acervo": acervo,
                "quantidade": len(patrimonios_detalhados) if patrimonios_detalhados else qtd,
                "valor_unitario": val_unit,
                "patrimonios": patrimonios_detalhados,
            })
        else:
            i += 1

    return itens_extraidos


def sincronizar_pdf_siscofis(itens_pdf: list[dict]) -> dict:
    """Sincroniza a carga com poucas consultas SQL e operações em lote.

    A versão anterior fazia SELECT/UPDATE/INSERT para cada patrimônio. Com
    ~3000 registros isso gera milhares de round-trips ao SQLite. Aqui os dados
    necessários são carregados em memória uma vez e as alterações são aplicadas
    com executemany(), mantendo uma única transação.
    """
    conn = sqlite3.connect(DB_NAME, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        c = conn.cursor()

        mapa_pdf_pat = {}
        for item in itens_pdf:
            for pat in item.get("patrimonios", []):
                pat_num = pat["numero"] if isinstance(pat, dict) else pat
                mapa_pdf_pat[pat_num] = {
                    "item": item,
                    "valor": pat["valor"] if isinstance(pat, dict) else item["valor_unitario"],
                }

        pats_pdf = set(mapa_pdf_pat)

        # Uma única leitura para conhecer a situação atual.
        c.execute("""
            SELECT p.nr_patrimonio, p.id, p.item_id, i.nome_material
            FROM patrimonios p
            LEFT JOIN itens i ON i.id = p.item_id
            WHERE p.status_movimentacao = 'Em carga'
        """)
        atuais = {r[0]: {"id": r[1], "item_id": r[2], "nome": r[3]} for r in c.fetchall()}

        novos = pats_pdf - set(atuais)
        removidos = set(atuais) - pats_pdf
        mantidos = pats_pdf & set(atuais)

        # Também carregamos todos os itens por nome uma única vez.
        c.execute("SELECT id, nome_material FROM itens")
        itens_por_nome = {r[1]: r[0] for r in c.fetchall()}

        # Itens novos: no máximo uma inserção por material distinto.
        itens_a_inserir = {}
        for pat in novos:
            item = mapa_pdf_pat[pat]["item"]
            nome = item["nome_material"]
            if nome not in itens_por_nome and nome not in itens_a_inserir:
                itens_a_inserir[nome] = item

        for item in itens_a_inserir.values():
            c.execute(
                """INSERT INTO itens
                   (nome_material, nr_ficha, nee_mat, conta_contabil, acervo, valor_unitario)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (item["nome_material"], item["nr_ficha"], item["nee_mat"],
                 item["conta_contabil"], item.get("acervo"), item["valor_unitario"]),
            )
            itens_por_nome[item["nome_material"]] = c.lastrowid

        # Atualiza os mantidos em lote.
        c.executemany(
            "UPDATE patrimonios SET valor_individual = ? WHERE nr_patrimonio = ?",
            [(mapa_pdf_pat[p]["valor"], p) for p in mantidos],
        )

        # Um mapa completo evita SELECT individual para cada novo patrimônio.
        c.execute("SELECT id, nr_patrimonio FROM patrimonios")
        todos_pats = {r[1]: r[0] for r in c.fetchall()}

        updates = []
        inserts = []
        for pat in novos:
            info = mapa_pdf_pat[pat]
            item = info["item"]
            item_id = itens_por_nome[item["nome_material"]]
            pat_val = info["valor"]
            if pat in todos_pats:
                updates.append((item_id, pat_val, todos_pats[pat]))
            else:
                inserts.append((item_id, pat, pat_val))

        if updates:
            c.executemany(
                """UPDATE patrimonios
                   SET item_id = ?, status_movimentacao = 'Em carga', valor_individual = ?
                   WHERE id = ?""",
                updates,
            )
        if inserts:
            c.executemany(
                """INSERT INTO patrimonios
                   (item_id, nr_patrimonio, status_conferencia, status_movimentacao, valor_individual)
                   VALUES (?, ?, 'Não conferido', 'Em carga', ?)""",
                inserts,
            )

        # Uma única operação para marcar os ausentes como fora de carga.
        if removidos:
            c.executemany(
                "UPDATE patrimonios SET status_movimentacao = 'Fora de carga' WHERE nr_patrimonio = ?",
                [(p,) for p in removidos],
            )

        conn.commit()

        lista_incluidos = [
            f"Patrimônio {p} - {mapa_pdf_pat[p]['item']['nome_material']} (R$ {mapa_pdf_pat[p]['valor']:.2f})"
            for p in sorted(novos)
        ]
        lista_removidos = [
            f"Patrimônio {p} - {atuais[p]['nome'] or 'Material Desconhecido'}"
            for p in sorted(removidos)
        ]

        return {
            "total_pdf": len(pats_pdf),
            "mantidos": len(mantidos),
            "incluidos": lista_incluidos,
            "removidos": lista_removidos,
        }
    finally:
        conn.close()


def iniciar_importacao_pdf(pdf_bytes: bytes) -> str:
    """Cria um job assíncrono para que a requisição HTTP não fique bloqueada."""
    job_id = uuid.uuid4().hex
    with IMPORT_JOBS_LOCK:
        IMPORT_JOBS[job_id] = {"status": "processando", "etapa": "Recebendo arquivo...", "percentual": 5}

    def worker():
        try:
            with IMPORT_JOBS_LOCK:
                IMPORT_JOBS[job_id].update(etapa="Extraindo texto do PDF...", percentual=20)
            texto = extrair_texto_pdf_bytes(pdf_bytes)

            with IMPORT_JOBS_LOCK:
                IMPORT_JOBS[job_id].update(etapa="Processando itens e patrimônios...", percentual=55)
            itens_extraidos = processar_relacao_siscofis(texto)

            with IMPORT_JOBS_LOCK:
                IMPORT_JOBS[job_id].update(etapa="Sincronizando com o banco de dados...", percentual=75)
            resultado = sincronizar_pdf_siscofis(itens_extraidos)

            with IMPORT_JOBS_LOCK:
                IMPORT_JOBS[job_id].update(
                    status="concluido", etapa="Importação concluída.", percentual=100, resultado=resultado
                )
        except Exception as exc:
            with IMPORT_JOBS_LOCK:
                IMPORT_JOBS[job_id].update(status="erro", etapa="Falha na importação.", percentual=100, erro=str(exc))

    threading.Thread(target=worker, name=f"import-{job_id[:8]}", daemon=True).start()
    return job_id


# -----------------------------------------------------------------------------
# BANCO DE DADOS (SQLite)
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_material TEXT NOT NULL,
            nr_ficha TEXT,
            nee_mat TEXT,
            conta_contabil TEXT,
            acervo TEXT,
            valor_unitario REAL,
            foto_objeto TEXT,
            foto_etiqueta TEXT,
            observacoes TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS patrimonios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            nr_patrimonio TEXT UNIQUE NOT NULL,
            local_armazenamento TEXT,
            data_conferencia TEXT,
            status_conferencia TEXT DEFAULT 'Não conferido',
            status_movimentacao TEXT DEFAULT 'Em carga',
            valor_individual REAL DEFAULT 0.0,
            boletim_admin TEXT,
            team TEXT,
            FOREIGN KEY (item_id) REFERENCES itens (id) ON DELETE CASCADE
        )
    """)
    try:
        c.execute("ALTER TABLE itens ADD COLUMN observacoes TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE patrimonios ADD COLUMN valor_individual REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    c.execute("CREATE INDEX IF NOT EXISTS idx_patrimonios_nr ON patrimonios(nr_patrimonio)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_patrimonios_status_mov ON patrimonios(status_movimentacao)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_patrimonios_item ON patrimonios(item_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_itens_ficha ON itens(nr_ficha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_itens_conta ON itens(conta_contabil)")
    conn.commit()
    conn.close()


init_db()

OPTIONS_LOCAIS = "".join(
    [f'<option value="{loc}">{loc}</option>' for loc in LOCAIS_DISPONIVEIS]
)
OPTIONS_STATUS = "".join(
    [f'<option value="{st}">{st}</option>' for st in STATUS_CONFERENCIA_OPTS]
)
OPTIONS_SITUACAO = "".join(
    [f'<option value="{sit}">{sit}</option>' for sit in SITUACAO_OPTS]
)

# -----------------------------------------------------------------------------
# HTML + CSS + JAVASCRIPT (FRONTEND)
# -----------------------------------------------------------------------------
HTML_PAGE = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Conferência de Carga - IME / SE/4</title>
    <style>
        :root {{ --primary: #1b365d; --secondary: #4a777a; --bg: #f4f6f9; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: var(--bg); margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1350px; margin: 0 auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ color: var(--primary); margin-top: 0; border-bottom: 2px solid var(--primary); padding-bottom: 10px; }}
        .tabs {{ display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 2px solid #ddd; flex-wrap: wrap; }}
        .tab-btn {{ padding: 10px 18px; border: none; background: #e0e0e0; cursor: pointer; border-radius: 5px 5px 0 0; font-weight: bold; font-size: 14px; transition: 0.2s; }}
        .tab-btn.active {{ background: var(--primary); color: white; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .form-group {{ margin-bottom: 15px; }}
        label {{ display: block; font-weight: bold; margin-bottom: 5px; font-size: 14px; }}
        input, select, textarea {{ width: 100%; padding: 9px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; font-size: 13px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        @media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
        button.btn-submit {{ background: var(--primary); color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; font-weight: bold; margin-top: 10px; }}
        button.btn-submit:hover {{ background: #132744; }}
        button.btn-excel {{ background: #1d6f42; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; font-weight: bold; margin-top: 10px; }}
        button.btn-excel:hover {{ background: #144e2e; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        th, td {{ border: 1px solid #ddd; padding: 9px; text-align: left; font-size: 13px; }}
        th {{ background: var(--primary); color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        
        .badge {{ padding: 4px 8px; border-radius: 4px; font-weight: bold; color: white; font-size: 11px; display: inline-block; }}
        .bg-conferido {{ background: #2e7d32; }}
        .bg-nao-conferido {{ background: #6c757d; }}
        .bg-nao-encontrado {{ background: #f57c00; }}
        .bg-fora-se {{ background: #d32f2f; }}
        
        tr.tr-fora-carga {{ background-color: #fde8e8 !important; color: #9b1c1c; }}
        tr.tr-incompleto {{ background-color: #fefcbf !important; }}
        
        .campo-vazio {{ color: #c53030; font-style: italic; font-weight: bold; font-size: 11px; }}
        .consulta-filtros-pendencias {{
            display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
            background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
            padding: 10px 12px; margin-top: 15px;
        }}
        .consulta-filtros-pendencias .filtro-titulo {{
            font-weight: bold; color: var(--primary); font-size: 13px; margin-right: 4px;
        }}
        .consulta-filtros-pendencias label {{
            display: inline-flex; align-items: center; gap: 5px; margin: 0;
            font-weight: normal; font-size: 13px; cursor: pointer;
        }}
        .consulta-filtros-pendencias input[type="checkbox"] {{ width: auto; margin: 0; cursor: pointer; }}
        .filtro-pendencia-incompleto {{ color: #9a6700; font-weight: 600 !important; }}
        .filtro-pendencia-vistoria {{ color: #5b6573; font-weight: 600 !important; }}
        .filtro-pendencia-fora {{ color: #9b1c1c; font-weight: 600 !important; }}
        .filtro-pendencia-acoes {{ margin-left: auto; display: flex; gap: 6px; }}
        .filtro-pendencia-acoes button {{
            border: 1px solid #cbd5e1; background: white; border-radius: 4px;
            padding: 5px 9px; cursor: pointer; font-size: 11px;
        }}
        .filtro-pendencia-acoes button:hover {{ background: #eef4fc; }}
        .legend-bar {{ display: flex; gap: 15px; background: #f8fafc; padding: 10px 15px; border-radius: 6px; border: 1px solid #e2e8f0; margin-top: 15px; font-size: 12px; font-weight: bold; flex-wrap: wrap; align-items: center; }}
        
        .box-info {{ background: #eef4fc; border-left: 4px solid var(--primary); padding: 12px; margin-bottom: 15px; font-size: 14px; }}
        .patrimonio-header-row {{ display: grid; grid-template-columns: 2fr 1.5fr 2fr 2fr 2fr 60px; gap: 8px; font-weight: bold; font-size: 12px; margin-bottom: 5px; color: #555; background: #eaeff5; padding: 8px; border-radius: 4px; }}
        
        .kpi-container {{ display: flex; gap: 15px; margin-bottom: 25px; flex-wrap: wrap; }}
        .kpi-card {{ flex: 1; min-width: 200px; background: #f8fafc; border: 1px solid #e2e8f0; border-left: 5px solid var(--primary); border-radius: 6px; padding: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.03); }}
        .kpi-card .kpi-title {{ font-size: 12px; color: #64748b; font-weight: bold; text-transform: uppercase; margin-bottom: 5px; }}
        .kpi-card .kpi-value {{ font-size: 22px; color: var(--primary); font-weight: bold; }}
        .kpi-card .kpi-sub {{ font-size: 11px; color: #777; margin-top: 4px; }}

        .modal-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: center; }}
        .modal-content {{ background: white; padding: 25px; border-radius: 8px; max-width: 650px; width: 90%; max-height: 85vh; overflow-y: auto; box-shadow: 0 4px 20px rgba(0,0,0,0.25); }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--primary); padding-bottom: 10px; margin-bottom: 15px; }}
        .modal-header h2 {{ margin: 0; color: var(--primary); font-size: 18px; }}
        .modal-close {{ background: none; border: none; font-size: 22px; cursor: pointer; font-weight: bold; color: #666; }}

        .modal-content-progress {{ background: white; padding: 30px; border-radius: 8px; max-width: 420px; width: 90%; box-shadow: 0 4px 20px rgba(0,0,0,0.25); text-align: center; }}
        .progress-status {{ margin: 18px 0 10px 0; font-size: 14px; color: #333; font-weight: 600; min-height: 20px; }}
        .progress-bar-track {{ width: 100%; height: 12px; background: #e2e8f0; border-radius: 6px; overflow: hidden; }}
        .progress-bar-fill {{ height: 100%; width: 30%; background: var(--primary); border-radius: 6px; animation: progress-indeterminate 1.3s ease-in-out infinite; }}
        @keyframes progress-indeterminate {{
            0% {{ margin-left: -30%; width: 30%; }}
            50% {{ width: 55%; }}
            100% {{ margin-left: 100%; width: 30%; }}
        }}
        .progress-icon {{ font-size: 32px; }}
        .resumo-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; margin-bottom: 12px; }}
        .list-box {{ max-height: 120px; overflow-y: auto; background: white; border: 1px solid #ccc; padding: 8px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-top: 5px; }}

        .problem-card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 10px; }}
        .problem-card {{ background: #fff5f5; border: 1px solid #feb2b2; border-radius: 6px; padding: 10px; text-align: center; }}
        .problem-card .num {{ font-size: 18px; font-weight: bold; color: #c53030; }}
        .problem-card .lbl {{ font-size: 11px; color: #742a2a; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📦 Sistema de Conferência de Carga (IME / SE/4)</h1>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="openTab('consulta')">🔍 Consultar</button>
            <button class="tab-btn" onclick="openTab('resumo')">📈 Resumo Gerencial</button>
            <button class="tab-btn" onclick="openTab('cadastro')">➕ Cadastrar Material</button>
            <button class="tab-btn" onclick="openTab('alterar_cadastro')">📝 Alterar Cadastro</button>
            <button class="tab-btn" onclick="openTab('saida')">🚚 Saída / Alienação</button>
            <button class="tab-btn" onclick="openTab('relatorios')">📊 Relatórios</button>
            <button class="tab-btn" onclick="openTab('importar')">📥 Importar Relação de Carga</button>
        </div>

        <!-- ABA 1: CONSULTA -->
        <div id="consulta" class="tab-content active">
            <div class="grid">
                <div><label>Descrição:</label><input type="text" id="b_desc" oninput="agendarBusca()"></div>
                <div><label>Nº Patrimônio:</label><input type="text" id="b_pat" oninput="agendarBusca()"></div>
                <div><label>Local:</label><input type="text" id="b_local" oninput="agendarBusca()" placeholder="Filtrar local..."></div>
                <div><label>Conta Contábil:</label><input type="text" id="b_conta" oninput="agendarBusca()"></div>
                <div><label>Nº Ficha:</label><input type="text" id="b_ficha" oninput="agendarBusca()"></div>
            </div>

            <div class="consulta-filtros-pendencias">
                <span class="filtro-titulo">🔎 Exibir somente:</span>
                <label class="filtro-pendencia-incompleto">
                    <input type="checkbox" class="filtro-pendencia" value="incompleto" onchange="agendarBusca()">
                    Cadastro incompleto
                </label>
                <label class="filtro-pendencia-vistoria">
                    <input type="checkbox" class="filtro-pendencia" value="vistoria" onchange="agendarBusca()">
                    Vistoria pendente
                </label>
                <label class="filtro-pendencia-fora">
                    <input type="checkbox" class="filtro-pendencia" value="fora_carga" onchange="agendarBusca()">
                    Fora de carga
                </label>
                <div class="filtro-pendencia-acoes">
                    <button type="button" onclick="selecionarFiltrosPendencias()">Selecionar todos</button>
                    <button type="button" onclick="limparFiltrosPendencias()">Limpar</button>
                </div>
            </div>

            <div class="legend-bar">
                <span>Legenda de Destaques:</span>
                <span><span class="badge" style="background:#fde8e8; color:#9b1c1c; border:1px solid #f8b4b4;"> Linha Vermelha </span> Item "Fora de carga"</span>
                <span><span class="badge" style="background:#fefcbf; color:#744210; border:1px solid #f6e05e;"> Linha Amarela </span> Cadastro Incompleto (Campos Ausentes)</span>
                <span><span class="badge bg-nao-conferido">Não conferido</span> Pendente de vistoria</span>
            </div>

            <div id="resultado_count" style="margin-top: 15px; font-weight: bold; color: var(--primary);"></div>
            <div style="overflow-x: auto;">
                <table id="tabela_dados">
                    <thead>
                        <tr>
                            <th>Descrição</th><th>Ficha</th><th>NEE</th><th>Conta</th><th>Patrimônio</th>
                            <th>Valor (R$)</th><th>Local</th><th>Observações</th><th>Últ. Conf.</th><th>Status</th><th>Situação</th><th>Boletim/TEAM</th><th>Fotos</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>

        <!-- ABA 2: RESUMO / DASHBOARD -->
        <div id="resumo" class="tab-content">
            <div class="box-info">
                📊 <b>Painel de Resumo e Análise Estatística da Carga</b><br>
                Acompanhe a distribuição de patrimônios por status de conferência, situação/movimentação, acervo por local e diagnóstico da qualidade dos cadastros.
            </div>

            <!-- CARDS KPI PRINCIPAIS -->
            <div class="kpi-container">
                <div class="kpi-card">
                    <div class="kpi-title">Total de Patrimônios</div>
                    <div class="kpi-value" id="kpi_total_pat">0</div>
                    <div class="kpi-sub" id="kpi_sub_mat">0 fichas distintas</div>
                </div>
                <div class="kpi-card" style="border-left-color: #2e7d32;">
                    <div class="kpi-title">Valor Total da Carga</div>
                    <div class="kpi-value" id="kpi_valor_total" style="color: #2e7d32;">R$ 0,00</div>
                    <div class="kpi-sub">Soma individual dos bens</div>
                </div>
                <div class="kpi-card" style="border-left-color: #dd6b20;">
                    <div class="kpi-title">Incompletos / Pendentes</div>
                    <div class="kpi-value" id="kpi_incompletos_qtd" style="color: #dd6b20;">0</div>
                    <div class="kpi-sub" id="kpi_incompletos_perc">0% da carga total</div>
                </div>
                <div class="kpi-card" style="border-left-color: #e53e3e;">
                    <div class="kpi-title">Valor em Pendência Cadastral</div>
                    <div class="kpi-value" id="kpi_incompletos_valor" style="color: #e53e3e;">R$ 0,00</div>
                    <div class="kpi-sub">Valor de itens com cadastro incompleto</div>
                </div>
            </div>

            <!-- PAINEL DE PROBLEMAS NO CADASTRO -->
            <div style="background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <h3 style="margin-top: 0; color: #c53030; font-size: 15px;">⚠️ Diagnóstico de Inconformidades Cadastrais (Incompletos)</h3>
                <div class="problem-card-grid">
                    <div class="problem-card">
                        <div class="num" id="diag_sem_foto_obj">0</div>
                        <div class="lbl">Sem Foto do Objeto</div>
                    </div>
                    <div class="problem-card">
                        <div class="num" id="diag_sem_foto_etiq">0</div>
                        <div class="lbl">Sem Foto da Etiqueta</div>
                    </div>
                    <div class="problem-card">
                        <div class="num" id="diag_sem_local">0</div>
                        <div class="lbl">Sem Local Definido</div>
                    </div>
                    <div class="problem-card">
                        <div class="num" id="diag_sem_ficha">0</div>
                        <div class="lbl">Sem Nº de Ficha</div>
                    </div>
                </div>
            </div>

            <div class="grid-2">
                <!-- TABELA: STATUS DE CONFERÊNCIA -->
                <div>
                    <h3>🔍 Estatística por Status de Conferência</h3>
                    <div style="overflow-x: auto;">
                        <table id="tabela_resumo_status">
                            <thead>
                                <tr>
                                    <th>Status Conferência</th>
                                    <th>Qtd.</th>
                                    <th>Valor Total (R$)</th>
                                    <th>% Qtd.</th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>

                <!-- TABELA: SITUAÇÃO / MOVIMENTAÇÃO -->
                <div>
                    <h3>🚚 Estatística por Situação / Movimentação</h3>
                    <div style="overflow-x: auto;">
                        <table id="tabela_resumo_situacao">
                            <thead>
                                <tr>
                                    <th>Situação / Movimentação</th>
                                    <th>Qtd.</th>
                                    <th>Valor Total (R$)</th>
                                    <th>% Qtd.</th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TABELA: LOCAIS DE ARMAZENAMENTO -->
            <div style="margin-top: 25px;">
                <h3>📍 Análise de Acervo por Local de Armazenamento</h3>
                <div style="overflow-x: auto;">
                    <table id="tabela_resumo_locais">
                        <thead>
                            <tr>
                                <th>Local de Armazenamento</th>
                                <th>Qtd. de Patrimônios</th>
                                <th>Valor Total Acumulado</th>
                                <th>% da Carga Total</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ABA 3: CADASTRO -->
        <div id="cadastro" class="tab-content">
            <form id="formCadastro" onsubmit="cadastrarMaterial(event)">
                <div class="grid">
                    <div><label>Descrição do Material *</label><input type="text" id="c_nome" required></div>
                    <div><label>Nº Ficha</label><input type="text" id="c_ficha"></div>
                    <div><label>NEE Mat.</label><input type="text" id="c_nee"></div>
                    <div><label>Conta Contábil</label><input type="text" id="c_conta"></div>
                    <div><label>Valor Padrão Item (R$)</label><input type="text" id="c_valor" placeholder="0,00"></div>
                </div>

                <div class="form-group" style="margin-top: 20px;">
                    <label>Patrimônios Associados *</label>
                    <div class="patrimonio-header-row">
                        <div>Nº Patrimônio</div>
                        <div>Valor (R$)</div>
                        <div>Local de Armazenamento</div>
                        <div>Status Conferência</div>
                        <div>Situação / Movimentação</div>
                        <div>Ação</div>
                    </div>
                    <div id="patrimonios_container"></div>
                    <button type="button" class="btn-submit" style="background: #4a777a; margin-top: 8px; font-size: 13px;" onclick="addPatrimonioRow()">➕ Adicionar Outro Patrimônio</button>
                </div>

                <div style="margin-top: 15px;">
                    <label>Observações</label>
                    <textarea id="c_observacoes" rows="4" placeholder="Anotações gerais ou peculiaridades sobre o item..."></textarea>
                </div>
                <div class="grid" style="margin-top: 15px;">
                    <div><label>Foto do Objeto</label><input type="file" id="c_foto_obj" accept="image/*"></div>
                    <div><label>Foto da Etiqueta</label><input type="file" id="c_foto_etiq" accept="image/*"></div>
                </div>
                <button type="submit" class="btn-submit">Salvar Material e Patrimônios</button>
            </form>
        </div>

        <!-- ABA 4: ALTERAR CADASTRO -->
        <div id="alterar_cadastro" class="tab-content">
            <div class="box-info">
                🔍 <b>Buscar Material para Edição</b><br>
                Pesquise por <b>Nº da Ficha</b> ou <b>Conta Contábil</b> para carregar a ficha completa do material e realizar alterações.
            </div>

            <div class="grid" style="align-items: end;">
                <div><label>Nº da Ficha</label><input type="text" id="ed_busca_ficha" placeholder="Digite o Nº da Ficha..."></div>
                <div><label>Conta Contábil</label><input type="text" id="ed_busca_conta" placeholder="Digite a Conta Contábil..."></div>
                <div><button type="button" class="btn-submit" onclick="buscarParaEdicao()">🔍 Pesquisar Material</button></div>
            </div>

            <div id="ed_selecao_container" style="display:none; margin-top: 15px;">
                <label>Selecione o Material Encontrado:</label>
                <select id="ed_select_item" onchange="carregarFormEdicao()"></select>
            </div>

            <hr style="margin: 20px 0; border: 0; border-top: 1px solid #ccc;">

            <form id="formEdicao" onsubmit="salvarEdicaoCadastro(event)" style="display:none;">
                <input type="hidden" id="ed_item_id">
                <div class="grid">
                    <div><label>Descrição do Material *</label><input type="text" id="ed_nome" required></div>
                    <div><label>Nº Ficha</label><input type="text" id="ed_ficha"></div>
                    <div><label>NEE Mat.</label><input type="text" id="ed_nee"></div>
                    <div><label>Conta Contábil</label><input type="text" id="ed_conta"></div>
                    <div><label>Valor Padrão Item (R$)</label><input type="text" id="ed_valor" placeholder="0,00"></div>
                </div>

                <div class="form-group" style="margin-top: 20px;">
                    <label>Patrimônios Associados *</label>
                    <div class="patrimonio-header-row">
                        <div>Nº Patrimônio</div>
                        <div>Valor (R$)</div>
                        <div>Local de Armazenamento</div>
                        <div>Status Conferência</div>
                        <div>Situação / Movimentação</div>
                        <div>Ação</div>
                    </div>
                    <div id="ed_patrimonios_container"></div>
                    <button type="button" class="btn-submit" style="background: #4a777a; margin-top: 8px; font-size: 13px;" onclick="addEdPatrimonioRow()">➕ Adicionar Outro Patrimônio</button>
                </div>

                <div style="margin-top: 15px;">
                    <label>Observações</label>
                    <textarea id="ed_observacoes" rows="4" placeholder="Anotações gerais ou peculiaridades sobre o item..."></textarea>
                </div>

                <div class="grid" style="margin-top: 15px;">
                    <div>
                        <label>Foto do Objeto</label>
                        <input type="file" id="ed_foto_obj" accept="image/*">
                        <div id="ed_preview_obj" style="margin-top: 5px; font-size: 12px; color: #666;"></div>
                    </div>
                    <div>
                        <label>Foto da Etiqueta</label>
                        <input type="file" id="ed_foto_etiq" accept="image/*">
                        <div id="ed_preview_etiq" style="margin-top: 5px; font-size: 12px; color: #666;"></div>
                    </div>
                </div>

                <button type="submit" class="btn-submit" style="background: #2e7d32; margin-top: 20px; font-size: 16px;">💾 Salvar Alterações no Cadastro</button>
            </form>
        </div>

        <!-- ABA 5: SAÍDA / ALIENAÇÃO -->
        <div id="saida" class="tab-content">
            <form onsubmit="registrarSaida(event)">
                <div class="grid">
                    <div><label>Nº de Patrimônio *</label><input type="text" id="s_pat" required></div>
                    <div>
                        <label>Tipo de Movimentação</label>
                        <select id="s_tipo" onchange="toggleTeam()">
                            <option value="Aguardando transferência">Aguardando transferência</option>
                            <option value="Aguardando alienação">Aguardando alienação</option>
                            <option value="Aguardando recolhimento">Aguardando recolhimento</option>
                            <option value="Fora de carga">Fora de carga</option>
                        </select>
                    </div>
                    <div><label>Boletim Administrativo *</label><input type="text" id="s_bol" required></div>
                    <div><label id="lbl_team">TEAM (Opcional)</label><input type="text" id="s_team"></div>
                </div>
                <button type="submit" class="btn-submit">Confirmar Movimentação</button>
            </form>
        </div>

        <!-- ABA 6: RELATÓRIOS -->
        <div id="relatorios" class="tab-content">
            <div class="box-info">
                📑 <b>Gerador de Relatórios Customizados</b><br>
                Escolha o tipo de relatório e os filtros desejados para emitir arquivos em Excel ou visualizações para Impressão / Salvar em PDF.
            </div>

            <div class="grid">
                <div>
                    <label>Tipo de Relatório</label>
                    <select id="r_tipo" onchange="atualizarFiltrosRelatorio()">
                        <option value="completo_fotos">Geral Completo (Com Fotos)</option>
                        <option value="completo_sem_fotos">Geral Completo (Sem Fotos)</option>
                        <option value="por_ficha">Filtrado por Ficha</option>
                        <option value="por_conta">Filtrado por Conta Contábil</option>
                        <option value="por_local">Filtrado por Local</option>
                        <option value="por_status">Filtrado por Status de Conferência</option>
                        <option value="por_situacao">Filtrado por Situação / Movimentação</option>
                    </select>
                </div>
                <div id="div_r_filtro" style="display: none;">
                    <label id="lbl_r_filtro">Valor do Filtro</label>
                    <input type="text" id="r_filtro_text" placeholder="Digite o termo do filtro..." style="display: none;">
                    <select id="r_filtro_select" style="display: none;"></select>
                </div>
            </div>

            <div style="margin-top: 20px; display: flex; gap: 15px;">
                <button type="button" class="btn-submit" onclick="gerarRelatorioPDF()">📄 Gerar Visualização / Salvar em PDF</button>
                <button type="button" class="btn-excel" onclick="exportarExcel()">📊 Exportar para Excel (.csv)</button>
            </div>
        </div>

        <!-- ABA 7: IMPORTAR RELAÇÃO DE CARGA -->
        <div id="importar" class="tab-content">
            <div class="box-info">
                📥 <b>Importação e Sincronização de Carga via PDF do SISCOFIS</b><br>
                Selecione o arquivo <b>.PDF</b> emitido pelo SISCOFIS para sincronizar com o banco de dados.<br>
                <b>Funcionamento automático:</b><br>
                • <b>Itens Novos no PDF:</b> São inseridos no banco com situação <i>'Em carga'</i>, status <i>'Não conferido'</i> e o valor individual do patrimônio extraído.<br>
                • <b>Itens Mantidos:</b> Têm seus locais e conferências anteriores preservados e seus valores atualizados.<br>
                • <b>Itens Ausentes do PDF:</b> São alterados para a situação <i>'Fora de carga'</i>.
            </div>
            
            <form onsubmit="importarPDF(event)">
                <div class="form-group">
                    <label>Selecione o Relatório de Carga (.PDF) *</label>
                    <input type="file" id="imp_arquivo_pdf" accept=".pdf" required>
                </div>
                <button type="submit" class="btn-submit">Processar e Sincronizar Carga</button>
            </form>
        </div>
    </div>

    <!-- JANELA MODAL POPUP - PROGRESSO DA IMPORTAÇÃO -->
    <div id="modal_progresso" class="modal-overlay">
        <div class="modal-content-progress">
            <div class="progress-icon">📥</div>
            <div id="progresso_status" class="progress-status">Lendo arquivo PDF...</div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill"></div>
            </div>
        </div>
    </div>

    <!-- JANELA MODAL POPUP - RESUMO DA IMPORTAÇÃO -->
    <div id="modal_resumo" class="modal-overlay">
        <div class="modal-content">
            <div class="modal-header">
                <h2>📋 Resumo da Importação de Carga (PDF)</h2>
                <button type="button" class="modal-close" onclick="fecharModalResumo()">&times;</button>
            </div>
            <div class="resumo-box">
                <p style="margin:0 0 5px 0;"><b>Total de patrimônios lidos no PDF:</b> <span id="res_total_pdf" style="font-size:16px; font-weight:bold; color:var(--primary);">0</span></p>
                <p style="margin:0; color: #2e7d32;"><b>✔ Mantidos na carga atual:</b> <span id="res_mantidos" style="font-weight:bold;">0</span></p>
            </div>

            <div class="resumo-box">
                <p style="margin:0; color: #2e7d32; font-weight: bold;">➕ Itens Incluídos em Carga (<span id="res_qtd_incluidos">0</span>):</p>
                <div class="list-box" id="res_lista_incluidos"></div>
            </div>

            <div class="resumo-box">
                <p style="margin:0; color: #d32f2f; font-weight: bold;">✖ Itens Removidos da Carga (<span id="res_qtd_removidos">0</span>):</p>
                <div class="list-box" id="res_lista_removidos"></div>
            </div>

            <div style="text-align: right; margin-top: 15px;">
                <button type="button" class="btn-submit" onclick="fecharModalResumo()">Fechar e Visualizar Dados</button>
            </div>
        </div>
    </div>

    <script>
        const OPTIONS_LOCAIS_HTML = `{OPTIONS_LOCAIS}`;
        const OPTIONS_STATUS_HTML = `{OPTIONS_STATUS}`;
        const OPTIONS_SITUACAO_HTML = `{OPTIONS_SITUACAO}`;
        let materiaisEncontradosEdicao = [];

        function openTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            
            const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick').includes(tabId));
            if(activeBtn) activeBtn.classList.add('active');

            if(tabId === 'consulta') buscar();
            if(tabId === 'resumo') carregarResumo();
        }}

        const fmtMoeda = (val) => Number(val || 0).toLocaleString('pt-BR', {{ style: 'currency', currency: 'BRL' }});

        async function carregarResumo() {{
            const res = await fetch('/api/resumo');
            const data = await res.json();

            const totalPat = data.geral.total_patrimonios || 1;

            // KPIs Principais
            document.getElementById('kpi_total_pat').innerText = data.geral.total_patrimonios;
            document.getElementById('kpi_sub_mat').innerText = `${{data.geral.total_materiais}} fichas de material`;
            document.getElementById('kpi_valor_total').innerText = fmtMoeda(data.geral.valor_total);
            
            document.getElementById('kpi_incompletos_qtd').innerText = data.geral.qtd_incompletos;
            const percIncompletos = ((data.geral.qtd_incompletos / totalPat) * 100).toFixed(1);
            document.getElementById('kpi_incompletos_perc').innerText = `${{percIncompletos}}% da carga total`;
            document.getElementById('kpi_incompletos_valor').innerText = fmtMoeda(data.geral.valor_incompletos);

            // Diagnóstico de Problemas no Cadastro
            document.getElementById('diag_sem_foto_obj').innerText = data.geral.sem_foto_obj;
            document.getElementById('diag_sem_foto_etiq').innerText = data.geral.sem_foto_etiq;
            document.getElementById('diag_sem_local').innerText = data.geral.sem_local;
            document.getElementById('diag_sem_ficha').innerText = data.geral.sem_ficha;

            // Tabela 1: Status de Conferência
            const tbStatus = document.querySelector('#tabela_resumo_status tbody');
            tbStatus.innerHTML = '';
            data.status.forEach(item => {{
                const perc = ((item.qtd / totalPat) * 100).toFixed(1);
                tbStatus.innerHTML += `<tr>
                    <td><b>${{item.status}}</b></td>
                    <td>${{item.qtd}}</td>
                    <td>${{fmtMoeda(item.valor)}}</td>
                    <td>${{perc}}%</td>
                </tr>`;
            }});

            // Tabela 2: Situação / Movimentação
            const tbSituacao = document.querySelector('#tabela_resumo_situacao tbody');
            tbSituacao.innerHTML = '';
            data.situacoes.forEach(item => {{
                const perc = ((item.qtd / totalPat) * 100).toFixed(1);
                tbSituacao.innerHTML += `<tr>
                    <td><b>${{item.situacao}}</b></td>
                    <td>${{item.qtd}}</td>
                    <td>${{fmtMoeda(item.valor)}}</td>
                    <td>${{perc}}%</td>
                </tr>`;
            }});

            // Tabela 3: Locais
            const tbLocais = document.querySelector('#tabela_resumo_locais tbody');
            tbLocais.innerHTML = '';
            data.locais.forEach(item => {{
                const perc = ((item.qtd / totalPat) * 100).toFixed(1);
                tbLocais.innerHTML += `<tr>
                    <td><b>${{item.local}}</b></td>
                    <td>${{item.qtd}}</td>
                    <td>${{fmtMoeda(item.valor)}}</td>
                    <td>${{perc}}%</td>
                </tr>`;
            }});
        }}

        function addPatrimonioRow(patVal = '', valorPatVal = '', localVal = '', statusVal = 'Não conferido', situacaoVal = 'Em carga') {{
            const container = document.getElementById('patrimonios_container');
            const div = document.createElement('div');
            div.className = 'grid patrimonio-row';
            div.style.gridTemplateColumns = '2fr 1.5fr 2fr 2fr 2fr 60px';
            div.style.gap = '8px';
            div.style.marginBottom = '10px';
            
            const valorDefault = valorPatVal !== '' ? valorPatVal : (document.getElementById('c_valor').value || '0,00');

            div.innerHTML = `
                <div><input type="text" class="input-pat" placeholder="Nº Patrimônio" value="${{patVal}}" required></div>
                <div><input type="text" class="input-valor-pat" placeholder="Valor (R$)" value="${{valorDefault}}"></div>
                <div><select class="select-local">${{OPTIONS_LOCAIS_HTML}}</select></div>
                <div><select class="select-status">${{OPTIONS_STATUS_HTML}}</select></div>
                <div><select class="select-situacao">${{OPTIONS_SITUACAO_HTML}}</select></div>
                <div><button type="button" style="background: #d32f2f; color: white; border: none; padding: 9px; border-radius: 4px; cursor: pointer; width: 100%; font-weight: bold;" onclick="removerLinhaPatrimonio(this)">🗑️</button></div>
            `;
            container.appendChild(div);
            if(localVal) div.querySelector('.select-local').value = localVal;
            if(statusVal) div.querySelector('.select-status').value = statusVal;
            if(situacaoVal) div.querySelector('.select-situacao').value = situacaoVal;
        }}

        function addEdPatrimonioRow(patVal = '', valorPatVal = '', localVal = '', statusVal = 'Não conferido', situacaoVal = 'Em carga') {{
            const container = document.getElementById('ed_patrimonios_container');
            const div = document.createElement('div');
            div.className = 'grid ed-patrimonio-row';
            div.style.gridTemplateColumns = '2fr 1.5fr 2fr 2fr 2fr 60px';
            div.style.gap = '8px';
            div.style.marginBottom = '10px';

            const valorStr = valorPatVal !== '' ? String(valorPatVal).replace('.', ',') : (document.getElementById('ed_valor').value || '0,00');

            div.innerHTML = `
                <div><input type="text" class="ed-input-pat" placeholder="Nº Patrimônio" value="${{patVal}}" required></div>
                <div><input type="text" class="ed-input-valor-pat" placeholder="Valor (R$)" value="${{valorStr}}"></div>
                <div><select class="ed-select-local">${{OPTIONS_LOCAIS_HTML}}</select></div>
                <div><select class="ed-select-status">${{OPTIONS_STATUS_HTML}}</select></div>
                <div><select class="ed-select-situacao">${{OPTIONS_SITUACAO_HTML}}</select></div>
                <div><button type="button" style="background: #d32f2f; color: white; border: none; padding: 9px; border-radius: 4px; cursor: pointer; width: 100%; font-weight: bold;" onclick="removerLinhaPatrimonio(this)">🗑️</button></div>
            `;
            container.appendChild(div);
            if(localVal) div.querySelector('.ed-select-local').value = localVal;
            if(statusVal) div.querySelector('.ed-select-status').value = statusVal;
            if(situacaoVal) div.querySelector('.ed-select-situacao').value = situacaoVal;
        }}

        function removerLinhaPatrimonio(btn) {{
            const container = btn.closest('#patrimonios_container, #ed_patrimonios_container');
            const rows = container.querySelectorAll('.patrimonio-row, .ed-patrimonio-row');
            if(rows.length > 1) {{
                btn.closest('.patrimonio-row, .ed-patrimonio-row').remove();
            }} else {{
                alert('É necessário ter ao menos um número de patrimônio vinculado!');
            }}
        }}

        function toggleTeam() {{
            const tipo = document.getElementById('s_tipo').value;
            const lbl = document.getElementById('lbl_team');
            if(tipo === 'Aguardando alienação') {{
                lbl.innerText = "TEAM (OBRIGATÓRIO para Alienação) *";
                lbl.style.color = "red";
            }} else {{
                lbl.innerText = "TEAM (Opcional)";
                lbl.style.color = "black";
            }}
        }}

        function atualizarFiltrosRelatorio() {{
            const tipo = document.getElementById('r_tipo').value;
            const divFiltro = document.getElementById('div_r_filtro');
            const lblFiltro = document.getElementById('lbl_r_filtro');
            const inputText = document.getElementById('r_filtro_text');
            const selectFilter = document.getElementById('r_filtro_select');

            if (tipo === 'por_ficha') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Digite o Nº da Ficha:';
                inputText.style.display = 'block';
                selectFilter.style.display = 'none';
                inputText.placeholder = 'Ex: 1045';
            }} else if (tipo === 'por_conta') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Digite a Conta Contábil:';
                inputText.style.display = 'block';
                selectFilter.style.display = 'none';
                inputText.placeholder = 'Ex: 14211.01';
            }} else if (tipo === 'por_local') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Selecione o Local:';
                inputText.style.display = 'none';
                selectFilter.style.display = 'block';
                selectFilter.innerHTML = OPTIONS_LOCAIS_HTML;
            }} else if (tipo === 'por_status') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Selecione o Status de Conferência:';
                inputText.style.display = 'none';
                selectFilter.style.display = 'block';
                selectFilter.innerHTML = OPTIONS_STATUS_HTML;
            }} else if (tipo === 'por_situacao') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Selecione a Situação / Movimentação:';
                inputText.style.display = 'none';
                selectFilter.style.display = 'block';
                selectFilter.innerHTML = OPTIONS_SITUACAO_HTML;
            }} else {{
                divFiltro.style.display = 'none';
            }}
        }}

        function getFiltroValue() {{
            const tipo = document.getElementById('r_tipo').value;
            if (['por_local', 'por_status', 'por_situacao'].includes(tipo)) {{
                return document.getElementById('r_filtro_select').value;
            }} else if (['por_ficha', 'por_conta'].includes(tipo)) {{
                return document.getElementById('r_filtro_text').value;
            }}
            return '';
        }}

        function fmtCampo(val, nomeCampo) {{
            if (!val || String(val).trim() === '') {{
                return `<span class="campo-vazio">⚠️ Ausente</span>`;
            }}
            return val;
        }}

        let buscaTimer = null;
        let buscaAbort = null;
        function agendarBusca() {{
            clearTimeout(buscaTimer);
            buscaTimer = setTimeout(() => buscar(), 250);
        }}

        async function buscar() {{
            if (buscaAbort) buscaAbort.abort();
            buscaAbort = new AbortController();
            const desc = document.getElementById('b_desc').value;
            const pat = document.getElementById('b_pat').value;
            const local = document.getElementById('b_local').value;
            const conta = document.getElementById('b_conta').value;
            const ficha = document.getElementById('b_ficha').value;
            const pendencias = Array.from(document.querySelectorAll('.filtro-pendencia:checked'))
                .map(el => el.value);

            const params = new URLSearchParams({{ desc, pat, local, conta, ficha, limit: '500' }});
            if (pendencias.length) params.set('pendencias', pendencias.join(','));

            try {{
                const res = await fetch(`/api/itens?${{params.toString()}}`, {{signal: buscaAbort.signal}});
                const data = await res.json();
                document.getElementById('resultado_count').innerText = `Total de registros encontrados: ${{data.total}} | Exibindo: ${{data.itens.length}}`;
                const tbody = document.querySelector('#tabela_dados tbody');
                const fragment = document.createDocumentFragment();

                data.itens.forEach(item => {{
                    let badgeClass = 'bg-conferido';
                    if(item.status_conferencia === 'Não Encontrado') badgeClass = 'bg-nao-encontrado';
                    else if(item.status_conferencia === 'Fora da SE') badgeClass = 'bg-fora-se';
                    else if(item.status_conferencia === 'Não conferido' || !item.status_conferencia) badgeClass = 'bg-nao-conferido';
                    const isForaDeCarga = item.status_movimentacao === 'Fora de carga';
                    const isIncompleto = (!item.nr_ficha || !item.nee_mat || !item.conta_contabil || !item.local_armazenamento || !item.data_conferencia);
                    const tr = document.createElement('tr');
                    tr.className = isForaDeCarga ? 'tr-fora-carga' : (isIncompleto ? 'tr-incompleto' : '');
                    const fotos = (item.foto_objeto ? `<a href="${{item.foto_objeto}}" target="_blank">📷 Objeto</a> ` : `<span class="campo-vazio">⚠️ Sem Foto Objeto</span><br>`) + (item.foto_etiqueta ? `<a href="${{item.foto_etiqueta}}" target="_blank">🏷️ Etiqueta</a>` : `<span class="campo-vazio">⚠️ Sem Foto Etiq.</span>`);
                    tr.innerHTML = `<td><b>${{fmtCampo(item.nome_material)}}</b></td><td>${{fmtCampo(item.nr_ficha)}}</td><td>${{fmtCampo(item.nee_mat)}}</td><td>${{fmtCampo(item.conta_contabil)}}</td><td><b>${{fmtCampo(item.nr_patrimonio)}}</b></td><td><b>${{fmtMoeda(item.valor_individual)}}</b></td><td>${{fmtCampo(item.local_armazenamento)}}</td><td>${{item.observacoes ? fmtCampo(item.observacoes) : '<span style="color:#999;">—</span>'}}</td><td>${{fmtCampo(item.data_conferencia)}}</td><td><span class="badge ${{badgeClass}}">${{item.status_conferencia || 'Não conferido'}}</span></td><td><b>${{item.status_movimentacao || 'Em carga'}}</b></td><td>${{item.boletim_admin ? 'Bol: ' + item.boletim_admin : ''}} ${{item.team ? '<br>TEAM: ' + item.team : ''}}</td><td>${{fotos}}</td>`;
                    fragment.appendChild(tr);
                }});
                tbody.replaceChildren(fragment);
            }} catch (err) {{
                if (err.name !== 'AbortError') console.error(err);
            }}
        }}

        function selecionarFiltrosPendencias() {{
            document.querySelectorAll('.filtro-pendencia').forEach(el => el.checked = true);
            agendarBusca();
        }}

        function limparFiltrosPendencias() {{
            document.querySelectorAll('.filtro-pendencia').forEach(el => el.checked = false);
            agendarBusca();
        }}

        async function fileToBase64(file) {{
            if(!file) return null;
            return new Promise((resolve, reject) => {{
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            }});
        }}

        async function cadastrarMaterial(e) {{
            e.preventDefault();
            const fObj = document.getElementById('c_foto_obj').files[0];
            const fEtiq = document.getElementById('c_foto_etiq').files[0];

            const listaPatrimonios = [];
            document.querySelectorAll('#patrimonios_container .patrimonio-row').forEach(row => {{
                const patVal = row.querySelector('.input-pat').value.trim();
                const valorIndividualVal = row.querySelector('.input-valor-pat').value.trim();
                const localVal = row.querySelector('.select-local').value;
                const statusVal = row.querySelector('.select-status').value;
                const situacaoVal = row.querySelector('.select-situacao').value;
                
                if(patVal) {{
                    listaPatrimonios.push({{
                        nr_patrimonio: patVal,
                        valor_individual: valorIndividualVal,
                        local: localVal,
                        status_conferencia: statusVal,
                        status_movimentacao: situacaoVal
                    }});
                }}
            }});

            if(listaPatrimonios.length === 0) {{
                alert('Informe pelo menos um patrimônio!');
                return;
            }}

            const body = {{
                nome_material: document.getElementById('c_nome').value,
                nr_ficha: document.getElementById('c_ficha').value,
                nee_mat: document.getElementById('c_nee').value,
                conta_contabil: document.getElementById('c_conta').value,
                valor_unitario: document.getElementById('c_valor').value,
                observacoes: document.getElementById('c_observacoes').value,
                patrimonios: listaPatrimonios,
                foto_objeto: await fileToBase64(fObj),
                foto_etiqueta: await fileToBase64(fEtiq)
            }};

            const res = await fetch('/api/cadastrar', {{ method: 'POST', body: JSON.stringify(body) }});
            const result = await res.json();
            alert(result.message);
            if(result.success) {{
                document.getElementById('formCadastro').reset();
                document.getElementById('patrimonios_container').innerHTML = '';
                addPatrimonioRow();
                openTab('consulta');
            }}
        }}

        async function buscarParaEdicao() {{
            const ficha = document.getElementById('ed_busca_ficha').value.trim();
            const conta = document.getElementById('ed_busca_conta').value.trim();

            if(!ficha && !conta) {{
                alert('Digite a Ficha ou a Conta Contábil para realizar a busca.');
                return;
            }}

            const res = await fetch(`/api/buscar_edicao?ficha=${{encodeURIComponent(ficha)}}&conta=${{encodeURIComponent(conta)}}`);
            materiaisEncontradosEdicao = await res.json();

            const selectContainer = document.getElementById('ed_selecao_container');
            const select = document.getElementById('ed_select_item');
            const formEdicao = document.getElementById('formEdicao');

            if(materiaisEncontradosEdicao.length === 0) {{
                alert('Nenhum material encontrado com os parâmetros informados.');
                selectContainer.style.display = 'none';
                formEdicao.style.display = 'none';
                return;
            }}

            select.innerHTML = '<option value="">-- Selecione o item para editar --</option>';
            materiaisEncontradosEdicao.forEach((mat, index) => {{
                select.innerHTML += `<option value="${{index}}">${{mat.nome_material}} | Ficha: ${{mat.nr_ficha || 'S/N'}} | Conta: ${{mat.conta_contabil || 'S/N'}} (${{mat.patrimonios.length}} patrimônio(s))</option>`;
            }});

            selectContainer.style.display = 'block';
            formEdicao.style.display = 'none';

            if(materiaisEncontradosEdicao.length === 1) {{
                select.value = "0";
                carregarFormEdicao();
            }}
        }}

        function carregarFormEdicao() {{
            const selectVal = document.getElementById('ed_select_item').value;
            const formEdicao = document.getElementById('formEdicao');
            
            if(selectVal === "") {{
                formEdicao.style.display = 'none';
                return;
            }}

            const mat = materiaisEncontradosEdicao[parseInt(selectVal)];
            document.getElementById('ed_item_id').value = mat.id;
            document.getElementById('ed_nome').value = mat.nome_material || '';
            document.getElementById('ed_ficha').value = mat.nr_ficha || '';
            document.getElementById('ed_nee').value = mat.nee_mat || '';
            document.getElementById('ed_conta').value = mat.conta_contabil || '';
            document.getElementById('ed_valor').value = mat.valor_unitario !== undefined ? String(mat.valor_unitario).replace('.', ',') : '0,00';
            document.getElementById('ed_observacoes').value = mat.observacoes || '';

            document.getElementById('ed_preview_obj').innerText = mat.foto_objeto ? "📷 Possui foto do objeto cadastrada." : "Nenhuma foto de objeto cadastrada.";
            document.getElementById('ed_preview_etiq').innerText = mat.foto_etiqueta ? "🏷️ Possui foto de etiqueta cadastrada." : "Nenhuma foto de etiqueta cadastrada.";

            const container = document.getElementById('ed_patrimonios_container');
            container.innerHTML = '';

            if(mat.patrimonios && mat.patrimonios.length > 0) {{
                mat.patrimonios.forEach(p => {{
                    addEdPatrimonioRow(p.nr_patrimonio, p.valor_individual, p.local_armazenamento, p.status_conferencia, p.status_movimentacao);
                }});
            }} else {{
                addEdPatrimonioRow();
            }}

            formEdicao.style.display = 'block';
        }}

        async function salvarEdicaoCadastro(e) {{
            e.preventDefault();
            const fObj = document.getElementById('ed_foto_obj').files[0];
            const fEtiq = document.getElementById('ed_foto_etiq').files[0];

            const listaPatrimonios = [];
            document.querySelectorAll('#ed_patrimonios_container .ed-patrimonio-row').forEach(row => {{
                const patVal = row.querySelector('.ed-input-pat').value.trim();
                const valorVal = row.querySelector('.ed-input-valor-pat').value.trim();
                const localVal = row.querySelector('.ed-select-local').value;
                const statusVal = row.querySelector('.ed-select-status').value;
                const situacaoVal = row.querySelector('.ed-select-situacao').value;
                
                if(patVal) {{
                    listaPatrimonios.push({{
                        nr_patrimonio: patVal,
                        valor_individual: valorVal,
                        local: localVal,
                        status_conferencia: statusVal,
                        status_movimentacao: situacaoVal
                    }});
                }}
            }});

            if(listaPatrimonios.length === 0) {{
                alert('Informe pelo menos um patrimônio!');
                return;
            }}

            const body = {{
                item_id: document.getElementById('ed_item_id').value,
                nome_material: document.getElementById('ed_nome').value,
                nr_ficha: document.getElementById('ed_ficha').value,
                nee_mat: document.getElementById('ed_nee').value,
                conta_contabil: document.getElementById('ed_conta').value,
                valor_unitario: document.getElementById('ed_valor').value,
                observacoes: document.getElementById('ed_observacoes').value,
                patrimonios: listaPatrimonios,
                foto_objeto: await fileToBase64(fObj),
                foto_etiqueta: await fileToBase64(fEtiq)
            }};

            const res = await fetch('/api/atualizar_cadastro', {{ method: 'POST', body: JSON.stringify(body) }});
            const result = await res.json();
            alert(result.message);
            if(result.success) {{
                document.getElementById('formEdicao').style.display = 'none';
                document.getElementById('ed_selecao_container').style.display = 'none';
                buscarParaEdicao();
                openTab('consulta');
            }}
        }}

        async function registrarSaida(e) {{
            e.preventDefault();
            const tipo = document.getElementById('s_tipo').value;
            const team = document.getElementById('s_team').value;

            if(tipo === 'Aguardando alienação' && !team) {{
                alert('Preenchimento do TEAM é obrigatório para Alienação!');
                return;
            }}

            const body = {{
                patrimonio: document.getElementById('s_pat').value,
                tipo: tipo,
                boletim: document.getElementById('s_bol').value,
                team: team
            }};
            const res = await fetch('/api/saida', {{ method: 'POST', body: JSON.stringify(body) }});
            const result = await res.json();
            alert(result.message);
            if(result.success) openTab('consulta');
        }}

        function gerarRelatorioPDF() {{
            const tipo = document.getElementById('r_tipo').value;
            const filtro = getFiltroValue();
            window.open(`/relatorio/imprimir?tipo=${{tipo}}&filtro=${{encodeURIComponent(filtro)}}`, '_blank');
        }}

        function exportarExcel() {{
            const tipo = document.getElementById('r_tipo').value;
            const filtro = getFiltroValue();
            window.location.href = `/api/exportar_csv?tipo=${{tipo}}&filtro=${{encodeURIComponent(filtro)}}`;
        }}

        function fecharModalResumo() {{
            document.getElementById('modal_resumo').style.display = 'none';
            openTab('consulta');
        }}

        async function importarPDF(e) {{
            e.preventDefault();
            const fileInput = document.getElementById('imp_arquivo_pdf').files[0];
            if(!fileInput) return;

            const elStatus = document.getElementById('progresso_status');
            const modalProgresso = document.getElementById('modal_progresso');
            const submitBtn = e.target.querySelector('button[type="submit"]');
            modalProgresso.style.display = 'flex';
            if (submitBtn) submitBtn.disabled = true;

            try {{
                elStatus.innerText = `Enviando PDF (${{(fileInput.size / 1024 / 1024).toFixed(1)}} MB)...`;
                // Envia o arquivo binário, sem converter para Base64 (economiza ~33% de memória).
                const res = await fetch('/api/importar_pdf', {{ method: 'POST', body: fileInput }});
                const start = await res.json();
                if (!start.success) throw new Error(start.message);

                let result = null;
                while (true) {{
                    await new Promise(r => setTimeout(r, 500));
                    const statusRes = await fetch(`/api/import_status?id=${{encodeURIComponent(start.job_id)}}`);
                    const status = await statusRes.json();
                    elStatus.innerText = `${{status.etapa}} (${{status.percentual}}%)`;
                    if (status.status === 'concluido') {{ result = {{success:true, data:status.resultado}}; break; }}
                    if (status.status === 'erro') {{ result = {{success:false, message:status.erro}}; break; }}
                }}

                if(result.success) {{
                    const data = result.data;
                    document.getElementById('res_total_pdf').innerText = data.total_pdf;
                    document.getElementById('res_mantidos').innerText = data.mantidos;
                    document.getElementById('res_qtd_incluidos').innerText = data.incluidos.length;
                    document.getElementById('res_lista_incluidos').innerHTML = data.incluidos.length ? data.incluidos.map(item => `<div>• ${{item}}</div>`).join('') : '<i style="color:#777;">Nenhum novo item incluído.</i>';
                    document.getElementById('res_qtd_removidos').innerText = data.removidos.length;
                    document.getElementById('res_lista_removidos').innerHTML = data.removidos.length ? data.removidos.map(item => `<div>• ${{item}}</div>`).join('') : '<i style="color:#777;">Nenhum item removido da carga.</i>';
                    document.getElementById('modal_resumo').style.display = 'flex';
                }} else alert(result.message);
            }} catch (err) {{
                alert('Erro ao importar PDF: ' + err.message);
            }} finally {{
                modalProgresso.style.display = 'none';
                if (submitBtn) submitBtn.disabled = false;
            }}
        }}

        window.onload = function() {{
            buscar();
            addPatrimonioRow();
        }};
    </script>
</body>
</html>
"""


# -----------------------------------------------------------------------------
# SERVIDOR HTTP LOCAL E API JSON
# -----------------------------------------------------------------------------
class RequestHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if not _requisicao_autenticada(self):
            _solicitar_login(self)
            return
        try:
            parsed_path = urllib.parse.urlparse(self.path)

            if parsed_path.path == "/":
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode("utf-8"))

            elif parsed_path.path == "/api/itens":
                params = urllib.parse.parse_qs(parsed_path.query)
                desc = params.get("desc", [""])[0]
                pat = params.get("pat", [""])[0]
                local = params.get("local", [""])[0]
                conta = params.get("conta", [""])[0]
                ficha = params.get("ficha", [""])[0]
                pendencias_raw = params.get("pendencias", [""])[0]
                pendencias = [
                    p.strip() for p in pendencias_raw.split(",")
                    if p.strip() in {"incompleto", "vistoria", "fora_carga"}
                ]

                try:
                    limit = min(max(int(params.get("limit", ["500"])[0]), 1), 500)
                except ValueError:
                    limit = 500
                rows = self.query_dados(desc, pat, local, conta, ficha, pendencias=pendencias)
                total = len(rows)
                rows = rows[:limit]

                resultados = []
                for r in rows:
                    resultados.append({
                        "nome_material": r[0],
                        "nr_ficha": r[1],
                        "nee_mat": r[2],
                        "conta_contabil": r[3],
                        "nr_patrimonio": r[4],
                        "local_armazenamento": r[5],
                        "data_conferencia": r[6],
                        "status_conferencia": r[7],
                        "status_movimentacao": r[8],
                        "boletim_admin": r[9],
                        "team": r[10],
                        "foto_objeto": r[11],
                        "foto_etiqueta": r[12],
                        "valor_individual": r[13],
                        "observacoes": r[14],
                    })

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"total": total, "itens": resultados}).encode("utf-8"))

            elif parsed_path.path == "/api/import_status":
                params = urllib.parse.parse_qs(parsed_path.query)
                job_id = params.get("id", [""])[0]
                with IMPORT_JOBS_LOCK:
                    job = dict(IMPORT_JOBS.get(job_id, {"status": "erro", "erro": "Importação não encontrada."}))
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(job).encode("utf-8"))

            elif parsed_path.path == "/api/resumo":
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()

                # 1. Agrupamento por Local
                c.execute("""
                    SELECT 
                        COALESCE(NULLIF(p.local_armazenamento, ''), 'Não informado') as local,
                        COUNT(p.id) as qtd,
                        SUM(COALESCE(p.valor_individual, i.valor_unitario, 0)) as valor
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                    GROUP BY COALESCE(NULLIF(p.local_armazenamento, ''), 'Não informado')
                    ORDER BY valor DESC
                """)
                locais = [
                    {"local": r[0], "qtd": r[1], "valor": r[2] or 0.0}
                    for r in c.fetchall()
                ]

                # 2. Agrupamento por Status de Conferência
                c.execute("""
                    SELECT 
                        COALESCE(NULLIF(p.status_conferencia, ''), 'Não conferido') as status,
                        COUNT(p.id) as qtd,
                        SUM(COALESCE(p.valor_individual, i.valor_unitario, 0)) as valor
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                    GROUP BY COALESCE(NULLIF(p.status_conferencia, ''), 'Não conferido')
                    ORDER BY qtd DESC
                """)
                status_list = [
                    {"status": r[0], "qtd": r[1], "valor": r[2] or 0.0}
                    for r in c.fetchall()
                ]

                # 3. Agrupamento por Situação / Movimentação
                c.execute("""
                    SELECT 
                        COALESCE(NULLIF(p.status_movimentacao, ''), 'Em carga') as situacao,
                        COUNT(p.id) as qtd,
                        SUM(COALESCE(p.valor_individual, i.valor_unitario, 0)) as valor
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                    GROUP BY COALESCE(NULLIF(p.status_movimentacao, ''), 'Em carga')
                    ORDER BY qtd DESC
                """)
                situacoes_list = [
                    {"situacao": r[0], "qtd": r[1], "valor": r[2] or 0.0}
                    for r in c.fetchall()
                ]

                # 4. Totais Gerais
                c.execute("""
                    SELECT 
                        COUNT(p.id) as total_patrimonios,
                        COUNT(DISTINCT i.id) as total_materiais,
                        SUM(COALESCE(p.valor_individual, i.valor_unitario, 0)) as valor_total
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                """)
                geral_row = c.fetchone()

                # 5. Análise de Incompletos / Pendências de Cadastro
                c.execute("""
                    SELECT 
                        COUNT(p.id) as qtd_incompletos,
                        SUM(COALESCE(p.valor_individual, i.valor_unitario, 0)) as valor_incompletos
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                    WHERE (i.nr_ficha IS NULL OR i.nr_ficha = '')
                       OR (i.nee_mat IS NULL OR i.nee_mat = '')
                       OR (i.conta_contabil IS NULL OR i.conta_contabil = '')
                       OR (p.local_armazenamento IS NULL OR p.local_armazenamento = '')
                       OR (p.data_conferencia IS NULL OR p.data_conferencia = '')
                """)
                inc_row = c.fetchone()

                # 6. Detalhamento Específico de Inconformidades
                c.execute("SELECT COUNT(i.id) FROM itens i WHERE i.foto_objeto IS NULL OR i.foto_objeto = ''")
                sem_foto_obj = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(i.id) FROM itens i WHERE i.foto_etiqueta IS NULL OR i.foto_etiqueta = ''")
                sem_foto_etiq = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(p.id) FROM patrimonios p WHERE p.local_armazenamento IS NULL OR p.local_armazenamento = ''")
                sem_local = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(i.id) FROM itens i WHERE i.nr_ficha IS NULL OR i.nr_ficha = ''")
                sem_ficha = c.fetchone()[0] or 0

                conn.close()

                resp = {
                    "geral": {
                        "total_patrimonios": geral_row[0] or 0,
                        "total_materiais": geral_row[1] or 0,
                        "valor_total": geral_row[2] or 0.0,
                        "qtd_incompletos": inc_row[0] or 0,
                        "valor_incompletos": inc_row[1] or 0.0,
                        "sem_foto_obj": sem_foto_obj,
                        "sem_foto_etiq": sem_foto_etiq,
                        "sem_local": sem_local,
                        "sem_ficha": sem_ficha,
                    },
                    "locais": locais,
                    "status": status_list,
                    "situacoes": situacoes_list,
                }

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode("utf-8"))

            elif parsed_path.path == "/api/buscar_edicao":
                params = urllib.parse.parse_qs(parsed_path.query)
                ficha = params.get("ficha", [""])[0]
                conta = params.get("conta", [""])[0]

                resultados = self.buscar_materiais_para_edicao(ficha, conta)
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resultados).encode("utf-8"))

            elif parsed_path.path == "/relatorio/imprimir":
                params = urllib.parse.parse_qs(parsed_path.query)
                tipo = params.get("tipo", ["completo_sem_fotos"])[0]
                filtro = params.get("filtro", [""])[0]
                self.gerar_pagina_relatorio(tipo, filtro)

            elif parsed_path.path == "/api/exportar_csv":
                params = urllib.parse.parse_qs(parsed_path.query)
                tipo = params.get("tipo", ["completo_sem_fotos"])[0]
                filtro = params.get("filtro", [""])[0]
                self.gerar_download_csv(tipo, filtro)

            else:
                super().do_GET()

        except Exception as e:
            print(f"❌ Erro na requisição GET: {e}")
            self.send_response(500)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<h2>Erro interno do servidor:</h2><pre>{e}</pre>".encode("utf-8")
            )

    def buscar_materiais_para_edicao(self, ficha="", conta=""):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        query = (
            "SELECT id, nome_material, nr_ficha, nee_mat, conta_contabil,"
            " valor_unitario, foto_objeto, foto_etiqueta, observacoes FROM itens WHERE 1=1"
        )
        params = []

        if ficha:
            query += " AND nr_ficha LIKE ?"
            params.append(f"%{ficha}%")
        if conta:
            query += " AND conta_contabil LIKE ?"
            params.append(f"%{conta}%")

        c.execute(query, params)
        itens_rows = c.fetchall()

        lista_materiais = []
        for row in itens_rows:
            item_id = row[0]
            c.execute(
                "SELECT nr_patrimonio, local_armazenamento, status_conferencia,"
                " status_movimentacao, valor_individual FROM patrimonios WHERE item_id = ?",
                (item_id,),
            )
            pat_rows = c.fetchall()

            patrimonios = [
                {
                    "nr_patrimonio": p[0],
                    "local_armazenamento": p[1],
                    "status_conferencia": p[2] or "Não conferido",
                    "status_movimentacao": p[3] or "Em carga",
                    "valor_individual": p[4] if p[4] is not None else (row[5] or 0.0),
                }
                for p in pat_rows
            ]

            lista_materiais.append({
                "id": row[0],
                "nome_material": row[1],
                "nr_ficha": row[2],
                "nee_mat": row[3],
                "conta_contabil": row[4],
                "valor_unitario": row[5],
                "foto_objeto": row[6],
                "foto_etiqueta": row[7],
                "observacoes": row[8],
                "patrimonios": patrimonios,
            })

        conn.close()
        return lista_materiais

    def query_dados(
        self,
        desc="",
        pat="",
        local="",
        conta="",
        ficha="",
        status="",
        situacao="",
        pendencias=None,
    ):
        query = """
            SELECT i.nome_material, i.nr_ficha, i.nee_mat, i.conta_contabil,
                   p.nr_patrimonio, p.local_armazenamento, p.data_conferencia,
                   p.status_conferencia, p.status_movimentacao, p.boletim_admin, p.team,
                   i.foto_objeto, i.foto_etiqueta,
                   COALESCE(p.valor_individual, i.valor_unitario, 0.0) as valor_patrimonio,
                   i.observacoes
            FROM itens i
            LEFT JOIN patrimonios p ON i.id = p.item_id
            WHERE 1=1
        """
        sql_params = []
        if desc:
            query += " AND i.nome_material LIKE ?"
            sql_params.append(f"%{desc}%")
        if pat:
            query += " AND p.nr_patrimonio LIKE ?"
            sql_params.append(f"%{pat}%")
        if local:
            query += " AND p.local_armazenamento LIKE ?"
            sql_params.append(f"%{local}%")
        if conta:
            query += " AND i.conta_contabil LIKE ?"
            sql_params.append(f"%{conta}%")
        if ficha:
            query += " AND i.nr_ficha LIKE ?"
            sql_params.append(f"%{ficha}%")
        if status:
            query += " AND p.status_conferencia LIKE ?"
            sql_params.append(f"%{status}%")
        if situacao:
            query += " AND p.status_movimentacao LIKE ?"
            sql_params.append(f"%{situacao}%")

        # Filtros combináveis: quando vários são selecionados, usa-se OR
        # entre as categorias de pendência.
        pendencias_validas = set(pendencias or [])
        condicoes_pendencia = []

        if "incompleto" in pendencias_validas:
            condicoes_pendencia.append(
                "(i.nr_ficha IS NULL OR i.nr_ficha = '' "
                "OR i.nee_mat IS NULL OR i.nee_mat = '' "
                "OR i.conta_contabil IS NULL OR i.conta_contabil = '' "
                "OR p.local_armazenamento IS NULL OR p.local_armazenamento = '' "
                "OR p.data_conferencia IS NULL OR p.data_conferencia = '')"
            )

        if "vistoria" in pendencias_validas:
            condicoes_pendencia.append(
                "(p.status_conferencia IS NULL OR p.status_conferencia = 'Não conferido')"
            )

        if "fora_carga" in pendencias_validas:
            condicoes_pendencia.append("(p.status_movimentacao = 'Fora de carga')")

        if condicoes_pendencia:
            query += " AND (" + " OR ".join(condicoes_pendencia) + ")"

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(query, sql_params)
        rows = c.fetchall()
        conn.close()
        return rows

    def gerar_pagina_relatorio(self, tipo, filtro):
        desc = pat = local = conta = ficha = status = situacao = ""
        if tipo == "por_ficha":
            ficha = filtro
        elif tipo == "por_conta":
            conta = filtro
        elif tipo == "por_local":
            local = filtro
        elif tipo == "por_status":
            status = filtro
        elif tipo == "por_situacao":
            situacao = filtro

        rows = self.query_dados(
            desc, pat, local, conta, ficha, status, situacao
        )
        com_fotos = tipo == "completo_fotos"

        html_relatorio = f"""<!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <title>Relatório de Patrimônio - IME / SE/4</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; color: #111; }}
                h2 {{ text-align: center; margin-bottom: 5px; }}
                p.sub {{ text-align: center; color: #555; font-size: 13px; margin-top: 0; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }}
                th, td {{ border: 1px solid #444; padding: 6px; text-align: left; }}
                th {{ background: #2b3e50; color: white; }}
                tr:nth-child(even) {{ background: #f2f2f2; }}
                .btn-print {{ background: #1b365d; color: white; border: none; padding: 10px 20px; cursor: pointer; font-size: 14px; margin-bottom: 15px; border-radius: 4px; }}
                @media print {{ .btn-print {{ display: none; }} }}
                .img-thumb {{ max-width: 80px; max-height: 80px; }}
            </style>
        </head>
        <body>
            <button class="btn-print" onclick="window.print()">🖨️ Imprimir / Salvar em PDF</button>
            <h2>RELATÓRIO DE CONFERÊNCIA DE PATRIMÔNIO - IME / SE/4</h2>
            <p class="sub">Emissão em: {date.today().strftime('%d/%m/%Y')} | Total de Registros: {len(rows)}</p>
            <table>
                <thead>
                    <tr>
                        <th>Material</th><th>Ficha</th><th>NEE</th><th>Conta</th><th>Patrimônio</th><th>Valor (R$)</th>
                        <th>Local</th><th>Últ. Conf.</th><th>Status</th><th>Movimentação</th><th>Boletim/TEAM</th>
                        {"<th>Fotos</th>" if com_fotos else ""}
                    </tr>
                </thead>
                <tbody>
        """
        for r in rows:
            foto_td = ""
            if com_fotos:
                obj_img = (
                    f'<img src="/{r[11]}" class="img-thumb">' if r[11] else ""
                )
                etiq_img = (
                    f'<img src="/{r[12]}" class="img-thumb">' if r[12] else ""
                )
                foto_td = f"<td>{obj_img} {etiq_img}</td>"

            val_formatted = f"R$ {r[13]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            html_relatorio += f"""
                <tr>
                    <td><b>{r[0] or ''}</b></td>
                    <td>{r[1] or ''}</td>
                    <td>{r[2] or ''}</td>
                    <td>{r[3] or ''}</td>
                    <td><b>{r[4] or ''}</b></td>
                    <td><b>{val_formatted}</b></td>
                    <td>{r[5] or ''}</td>
                    <td>{r[6] or ''}</td>
                    <td>{r[7] or 'Não conferido'}</td>
                    <td>{r[8] or ''}</td>
                    <td>{r[9] or ''} {f' / TEAM: {r[10]}' if r[10] else ''}</td>
                    {foto_td}
                </tr>
            """

        html_relatorio += """
                </tbody>
            </table>
        </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_relatorio.encode("utf-8"))

    def gerar_download_csv(self, tipo, filtro):
        desc = pat = local = conta = ficha = status = situacao = ""
        if tipo == "por_ficha":
            ficha = filtro
        elif tipo == "por_conta":
            conta = filtro
        elif tipo == "por_local":
            local = filtro
        elif tipo == "por_status":
            status = filtro
        elif tipo == "por_situacao":
            situacao = filtro

        rows = self.query_dados(
            desc, pat, local, conta, ficha, status, situacao
        )

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow([
            "Material",
            "Ficha",
            "NEE",
            "Conta Contabil",
            "Patrimonio",
            "Valor Individual (RS)",
            "Local",
            "Data Conferencia",
            "Status Conferencia",
            "Status Movimentacao",
            "Boletim Admin",
            "TEAM",
        ])

        for r in rows:
            writer.writerow([
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                f"{r[13]:.2f}".replace(".", ","),
                r[5],
                r[6],
                r[7] or "Não conferido",
                r[8],
                r[9],
                r[10],
            ])

        csv_data = output.getvalue().encode("utf-8-sig")

        self.send_response(200)
        self.send_header("Content-type", "text/csv; charset=utf-8-sig")
        self.send_header(
            "Content-Disposition", "attachment; filename=relatorio_patrimonio.csv"
        )
        self.end_headers()
        self.wfile.write(csv_data)

    def do_POST(self):
        if not _requisicao_autenticada(self):
            _solicitar_login(self)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            post_data = self.rfile.read(content_length)
            is_pdf_upload = self.path == "/api/importar_pdf" and self.headers.get("Content-Type", "").split(";", 1)[0].lower() == "application/pdf"
            data = {} if is_pdf_upload else json.loads(post_data.decode("utf-8"))

            def save_base64_img(b64_str, prefix):
                if not b64_str or "," not in str(b64_str):
                    return None
                header, encoded = b64_str.split(",", 1)
                ext = header.split(";")[0].split("/")[1]
                filename = f"{prefix}_{os.urandom(4).hex()}.{ext}"
                filepath = os.path.join(UPLOAD_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(encoded))
                return filepath

            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()

            if self.path == "/api/cadastrar":
                patrimonios = data.get("patrimonios", [])
                pats_nums = [
                    p.get("nr_patrimonio", "").strip()
                    for p in patrimonios
                    if p.get("nr_patrimonio", "").strip()
                ]

                if len(pats_nums) != len(set(pats_nums)):
                    conn.close()
                    resp = {
                        "success": False,
                        "message": (
                            "O formulário contém números de patrimônio"
                            " duplicados entre si."
                        ),
                    }
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps(resp).encode("utf-8"))
                    return

                if pats_nums:
                    placeholders = ",".join(["?"] * len(pats_nums))
                    c.execute(
                        f"SELECT nr_patrimonio FROM patrimonios WHERE nr_patrimonio IN ({placeholders})",
                        pats_nums,
                    )
                    existentes = [r[0] for r in c.fetchall()]

                    if existentes:
                        conn.close()
                        resp = {
                            "success": False,
                            "message": (
                                "Erro ao cadastrar: O(s) patrimônio(s) já"
                                f" consta(m) cadastrado(s): {', '.join(existentes)}"
                            ),
                        }
                        self.send_response(200)
                        self.send_header("Content-type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps(resp).encode("utf-8"))
                        return

                path_obj = save_base64_img(data.get("foto_objeto"), "OBJ")
                path_etiq = save_base64_img(data.get("foto_etiqueta"), "ETIQ")
                val_default = parse_float(data.get("valor_unitario"))

                c.execute(
                    """
                    INSERT INTO itens (nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario, foto_objeto, foto_etiqueta, observacoes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        data["nome_material"],
                        data["nr_ficha"],
                        data["nee_mat"],
                        data["conta_contabil"],
                        val_default,
                        path_obj,
                        path_etiq,
                        data.get("observacoes", ""),
                    ),
                )

                item_id = c.lastrowid
                hoje = date.today().strftime("%d/%m/%Y")
                inseridos = 0

                for p in patrimonios:
                    pat_num = p.get("nr_patrimonio", "").strip()
                    pat_val = parse_float(p.get("valor_individual")) if p.get("valor_individual") else val_default
                    pat_local = p.get("local", "Lab Motores")
                    pat_status = p.get("status_conferencia", "Não conferido")
                    pat_situacao = p.get("status_movimentacao", "Em carga")

                    if pat_num:
                        c.execute(
                            """
                            INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia, status_movimentacao, valor_individual)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                item_id,
                                pat_num,
                                pat_local,
                                hoje,
                                pat_status,
                                pat_situacao,
                                pat_val,
                            ),
                        )
                        inseridos += 1

                conn.commit()
                conn.close()
                resp = {
                    "success": True,
                    "message": f"Material e {inseridos} patrimônio(s) registrados com sucesso!",
                }

            elif self.path == "/api/atualizar_cadastro":
                item_id = data.get("item_id")
                nome = data.get("nome_material")
                ficha = data.get("nr_ficha")
                nee = data.get("nee_mat")
                conta = data.get("conta_contabil")
                val_default = parse_float(data.get("valor_unitario"))

                c.execute(
                    """
                    UPDATE itens
                    SET nome_material = ?, nr_ficha = ?, nee_mat = ?, conta_contabil = ?, valor_unitario = ?, observacoes = ?
                    WHERE id = ?
                """,
                    (nome, ficha, nee, conta, val_default, data.get("observacoes", ""), item_id),
                )

                if data.get("foto_objeto") and "," in str(data.get("foto_objeto")):
                    path_obj = save_base64_img(data["foto_objeto"], "OBJ")
                    c.execute(
                        "UPDATE itens SET foto_objeto = ? WHERE id = ?",
                        (path_obj, item_id),
                    )

                if data.get("foto_etiqueta") and "," in str(data.get("foto_etiqueta")):
                    path_etiq = save_base64_img(data["foto_etiqueta"], "ETIQ")
                    c.execute(
                        "UPDATE itens SET foto_etiqueta = ? WHERE id = ?",
                        (path_etiq, item_id),
                    )

                patrimonios_novos = data.get("patrimonios", [])
                pats_mantidos = []
                hoje = date.today().strftime("%d/%m/%Y")

                for p in patrimonios_novos:
                    pat_num = p.get("nr_patrimonio", "").strip()
                    pat_local = p.get("local", "Lab Motores")
                    pat_status = p.get("status_conferencia", "Não conferido")
                    pat_situacao = p.get("status_movimentacao", "Em carga")
                    pat_val = parse_float(p.get("valor_individual")) if p.get("valor_individual") else val_default

                    if not pat_num:
                        continue

                    pats_mantidos.append(pat_num)

                    c.execute(
                        "SELECT id FROM patrimonios WHERE nr_patrimonio = ?",
                        (pat_num,),
                    )
                    row = c.fetchone()
                    if row:
                        c.execute(
                            """
                            UPDATE patrimonios 
                            SET item_id = ?, local_armazenamento = ?, status_conferencia = ?, status_movimentacao = ?, valor_individual = ?, data_conferencia = ?
                            WHERE id = ?
                        """,
                            (item_id, pat_local, pat_status, pat_situacao, pat_val, hoje, row[0]),
                        )
                    else:
                        c.execute(
                            """
                            INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia, status_movimentacao, valor_individual)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                item_id,
                                pat_num,
                                pat_local,
                                hoje,
                                pat_status,
                                pat_situacao,
                                pat_val,
                            ),
                        )

                if pats_mantidos:
                    placeholders = ",".join(["?"] * len(pats_mantidos))
                    c.execute(
                        f"DELETE FROM patrimonios WHERE item_id = ? AND nr_patrimonio NOT IN ({placeholders})",
                        [item_id] + pats_mantidos,
                    )
                else:
                    c.execute("DELETE FROM patrimonios WHERE item_id = ?", (item_id,))

                conn.commit()
                conn.close()
                resp = {
                    "success": True,
                    "message": "Cadastro atualizado com sucesso!",
                }

            elif self.path == "/api/saida":
                c.execute(
                    """
                    UPDATE patrimonios
                    SET status_movimentacao = ?, boletim_admin = ?, team = ?
                    WHERE nr_patrimonio = ?
                """,
                    (
                        data["tipo"],
                        data["boletim"],
                        data["team"],
                        data["patrimonio"],
                    ),
                )
                conn.commit()
                conn.close()
                resp = {
                    "success": True,
                    "message": f"Movimentação ({data['tipo']}) registrada!",
                }

            elif self.path == "/api/importar_pdf":
                # Recebe application/pdf diretamente; evita JSON + Base64.
                conn.close()
                if not post_data:
                    resp = {"success": False, "message": "Arquivo PDF vazio."}
                else:
                    job_id = iniciar_importacao_pdf(post_data)
                    resp = {"success": True, "job_id": job_id}


            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))

        except Exception as e:
            print(f"❌ Erro no processamento de requisição POST: {e}")
            self.send_response(500)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    "success": False,
                    "message": f"Erro interno: {e}",
                }).encode("utf-8")
            )


# -----------------------------------------------------------------------------
# EXECUÇÃO DO SERVIDOR COM BUSCA AUTOMÁTICA DE PORTA LIVRE
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    port = PORT

    while True:
        try:
            with socketserver.ThreadingTCPServer(("", port), RequestHandler) as httpd:
                print(f"🚀 Servidor rodando em http://localhost:{port}")
                webbrowser.open(f"http://localhost:{port}")
                httpd.serve_forever()
            break
        except OSError as e:
            if (
                getattr(e, "winerror", None) == 10048
                or getattr(e, "errno", None) == 98
            ):
                port += 1
            else:
                raise e