# GMC
# 📦 GMC — Asset & Inventory Management System (SE/4 - IME)

An asset-tracking system (inventory audits, material registration and movement/disposal tracking) built for the **Mechanical and Materials Engineering Section (SE/4)** of the **Military Institute of Engineering (IME)**, with integration to the **SISCOFIS** inventory export.

The repository contains **three independent implementations** of the same system, plus the version history of the main implementation (a pure-HTTP server), allowing different interface and architecture approaches to be compared.

---

## 🧩 Available Implementations

| Implementation | Main file | Stack | Status |
|---|---|---|---|
| **Native HTTP server (main)** | `GMC_html_v5.py` (latest version) | `http.server` + SQLite + embedded HTML/CSS/JS, no web-framework dependencies | ✅ Actively developed — most complete and recommended version |
| **Streamlit** | `GMC.py` | `streamlit` + SQLite | Simpler prototype/alternative version |
| **Desktop (Tkinter)** | `GMC_desktop.py` | `tkinter`/`ttk` + SQLite + Pillow | Offline desktop version, no web server |

> All three implementations share the same conceptual data model (Material × Asset), but each keeps its own `patrimonio_se4.db` database and `fotos_patrimonio/` photo folder in the directory it's run from.

---

## 🚀 Main Version: `GMC_html_v5.py`

The main implementation is a **self-contained HTTP server** (standard library `http.server`/`socketserver`, no Flask/Django), serving a single page (a simple SPA) with HTML, CSS and JavaScript embedded directly in the Python file, and persisting data to **SQLite**.

### Key Features

- **🔍 Search and filters**: search by description, asset number, storage location, accounting code, form/record number, and registration gaps (missing photo, location, record number, etc.).
- **➕ Material registration**: register one material with N linked asset numbers, object and label photos (base64 upload), unit/individual value, and notes.
- **📝 Record updates**: edit a material and its linked assets (lookup by record number / accounting code).
- **🚚 Disposal / transfer logging**: write off an asset with an administrative bulletin and a TEAM (Material Examination and Verification Report), when applicable.
- **📄 Automatic SISCOFIS import (PDF)**: extracts text from SISCOFIS inventory PDF exports (`pypdf`), supports both the old and new column layouts, and performs a **batch sync** with the database (new, kept, and removed/no-longer-on-inventory items) — processed on a **background thread** with progress tracking via `/api/import_status`, so the UI doesn't freeze during large imports.
- **📊 Dashboard/Summary (`/api/resumo`)**: overall totals, grouping by location, by audit status, by movement status, and gap indicators (missing photo, missing location, missing record number, etc.).
- **🖨️ Reports**: printable HTML report generation (`/relatorio/imprimir`) with or without photos, filterable by record/account/location/status/situation, plus **CSV** export (`/api/exportar_csv`).
- **🔒 HTTP Basic Authentication**: server access protected by username/password (configurable via the `GMC_USER` / `GMC_SENHA` environment variables), introduced in v5.
- **⚡ Performance**: SQLite **WAL** mode, indexes on the most-queried columns, `ThreadingTCPServer` (concurrent requests), and PDF sync via `executemany`/batched transactions (avoiding thousands of individual round-trips to the database on inventories with thousands of items).
- **🔌 Automatic free-port lookup**: if the default port (`8000`) is taken, the server automatically tries the next available port and opens the browser.

### Data Model (SQLite)

