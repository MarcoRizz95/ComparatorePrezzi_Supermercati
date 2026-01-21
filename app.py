import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Scontrini AI 2026", layout="centered")

try:
    # 1. Recupero API KEY di Gemini dai Secrets
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    # 2. Configurazione Google Sheets dal Service Account
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
    worksheet = sh.get_worksheet(0)
    
    # PUNZIAMO SUL MODELLO DI NUOVA GENERAZIONE 2.5 PRO
    # (Se nel 2026 il nome è leggermente diverso, es. gemini-3-pro, cambialo qui)
    MODEL_NAME = 'gemini-2.5-flash' 
    model = genai.GenerativeModel(MODEL_NAME)
    
except Exception as e:
    st.error(f"Errore di configurazione iniziale: {e}")
    st.stop()

# --- MAPPA INSEGNE ---
INSEGNE_MAP = {
    "04916380159": "ESSELUNGA",
    "00796350239": "MARTINELLI",
    "00212810235": "EUROSPAR",
    "00150240230": "LIDL",
    "00858310238": "MIGROSS",
    "00542090238": "IPERFAMILA",
    "01274580248": "FAMILA"
}

# --- INTERFACCIA APP ---
st.title("🛒 Scanner Scontrini")
st.write(f"Motore di analisi: `{MODEL_NAME}`")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino caricato", use_container_width=True)

    if st.button("Analizza e Salva"):
        with st.spinner("Analisi in corso..."):
            try:
                # Prompt potenziato per Migross ed Esselunga
                prompt = """
                Analizza questo scontrino. 
                DATI TESTATA (Cerca in alto e in basso): p_iva, indirizzo, data.
                DATI PRODOTTI: 
                - Identifica ogni articolo. 
                - Se la riga successiva contiene '-SCONTO' o un valore negativo, sottrailo al prodotto precedente.
                - Restituisci JSON con chiavi 'testata' e 'prodotti'.
                - In 'prodotti' inserisci: nome_letto, prezzo_unitario, quantita, is_offerta, proposta_normalizzazione.
                """
                
                response = model.generate_content([prompt, img])
                raw_text = response.text.strip().replace('```json', '').replace('```', '')
                dati = json.loads(raw_text)
                
                st.subheader("Payload JSON")
                st.json(dati)

                # Gestione robusta
                testata = dati.get('testata', {})
                prodotti = dati.get('prodotti', [])
                if isinstance(dati, list): prodotti = dati

                # Recupero Insegna tramite P.IVA
                p_iva_raw = str(testata.get('p_iva', '')).replace(' ', '').replace('.', '')
                insegna = INSEGNE_MAP.get(p_iva_raw, f"SCONOSCIUTO ({p_iva_raw})")
                
                nuove_righe = []
                for p in prodotti:
                    p_unitario = float(p.get('prezzo_unitario', 0))
                    qt = float(p.get('quantita', 1))
                    
                    nuove_righe.append([
                        testata.get('data', 'DATA MANCANTE'),
                        insegna,
                        testata.get('indirizzo', 'INDIRIZZO MANCANTE'),
                        str(p.get('nome_letto', '')).upper(),
                        p_unitario * qt,
                        0, # Sconto (già calcolato nell'unitario dall'IA)
                        p_unitario,
                        p.get('is_offerta', 'NO'),
                        qt,
                        "SI",
                        str(p.get('proposta_normalizzazione', '')).upper()
                    ])
                
                if nuove_righe:
                    worksheet.append_rows(nuove_righe)
                    st.success(f"✅ Ottimo! Salvati {len(nuove_righe)} articoli di {insegna}!")
                else:
                    st.warning("Nessun prodotto trovato.")

            except Exception as e:
                st.error(f"Errore durante l'analisi o salvataggio: {e}")
