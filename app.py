import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="Spesa Smart Master", layout="centered", page_icon="🛒")

def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

# --- CONNESSIONI ---
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
    st.error(f"Errore: {e}")
    st.stop()

# --- INTERFACCIA ---
tab_carica, tab_cerca = st.tabs(["📷 CARICA SCONTRINO", "🔍 CERCA PREZZI"])

with tab_carica:
    if 'dati_analizzati' not in st.session_state:
        st.session_state.dati_analizzati = None

    uploaded_file = st.file_uploader("Foto scontrino", type=['jpg', 'jpeg', 'png'])

    if uploaded_file:
        img = ImageOps.exif_transpose(Image.open(uploaded_file))
        st.image(img, use_container_width=True)
        
        if st.button("🚀 ANALIZZA"):
            with st.spinner("L'IA sta imparando dai tuoi dati precedenti..."):
                # Recupero glossario dinamico dallo Sheet (punto 2 della tua domanda)
                try:
                    all_db = worksheet.get_all_records()
                    glossario = list(set([str(r.get('Nome Standard Proposto', '')).upper() for r in all_db if r.get('Nome Standard Proposto')]))
                except:
                    glossario = []

                prompt = f"""
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
                
                response = model.generate_content([prompt, img])
                st.session_state.dati_analizzati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                st.rerun()

    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti_raw = d.get('prodotti', [])
        
        tot_calc = sum([clean_price(p.get('prezzo_unitario', 0)) * float(p.get('quantita', 1)) for p in prodotti_raw])
        st.info(f"💰 Somma articoli rilevati: €{tot_calc:.2f}")

        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo_letto', ''))).upper()
        with c3: data_f = st.text_input("Data", value="/".join(testata.get('data_iso', '2026-01-01').split("-")[::-1]))

        lista_edit = [{"Prodotto": str(p.get('nome_letto', '')).upper(), "Prezzo Un.": clean_price(p.get('prezzo_unitario', 0)), "Qtà": float(p.get('quantita', 1)), "Offerta": str(p.get('is_offerta', 'NO')).upper(), "Nome Standard": str(p.get('nome_standard', p.get('nome_letto', ''))).upper()} for p in prodotti_raw]
        edited_df = st.data_editor(pd.DataFrame(lista_edit), use_container_width=True, num_rows="dynamic", hide_index=True)

        if st.button("💾 SALVA TUTTO"):
            final_rows = [[data_f, insegna_f, indirizzo_f, str(r['Prodotto']).upper(), clean_price(r['Prezzo Un.']) * float(r['Qtà']), 0, clean_price(r['Prezzo Un.']), r['Offerta'], r['Qtà'], "SI", str(r['Nome Standard']).upper()] for _, r in edited_df.iterrows()]
            worksheet.append_rows(final_rows)
            st.success("Salvataggio completato!")
            st.session_state.dati_analizzati = None
            st.rerun()

# --- TAB 2: RICERCA (Logica Ultimo Prezzo) ---
with tab_cerca:
    query = st.text_input("Cerca prodotto", "").upper().strip()
    if query:
        df_all = pd.DataFrame(worksheet.get_all_records())
        df_all.columns = [c.strip() for c in df_all.columns]
        mask = df_all['Prodotto'].astype(str).str.contains(query, na=False) | df_all['Nome Standard Proposto'].astype(str).str.contains(query, na=False)
        res = df_all[mask].copy()
        if not res.empty:
            res['Prezzo_Netto'] = res['Prezzo_Netto'].apply(clean_price)
            res['dt'] = pd.to_datetime(res['Data'], format='%d/%m/%Y', errors='coerce')
            res = res.sort_values(by='dt', ascending=False).drop_duplicates(subset=['Supermercato', 'Indirizzo'])
            res = res.sort_values(by='Prezzo_Netto')
            
            best = res.iloc[0]
            st.success(f"🏆 {best['Supermercato']} - €{best['Prezzo_Netto']:.2f} ({best['Data']})")
            st.dataframe(res[['Prezzo_Netto', 'Supermercato', 'Indirizzo', 'Data', 'Offerta', 'Prodotto']], use_container_width=True, hide_index=True)