- **`itens`** (materials): `nome_material` (name), `nr_ficha` (record #), `nee_mat`, `conta_contabil` (accounting code), `acervo`, `valor_unitario` (unit value), `foto_objeto`, `foto_etiqueta`, `observacoes` (notes).
- **`patrimonios`** (assets): `nr_patrimonio` (unique asset #), `item_id` (FK → `itens`), `local_armazenamento` (storage location), `data_conferencia` (last audit date), `status_conferencia` (audit status), `status_movimentacao` (movement status), `valor_individual`, `boletim_admin`, `team`.

### Main Endpoints

| Route | Method | Description |
|---|---|---|
| `/` | GET | Main page (SPA) |
| `/api/itens` | GET | Lists/filters materials and assets |
| `/api/resumo` | GET | Aggregated data for the dashboard |
| `/api/buscar_edicao` | GET | Looks up materials for editing (by record # / account) |
| `/api/cadastrar` | POST | Registers a material + its assets |
| `/api/atualizar_cadastro` | POST | Updates a material + its linked assets |
| `/api/saida` | POST | Logs a disposal/transfer for an asset |
| `/api/importar_pdf` | POST | Receives a SISCOFIS PDF and starts the async import |
| `/api/import_status` | GET | Polls the progress of an ongoing import |
| `/relatorio/imprimir` | GET | Printable HTML report |
| `/api/exportar_csv` | GET | CSV export |

---

## 🗂️ Version History (`GMC_html*.py`)

The main file evolved incrementally. All versions share the same architecture (native HTTP server + SQLite), but each one adds or fixes something:

| Version | Main additions over the previous one |
|---|---|
| `GMC_html.py` | Initial version: basic CRUD + dashboard (`/api/resumo`). |
| `GMC_html_v1.py` | Adds **SISCOFIS PDF import** (`/api/importar_pdf`). |
| `GMC_html_v2.py` | Introduces `ThreadingTCPServer`, SQLite **WAL** mode, **batched** sync (`executemany`), and PDF import on a **background thread** — optimizations aimed at handling large inventories (thousands of items) without freezing the server. |
| `GMC_html_v3.py` | Fixes and refinements over v2 (same architecture). |
| `GMC_html_v4.py` | Fixes and refinements over v3 (same architecture). |
| `GMC_html_v5.py` | Adds **HTTP Basic authentication** (username/password configurable via environment variable). Latest and recommended version. |

> The `.spec` files (`GMC_html.spec`, `GMC_html_v1.spec`, `GMC_html_v3.spec`, `GMC_html_v4.spec`) are **PyInstaller** configurations for building a standalone executable (`.exe`) of each corresponding version — there's no `.spec` for v5 in this file set; generate a new one with `pyinstaller GMC_html_v5.py` if needed.

---

## 🖥️ Alternative Implementation: Streamlit (`GMC.py`)

A simplified version using the **Streamlit** framework, with sidebar-menu navigation and the same four core functions: search, registration, audit/location update, and disposal/transfer logging. Faster to prototype, but without PDF import, dashboard, reports, or authentication.

### How to run

```bash
pip install streamlit
streamlit run GMC.py
```

---

## 🖥️ Alternative Implementation: Desktop (`GMC_desktop.py`)

An offline desktop version using **Tkinter/ttk**, with tabs for search, registration, audit updates, and disposal logging. Doesn't require a browser or network — useful for local, standalone use with no server needed.

### How to run

```bash
pip install pillow
python GMC_desktop.py
```

---

## 📦 Requirements (main version `GMC_html_v5.py`)

- Python 3.10+ (uses `list[dict]` type hints and modern typing syntax)
- `pypdf` library (text extraction from SISCOFIS PDFs)
- All other dependencies are part of Python's standard library (`http.server`, `sqlite3`, `socketserver`, `threading`, etc.)

### Installation

```bash
pip install pypdf
```

### Running

```bash
python GMC_html_v5.py
```

The server starts at `http://localhost:8000` (or the next available port) and automatically opens the default browser. By default it prompts for a username/password (`se4` / `ime@2026`) — **change the default password** by setting environment variables before putting the system into real use:

```bash
# Linux/Mac
GMC_USER=username GMC_SENHA=strong_password python GMC_html_v5.py

# Windows (cmd)
set GMC_USER=username & set GMC_SENHA=strong_password & python GMC_html_v5.py
```

---

## 🖨️ Building an Executable (PyInstaller)

To distribute the main version as a self-contained `.exe` (no Python installation required on the target machine):

```bash
pip install pyinstaller
pyinstaller GMC_html_v5.py --onefile --name GMC_html_v5
```

(The `.spec` files already present in the repository can be reused/adapted as a reference for earlier versions.)

---

## 📁 Runtime Structure

When any of the implementations is run, the following are automatically created in the working directory:

```
.
├── patrimonio_se4.db       # SQLite database
└── fotos_patrimonio/       # Object and label photos uploaded through registration
```

---

## ⚠️ Notes & Limitations

- Each implementation (`GMC.py`, `GMC_desktop.py`, `GMC_html_v5.py`) uses its **own `patrimonio_se4.db` file** — there's no automatic data sync between them if run from different directories.
- SISCOFIS PDF import depends on the **text layout** of the exported document (compatible with both the old and new column layouts); future changes to the SISCOFIS export format may require adjustments to `processar_relacao_siscofis`.
- HTTP Basic authentication (v5) protects access to the interface, but **credentials are sent unencrypted** if the server is exposed beyond `localhost`/a trusted network — for network use, consider putting the server behind HTTPS (reverse proxy) before exposing it to other hosts.
- `GMC.pyproj` / `GMC.slnx` are **Visual Studio (Python Tools)** project files, currently pointing to `GMC.py` (the Streamlit version) as the startup file.

---
## ✍️ Author

Developed by **Diogo Lopes Fernandes**.
