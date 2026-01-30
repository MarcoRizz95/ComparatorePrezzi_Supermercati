import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- 1. FUNZIONI DI SERVIZIO (Definite subito per evitare NameError) ---

def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

def get_col_name(df, keyword):
    """Cerca il nome della colonna che contiene una determinata parola"""
    for c in df.columns:
        if keyword.upper() in str(c).upper().strip():
            return c
    return None

# --- 2. CONFIGURAZIONE PAGINA E CSS ---
st.set_page_config(page_title="Scanner Spesa V21", layout="centered", page_icon="🛒")

# CSS per Tab Leggibili e Layout
st.markdown("""
    <style>
    /* Sfondo generale e box */
    .stApp { background-color: #f8f9fa; }
    .header-box { padding: 20px; border-radius: 15px; border: 2px solid #d1d3d4; margin-bottom: 20px; background-color: #ffffff; color: #000000; }
    
    /* FIX COLORI TAB: Forza visibilità testo */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] {
        background-color: #e0e0e0 !important;
        color: #333333 !important;
        border-radius: 10px 10px 0 0;
        padding: 10px 20px;
        font-weight: bold;
    }
    .stTabs [aria-selected="true"] {
        background-color: #004a99 !important;
        color: #ffffff !important;
    }
    
    /* Box Vincitore Ricerca */
    .winner-box { background-color: #d4edda; padding: 15px; border-radius: 10px; border-left: 5px solid #28a745; margin-bottom: 20px; color: #155724; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. CONNESSIONE AI E DATABASE ---
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

# --- 4. LOGICA APP (TABS) ---

tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None

    files = st.file_uploader("Carica foto scontrino (anche multiple)", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)

    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        st.image(imgs, width=120)
        
        if st.button("🚀 ANALIZZA SCONTRINO"):
            with st.spinner("L'IA sta elaborando le foto..."):
                try:
                    all_db_data = worksheet.get_all_records()
                    glossario = list(set([str(r.get('Nome Standard Proposto', r.get('Proposta_Normalizzazione', ''))).upper() for r in all_db_data if r]))
                    
                    # PROMPT ORIGINALE INTEGRATO PER MULTI-FOTO
                    prompt = f"""
                    ATTENZIONE: Se caricate più immagini, sono parti dello STESSO scontrino. Analizzale insieme come un unico documento.

                    Agisci come un contabile esperto. Analizza lo scontrino con queste REGOLE FISSE:

                    1. SCONTI: Se vedi 'SCONTO', 'FIDATY', prezzi negativi (es: 1,50-S o -0,90) o sconti con "%" 
                       NON creare nuove righe. Sottrai il valore al prodotto sopra.
                       Esempio: riga 1 'MOZZARELLA 4.00' e riga 2 'SCONTO -1.00' = Mozzarella a 3.00 (is_offerta: SI), senza estrarre la riga di sconto.

                    2. MOLTIPLICAZIONI: Se vedi '2 x 1.50' sopra un prodotto, 
                       prezzo_unitario è 1.50 e quantita è 2.

                    3. NORMALIZZAZIONE: Usa i nomi da questa lista se corrispondono: {glossario[:150]}

                    4. ESTREMA PRECISIONE: Non inventare prodotti e non "raggrupparli". Se ci sono due righe uguali, non salvarne una unica con prezzo e quantità doppie.
                       Ogni riga fisica deve essere letta.

                    JSON:
                    {{
                      "testata": {{ "p_iva": "", "indirizzo_letto": "", "data_iso": "YYYY-MM-DD", "totale_scontrino_letto": 0.0 }},
                      "prodotti": [ {{ "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "nome_standard": "" }} ]
                    }}
                    """
                    response = model.generate_content([prompt, *imgs])
                    st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore: {e}")

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti_raw = d.get('prodotti', [])
        
        tot_calc = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita', 1)) for p in prodotti_raw])
        st.info(f"💰 Somma articoli rilevati: €{tot_calc:.2f}")

        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        
        st.markdown('<div class="header-box">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: data_f = st.text_input("Data", value="/".join(testata.get('data_iso', '2026-01-01').split("-")[::-1]))
        st.markdown('</div>', unsafe_allow_html=True)

        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Nome Standard": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in prodotti_raw]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 SALVA NEL DATABASE"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Nome Standard']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("✅ Dati salvati!"); st.session_state.dati_analizzati = None; st.rerun()

with tab_cerca:
    st.subheader("🔍 Ricerca Prezzi")
    query = st.text_input("Cerca un prodotto nel database", "").upper().strip()
    
    if query:
        with st.spinner("Consultazione database..."):
            all_data = worksheet.get_all_records()
            if all_data:
                df_all = pd.DataFrame(all_data)
                df_all.columns = [str(c).strip() for c in df_all.columns]
                
                # Cerchiamo le colonne in modo flessibile
                c_prod = get_col_name(df_all, 'PRODOTTO')
                c_norm = get_col_name(df_all, 'NORMALIZZAZIONE')
                c_prezzo = get_col_name(df_all, 'NETTO') or get_col_name(df_all, 'UNITARIO')
                c_data = get_col_name(df_all, 'DATA')
                c_super = get_col_name(df_all, 'SUPERMERCATO')
                c_indirizzo = get_col_name(df_all, 'INDIRIZZO')
                c_off = get_col_name(df_all, 'OFFERTA')

                if c_prod and c_prezzo:
                    mask = df_all[c_prod].astype(str).str.contains(query, na=False)
                    if c_norm: mask |= df_all[c_norm].astype(str).str.contains(query, na=False)
                    
                    res = df_all[mask].copy()
                    if not res.empty:
                        res[c_prezzo] = res[c_prezzo].apply(clean_price)
                        res['dt'] = pd.to_datetime(res[c_data], format='%d/%m/%Y', errors='coerce')
                        res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=[c_super, c_indirizzo])
                        res = res.sort_values(by=c_prezzo)
                        
                        best = res.iloc[0]
                        st.markdown(f'<div class="winner-box">🏆 <b>{best[c_super]}</b> è il più economico per "{query}": <b>€{best[c_prezzo]:.2f}</b> ({best[c_data]})</div>', unsafe_allow_html=True)
                        
                        disp = res[[c_prezzo, c_super, c_indirizzo, c_data, c_off, c_prod]]
                        disp.columns = ['Prezzo €', 'Supermercato', 'Indirizzo', 'Data', 'Offerta', 'Nome Scontrino']
                        st.dataframe(disp, use_container_width=True, hide_index=True)
                    else: st.warning("Nessun prodotto trovato.")
                else: st.error("Errore: colonne del database non riconosciute.")
