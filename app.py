import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- 1. FUNZIONI DI SERVIZIO ---

def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

def get_col_name(df, keyword):
    for c in df.columns:
        if keyword.upper() in str(c).upper().strip():
            return c
    return None

# --- 2. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Spesa V22", layout="centered", page_icon="🛒")

# CSS MINIMALE (Solo per i bordi dei box, senza forzare colori di sfondo)
st.markdown("""
    <style>
    .header-box { 
        padding: 15px; 
        border-radius: 10px; 
        border: 1px solid #cccccc; 
        margin-bottom: 20px; 
    }
    .stMetric { 
        background-color: rgba(0,0,0,0.03); 
        padding: 10px; 
        border-radius: 5px; 
    }
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

# --- 4. LOGICA TABS ---
tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None

    files = st.file_uploader("Trascina o scatta foto (anche multiple)", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)

    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        st.image(imgs, width=150)
        
        if st.button("🚀 ANALIZZA ORA"):
            with st.spinner("L'IA sta leggendo le foto..."):
                try:
                    all_db_data = worksheet.get_all_records()
                    glossario = list(set([str(r.get('Nome Standard Proposto', r.get('Proposta_Normalizzazione', ''))).upper() for r in all_db_data if r]))
                    
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

                    JSON richiesto:
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
        st.info(f"Somma articoli rilevati: **€{tot_calc:.2f}**")

        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        
        st.write("### 📝 Revisione Testata")
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: 
            d_iso = testata.get('data_iso', '2026-01-01')
            try: d_ita = "/".join(d_iso.split("-")[::-1])
            except: d_ita = d_iso
            data_f = st.text_input("Data", value=d_ita)

        st.write("### 📦 Elenco Prodotti")
        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Nome Standard": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in prodotti_raw]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 CONFERMA E SALVA NEL DATABASE"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Nome Standard']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("✅ Salvataggio completato!"); st.session_state.dati_analizzati = None; st.rerun()

with tab_cerca:
    st.write("## 🔍 Cerca Prezzi nel Database")
    query = st.text_input("Inserisci nome prodotto (es: Tonno, Mozzarella...)", key="search_input").upper().strip()
    
    if query:
        with st.spinner("Consultazione in corso..."):
            all_data = worksheet.get_all_records()
            if all_data:
                df_all = pd.DataFrame(all_data)
                df_all.columns = [str(c).strip() for c in df_all.columns]
                
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
                        st.info(f"🏆 Il più economico: **{best[c_super]}** a **€{best[c_prezzo]:.2f}** ({best[c_data]})")
                        
                        disp = res[[c_prezzo, c_super, c_indirizzo, c_data, c_off, c_prod]]
                        disp.columns = ['€ Prezzo', 'Negozio', 'Indirizzo', 'Data', 'Offerta', 'Nome Scontrino']
                        st.dataframe(disp, use_container_width=True, hide_index=True)
                    else: st.warning("Nessun prodotto trovato.")
                else: st.error("Errore: Colonne 'Prodotto' o 'Prezzo' non trovate nello Sheet.")
