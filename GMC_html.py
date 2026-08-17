import http.server
import socketserver
import sqlite3
import json
import urllib.parse
import os
import webbrowser
import base64
import csv
import io
from datetime import date

PORT = 8000
DB_NAME = "patrimonio_se4.db"
UPLOAD_DIR = "fotos_patrimonio"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# =============================================================================
# ⚙️ CONFIGURAÇÃO DE OPÇÕES DOS MENUS SUSPENSOS
# =============================================================================
LOCAIS_DISPONIVEIS = [
    "Lab Motores",
    "Lab Projetos Mecänicos",
    "Lab Aerodinämica",
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
    "Sala 4026",
    "Sala PGMEC",
    "Material de TI",
]

STATUS_CONFERENCIA_OPTS = [
    "Conferido",
    "Não Encontrado",
    "Fora da SE"
]

SITUACAO_OPTS = [
    "Em carga",
    "Aguardando transferência",
    "Aguardando alienação",
    "Aguardando recolhimento"
]
# =============================================================================

# -----------------------------------------------------------------------------
# BANCO DE DADOS (SQLite)
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_material TEXT NOT NULL,
            nr_ficha TEXT,
            nee_mat TEXT,
            conta_contabil TEXT,
            acervo TEXT,
            valor_unitario REAL,
            foto_objeto TEXT,
            foto_etiqueta TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS patrimonios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            nr_patrimonio TEXT UNIQUE NOT NULL,
            local_armazenamento TEXT,
            data_conferencia TEXT,
            status_conferencia TEXT,
            status_movimentacao TEXT DEFAULT 'Em carga',
            boletim_admin TEXT,
            team TEXT,
            FOREIGN KEY (item_id) REFERENCES itens (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Monta as opções do menu suspenso para o HTML
OPTIONS_LOCAIS = "".join([f'<option value="{loc}">{loc}</option>' for loc in LOCAIS_DISPONIVEIS])
OPTIONS_STATUS = "".join([f'<option value="{st}">{st}</option>' for st in STATUS_CONFERENCIA_OPTS])
OPTIONS_SITUACAO = "".join([f'<option value="{sit}">{sit}</option>' for sit in SITUACAO_OPTS])

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
        .container {{ max-width: 1300px; margin: 0 auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
        h1 {{ color: var(--primary); margin-top: 0; border-bottom: 2px solid var(--primary); padding-bottom: 10px; }}
        .tabs {{ display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 2px solid #ddd; flex-wrap: wrap; }}
        .tab-btn {{ padding: 10px 18px; border: none; background: #e0e0e0; cursor: pointer; border-radius: 5px 5px 0 0; font-weight: bold; font-size: 14px; transition: 0.2s; }}
        .tab-btn.active {{ background: var(--primary); color: white; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .form-group {{ margin-bottom: 15px; }}
        label {{ display: block; font-weight: bold; margin-bottom: 5px; font-size: 14px; }}
        input, select, textarea {{ width: 100%; padding: 9px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; font-size: 13px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; }}
        button.btn-submit {{ background: var(--primary); color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; font-weight: bold; margin-top: 10px; }}
        button.btn-submit:hover {{ background: #132744; }}
        button.btn-excel {{ background: #1d6f42; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; font-weight: bold; margin-top: 10px; }}
        button.btn-excel:hover {{ background: #144e2e; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        th, td {{ border: 1px solid #ddd; padding: 9px; text-align: left; font-size: 13px; }}
        th {{ background: var(--primary); color: white; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .badge {{ padding: 3px 8px; border-radius: 3px; font-weight: bold; color: white; font-size: 11px; }}
        .bg-conferido {{ background: #2e7d32; }}
        .bg-nao-encontrado {{ background: #f57c00; }}
        .bg-fora-se {{ background: #d32f2f; }}
        .box-info {{ background: #eef4fc; border-left: 4px solid var(--primary); padding: 12px; margin-bottom: 15px; font-size: 14px; }}
        .patrimonio-header-row {{ display: grid; grid-template-columns: 2fr 2fr 2fr 2fr 80px; gap: 8px; font-weight: bold; font-size: 12px; margin-bottom: 5px; color: #555; background: #eaeff5; padding: 8px; border-radius: 4px; }}
        
        /* Estilos do Dashboard/Resumo */
        .kpi-container {{ display: flex; gap: 20px; margin-bottom: 25px; flex-wrap: wrap; }}
        .kpi-card {{ flex: 1; min-width: 220px; background: #f8fafc; border: 1px solid #e2e8f0; border-left: 5px solid var(--primary); border-radius: 6px; padding: 18px; box-shadow: 0 2px 5px rgba(0,0,0,0.03); }}
        .kpi-card .kpi-title {{ font-size: 13px; color: #64748b; font-weight: bold; text-transform: uppercase; margin-bottom: 6px; }}
        .kpi-card .kpi-value {{ font-size: 24px; color: var(--primary); font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📦 Sistema de Conferência de Carga (IME / SE/4)</h1>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="openTab('consulta')">🔍 Consultar</button>
            <button class="tab-btn" onclick="openTab('resumo')">📈 Resumo</button>
            <button class="tab-btn" onclick="openTab('cadastro')">➕ Cadastrar Material</button>
            <button class="tab-btn" onclick="openTab('alterar_cadastro')">📝 Alterar Cadastro</button>
            <button class="tab-btn" onclick="openTab('saida')">🚚 Saída / Alienação</button>
            <button class="tab-btn" onclick="openTab('relatorios')">📊 Relatórios</button>
            <button class="tab-btn" onclick="openTab('importar')">📥 Importar Planilha</button>
        </div>

        <!-- ABA 1: CONSULTA -->
        <div id="consulta" class="tab-content active">
            <div class="grid">
                <div><label>Descrição:</label><input type="text" id="b_desc" oninput="buscar()"></div>
                <div><label>Nº Patrimônio:</label><input type="text" id="b_pat" oninput="buscar()"></div>
                <div><label>Local:</label><input type="text" id="b_local" oninput="buscar()" placeholder="Filtrar local..."></div>
                <div><label>Conta Contábil:</label><input type="text" id="b_conta" oninput="buscar()"></div>
                <div><label>Nº Ficha:</label><input type="text" id="b_ficha" oninput="buscar()"></div>
            </div>
            <div id="resultado_count" style="margin-top: 15px; font-weight: bold; color: var(--primary);"></div>
            <div style="overflow-x: auto;">
                <table id="tabela_dados">
                    <thead>
                        <tr>
                            <th>Descrição</th><th>Ficha</th><th>NEE</th><th>Conta</th><th>Patrimônio</th>
                            <th>Local</th><th>Últ. Conf.</th><th>Status</th><th>Situação</th><th>Boletim/TEAM</th><th>Fotos</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>

        <!-- ABA 2: RESUMO / DASHBOARD -->
        <div id="resumo" class="tab-content">
            <div class="box-info">
                📊 <b>Resumo Geral do Acervo e Carga Patrimonial</b><br>
                Acompanhe o total de patrimônios registrados, o valor total acumulado e a distribuição de bens por local de armazenamento.
            </div>

            <div class="kpi-container">
                <div class="kpi-card">
                    <div class="kpi-title">Total de Patrimônios</div>
                    <div class="kpi-value" id="kpi_total_pat">0</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-title">Materiais / Fichas</div>
                    <div class="kpi-value" id="kpi_total_mat">0</div>
                </div>
                <div class="kpi-card" style="border-left-color: #2e7d32;">
                    <div class="kpi-title">Valor Total do Acervo</div>
                    <div class="kpi-value" id="kpi_valor_total" style="color: #2e7d32;">R$ 0,00</div>
                </div>
            </div>

            <h3>📍 Valor e Quantidade de Itens por Local</h3>
            <div style="overflow-x: auto;">
                <table id="tabela_resumo">
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

        <!-- ABA 3: CADASTRO -->
        <div id="cadastro" class="tab-content">
            <form id="formCadastro" onsubmit="cadastrarMaterial(event)">
                <div class="grid">
                    <div><label>Descrição do Material *</label><input type="text" id="c_nome" required></div>
                    <div><label>Nº Ficha</label><input type="text" id="c_ficha"></div>
                    <div><label>NEE Mat.</label><input type="text" id="c_nee"></div>
                    <div><label>Conta Contábil</label><input type="text" id="c_conta"></div>
                    <div><label>Valor Unitário (R$)</label><input type="number" step="0.01" id="c_valor"></div>
                </div>

                <div class="form-group" style="margin-top: 20px;">
                    <label>Patrimônios Associados *</label>
                    <div class="patrimonio-header-row">
                        <div>Nº Patrimônio</div>
                        <div>Local de Armazenamento</div>
                        <div>Status Conferência</div>
                        <div>Situação / Movimentação</div>
                        <div>Ação</div>
                    </div>
                    <div id="patrimonios_container"></div>
                    <button type="button" class="btn-submit" style="background: #4a777a; margin-top: 8px; font-size: 13px;" onclick="addPatrimonioRow()">➕ Adicionar Outro Patrimônio</button>
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
                    <div><label>Valor Unitário (R$)</label><input type="number" step="0.01" id="ed_valor"></div>
                </div>

                <div class="form-group" style="margin-top: 20px;">
                    <label>Patrimônios Associados *</label>
                    <div class="patrimonio-header-row">
                        <div>Nº Patrimônio</div>
                        <div>Local de Armazenamento</div>
                        <div>Status Conferência</div>
                        <div>Situação / Movimentação</div>
                        <div>Ação</div>
                    </div>
                    <div id="ed_patrimonios_container"></div>
                    <button type="button" class="btn-submit" style="background: #4a777a; margin-top: 8px; font-size: 13px;" onclick="addEdPatrimonioRow()">➕ Adicionar Outro Patrimônio</button>
                </div>

                <div class="grid" style="margin-top: 15px;">
                    <div>
                        <label>Foto do Objeto (Deixe em branco para manter a atual)</label>
                        <input type="file" id="ed_foto_obj" accept="image/*">
                        <div id="ed_preview_obj" style="margin-top: 5px; font-size: 12px; color: #666;"></div>
                    </div>
                    <div>
                        <label>Foto da Etiqueta (Deixe em branco para manter a atual)</label>
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
                    <input type="text" id="r_filtro_val" placeholder="Digite o termo do filtro...">
                </div>
            </div>

            <div style="margin-top: 20px; display: flex; gap: 15px;">
                <button type="button" class="btn-submit" onclick="gerarRelatorioPDF()">📄 Gerar Visualização / Salvar em PDF</button>
                <button type="button" class="btn-excel" onclick="exportarExcel()">📊 Exportar para Excel (.csv)</button>
            </div>
        </div>

        <!-- ABA 7: IMPORTAR PLANILHA -->
        <div id="importar" class="tab-content">
            <div class="box-info">
                📥 <b>Importação de Dados em Massa via Planilha CSV</b><br>
                Selecione um arquivo <b>.CSV</b> (separado por vírgula ou ponto e vírgula) para cadastrar materiais e patrimônios automaticamente.<br>
                <b>Colunas esperadas na planilha:</b> <code>nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario, nr_patrimonio, local_armazenamento</code>
            </div>
            
            <form onsubmit="importarCSV(event)">
                <div class="form-group">
                    <label>Selecione o Arquivo CSV *</label>
                    <input type="file" id="imp_arquivo" accept=".csv" required>
                </div>
                <button type="submit" class="btn-submit">Processar e Importar Planilha</button>
            </form>
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
            event.target.classList.add('active');
            if(tabId === 'consulta') buscar();
            if(tabId === 'resumo') carregarResumo();
        }}

        async function carregarResumo() {{
            const res = await fetch('/api/resumo');
            const data = await res.json();

            const fmtMoeda = (val) => val.toLocaleString('pt-BR', {{ style: 'currency', currency: 'BRL' }});

            document.getElementById('kpi_total_pat').innerText = data.geral.total_patrimonios;
            document.getElementById('kpi_total_mat').innerText = data.geral.total_materiais;
            document.getElementById('kpi_valor_total').innerText = fmtMoeda(data.geral.valor_total);

            const tbody = document.querySelector('#tabela_resumo tbody');
            tbody.innerHTML = '';

            const valorGeral = data.geral.valor_total || 1;

            data.locais.forEach(item => {{
                const perc = ((item.valor / valorGeral) * 100).toFixed(1);
                let row = `<tr>
                    <td><b>${{item.local}}</b></td>
                    <td>${{item.qtd}}</td>
                    <td>${{fmtMoeda(item.valor)}}</td>
                    <td>${{perc}}%</td>
                </tr>`;
                tbody.innerHTML += row;
            }});
        }}

        function addPatrimonioRow(patVal = '', localVal = '', statusVal = 'Conferido', situacaoVal = 'Em carga') {{
            const container = document.getElementById('patrimonios_container');
            const div = document.createElement('div');
            div.className = 'grid patrimonio-row';
            div.style.gridTemplateColumns = '2fr 2fr 2fr 2fr 80px';
            div.style.gap = '8px';
            div.style.marginBottom = '10px';
            div.innerHTML = `
                <div><input type="text" class="input-pat" placeholder="Nº Patrimônio" value="${{patVal}}" required></div>
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

        function addEdPatrimonioRow(patVal = '', localVal = '', statusVal = 'Conferido', situacaoVal = 'Em carga') {{
            const container = document.getElementById('ed_patrimonios_container');
            const div = document.createElement('div');
            div.className = 'grid ed-patrimonio-row';
            div.style.gridTemplateColumns = '2fr 2fr 2fr 2fr 80px';
            div.style.gap = '8px';
            div.style.marginBottom = '10px';
            div.innerHTML = `
                <div><input type="text" class="ed-input-pat" placeholder="Nº Patrimônio" value="${{patVal}}" required></div>
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
            const inputFiltro = document.getElementById('r_filtro_val');

            if(tipo === 'por_ficha') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Digite o Nº da Ficha:';
                inputFiltro.placeholder = 'Ex: 1045';
            }} else if(tipo === 'por_conta') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Digite a Conta Contábil:';
                inputFiltro.placeholder = 'Ex: 14211.01';
            }} else if(tipo === 'por_local') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Digite o Nome do Local:';
                inputFiltro.placeholder = 'Ex: Lab Motores';
            }} else if(tipo === 'por_status') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Status de Conferência:';
                inputFiltro.placeholder = 'Conferido, Não Encontrado ou Fora da SE';
            }} else if(tipo === 'por_situacao') {{
                divFiltro.style.display = 'block';
                lblFiltro.innerText = 'Situação / Movimentação:';
                inputFiltro.placeholder = 'Em carga, Aguardando transferência, etc.';
            }} else {{
                divFiltro.style.display = 'none';
            }}
        }}

        async function buscar() {{
            const desc = document.getElementById('b_desc').value;
            const pat = document.getElementById('b_pat').value;
            const local = document.getElementById('b_local').value;
            const conta = document.getElementById('b_conta').value;
            const ficha = document.getElementById('b_ficha').value;

            const res = await fetch(`/api/itens?desc=${{desc}}&pat=${{pat}}&local=${{local}}&conta=${{conta}}&ficha=${{ficha}}`);
            const data = await res.json();

            document.getElementById('resultado_count').innerText = `Total de registros encontrados: ${{data.length}}`;
            const tbody = document.querySelector('#tabela_dados tbody');
            tbody.innerHTML = '';

            data.forEach(item => {{
                let badgeClass = 'bg-conferido';
                if(item.status_conferencia === 'Não Encontrado') badgeClass = 'bg-nao-encontrado';
                else if(item.status_conferencia === 'Fora da SE') badgeClass = 'bg-fora-se';

                let row = `<tr>
                    <td><b>${{item.nome_material || ''}}</b></td>
                    <td>${{item.nr_ficha || ''}}</td>
                    <td>${{item.nee_mat || ''}}</td>
                    <td>${{item.conta_contabil || ''}}</td>
                    <td><b>${{item.nr_patrimonio || ''}}</b></td>
                    <td>${{item.local_armazenamento || ''}}</td>
                    <td>${{item.data_conferencia || ''}}</td>
                    <td><span class="badge ${{badgeClass}}">${{item.status_conferencia || ''}}</span></td>
                    <td>${{item.status_movimentacao || ''}}</td>
                    <td>${{item.boletim_admin ? 'Bol: ' + item.boletim_admin : ''}} ${{item.team ? '<br>TEAM: ' + item.team : ''}}</td>
                    <td>
                        ${{item.foto_objeto ? `<a href="${{item.foto_objeto}}" target="_blank">📷 Objeto</a> ` : ''}}
                        ${{item.foto_etiqueta ? `<a href="${{item.foto_etiqueta}}" target="_blank">🏷️ Etiqueta</a>` : ''}}
                    </td>
                </tr>`;
                tbody.innerHTML += row;
            }});
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
                const localVal = row.querySelector('.select-local').value;
                const statusVal = row.querySelector('.select-status').value;
                const situacaoVal = row.querySelector('.select-situacao').value;
                if(patVal) {{
                    listaPatrimonios.push({{
                        nr_patrimonio: patVal,
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
            document.getElementById('ed_valor').value = mat.valor_unitario || 0;

            document.getElementById('ed_preview_obj').innerText = mat.foto_objeto ? "📷 Possui foto do objeto cadastrada." : "Nenhuma foto de objeto cadastrada.";
            document.getElementById('ed_preview_etiq').innerText = mat.foto_etiqueta ? "🏷️ Possui foto de etiqueta cadastrada." : "Nenhuma foto de etiqueta cadastrada.";

            const container = document.getElementById('ed_patrimonios_container');
            container.innerHTML = '';

            if(mat.patrimonios && mat.patrimonios.length > 0) {{
                mat.patrimonios.forEach(p => {{
                    addEdPatrimonioRow(p.nr_patrimonio, p.local_armazenamento, p.status_conferencia, p.status_movimentacao);
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
                const localVal = row.querySelector('.ed-select-local').value;
                const statusVal = row.querySelector('.ed-select-status').value;
                const situacaoVal = row.querySelector('.ed-select-situacao').value;
                if(patVal) {{
                    listaPatrimonios.push({{
                        nr_patrimonio: patVal,
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
            const filtro = document.getElementById('r_filtro_val').value;
            window.open(`/relatorio/imprimir?tipo=${{tipo}}&filtro=${{encodeURIComponent(filtro)}}`, '_blank');
        }}

        function exportarExcel() {{
            const tipo = document.getElementById('r_tipo').value;
            const filtro = document.getElementById('r_filtro_val').value;
            window.location.href = `/api/exportar_csv?tipo=${{tipo}}&filtro=${{encodeURIComponent(filtro)}}`;
        }}

        async function importarCSV(e) {{
            e.preventDefault();
            const fileInput = document.getElementById('imp_arquivo').files[0];
            if(!fileInput) return;

            const reader = new FileReader();
            reader.onload = async function(evt) {{
                const content = evt.target.result;
                const res = await fetch('/api/importar_csv', {{ method: 'POST', body: JSON.stringify({{ csv_text: content }}) }});
                const result = await res.json();
                alert(result.message);
                if(result.success) openTab('consulta');
            }};
            reader.readAsText(fileInput, 'UTF-8');
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
        try:
            parsed_path = urllib.parse.urlparse(self.path)
            
            if parsed_path.path == "/":
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode('utf-8'))
                
            elif parsed_path.path == "/api/itens":
                params = urllib.parse.parse_qs(parsed_path.query)
                desc = params.get('desc', [''])[0]
                pat = params.get('pat', [''])[0]
                local = params.get('local', [''])[0]
                conta = params.get('conta', [''])[0]
                ficha = params.get('ficha', [''])[0]

                rows = self.query_dados(desc, pat, local, conta, ficha)

                resultados = []
                for r in rows:
                    resultados.append({
                        "nome_material": r[0], "nr_ficha": r[1], "nee_mat": r[2], "conta_contabil": r[3],
                        "nr_patrimonio": r[4], "local_armazenamento": r[5], "data_conferencia": r[6],
                        "status_conferencia": r[7], "status_movimentacao": r[8], "boletim_admin": r[9],
                        "team": r[10], "foto_objeto": r[11], "foto_etiqueta": r[12]
                    })

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resultados).encode('utf-8'))

            elif parsed_path.path == "/api/resumo":
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                
                c.execute('''
                    SELECT 
                        COALESCE(p.local_armazenamento, 'Não informado') as local,
                        COUNT(p.id) as qtd,
                        SUM(COALESCE(i.valor_unitario, 0)) as valor
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                    GROUP BY p.local_armazenamento
                    ORDER BY valor DESC
                ''')
                locais_rows = c.fetchall()
                locais = [{"local": r[0], "qtd": r[1], "valor": r[2] or 0.0} for r in locais_rows]

                c.execute('''
                    SELECT 
                        COUNT(p.id) as total_patrimonios,
                        COUNT(DISTINCT i.id) as total_materiais,
                        SUM(COALESCE(i.valor_unitario, 0)) as valor_total
                    FROM patrimonios p
                    JOIN itens i ON p.item_id = i.id
                ''')
                geral_row = c.fetchone()
                conn.close()

                resp = {
                    "geral": {
                        "total_patrimonios": geral_row[0] or 0,
                        "total_materiais": geral_row[1] or 0,
                        "valor_total": geral_row[2] or 0.0
                    },
                    "locais": locais
                }

                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode('utf-8'))

            elif parsed_path.path == "/api/buscar_edicao":
                params = urllib.parse.parse_qs(parsed_path.query)
                ficha = params.get('ficha', [''])[0]
                conta = params.get('conta', [''])[0]

                resultados = self.buscar_materiais_para_edicao(ficha, conta)
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resultados).encode('utf-8'))

            elif parsed_path.path == "/relatorio/imprimir":
                params = urllib.parse.parse_qs(parsed_path.query)
                tipo = params.get('tipo', ['completo_sem_fotos'])[0]
                filtro = params.get('filtro', [''])[0]
                self.gerar_pagina_relatorio(tipo, filtro)

            elif parsed_path.path == "/api/exportar_csv":
                params = urllib.parse.parse_qs(parsed_path.query)
                tipo = params.get('tipo', ['completo_sem_fotos'])[0]
                filtro = params.get('filtro', [''])[0]
                self.gerar_download_csv(tipo, filtro)

            else:
                super().do_GET()

        except Exception as e:
            print(f"❌ Erro na requisição GET: {e}")
            self.send_response(500)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2>Erro interno do servidor:</h2><pre>{e}</pre>".encode('utf-8'))

    def buscar_materiais_para_edicao(self, ficha="", conta=""):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        query = "SELECT id, nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario, foto_objeto, foto_etiqueta FROM itens WHERE 1=1"
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
            c.execute("SELECT nr_patrimonio, local_armazenamento, status_conferencia, status_movimentacao FROM patrimonios WHERE item_id = ?", (item_id,))
            pat_rows = c.fetchall()
            
            patrimonios = [{
                "nr_patrimonio": p[0],
                "local_armazenamento": p[1],
                "status_conferencia": p[2] or "Conferido",
                "status_movimentacao": p[3] or "Em carga"
            } for p in pat_rows]

            lista_materiais.append({
                "id": row[0],
                "nome_material": row[1],
                "nr_ficha": row[2],
                "nee_mat": row[3],
                "conta_contabil": row[4],
                "valor_unitario": row[5],
                "foto_objeto": row[6],
                "foto_etiqueta": row[7],
                "patrimonios": patrimonios
            })

        conn.close()
        return lista_materiais

    def query_dados(self, desc="", pat="", local="", conta="", ficha="", status="", situacao=""):
        query = '''
            SELECT i.nome_material, i.nr_ficha, i.nee_mat, i.conta_contabil,
                   p.nr_patrimonio, p.local_armazenamento, p.data_conferencia,
                   p.status_conferencia, p.status_movimentacao, p.boletim_admin, p.team,
                   i.foto_objeto, i.foto_etiqueta
            FROM itens i
            LEFT JOIN patrimonios p ON i.id = p.item_id
            WHERE 1=1
        '''
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

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(query, sql_params)
        rows = c.fetchall()
        conn.close()
        return rows

    def gerar_pagina_relatorio(self, tipo, filtro):
        desc = pat = local = conta = ficha = status = situacao = ""
        if tipo == "por_ficha": ficha = filtro
        elif tipo == "por_conta": conta = filtro
        elif tipo == "por_local": local = filtro
        elif tipo == "por_status": status = filtro
        elif tipo == "por_situacao": situacao = filtro

        rows = self.query_dados(desc, pat, local, conta, ficha, status, situacao)
        com_fotos = (tipo == "completo_fotos")

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
                        <th>Material</th><th>Ficha</th><th>NEE</th><th>Conta</th><th>Patrimônio</th>
                        <th>Local</th><th>Últ. Conf.</th><th>Status</th><th>Movimentação</th><th>Boletim/TEAM</th>
                        {"<th>Fotos</th>" if com_fotos else ""}
                    </tr>
                </thead>
                <tbody>
        """
        for r in rows:
            foto_td = ""
            if com_fotos:
                obj_img = f'<img src="/{r[11]}" class="img-thumb">' if r[11] else ''
                etiq_img = f'<img src="/{r[12]}" class="img-thumb">' if r[12] else ''
                foto_td = f"<td>{obj_img} {etiq_img}</td>"

            html_relatorio += f"""
                <tr>
                    <td><b>{r[0] or ''}</b></td>
                    <td>{r[1] or ''}</td>
                    <td>{r[2] or ''}</td>
                    <td>{r[3] or ''}</td>
                    <td><b>{r[4] or ''}</b></td>
                    <td>{r[5] or ''}</td>
                    <td>{r[6] or ''}</td>
                    <td>{r[7] or ''}</td>
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
        self.wfile.write(html_relatorio.encode('utf-8'))

    def gerar_download_csv(self, tipo, filtro):
        desc = pat = local = conta = ficha = status = situacao = ""
        if tipo == "por_ficha": ficha = filtro
        elif tipo == "por_conta": conta = filtro
        elif tipo == "por_local": local = filtro
        elif tipo == "por_status": status = filtro
        elif tipo == "por_situacao": situacao = filtro

        rows = self.query_dados(desc, pat, local, conta, ficha, status, situacao)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(["Material", "Ficha", "NEE", "Conta Contabil", "Patrimonio", "Local", "Data Conferencia", "Status Conferencia", "Status Movimentacao", "Boletim Admin", "TEAM"])

        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]])

        csv_data = output.getvalue().encode('utf-8-sig')

        self.send_response(200)
        self.send_header("Content-type", "text/csv; charset=utf-8-sig")
        self.send_header("Content-Disposition", "attachment; filename=relatorio_patrimonio.csv")
        self.end_headers()
        self.wfile.write(csv_data)

    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            def save_base64_img(b64_str, prefix):
                if not b64_str: return None
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
                patrimonios = data.get('patrimonios', [])
                pats_nums = [p.get('nr_patrimonio', '').strip() for p in patrimonios if p.get('nr_patrimonio', '').strip()]

                if len(pats_nums) != len(set(pats_nums)):
                    conn.close()
                    resp = {"success": False, "message": "O formulário contém números de patrimônio duplicados entre si. Verifique os valores digitados."}
                    self.send_response(200)
                    self.send_header("Content-type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps(resp).encode('utf-8'))
                    return

                if pats_nums:
                    placeholders = ','.join(['?'] * len(pats_nums))
                    c.execute(f"SELECT nr_patrimonio FROM patrimonios WHERE nr_patrimonio IN ({placeholders})", pats_nums)
                    existentes = [r[0] for r in c.fetchall()]

                    if existentes:
                        conn.close()
                        resp = {
                            "success": False, 
                            "message": f"Erro ao cadastrar: O(s) patrimônio(s) a seguir já consta(m) cadastrado(s) no sistema: {', '.join(existentes)}"
                        }
                        self.send_response(200)
                        self.send_header("Content-type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps(resp).encode('utf-8'))
                        return

                path_obj = save_base64_img(data.get('foto_objeto'), "OBJ")
                path_etiq = save_base64_img(data.get('foto_etiqueta'), "ETIQ")

                c.execute('''
                    INSERT INTO itens (nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario, foto_objeto, foto_etiqueta)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (data['nome_material'], data['nr_ficha'], data['nee_mat'], data['conta_contabil'], float(data['valor_unitario'] or 0), path_obj, path_etiq))
                
                item_id = c.lastrowid
                hoje = date.today().strftime("%d/%m/%Y")
                inseridos = 0

                for p in patrimonios:
                    pat_num = p.get('nr_patrimonio', '').strip()
                    pat_local = p.get('local', 'Lab Motores')
                    pat_status = p.get('status_conferencia', 'Conferido')
                    pat_situacao = p.get('status_movimentacao', 'Em carga')

                    if pat_num:
                        c.execute('''
                            INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia, status_movimentacao)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (item_id, pat_num, pat_local, hoje, pat_status, pat_situacao))
                        inseridos += 1

                conn.commit()
                conn.close()
                resp = {"success": True, "message": f"Material e {inseridos} patrimônio(s) registrados com sucesso!"}

            elif self.path == "/api/atualizar_cadastro":
                item_id = data.get('item_id')
                nome = data.get('nome_material')
                ficha = data.get('nr_ficha')
                nee = data.get('nee_mat')
                conta = data.get('conta_contabil')
                valor = float(data.get('valor_unitario') or 0)

                c.execute('''
                    UPDATE itens
                    SET nome_material = ?, nr_ficha = ?, nee_mat = ?, conta_contabil = ?, valor_unitario = ?
                    WHERE id = ?
                ''', (nome, ficha, nee, conta, valor, item_id))

                if data.get('foto_objeto'):
                    path_obj = save_base64_img(data['foto_objeto'], "OBJ")
                    c.execute("UPDATE itens SET foto_objeto = ? WHERE id = ?", (path_obj, item_id))
                
                if data.get('foto_etiqueta'):
                    path_etiq = save_base64_img(data['foto_etiqueta'], "ETIQ")
                    c.execute("UPDATE itens SET foto_etiqueta = ? WHERE id = ?", (path_etiq, item_id))

                patrimonios_novos = data.get('patrimonios', [])
                pats_mantidos = []
                hoje = date.today().strftime("%d/%m/%Y")

                for p in patrimonios_novos:
                    pat_num = p.get('nr_patrimonio', '').strip()
                    pat_local = p.get('local', 'Lab Motores')
                    pat_status = p.get('status_conferencia', 'Conferido')
                    pat_situacao = p.get('status_movimentacao', 'Em carga')

                    if not pat_num: continue

                    pats_mantidos.append(pat_num)

                    c.execute("SELECT id FROM patrimonios WHERE nr_patrimonio = ? AND item_id = ?", (pat_num, item_id))
                    row = c.fetchone()
                    if row:
                        c.execute('''
                            UPDATE patrimonios 
                            SET local_armazenamento = ?, status_conferencia = ?, status_movimentacao = ?, data_conferencia = ?
                            WHERE id = ?
                        ''', (pat_local, pat_status, pat_situacao, hoje, row[0]))
                    else:
                        try:
                            c.execute('''
                                INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia, status_movimentacao)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (item_id, pat_num, pat_local, hoje, pat_status, pat_situacao))
                        except sqlite3.IntegrityError:
                            c.execute('''
                                UPDATE patrimonios 
                                SET item_id = ?, local_armazenamento = ?, status_conferencia = ?, status_movimentacao = ?, data_conferencia = ?
                                WHERE nr_patrimonio = ?
                            ''', (item_id, pat_local, pat_status, pat_situacao, hoje, pat_num))

                if pats_mantidos:
                    placeholders = ','.join(['?'] * len(pats_mantidos))
                    c.execute(f"DELETE FROM patrimonios WHERE item_id = ? AND nr_patrimonio NOT IN ({placeholders})", [item_id] + pats_mantidos)
                else:
                    c.execute("DELETE FROM patrimonios WHERE item_id = ?", (item_id,))

                conn.commit()
                conn.close()
                resp = {"success": True, "message": "Cadastro do material e patrimônios atualizado com sucesso!"}

            elif self.path == "/api/saida":
                c.execute('''
                    UPDATE patrimonios
                    SET status_movimentacao = ?, boletim_admin = ?, team = ?
                    WHERE nr_patrimonio = ?
                ''', (data['tipo'], data['boletim'], data['team'], data['patrimonio']))
                conn.commit()
                conn.close()
                resp = {"success": True, "message": f"Movimentação ({data['tipo']}) registrada!"}

            elif self.path == "/api/importar_csv":
                csv_text = data.get('csv_text', '')
                f = io.StringIO(csv_text)
                
                first_line = f.readline()
                delimiter = ';' if ';' in first_line else ','
                f.seek(0)

                reader = csv.DictReader(f, delimiter=delimiter)
                hoje = date.today().strftime("%d/%m/%Y")
                importados = 0
                duplicados = []

                for row in reader:
                    nome = row.get('nome_material') or row.get('Material')
                    pat = row.get('nr_patrimonio') or row.get('Patrimonio')
                    if not nome or not pat: continue

                    pat = str(pat).strip()

                    c.execute("SELECT id FROM itens WHERE nome_material = ?", (nome,))
                    res_item = c.fetchone()
                    if res_item:
                        item_id = res_item[0]
                    else:
                        c.execute('''
                            INSERT INTO itens (nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (
                            nome,
                            row.get('nr_ficha') or row.get('Ficha') or '',
                            row.get('nee_mat') or row.get('NEE') or '',
                            row.get('conta_contabil') or row.get('Conta') or '',
                            float(row.get('valor_unitario') or 0)
                        ))
                        item_id = c.lastrowid

                    local_arm = row.get('local_armazenamento') or row.get('Local') or 'Lab Motores'
                    try:
                        c.execute('''
                            INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia, status_movimentacao)
                            VALUES (?, ?, ?, ?, 'Conferido', 'Em carga')
                        ''', (item_id, pat, local_arm, hoje))
                        importados += 1
                    except sqlite3.IntegrityError:
                        duplicados.append(pat)

                conn.commit()
                conn.close()

                msg = f"Importação concluída! {importados} patrimônio(s) novo(s) inserido(s)."
                if duplicados:
                    msg += f"\n\n⚠️ O(s) seguinte(s) patrimônio(s) já existia(m) e foi(ram) ignorado(s): {', '.join(duplicados)}"

                resp = {"success": True, "message": msg}

            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode('utf-8'))

        except Exception as e:
            print(f"❌ Erro no processamento de requisição POST: {e}")
            self.send_response(500)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "message": f"Erro interno: {e}"}).encode('utf-8'))

# -----------------------------------------------------------------------------
# EXECUÇÃO DO SERVIDOR COM BUSCA AUTOMÁTICA DE PORTA LIVRE
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    port = PORT
    
    while True:
        try:
            with socketserver.TCPServer(("", port), RequestHandler) as httpd:
                print(f"🚀 Servidor rodando em http://localhost:{port}")
                webbrowser.open(f"http://localhost:{port}")
                httpd.serve_forever()
            break
        except OSError as e:
            if getattr(e, 'winerror', None) == 10048 or getattr(e, 'errno', None) == 98:
                port += 1
            else:
                raise e