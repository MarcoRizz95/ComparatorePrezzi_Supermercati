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
    MODEL_NAME = 'gemini-2.5-pro' 
    model = genai.GenerativeModel(MODEL_NAME)
    
except Exception as e:
    st.error(f"Errore di configurazione iniziale: {e}")
    st.stop()

# --- MAPPA INSEGNE ---
INSEGNE_MAP = {
    "04916380159": "ESSELUNGA",
    "00796350239": "MARTINELLI",
    "00212810235": "EUROSPAR",
    "00150240230": "LIDL"
}

# --- INTERFACCIA APP ---
st.title("🛒 Scanner Scontrini Pro")
st.write(f"Motore di analisi: `{MODEL_NAME}`")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino caricato", use_container_width=True)

    if st.button("Analizza e Salva"):
        with st.spinner(f"L'IA {MODEL_NAME} sta analizzando i dati..."):
            try:
                prompt = """
                Analizza questo scontrino. 
                - Sconti: sottrai il valore negativo al prodotto precedente.
                - Moltiplicazioni: se vedi 'pz x' sopra un nome prodotto, usa quel prezzo come unitario.
                - P_IVA: estrai la Partita IVA.
                - Restituisci JSON con prodotti (nome_letto, prezzo_unitario, quantita, is_offerta, proposta_normalizzazione).
                """
                
                response = model.generate_content([prompt, img])
                
                # Pulizia della risposta JSON
                clean_json = response.text.strip().replace('```json', '').replace('```', '')
                dati = json.loads(clean_json)
                
                st.subheader("Dati Estratti (JSON)")
                st.json(dati)

                # Identificazione Supermercato
                p_iva = dati['testata'].get('p_iva', '').replace(' ', '').replace('.', '')
                insegna = INSEGNE_MAP.get(p_iva, f"SCONOSCIUTO ({p_iva})")

                # Preparazione righe per lo Sheet
                nuove_righe = []
                for p in dati['prodotti']:
                    nuove_righe.append([
                        dati['testata'].get('data', ''),
                        insegna,
                        dati['testata'].get('indirizzo', ''),
                        p.get('nome_letto', '').upper(),
                        p.get('prezzo_unitario', 0) * p.get('quantita', 1),
                        0, # Sconto (già calcolato nel netto)
                        p.get('prezzo_unitario', 0),
                        p.get('is_offerta', 'NO'),
                        p.get('quantita', 1),
                        "SI", # Da Normalizzare
                        p.get('proposta_normalizzazione', '').upper()
                    ])
                
                worksheet.append_rows(nuove_righe)
                st.success(f"✅ Ottimo! {len(nuove_righe)} articoli salvati correttamente.")
                
            except Exception as e:
                st.error(f"Errore durante l'analisi o il salvataggio: {e}")
                st.info("Potrebbe essere un problema di quota giornaliera o di lettura del formato scontrino.")
