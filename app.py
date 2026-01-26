import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Spesa Smart AI", layout="centered", page_icon="🛒")

# CSS per rifinitura estetica
st.markdown("""
    <style>
    .header-box { padding: 20px; border-radius: 15px; border: 2px solid #e0e0e0; margin-bottom: 20px; background-color: white; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 10px 10px 0 0; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #007bff; color: white !important; }
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

# --- INTERFACCIA A TAB ---
tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

# --- TAB 1: CARICAMENTO ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None

    uploaded_file = st.file_uploader("Carica o scatta una foto", type=['jpg', 'jpeg', 'png'], label_visibility="collapsed")

    if uploaded_file:
        img = ImageOps.exif_transpose(Image.open(uploaded_file))
        st.image(img, use_container_width=True)
        
        if st.button("🚀 ANALIZZA"):
            with st.spinner("L'IA sta elaborando..."):
                try:
                    prompt = """Analizza lo scontrino. Estrai: p_iva, indirizzo_letto, data_iso (YYYY-MM-DD), totale_scontrino_letto. 
                    Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard. 
                    SCONTI: Sottrai righe negative al prodotto precedente. NO AGGREGAZIONE. Restituisci JSON."""
                    response = model.generate_content([prompt, img])
                    st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore analisi: {e}")

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        
        # 1. VALIDAZIONE CONTABILE (Punto 2 richiesto)
        prodotti_raw = d.get('prodotti', [])
        totale_calcolato = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita', 1)) for p in prodotti_raw])
        totale_letto = clean_price(testata.get('totale_scontrino_letto', 0))
        
        st.subheader("📝 Revisione Dati")
        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            st.metric("Totale Scontrino", f"€{totale_letto:.2f}")
        with col_t2:
            st.metric("Totale Calcolato", f"€{totale_calcolato:.2f}", delta=round(totale_calcolato - totale_letto, 2), delta_color="inverse")
        with col_t3:
            if abs(totale_calcolato - totale_letto) < 0.05:
                st.success("✅ Totale OK")
            else:
                st.warning("⚠️ Controlla prezzi")

        # Sezione Negozio
        piva_letta = clean_piva(testata.get('p_iva', ''))
        match_negozio = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_letta), None) if piva_letta else None
        
        st.markdown('<div class="header-box">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            insegna_f = st.text_input("Supermercato", value=(match_negozio['Insegna_Standard'] if match_negozio else f"NUOVO ({piva_letta})")).upper()
        with c2:
            indirizzo_f = st.text_input("Indirizzo", value=(match_negozio['Indirizzo_Standard (Pulito)'] if match_negozio else testata.get('indirizzo_letto', ''))).upper()
        with c3:
            data_iso = testata.get('data_iso', '2026-01-01')
            data_f = st.text_input("Data (DD/MM/YYYY)", value="/".join(data_iso.split("-")[::-1]))
        st.markdown('</div>', unsafe_allow_html=True)

        # Tabella Prodotti
        lista_pulita = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Normalizzato": str(p.get('nome_standard', '')).upper()} for p in prodotti_raw]
        df_edit = pd.DataFrame(lista_pulita)
        edited_df = st.data_editor(df_edit, use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 SALVA NEL DATABASE"):
            try:
                final_rows = [[data_f, insegna_f, indirizzo_f, str(row['Prodotto']).upper(), clean_price(row['Prezzo Un.']) * float(row['Qtà']), 0, clean_price(row['Prezzo Un.']), str(row['Offerta']).upper(), row['Qtà'], "SI", str(row['Normalizzato']).upper()] for _, row in edited_df.iterrows()]
                worksheet.append_rows(final_rows)
                st.success("✅ Salvataggio completato!")
                st.session_state.dati_analizzati = None
                st.rerun()
            except Exception as e:
                st.error(f"Errore: {e}")

# --- TAB 2: RICERCA (Punto 1 richiesto) ---
with tab_cerca:
    st.subheader("🔍 Confronta Prezzi nel Database")
    query = st.text_input("Cerca un prodotto (es: Pomodoro, Pasta...)", "").upper()
    
    if query:
        # Carichiamo tutti i dati per la ricerca
        with st.spinner("Ricerca in corso..."):
            all_data = worksheet.get_all_records()
            df_all = pd.DataFrame(all_data)
            
            # Filtro per nome prodotto (cerca sia nel nome letto che nel normalizzato)
            mask = df_all['Prodotto'].str.contains(query, na=False) | df_all['Nome Standard Proposto'].str.contains(query, na=False)
            risultati = df_all[mask].copy()
            
            if not risultati.empty:
                # Pulizia e ordinamento
                risultati['Prezzo_Netto'] = risultati['Prezzo_Netto'].apply(clean_price)
                risultati = risultati.sort_values(by='Prezzo_Netto', ascending=True)
                
                # Visualizzazione "Miglior Prezzo"
                best = risultati.iloc[0]
                st.success(f"🏆 Miglior prezzo trovato: **€{best['Prezzo_Netto']:.2f}** presso **{best['Supermercato']}** ({best['Data']})")
                
                # Tabella completa
                st.write("### Tutti i risultati:")
                st.dataframe(risultati[['Prezzo_Netto', 'Supermercato', 'Indirizzo', 'Prodotto', 'Data', 'In Offerta']], use_container_width=True, hide_index=True)
            else:
                st.info("Nessun prodotto trovato con questo nome.")
