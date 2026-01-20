import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image

# --- CONFIGURAZIONE E CONNESSIONE ---
st.set_page_config(page_title="AI Scanner Scontrini", layout="centered")

# Recupero Credenziali dai Secrets (STRUTTURA PIATTA)
try:
    # 1. Carichiamo la chiave API di Gemini
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    # 2. Ricostruiamo il dizionario per Google Sheets prendendo i campi uno ad uno
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
    
    creds = Credentials.from_service_account_info(google_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    
    # Rilevamento modello
    modelli_validi = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    modello_scelto = next((m for m in modelli_validi if 'flash' in m.lower()), modelli_validi[0])
    model = genai.GenerativeModel(modello_scelto)
    
except Exception as e:
    st.error(f"Errore di configurazione: {e}")
    st.info("Assicurati di aver inserito tutti i campi nei Secrets di Streamlit.")
    st.stop()

# --- MAPPA INSEGNE ---
INSEGNE_MAP = {
    "04916380159": "ESSELUNGA",
    "00796350239": "MARTINELLI",
    "00212810235": "EUROSPAR",
    "00150240230": "LIDL"
}

# --- INTERFACCIA APP ---
st.title("🛒 Scanner Scontrini Smart")
st.write(f"Modello attivo: `{modello_scelto}`")

uploaded_file = st.file_uploader("Carica o scatta una foto", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino pronto", use_container_width=True)

    if st.button("Analizza e Salva"):
        with st.spinner("L'IA sta leggendo lo scontrino..."):
            try:
                prompt = "Analizza lo scontrino. Estrai P_IVA, indirizzo, data. Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, proposta_normalizzazione. Restituisci SOLO JSON."
                response = model.generate_content([prompt, img])
                dati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                
                st.subheader("Payload JSON")
                st.json(dati)

                p_iva = dati['testata'].get('p_iva', '').replace(' ', '').replace('.', '')
                insegna = INSEGNE_MAP.get(p_iva, f"SCONOSCIUTO ({p_iva})")

                nuove_righe = []
                for p in dati['prodotti']:
                    nuove_righe.append([
                        dati['testata'].get('data', ''),
                        insegna,
                        dati['testata'].get('indirizzo', ''),
                        p.get('nome_letto', '').upper(),
                        p.get('prezzo_unitario', 0) * p.get('quantita', 1),
                        0, p.get('prezzo_unitario', 0), p.get('is_offerta', 'NO'),
                        p.get('quantita', 1), "SI", p.get('proposta_normalizzazione', '').upper()
                    ])
                
                worksheet.append_rows(nuove_righe)
                st.success(f"✅ Salvate {len(nuove_righe)} righe di {insegna} nel database!")
            except Exception as e:
                st.error(f"Errore analisi: {e}")
