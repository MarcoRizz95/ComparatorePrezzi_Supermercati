import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Scanner Scontrini AI", page_icon="🛒")
st.title("🛒 Scanner Scontrini per Comparatore")
st.write("Carica la foto e l'IA popolerà automaticamente il database su Google Sheets.")

# --- GESTIONE SEGRETI (API Key e Google Sheets) ---
# In Streamlit Cloud, questi si inseriscono nelle impostazioni (Secrets)
if "GEMINI_API_KEY" in st.secrets:
    API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    API_KEY = st.sidebar.text_input("Inserisci Gemini API Key", type="password")

if API_KEY:
    genai.configure(api_key=API_KEY)
    
    # 1. LOGICA AUTO-SELEZIONE MODELLO (Come su Colab)
    try:
        modelli_validi = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        modello_scelto = next((m for m in modelli_validi if 'flash' in m.lower()), modelli_validi[0])
        model = genai.GenerativeModel(modello_scelto)
        st.sidebar.success(f"Modello attivo: {modello_scelto}")
    except Exception as e:
        st.error(f"Errore nel rilevamento modelli: {e}")

    # 2. MAPPA INSEGNE
    INSEGNE_MAP = {
        "04916380159": "ESSELUNGA",
        "00796350239": "MARTINELLI",
        "00212810235": "EUROSPAR",
        "00150240230": "LIDL"
    }

    # 3. CARICAMENTO FILE
    uploaded_file = st.file_uploader("Scatta o seleziona una foto dello scontrino", type=['jpg', 'jpeg', 'png'])

    if uploaded_file:
        img = Image.open(uploaded_file)
        st.image(img, caption="Scontrino pronto per l'analisi", use_container_width=True)

        if st.button("Analizza e Salva su Database"):
            try:
                with st.spinner("L'intelligenza artificiale sta leggendo lo scontrino..."):
                    # RECUPERO GLOSSARIO (Per normalizzazione)
                    # Nota: Qui serve la connessione a Sheets tramite Service Account
                    # (Vedi sotto come configurare le credenziali)
                    
                    prompt = """
                    Analizza questo scontrino riga per riga con precisione chirurgica.
                    
                    LOGICA:
                    - Moltiplicazioni: Se vedi 'pz x' sopra un prodotto, estrai il prezzo unitario.
                    - Sconti: Sottrai le righe con il segno meno al prodotto precedente.
                    - P_IVA: Estrai la Partita IVA per identificare il negozio.
                    - No Aggregazione: Ogni riga fisica deve essere un oggetto nel JSON.

                    RESTITUISCI SOLO JSON:
                    {
                      "testata": { "p_iva": "", "indirizzo": "", "data": "" },
                      "prodotti": [
                        { "nome_letto": "", "prezzo_unitario": 0.0, "quantita": 1, "is_offerta": "SI/NO", "proposta_normalizzazione": "", "da_normalizzare": "SI" }
                      ]
                    }
                    """
                    
                    response = model.generate_content([prompt, img])
                    raw_text = response.text.strip().replace('```json', '').replace('```', '')
                    dati = json.loads(raw_text)

                    # MOSTRA IL PAYLOAD JSON (Richiesta punto 3)
                    st.subheader("Payload JSON Estratto")
                    st.json(dati)

                    # IDENTIFICAZIONE INSEGNA
                    p_iva = dati['testata'].get('p_iva', '').replace(' ', '').replace('.', '')
                    insegna = INSEGNE_MAP.get(p_iva, f"SCONOSCIUTO ({p_iva})")

                    # SALVATAGGIO SU GOOGLE SHEETS
                    # (Qui va inserita la logica gspread usando le credenziali dai segreti)
                    st.success(f"Dati di {insegna} pronti per il database!")
                    
            except Exception as e:
                st.error(f"Errore durante l'analisi: {e}")
else:
    st.warning("Inserisci la tua API Key per iniziare.")
