import streamlit as st
import sqlite3
import os
from datetime import date

# Configuração da página
st.set_page_config(page_title="Conferência de Carga - IME / SE/4", layout="wide", page_icon="📦")

DB_NAME = "patrimonio_se4.db"
UPLOAD_DIR = "fotos_patrimonio"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_connection():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = get_connection()
    c = conn.cursor()
    # Tabela de Itens (Material / Ficha)
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
    # Tabela de Patrimônios (Relacionamento 1 para N)
    c.execute('''
        CREATE TABLE IF NOT EXISTS patrimonios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            nr_patrimonio TEXT UNIQUE NOT NULL,
            local_armazenamento TEXT,
            data_conferencia TEXT,
            status_conferencia TEXT,
            status_movimentacao TEXT DEFAULT 'Em Carga',
            boletim_admin TEXT,
            team TEXT,
            FOREIGN KEY (item_id) REFERENCES itens (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_uploaded_file(uploaded_file, prefix):
    if uploaded_file is not None:
        filename = f"{prefix}_{uploaded_file.name}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return filepath
    return None

# -----------------------------------------------------------------------------
# INTERFACE E NAVEGAÇÃO
# -----------------------------------------------------------------------------
st.title("📦 Sistema de Conferência de Material e Carga")
st.caption("Seção de Engenharia Mecânica e de Materiais (SE/4) - IME")

menu = [
    "🔍 Consultar / Pesquisar",
    "➕ Cadastrar Novo Material",
    "📝 Atualizar Conferência / Local",
    "🚚 Registar Saída / Alienação"
]
opcao = st.sidebar.selectbox("Navegação", menu)

# -----------------------------------------------------------------------------
# 1. CONSULTAR / PESQUISAR
# -----------------------------------------------------------------------------
if opcao == "🔍 Consultar / Pesquisar":
    st.subheader("🔍 Consultas e Filtros")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        busca_desc = st.text_input("Filtrar por Descrição / Nome")
        busca_patrimonio = st.text_input("Filtrar por Nr. Patrimônio")
    with col2:
        busca_conta = st.text_input("Filtrar por Conta Contábil")
        busca_nee = st.text_input("Filtrar por NEE")
    with col3:
        busca_ficha = st.text_input("Filtrar por Nr. Ficha")
        busca_local = st.text_input("Filtrar por Local de Armazenamento")

    query = '''
        SELECT 
            i.id as Item_ID,
            i.nome_material AS "Descrição",
            i.nr_ficha AS "Ficha",
            i.nee_mat AS "NEE",
            i.conta_contabil AS "Conta Contábil",
            p.nr_patrimonio AS "Patrimônio",
            p.local_armazenamento AS "Local",
            p.data_conferencia AS "Última Conferência",
            p.status_conferencia AS "Status Conferência",
            p.status_movimentacao AS "Situação",
            p.boletim_admin AS "Boletim Admin",
            p.team AS "TEAM",
            i.foto_objeto,
            i.foto_etiqueta
        FROM itens i
        LEFT JOIN patrimonios p ON i.id = p.item_id
        WHERE 1=1
    '''
    params = []

    if busca_desc:
        query += " AND i.nome_material LIKE ?"
        params.append(f"%{busca_desc}%")
    if busca_patrimonio:
        query += " AND p.nr_patrimonio LIKE ?"
        params.append(f"%{busca_patrimonio}%")
    if busca_conta:
        query += " AND i.conta_contabil LIKE ?"
        params.append(f"%{busca_conta}%")
    if busca_nee:
        query += " AND i.nee_mat LIKE ?"
        params.append(f"%{busca_nee}%")
    if busca_ficha:
        query += " AND i.nr_ficha LIKE ?"
        params.append(f"%{busca_ficha}%")
    if busca_local:
        query += " AND p.local_armazenamento LIKE ?"
        params.append(f"%{busca_local}%")

    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    cols = [desc[0] for desc in c.description]
    conn.close()

    registros = [dict(zip(cols, row)) for row in rows]

    st.markdown(f"**Total de registros encontrados:** `{len(registros)}`")
    
    if registros:
        # Tabela exibida via componente simples
        dados_visiveis = []
        for r in registros:
            item_limpo = {k: (v if v is not None else "") for k, v in r.items() if k not in ['foto_objeto', 'foto_etiqueta', 'Item_ID']}
            dados_visiveis.append(item_limpo)

        st.table(dados_visiveis)

        st.write("---")
        st.subheader("📷 Visualizar Fotos do Item Selecionado")
        
        pats_validos = sorted(list(set([r["Patrimônio"] for r in registros if r["Patrimônio"]])))
        
        if pats_validos:
            patrimonio_sel = st.selectbox("Selecione um Patrimônio para ver as fotos:", pats_validos)
            row = next((r for r in registros if r["Patrimônio"] == patrimonio_sel), None)
            
            if row:
                col_img1, col_img2 = st.columns(2)
                
                with col_img1:
                    st.markdown("**Foto do Objeto:**")
                    if row['foto_objeto'] and os.path.exists(row['foto_objeto']):
                        st.image(row['foto_objeto'])
                    else:
                        st.info("Sem foto do objeto cadastrada.")
                        
                with col_img2:
                    st.markdown("**Foto das Etiquetas:**")
                    if row['foto_etiqueta'] and os.path.exists(row['foto_etiqueta']):
                        st.image(row['foto_etiqueta'])
                    else:
                        st.info("Sem foto da etiqueta cadastrada.")
        else:
            st.info("Nenhum patrimônio cadastrado para este item ainda.")
    else:
        st.info("Nenhum registro encontrado. Acesse a aba 'Cadastrar Novo Material' no menu lateral!")

# -----------------------------------------------------------------------------
# 2. CADASTRAR NOVO MATERIAL E PATRIMÔNIOS
# -----------------------------------------------------------------------------
elif opcao == "➕ Cadastrar Novo Material":
    st.subheader("➕ Cadastrar Material e seus Patrimônios")

    with st.form("form_cadastro", clear_on_submit=True):
        st.markdown("### 1. Dados do Material")
        col1, col2 = st.columns(2)
        with col1:
            nome_material = st.text_input("Descrição / Nome do Material *")
            nr_ficha = st.text_input("Número da Ficha")
            nee_mat = st.text_input("NEE Mat.")
        with col2:
            conta_contabil = st.text_input("Conta Contábil")
            acervo = st.selectbox("Acervo", ["N", "S"])
            valor_unitario = st.number_input("Valor Unitário (R$)", min_value=0.0, format="%.2f")

        st.markdown("### 2. Fotos do Material")
        f_obj = st.file_uploader("Foto Descritiva do Objeto", type=["png", "jpg", "jpeg"])
        f_etiq = st.file_uploader("Foto das Etiquetas", type=["png", "jpg", "jpeg"])

        st.markdown("### 3. Números de Patrimônio (1 para N)")
        st.info("Digite os números de patrimônio separados por vírgula (ex: 106280200009854, 106280200009855)")
        patrimonios_input = st.text_area("Lista de Patrimônios *")
        
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            local_padrao = st.text_input("Local de Armazenamento Padrão", value="Lab Motores")
        with col_p2:
            status_conf_padrao = st.selectbox("Status da Conferência Visual", ["Conferido", "Não encontrado", "Transferir"])

        submitted = st.form_submit_button("Salvar Cadastro")

        if submitted:
            if not nome_material or not patrimonios_input:
                st.error("Campos Nome do Material e Lista de Patrimônios são obrigatórios!")
            else:
                path_obj = save_uploaded_file(f_obj, "OBJ")
                path_etiq = save_uploaded_file(f_etiq, "ETIQ")

                conn = get_connection()
                c = conn.cursor()
                c.execute('''
                    INSERT INTO itens (nome_material, nr_ficha, nee_mat, conta_contabil, acervo, valor_unitario, foto_objeto, foto_etiqueta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (nome_material, nr_ficha, nee_mat, conta_contabil, acervo, valor_unitario, path_obj, path_etiq))
                
                item_id = c.lastrowid

                lista_patrimonios = [p.strip() for p in patrimonios_input.split(",") if p.strip()]
                hoje = date.today().strftime("%d/%m/%Y")
                
                erros = 0
                for pat in lista_patrimonios:
                    try:
                        c.execute('''
                            INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (item_id, pat, local_padrao, hoje, status_conf_padrao))
                    except sqlite3.IntegrityError:
                        erros += 1

                conn.commit()
                conn.close()

                if erros > 0:
                    st.warning(f"Material cadastrado, mas {erros} patrimônio(s) falharam por duplicidade.")
                else:
                    st.success(f"Material e {len(lista_patrimonios)} patrimônio(s) cadastrados com sucesso!")

# -----------------------------------------------------------------------------
# 3. ATUALIZAR CONFERÊNCIA DE PATRIMÔNIO
# -----------------------------------------------------------------------------
elif opcao == "📝 Atualizar Conferência / Local":
    st.subheader("📝 Atualizar Local e Status da Conferência")

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT nr_patrimonio, local_armazenamento FROM patrimonios")
    pats = c.fetchall()
    conn.close()

    if not pats:
        st.info("Nenhum patrimônio cadastrado para atualizar.")
    else:
        lista_pats = [p[0] for p in pats]
        pat_selecionado = st.selectbox("Selecione o Número de Patrimônio:", lista_pats)
        local_atual = next((p[1] for p in pats if p[0] == pat_selecionado), "")

        with st.form("form_atualiza_conf"):
            novo_local = st.text_input("Local de Armazenamento", value=local_atual or "")
            novo_status = st.selectbox("Status da Conferência Visual", ["Conferido", "Não encontrado", "Transferir"], index=0)
            nova_data = st.date_input("Data da última conferência", value=date.today())

            btn_atualizar = st.form_submit_button("Atualizar Patrimônio")

            if btn_atualizar:
                conn = get_connection()
                c = conn.cursor()
                c.execute('''
                    UPDATE patrimonios
                    SET local_armazenamento = ?, status_conferencia = ?, data_conferencia = ?
                    WHERE nr_patrimonio = ?
                ''', (novo_local, novo_status, nova_data.strftime("%d/%m/%Y"), pat_selecionado))
                conn.commit()
                conn.close()
                st.success(f"Patrimônio {pat_selecionado} atualizado com sucesso!")

# -----------------------------------------------------------------------------
# 4. REGISTRAR SAÍDA / ALIENAÇÃO
# -----------------------------------------------------------------------------
elif opcao == "🚚 Registar Saída / Alienação":
    st.subheader("🚚 Registrar Saída ou Alienação de Material")

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT nr_patrimonio FROM patrimonios")
    pats = c.fetchall()
    conn.close()

    if not pats:
        st.info("Nenhum patrimônio cadastrado.")
    else:
        lista_pats = [p[0] for p in pats]
        pat_selecionado = st.selectbox("Selecione o Patrimônio para Saída/Alienação:", lista_pats)
        tipo_mov = st.radio("Tipo de Movimentação:", ["Saída / Transferência", "Alienação"])

        with st.form("form_saida"):
            boletim = st.text_input("Boletim Administrativo (Obrigatório) *")
            team = st.text_input("TEAM - Termo de Exame e Averiguação de Material " + ("(Obrigatório para Alienação) *" if tipo_mov == "Alienação" else "(Opcional)"))

            btn_registar = st.form_submit_button("Confirmar Movimentação")

            if btn_registar:
                valido = True
                if not boletim:
                    st.error("A informação do Boletim Administrativo é obrigatória!")
                    valido = False
                if tipo_mov == "Alienação" and not team:
                    st.error("Para Alienação, o preenchimento do TEAM é OBRIGATÓRIO!")
                    valido = False

                if valido:
                    conn = get_connection()
                    c = conn.cursor()
                    nova_sit = "Alienado" if tipo_mov == "Alienação" else "Saída / Transferido"
                    c.execute('''
                        UPDATE patrimonios
                        SET status_movimentacao = ?, boletim_admin = ?, team = ?
                        WHERE nr_patrimonio = ?
                    ''', (nova_sit, boletim, team, pat_selecionado))
                    conn.commit()
                    conn.close()
                    st.success(f"Movimentação do patrimônio {pat_selecionado} registrada como '{nova_sit}'!")