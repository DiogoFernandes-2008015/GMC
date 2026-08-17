import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
import os
import shutil
from datetime import date
from PIL import Image, ImageTk

DB_NAME = "patrimonio_se4.db"
UPLOAD_DIR = "fotos_patrimonio"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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
            status_movimentacao TEXT DEFAULT 'Em Carga',
            boletim_admin TEXT,
            team TEXT,
            FOREIGN KEY (item_id) REFERENCES itens (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class AppPatrimonio:
    def __init__(self, root):
        self.root = root
        self.root.title("Sistema de Conferência de Material e Carga - IME / SE/4")
        self.root.geometry("1100x650")

        # Guias (Notebook)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_consulta = ttk.Frame(self.tabs)
        self.tab_cadastro = ttk.Frame(self.tabs)
        self.tab_atualiza = ttk.Frame(self.tabs)
        self.tab_saida = ttk.Frame(self.tabs)

        self.tabs.add(self.tab_consulta, text="🔍 Consultar / Pesquisar")
        self.tabs.add(self.tab_cadastro, text="➕ Cadastrar Material")
        self.tabs.add(self.tab_atualiza, text="📝 Atualizar Conferência")
        self.tabs.add(self.tab_saida, text="🚚 Registrar Saída / Alienação")

        self.setup_consulta()
        self.setup_cadastro()
        self.setup_atualiza()
        self.setup_saida()

    # --- ABA 1: CONSULTA ---
    def setup_consulta(self):
        frame_busca = ttk.LabelFrame(self.tab_consulta, text="Filtros de Busca")
        frame_busca.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_busca, text="Descrição:").grid(row=0, column=0, padx=5, pady=5)
        self.ent_b_desc = ttk.Entry(frame_busca)
        self.ent_b_desc.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(frame_busca, text="Patrimônio:").grid(row=0, column=2, padx=5, pady=5)
        self.ent_b_pat = ttk.Entry(frame_busca)
        self.ent_b_pat.grid(row=0, column=3, padx=5, pady=5)

        ttk.Button(frame_busca, text="Pesquisar", command=self.buscar_dados).grid(row=0, column=4, padx=10, pady=5)

        # Tabela
        cols = ("ID", "Descrição", "Ficha", "NEE", "Conta", "Patrimônio", "Local", "Últ. Conf.", "Status Conf.", "Situação", "Boletim", "TEAM")
        self.tree = ttk.Treeview(self.tab_consulta, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=90)
        self.tree.column("Descrição", width=200)

        self.tree.pack(fill="both", expand=True, padx=10, pady=5)
        self.buscar_dados()

    def buscar_dados(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        query = '''
            SELECT i.id, i.nome_material, i.nr_ficha, i.nee_mat, i.conta_contabil,
                   p.nr_patrimonio, p.local_armazenamento, p.data_conferencia, 
                   p.status_conferencia, p.status_movimentacao, p.boletim_admin, p.team
            FROM itens i
            LEFT JOIN patrimonios p ON i.id = p.item_id
            WHERE 1=1
        '''
        params = []
        if self.ent_b_desc.get():
            query += " AND i.nome_material LIKE ?"
            params.append(f"%{self.ent_b_desc.get()}%")
        if self.ent_b_pat.get():
            query += " AND p.nr_patrimonio LIKE ?"
            params.append(f"%{self.ent_b_pat.get()}%")

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(query, params)
        for r in c.fetchall():
            self.tree.insert("", "end", values=r)
        conn.close()

    # --- ABA 2: CADASTRO ---
    def setup_cadastro(self):
        f = ttk.Frame(self.tab_cadastro)
        f.pack(fill="both", expand=True, padx=15, pady=15)

        ttk.Label(f, text="Descrição / Nome do Material:").grid(row=0, column=0, sticky="w", pady=5)
        self.c_nome = ttk.Entry(f, width=50)
        self.c_nome.grid(row=0, column=1, columnspan=3, sticky="w", pady=5)

        ttk.Label(f, text="Nº Ficha:").grid(row=1, column=0, sticky="w", pady=5)
        self.c_ficha = ttk.Entry(f)
        self.c_ficha.grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(f, text="NEE Mat:").grid(row=1, column=2, sticky="w", pady=5)
        self.c_nee = ttk.Entry(f)
        self.c_nee.grid(row=1, column=3, sticky="w", pady=5)

        ttk.Label(f, text="Conta Contábil:").grid(row=2, column=0, sticky="w", pady=5)
        self.c_conta = ttk.Entry(f)
        self.c_conta.grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(f, text="Valor Unitário (R$):").grid(row=2, column=2, sticky="w", pady=5)
        self.c_valor = ttk.Entry(f)
        self.c_valor.grid(row=2, column=3, sticky="w", pady=5)

        ttk.Label(f, text="Números de Patrimônio (separados por vírgula):").grid(row=3, column=0, sticky="w", pady=5)
        self.c_pats = tk.Text(f, height=4, width=40)
        self.c_pats.grid(row=3, column=1, columnspan=3, sticky="w", pady=5)

        ttk.Label(f, text="Local Padrão:").grid(row=4, column=0, sticky="w", pady=5)
        self.c_local = ttk.Entry(f)
        self.c_local.insert(0, "Lab Motores")
        self.c_local.grid(row=4, column=1, sticky="w", pady=5)

        ttk.Button(f, text="Salvar Cadastro", command=self.salvar_cadastro).grid(row=5, column=1, pady=15)

    def salvar_cadastro(self):
        nome = self.c_nome.get()
        pats_str = self.c_pats.get("1.0", tk.END).strip()

        if not nome or not pats_str:
            messagebox.showerror("Erro", "Nome do Material e Patrimônios são obrigatórios!")
            return

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            INSERT INTO itens (nome_material, nr_ficha, nee_mat, conta_contabil, valor_unitario)
            VALUES (?, ?, ?, ?, ?)
        ''', (nome, self.c_ficha.get(), self.c_nee.get(), self.c_conta.get(), float(self.c_valor.get() or 0)))
        
        item_id = c.lastrowid
        lista_pats = [p.strip() for p in pats_str.split(",") if p.strip()]
        hoje = date.today().strftime("%d/%m/%Y")

        for pat in lista_pats:
            try:
                c.execute('''
                    INSERT INTO patrimonios (item_id, nr_patrimonio, local_armazenamento, data_conferencia, status_conferencia)
                    VALUES (?, ?, ?, ?, 'Conferido')
                ''', (item_id, pat, self.c_local.get(), hoje))
            except sqlite3.IntegrityError:
                pass

        conn.commit()
        conn.close()
        messagebox.showinfo("Sucesso", "Material cadastrado com sucesso!")
        self.buscar_dados()

    # --- ABA 3: ATUALIZAR CONFERÊNCIA ---
    def setup_atualiza(self):
        f = ttk.Frame(self.tab_atualiza)
        f.pack(fill="both", expand=True, padx=15, pady=15)

        ttk.Label(f, text="Nº de Patrimônio:").grid(row=0, column=0, sticky="w", pady=5)
        self.a_pat = ttk.Entry(f)
        self.a_pat.grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(f, text="Novo Local:").grid(row=1, column=0, sticky="w", pady=5)
        self.a_local = ttk.Entry(f)
        self.a_local.grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(f, text="Status Conferência:").grid(row=2, column=0, sticky="w", pady=5)
        self.a_status = ttk.Combobox(f, values=["Conferido", "Não encontrado", "Transferir"])
        self.a_status.set("Conferido")
        self.a_status.grid(row=2, column=1, sticky="w", pady=5)

        ttk.Button(f, text="Atualizar Status", command=self.atualizar_conf).grid(row=3, column=1, pady=15)

    def atualizar_conf(self):
        pat = self.a_pat.get().strip()
        if not pat:
            messagebox.showerror("Erro", "Informe o patrimônio!")
            return

        hoje = date.today().strftime("%d/%m/%Y")
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            UPDATE patrimonios
            SET local_armazenamento = ?, status_conferencia = ?, data_conferencia = ?
            WHERE nr_patrimonio = ?
        ''', (self.a_local.get(), self.a_status.get(), hoje, pat))
        conn.commit()
        conn.close()
        messagebox.showinfo("Sucesso", "Patrimônio atualizado!")
        self.buscar_dados()

    # --- ABA 4: SAÍDA / ALIENAÇÃO ---
    def setup_saida(self):
        f = ttk.Frame(self.tab_saida)
        f.pack(fill="both", expand=True, padx=15, pady=15)

        ttk.Label(f, text="Nº de Patrimônio:").grid(row=0, column=0, sticky="w", pady=5)
        self.s_pat = ttk.Entry(f)
        self.s_pat.grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(f, text="Tipo Movimentação:").grid(row=1, column=0, sticky="w", pady=5)
        self.s_tipo = ttk.Combobox(f, values=["Saída / Transferência", "Alienação"])
        self.s_tipo.set("Saída / Transferência")
        self.s_tipo.grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(f, text="Boletim Administrativo (Obrigatório):").grid(row=2, column=0, sticky="w", pady=5)
        self.s_bol = ttk.Entry(f)
        self.s_bol.grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(f, text="TEAM (Exigido para Alienação):").grid(row=3, column=0, sticky="w", pady=5)
        self.s_team = ttk.Entry(f)
        self.s_team.grid(row=3, column=1, sticky="w", pady=5)

        ttk.Button(f, text="Confirmar Movimentação", command=self.registrar_saida).grid(row=4, column=1, pady=15)

    def registrar_saida(self):
        pat = self.s_pat.get().strip()
        bol = self.s_bol.get().strip()
        team = self.s_team.get().strip()
        tipo = self.s_tipo.get()

        if not pat or not bol:
            messagebox.showerror("Erro", "Patrimônio e Boletim Administrativo são obrigatórios!")
            return
        if tipo == "Alienação" and not team:
            messagebox.showerror("Erro", "TEAM é obrigatório para Alienação!")
            return

        situacao = "Alienado" if tipo == "Alienação" else "Saída / Transferido"

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            UPDATE patrimonios
            SET status_movimentacao = ?, boletim_admin = ?, team = ?
            WHERE nr_patrimonio = ?
        ''', (situacao, bol, team, pat))
        conn.commit()
        conn.close()
        messagebox.showinfo("Sucesso", f"Movimentação '{situacao}' registrada!")
        self.buscar_dados()

if __name__ == "__main__":
    root = tk.Tk()
    app = AppPatrimonio(root)
    root.mainloop()
