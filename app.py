import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image

# --- CONFIGURAZIONE E CONNESSIONE ---
st.set_page_config(page_title="AI Scanner Scontrini", layout="centered")

# Recupero Credenziali dai Secrets di Streamlit
try:
    # Carichiamo la chiave API di Gemini
    API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    
    # Carichiamo le credenziali di Google Sheets dal JSON salvato nei segreti
    google_secrets = dict(st.secrets["google_sheets"])
    creds = Credentials.from_service_account_info(google_secrets, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sh = gc.open("Database_Prezzi")
    worksheet = sh.get_worksheet(0)
    
    # Rilevamento modello (come su Colab)
    modelli_validi = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    modello_scelto = next((m for m in modelli_validi if 'flash' in m.lower()), modelli_validi[0])
    model = genai.GenerativeModel(modello_scelto)
except Exception as e:
    st.error(f"Errore di configurazione: {e}")
    st.stop()

# --- INTERFACCIA APP ---
st.title("🛒 Scanner Scontrini Smart")
st.write(f"Modello attivo: `{modello_scelto}`")

uploaded_file = st.file_uploader("Carica o scatta una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Scontrino pronto", use_container_width=True)

    if st.button("Analizza e Salva nel Database"):
        with st.spinner("L'IA sta leggendo..."):
            try:
                # Prompt (Stessa logica V8.1 di Colab)
                prompt = "Analizza lo scontrino. Estrai P_IVA, indirizzo, data. Per ogni prodotto: nome_letto, prezzo_unitario, quantita, is_offerta, proposta_normalizzazione. Restituisci SOLO JSON."
                
                response = model.generate_content([prompt, img])
                dati = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
                
                st.subheader("Payload JSON")
                st.json(dati)

                # Scrittura su Sheets
                nuove_righe = []
                for p in dati['prodotti']:
                    nuove_righe.append([
                        dati['testata'].get('data', ''),
                        "DA MAPPARE", # Qui potremmo aggiungere il dizionario P.IVA dopo
                        dati['testata'].get('indirizzo', ''),
                        p.get('nome_letto', '').upper(),
                        p.get('prezzo_unitario', 0) * p.get('quantita', 1),
                        0, p.get('prezzo_unitario', 0), p.get('is_offerta', 'NO'),
                        p.get('quantita', 1), "SI", p.get('proposta_normalizzazione', '').upper()
                    ])
                
                worksheet.append_rows(nuove_righe)
                st.success(f"✅ Salvate {len(nuove_righe)} righe nel foglio Google!")
            except Exception as e:
                st.error(f"Errore durante l'analisi: {e}")
