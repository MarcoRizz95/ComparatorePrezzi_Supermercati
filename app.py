import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
from datetime import datetime

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Spesa Smart AI", layout="centered", page_icon="🛒")

st.markdown("""
    <style>
    .header-box { padding: 20px; border-radius: 15px; border: 2px solid #e0e0e0; margin-bottom: 20px; background-color: white; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; background-color: #f0f2f6; border-radius: 10px 10px 0 0; }
    .stTabs [aria-selected="true"] { background-color: #007bff; color: white !important; }
    .winner-box { background-color: #d4edda; padding: 15px; border-radius: 10px; border-left: 5px solid #28a745; margin-bottom: 20px; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNZIONI DI SERVIZIO ---
def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

# --- CONNESSIONE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    google_info = dict(st.secrets)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    lista_negozi = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
except Exception as e:
    st.error(f"Errore connessione: {e}")
    st.stop()

tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

# --- TAB 1: CARICAMENTO (Invariato) ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None
    uploaded_file = st.file_uploader("Carica o scatta una foto", type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")
    if uploaded_file:
        img = ImageOps.exif_transpose(Image.open(uploaded_file))
        st.image(img, use_container_width=True)
        if st.button("🚀 ANALIZZA"):
            with st.spinner("Analisi in corso..."):
                try:
                    prompt = "Analizza lo scontrino. Estrai P_IVA, indirizzo, data_iso, totale_scontrino. Prodotti: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard. SCONTI: sottrai al precedente. JSON."
                    response = model.generate_content([prompt, img])
                    st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    st.rerun()
                except Exception as e: st.error(f"Errore: {e}")

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti_raw = d.get('prodotti', [])
        tot_calcolato = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita', 1)) for p in prodotti_raw])
        st.info(f"💰 **Somma totale articoli rilevati: €{tot_calcolato:.2f}**")
        piva_letta = clean_piva(testata.get('p_iva', ''))
        match_negozio = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_letta), None) if piva_letta else None
        st.markdown('<div class="header-box">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match_negozio['Insegna_Standard'] if match_negozio else f"NUOVO ({piva_letta})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match_negozio['Indirizzo_Standard (Pulito)'] if match_negozio else testata.get('indirizzo_letto', ''))).upper()
        with c3: data_f = st.text_input("Data (DD/MM/YYYY)", value="/".join(testata.get('data_iso', '2026-01-01').split("-")[::-1]))
        st.markdown('</div>', unsafe_allow_html=True)
        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Normalizzato": str(p.get('nome_standard', '')).upper()} for p in prodotti_raw]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)
        if st.button("💾 SALVA NEL DATABASE"):
            try:
                final_rows = [[data_f, insegna_f, indirizzo_f, str(row['Prodotto']).upper(), clean_price(row['Prezzo Un.']) * float(row['Qtà']), 0, clean_price(row['Prezzo Un.']), str(row['Offerta']).upper(), row['Qtà'], "SI", str(row['Normalizzato']).upper()] for _, row in edited_df.iterrows()]
                worksheet.append_rows(final_rows)
                st.success("✅ Salvataggio completato!"); st.session_state.dati_analizzati = None; st.rerun()
            except Exception as e: st.error(f"Errore: {e}")

# --- TAB 2: RICERCA (Logica "Ultimo Prezzo") ---
with tab_cerca:
    st.subheader("🔍 Ricerca Prezzi")
    query = st.text_input("Scrivi il prodotto da cercare", "").upper().strip()
    
    if query:
        with st.spinner("Sto consultando il database..."):
            all_data = worksheet.get_all_records()
            if all_data:
                # Creiamo il DataFrame e forziamo i nomi colonne a puliti (senza spazi)
                df_all = pd.DataFrame(all_data)
                df_all.columns = [c.strip() for c in df_all.columns]
                
                # Cerchiamo i nomi reali delle colonne per evitare KeyError
                col_prod = next((c for c in df_all.columns if 'PRODOTTO' in c.upper()), df_all.columns[3])
                col_std = next((c for c in df_all.columns if 'NORMALIZZATO' in c.upper() or 'NOME STANDARD' in c.upper()), df_all.columns[-1])
                col_prezzo = next((c for c in df_all.columns if 'NETTO' in c.upper() or 'UNITARIO' in c.upper()), df_all.columns[6])
                col_data = next((c for c in df_all.columns if 'DATA' in c.upper()), df_all.columns[0])
                col_super = next((c for c in df_all.columns if 'SUPERMERCATO' in c.upper()), df_all.columns[1])
                col_indirizzo = next((c for c in df_all.columns if 'INDIRIZZO' in c.upper()), df_all.columns[2])
                col_offerta = next((c for c in df_all.columns if 'OFFERTA' in c.upper()), df_all.columns[7])

                # 1. FILTRO PER PAROLA CHIAVE
                mask = df_all[col_prod].astype(str).str.contains(query, na=False) | df_all[col_std].astype(str).str.contains(query, na=False)
                risultati = df_all[mask].copy()
                
                if not risultati.empty:
                    # 2. CONVERSIONE DATE E PREZZI PER ORDINAMENTO
                    risultati[col_prezzo] = risultati[col_prezzo].apply(clean_price)
                    # Convertiamo le date DD/MM/YYYY in oggetti datetime per ordinarle correttamente
                    risultati['dt_obj'] = pd.to_datetime(risultati[col_data], format='%d/%m/%Y', errors='coerce')
                    
                    # 3. LOGICA: ULTIMO PREZZO PER PUNTO VENDITA
                    # Ordiniamo per data decrescente (più recente sopra)
                    risultati = risultati.sort_values(by='dt_obj', ascending=False)
                    # Teniamo solo la prima riga per ogni combinazione Supermercato + Indirizzo
                    risultati = risultati.drop_duplicates(subset=[col_super, col_indirizzo])
                    
                    # 4. IDENTIFICAZIONE VINCITORE (Più economico tra i più recenti)
                    risultati = risultati.sort_values(by=col_prezzo, ascending=True)
                    best = risultati.iloc[0]
                    
                    # Interfaccia grafica
                    st.markdown(f"""
                    <div class="winner-box">
                        🏆 <b>{best[col_super]}</b> è il più economico oggi!<br>
                        Prezzo: <b>€{best[col_prezzo]:.2f}</b> ({best[col_data]})<br>
                        Sede: {best[col_indirizzo]}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # Tabella completa
                    st.write("### Confronto tra i vari punti vendita (ultimo prezzo rilevato):")
                    df_display = risultati[[col_prezzo, col_super, col_indirizzo, col_data, col_offerta, col_prod]]
                    df_display.columns = ['Prezzo €', 'Supermercato', 'Indirizzo', 'Data Rilevazione', 'In Offerta', 'Nome Scontrino']
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                else:
                    st.warning(f"Nessun risultato trovato per '{query}'.")
            else:
                st.info("Il database è ancora vuoto.")
