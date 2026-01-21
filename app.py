import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Master Scanner V11", layout="centered", page_icon="🛒")

# --- CONNESSIONE AI E DATABASE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    google_info = {
        "type": st.secrets["type"],
        "project_id": st.secrets["project_id"],
        "private_key_id": st.secrets["private_key_id"],
        "private_key": st.secrets["private_key"],
        "client_email": st.secrets["client_email"],
        "client_id": st.secrets["client_id"],
        "auth_uri": st.secrets["auth_uri"],
        "token_uri": st.secrets["token_uri"],
        "auth_provider_x509_cert_url": st.secrets["auth_provider_x509_cert_url"],
        "client_x509_cert_url": st.secrets["client_x509_cert_url"]
    }
    
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    
    # Foglio Database Prezzi (Primo tab)
    worksheet = sh.get_worksheet(0)
    
    # Lettura Anagrafe Negozi (Secondo tab - 4 COLONNE)
    try:
        ws_negozi = sh.worksheet("Anagrafe_Negozi")
        lista_negozi = ws_negozi.get_all_records()
    except:
        lista_negozi = []
        st.error("Errore: Tab 'Anagrafe_Negozi' non trovato o colonne non corrette.")

    # Lettura Glossario Prodotti per Normalizzazione
    try:
        dati_db = worksheet.get_all_records()
        glossario_prodotti = list(set([str(r.get('Nome Standard Proposto', '')).upper() for r in dati_db if r.get('Nome Standard Proposto')]))
    except:
        glossario_prodotti = []

    # Modello Gemini 1.5 Pro
    model = genai.GenerativeModel('models/gemini-2.5-flash')
    
except Exception as e:
    st.error(f"Errore di configurazione: {e}")
    st.stop()

# --- INTERFACCIA ---
st.title("🛒 Scanner Scontrini - v3")
st.write("Versione 11 - Match Punti Vendita Multilivello")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino caricato", use_container_width=True)

    if st.button("Analizza e Salva"):
        with st.spinner("Analisi e ricerca match in corso..."):
            try:
                # Costruiamo l'elenco dei negozi per l'IA (usando le tue 4 colonne)
                negozi_str = ""
                for i, n in enumerate(lista_negozi):
                    negozi_str += f"ID {i}: {n['Insegna_Standard']} | P.IVA: {n['P_IVA']} | Indirizzo Scontrino: {n['Indirizzo_Scontrino (Grezzo)']} | Indirizzo Pulito: {n['Indirizzo_Standard (Pulito)']}\n"

                prompt = f"""
                Analizza questo scontrino.
                
                NEGOZI CONOSCIUTI:
                {negozi_str}

                PRODOTTI CONOSCIUTI:
                {", ".join(glossario_prodotti[:100])}

                ISTRUZIONI:
                1. Estrai DATA (YYYY-MM-DD), P_IVA e INDIRIZZO dallo scontrino.
                2. Trova il match con 'NEGOZI CONOSCIUTI'. Restituisci l'ID se corrisponde P.IVA e l'indirizzo è simile a uno degli indirizzi conosciuti. Altrimenti 'NUOVO'.
                3. Estrai ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, nome_standard (se simile a prodotti conosciuti).
                4. SCONTI: Sottrai righe negative al prodotto precedente.
                5. NO AGGREGAZIONE: Una riga per ogni articolo fisico.

                RISPONDI SOLO JSON:
                {{
                  "match_id": "ID o NUOVO",
                  "testata": {{ "p_iva": "", "indirizzo_letto": "", "data_iso": "" }},
                  "prodotti": [
                    {{ "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "nome_standard": "" }}
                  ]
                }}
                """
                
                response = model.generate_content([prompt, img])
                dati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                
                st.subheader("Payload JSON")
                st.json(dati)

                # --- ELABORAZIONE E NORMALIZZAZIONE ---
                testata = dati.get('testata', {})
                match_id = dati.get('match_id', 'NUOVO')
                
                if str(match_id).isdigit() and int(match_id) < len(lista_negozi):
                    # Match trovato: usiamo i dati dell'anagrafe
                    negozio = lista_negozi[int(match_id)]
                    insegna = str(negozio['Insegna_Standard']).upper()
                    indirizzo = str(negozio['Indirizzo_Standard (Pulito)']).upper()
                else:
                    # Nessun match: usiamo dati grezzi
                    p_iva = str(testata.get('p_iva', '')).replace(' ', '')
                    insegna = f"NUOVO ({p_iva})".upper()
                    indirizzo = str(testata.get('indirizzo_letto', 'DA VERIFICARE')).upper()

                # Formattazione Data (YYYY-MM-DD -> DD/MM/YYYY)
                d_raw = testata.get('data_iso', '2026-01-01')
                try:
                    y, m, d = d_raw.split('-')
                    data_ita = f"{d}/{m}/{y}"
                except:
                    data_ita = d_raw

                # Creazione righe
                righe_da_scrivere = []
                for p in dati.get('prodotti', []):
                    p_unitario = float(p.get('prezzo_unitario', 0))
                    qt = float(p.get('quantita', 1))
                    
                    righe_da_scrivere.append([
                        data_ita,
                        insegna,
                        indirizzo,
                        str(p.get('nome_letto', '')).upper(),
                        p_unitario * qt,
                        0, # Sconto (già calcolato)
                        p_unitario,
                        p.get('is_offerta', 'NO'),
                        qt,
                        "SI",
                        str(p.get('nome_standard', p.get('nome_letto', ''))).upper()
                    ])
                
                if righe_da_scrivere:
                    worksheet.append_rows(righe_da_scrivere)
                    st.success(f"✅ Salvati {len(righe_da_scrivere)} prodotti per {insegna}!")
                else:
                    st.warning("Nessun prodotto trovato.")

            except Exception as e:
                st.error(f"Errore: {e}")
