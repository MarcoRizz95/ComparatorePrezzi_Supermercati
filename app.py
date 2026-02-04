import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
import json
from PIL import Image, ImageOps 
import pandas as pd
import re
import requests
import uuid
from streamlit_js_eval import get_geolocation
from geopy.geocoders import Nominatim

# --- 1. FUNZIONI DI SERVIZIO (Tutte mantenute) ---

def get_road_distance(lat1, lon1, lat2, lon2):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        r = requests.get(url, timeout=3) # Timeout ridotto per velocità
        data = r.json()
        if data['code'] == 'Ok':
            return round(data['routes'][0]['distance'] / 1000, 1)
    except: pass
    return None

def get_coords_from_address(address):
    try:
        geolocator = Nominatim(user_agent="comparatore_spesa_v31_norm")
        location = geolocator.geocode(address)
        if location: return location.latitude, location.longitude
    except: pass
    return None, None

def clean_piva(piva):
    solo_numeri = re.sub(r'\D', '', str(piva))
    return solo_numeri.zfill(11) if solo_numeri else ""

def clean_price(price_str):
    if isinstance(price_str, (int, float)): return float(price_str)
    cleaned = re.sub(r'[^\d,.-]', '', str(price_str)).replace(',', '.')
    try: return float(cleaned)
    except: return 0.0

def generate_short_id():
    """Genera un ID univoco breve per i nuovi prodotti"""
    return str(uuid.uuid4())[:8]

# --- 2. CONNESSIONE ---
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
    google_info = dict(st.secrets)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(google_info, scopes=scopes)
    gc = gspread.authorize(creds)
    
    # Apertura Fogli
    sh = gc.open("Database_Prezzi")
    ws_scontrini = sh.worksheet("Scontrini") # Ex foglio principale
    ws_catalogo = sh.worksheet("Catalogo")   # NUOVO FOGLIO
    ws_negozi = sh.worksheet("Anagrafe_Negozi")
    
    # Caricamento Cache
    lista_negozi_raw = ws_negozi.get_all_records()
    model = genai.GenerativeModel('models/gemini-2.5-flash')
    
except Exception as e:
    st.error(f"Errore connessione o Fogli mancanti (Controlla di aver creato il foglio 'Catalogo'): {e}")
    st.stop()

# --- 3. GESTIONE POSIZIONE ---
if 'my_lat' not in st.session_state: st.session_state.my_lat = None
if 'my_lon' not in st.session_state: st.session_state.my_lon = None

st.title("🛍️ Spesa Normalizzata & Geolocalizzata")

tab_carica, tab_cerca = st.tabs(["📷 CARICA & NORMALIZZA", "🔍 CONFRONTA PREZZI"])

# --- TAB 1: CARICAMENTO ---
with tab_carica:
    if 'dati_analizzati' not in st.session_state: st.session_state.dati_analizzati = None
    
    files = st.file_uploader("Carica scontrini", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
    
    if files:
        imgs = [ImageOps.exif_transpose(Image.open(f)) for f in files]
        st.image(imgs, width=150)
        
        if st.button("🚀 ANALIZZA E NORMALIZZA"):
            with st.spinner("L'IA sta estraendo e strutturando i dati..."):
                try:
                    # Carichiamo il catalogo esistente per aiutare l'IA (opzionale, per ora passiamo solo il glossario nomi)
                    catalogo_raw = ws_catalogo.get_all_records()
                    nomi_noti = list(set([r['NOME_NORMALIZZATO'] for r in catalogo_raw if r['NOME_NORMALIZZATO']]))
                    
                    # PROMPT EVOLUTO: Estrae attributi strutturati
                    prompt = f"""
                    
                    Agisci con due ruoli simultanei: 
                    1. CONTABILE (per i calcoli di cassa precisi)
                    2. DATA MANAGER (per la normalizzazione del database)

                    Analizza le immagini dello scontrino (se più di una foto o immagine, si tratta dello stesso scontrino e quindi con gli stessi dati di testata come indirizzo etc etc...)
                    seguendo rigorosamente queste FASI:

                    --- FASE 1: PULIZIA CONTABILE (Regole 'V18') ---
                    
                    A. SCONTI E PREZZI NEGATIVI: 
                       Se vedi righe come 'SCONTO', 'FIDATY', 'ABBUONO' o importi col segno meno (es. -0.50) subito sotto un prodotto:
                       - NON creare una riga per lo sconto.
                       - SOTTRAI il valore al prezzo del prodotto sopra. 
                       - Imposta 'is_offerta' su "SI".
                       Esempio: "MOZZARELLA 3.00" seguito da "SCONTO -1.00" -> Scrivi 1 riga con Prezzo Unitario 2.00.

                    B. MOLTIPLICATORI (Quantità):
                       Se vedi indicazioni come '3 x 1.50' (3 pezzi a 1.50 l'uno):
                       - 'quantita_acquistata' = 3
                       - 'prezzo_unitario' = 1.50
                       
                    C. PREZZO AL KG/LITRO DELLO SCONTRINO:
                       Ignora le righe che indicano il prezzo al kg calcolato dalla bilancia (es. '1.200 kg x 10.00 €/kg'). A te interessa il prezzo finale dell'oggetto.

                    --- FASE 2: ESTRAZIONE E NORMALIZZAZIONE DATABASE ---
                    Per ogni riga risultante dalla Fase 1, estrai i dati per il catalogo:
                    
                    1. 'nome_grezzo': Il testo originale dello scontrino.
                    2. 'nome_normalizzato': Crea un nome standard descrittivo (es. 'LATTE GRANAROLO P.S. 1L').
                       - Se il prodotto è simile a uno di questi, usa ESATTAMENTE questo nome: {nomi_noti[:50]}
                    3. 'brand': La marca (es. BARILLA, GRANAROLO, COOP). Se non c'è, 'GENERICO'.
                    4. 'categoria': (es. LATTE, PASTA, BISCOTTI, ORTOFRUTTA).
                    5. 'formato': SOLO IL NUMERO della quantità netta del prodotto.
                       - Esempio: "Latte 1L" -> 1.0
                       - Esempio: "Pasta 500g" -> 0.5 (Converti sempre in KG o L)
                       - Esempio: "Tonno 3x80g" -> 0.24 (Somma il totale: 240g -> 0.24kg)
                    6. 'unita': Usa SOLO: 'KG' (per peso), 'L' (per liquidi/detersivi liquidi), 'PZ' (per oggetti non misurabili).
                       - IMPORTANTE: Converti tutto. 500ml -> 0.5 L. 200gr -> 0.2 KG.

                    --- OUTPUT FORMAT (JSON) ---
                    {{
                      "testata": {{ "p_iva": "solo cifre", "indirizzo": "indirizzo completo", "data_iso": "YYYY-MM-DD" }},
                      "prodotti": [
                        {{
                          "nome_grezzo": "LATTE GRAN. P.S.",
                          "nome_normalizzato": "LATTE GRANAROLO P.S. 1L",
                          "brand": "GRANAROLO",
                          "categoria": "LATTE",
                          "formato": 1.0,
                          "unita": "L",
                          "prezzo_unitario": 1.50,
                          "quantita_acquistata": 1,
                          "is_offerta": "NO"
                        }}
                      ]
                    }}
                    """
                    response = model.generate_content([prompt, *imgs])
                    text_resp = response.text.strip().replace('```json', '').replace('```', '')
                    st.session_state.dati_analizzati = json.loads(text_resp)
                    st.rerun()
                except Exception as e: st.error(f"Errore IA: {e}")

    # --- FASE DI REVISIONE E SALVATAGGIO ---
    if st.session_state.dati_analizzati:
        d = st.session_state.dati_analizzati
        testata = d.get('testata', {})
        prodotti = d.get('prodotti', [])
        
        # Match Negozio
        piva_l = clean_piva(testata.get('p_iva', ''))
        match = next((n for n in lista_negozi_raw if clean_piva(n.get('P_IVA', '')) == piva_l), None)
        
        c1, c2, c3 = st.columns(3)
        with c1: insegna_f = st.text_input("Supermercato", value=(match['Insegna_Standard'] if match else f"NUOVO ({piva_l})")).upper()
        with c2: indirizzo_f = st.text_input("Indirizzo", value=(match['Indirizzo_Standard (Pulito)'] if match else testata.get('indirizzo', ''))).upper()
        with c3: data_f = st.text_input("Data", value=testata.get('data_iso', '2026-01-01'))

        st.markdown("### 🛠️ Verifica Normalizzazione")
        st.caption("Controlla che il 'Nome Normalizzato', 'Formato' e 'Unità' siano corretti per permettere il confronto prezzi.")
        
        # Creiamo un DataFrame per l'editor
        df_editor = pd.DataFrame(prodotti)
        # Rinominiamo per l'utente
        col_map = {
            "nome_grezzo": "Scontrino", 
            "nome_normalizzato": "Nome Catalogo (Editabile)", 
            "prezzo_unitario": "Prezzo €", 
            "quantita_acquistata": "Qtà",
            "formato": "Peso/Vol (Tot)",
            "unita": "Unità (KG/L/PZ)",
            "brand": "Marca",
            "categoria": "Cat",
            "is_offerta": "Offerta"
        }
        df_editor = df_editor.rename(columns=col_map)
        
        edited_df = st.data_editor(df_editor, use_container_width=True, num_rows="dynamic", hide_index=True)

    if st.button("💾 SALVA NEL DATABASE RELAZIONALE"):
            with st.spinner("Salvataggio e pulizia dati in corso..."):
                import math 
                import time

                # --- FUNZIONE DI PULIZIA ANTI-CRASH (SANITIZATION) ---
                def sanitize_value(val):
                    """
                    Converte NaN, Infinity o valori corrotti in 0.0 o stringa vuota.
                    Previene l'errore 'InvalidJSONError' di Google Sheets.
                    """
                    if val is None:
                        return ""
                    if isinstance(val, float):
                        if math.isnan(val) or math.isinf(val):
                            return 0.0
                    return val

                # 1. CONTROLLO INTESTAZIONI CATALOGO
                # Se il foglio è nuovo o vuoto, scriviamo l'intestazione per evitare errori
                try:
                    if not ws_catalogo.get_all_values():
                        ws_catalogo.append_row(["ID_PRODOTTO", "NOME_NORMALIZZATO", "BRAND", "CATEGORIA", "FORMATO", "UNITA"])
                except Exception: 
                    pass # Se fallisce la lettura (es. timeout), proviamo comunque a procedere
                
                # 2. CARICAMENTO CATALOGO ESISTENTE
                try:
                    cat_records = ws_catalogo.get_all_records()
                    df_cat = pd.DataFrame(cat_records)
                except: 
                    df_cat = pd.DataFrame() # Se fallisce, creiamo un DF vuoto e gestiremo tutto come nuovi prodotti
                
                rows_scontrini = []
                rows_catalogo_new = []
                
                # Ciclo su tutte le righe modificate dall'utente nell'editor
                for idx, row in edited_df.iterrows():
                    
                    # --- A. PREPARAZIONE E PULIZIA DATI ---
                    norm_name = str(row["Nome Catalogo (Editabile)"]).upper().strip()
                    brand = str(row["Marca"]).upper().strip()
                    cat = str(row["Cat"]).upper().strip()
                    unit = str(row["Unità (KG/L/PZ)"]).upper().strip()
                    
                    # Gestione sicura del formato (peso/volume)
                    try: 
                        fmt_str = str(row["Peso/Vol (Tot)"]).replace(',', '.')
                        fmt = float(fmt_str) if fmt_str else 1.0
                    except: 
                        fmt = 1.0
                    fmt = sanitize_value(fmt)
                    
                    # --- B. GESTIONE ID PRODOTTO (LOGICA RELAZIONALE) ---
                    prod_id = None
                    
                    # 1. Cerca nel catalogo scaricato da Google
                    if not df_cat.empty and 'NOME_NORMALIZZATO' in df_cat.columns:
                        match_prod = df_cat[df_cat['NOME_NORMALIZZATO'] == norm_name]
                        if not match_prod.empty:
                            prod_id = str(match_prod.iloc[0]['ID_PRODOTTO'])
                    
                    # 2. Cerca nei prodotti "nuovi" che stiamo creando in questo stesso ciclo
                    if not prod_id:
                        for new_p in rows_catalogo_new:
                            # new_p[1] corrisponde al nome normalizzato nella lista che stiamo costruendo
                            if new_p[1] == norm_name:
                                prod_id = str(new_p[0])
                                break
                    
                    # 3. Se ancora nullo, crea un NUOVO ID
                    if not prod_id:
                        prod_id = generate_short_id()
                        # Aggiungiamo alla lista dei nuovi prodotti da scrivere su Sheets
                        rows_catalogo_new.append([
                            str(prod_id), 
                            norm_name, 
                            brand, 
                            cat, 
                            fmt, 
                            unit
                        ])
                    
                    # --- C. PREPARAZIONE RIGA SCONTRINO ---
                    # Gestione sicura prezzi e quantità
                    try: p_unit_str = str(row["Prezzo €"]).replace(',', '.')
                    except: p_unit_str = "0"
                    p_unit = float(p_unit_str) if p_unit_str else 0.0
                    
                    try: qta_str = str(row["Qtà"]).replace(',', '.')
                    except: qta_str = "1"
                    qta = float(qta_str) if qta_str else 1.0
                    
                    # Sanificazione finale matematica
                    p_unit = sanitize_value(p_unit)
                    qta = sanitize_value(qta)
                    tot_riga = sanitize_value(p_unit * qta)

                    # Costruzione della riga esatta (11 Colonne)
                    riga_completa = [
                        str(data_f),                        # A: Data
                        str(insegna_f),                     # B: Negozio
                        str(indirizzo_f),                   # C: Indirizzo
                        str(row["Scontrino"]).upper(),      # D: Descrizione Grezza
                        tot_riga,                           # E: Totale Riga
                        0,                                  # F: (ex sconto, mantenuto a 0)
                        p_unit,                             # G: Prezzo Unitario
                        str(row["Offerta"]).upper(),        # H: Offerta
                        qta,                                # I: Quantità
                        "SI",                               # J: Check
                        str(prod_id)                        # K: ID_PRODOTTO (Link al catalogo)
                    ]
                    rows_scontrini.append(riga_completa)

                # --- D. SCRITTURA SU GOOGLE SHEETS (BATCH) ---
                try:
                    # Scriviamo i nuovi prodotti nel catalogo (se ce ne sono)
                    if rows_catalogo_new:
                        ws_catalogo.append_rows(rows_catalogo_new, value_input_option='USER_ENTERED')
                    
                    # Scriviamo le righe dello scontrino
                    if rows_scontrini:
                        ws_scontrini.append_rows(rows_scontrini, value_input_option='USER_ENTERED')
                        
                    st.success(f"✅ Salvataggio riuscito! Aggiunte {len(rows_scontrini)} righe.")
                    st.session_state.dati_analizzati = None # Reset dati
                    
                    # Piccola pausa per dare tempo all'API di Google di respirare
                    time.sleep(1) 
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"⚠️ Errore durante la scrittura su Google Sheets: {e}")
                    st.warning("Dettaglio tecnico: Controlla che non ci siano celle con formule corrotte nel foglio.")

# --- TAB 2: RICERCA AVANZATA (NORMALIZZATA) ---
with tab_cerca:
    # Gestione Posizione (Uguale a prima)
    if st.session_state.my_lat:
        st.success(f"📍 Posizione attiva")
        if st.button("🔄 Resetta Posizione"):
            st.session_state.my_lat = None; st.session_state.my_lon = None; st.rerun()
    else:
        with st.expander("📍 Imposta posizione per calcolare distanza", expanded=True):
            c_gps, c_man = st.columns([1, 2])
            with c_gps:
                if st.button("Usa GPS"):
                    loc = get_geolocation()
                    if loc:
                        st.session_state.my_lat = loc['coords']['latitude']
                        st.session_state.my_lon = loc['coords']['longitude']
                        st.rerun()
            with c_man:
                addr_in = st.text_input("Indirizzo o Città")
                if st.button("Cerca Indirizzo"):
                    lat, lon = get_coords_from_address(addr_in)
                    if lat: st.session_state.my_lat, st.session_state.my_lon = lat, lon; st.rerun()

    st.markdown("---")
    query = st.text_input("🔍 Cerca Prodotto (es. Latte, Tonno, Granarolo)", key="search_norm").upper().strip()
    
    if query:
        with st.spinner("Unione tabelle e calcolo prezzi..."):
            # 1. Scarica entrambi i dataset
            data_scontrini = ws_scontrini.get_all_records()
            data_catalogo = ws_catalogo.get_all_records()
            
            if data_scontrini and data_catalogo:
                df_s = pd.DataFrame(data_scontrini)
                df_c = pd.DataFrame(data_catalogo)
                
                # Assicuriamoci che ID_PRODOTTO sia stringa per il join
                df_s['ID_PRODOTTO'] = df_s['ID_PRODOTTO'].astype(str)
                df_c['ID_PRODOTTO'] = df_c['ID_PRODOTTO'].astype(str)
                
                # 2. JOIN RELAZIONALE (La magia!)
                # Uniamo lo storico (Scontrini) con i dettagli normalizzati (Catalogo)
                df_full = pd.merge(df_s, df_c, on='ID_PRODOTTO', how='inner')
                
                # 3. Filtro Ricerca
                # Cerchiamo sia nel nome normalizzato, che nel brand, che nella categoria
                mask = (
                    df_full['NOME_NORMALIZZATO'].str.contains(query, na=False) |
                    df_full['BRAND'].str.contains(query, na=False) |
                    df_full['CATEGORIA'].str.contains(query, na=False)
                )
                res = df_full[mask].copy()
                
                if not res.empty:
                    # 4. Calcolo Prezzo Confrontabile (€/L o €/Kg)
                    # Convertiamo colonne numeriche
                    res['Prezzo_Unitario'] = res['Prezzo_Unitario'].apply(clean_price)
                    res['FORMATO'] = pd.to_numeric(res['FORMATO'], errors='coerce').fillna(1)
                    
                    # Calcolo Prezzo/Misura
                    res['PREZZO_AL_L_KG'] = res['Prezzo_Unitario'] / res['FORMATO']
                    
                    # 5. Calcolo Distanze (Logica esistente)
                    def add_dist(row):
                        if not st.session_state.my_lat: return 999
                        addr_clean = re.sub(r'\W+', '', str(row['Indirizzo'])).upper()
                        neg = next((n for n in lista_negozi_raw if re.sub(r'\W+', '', str(n.get('Indirizzo_Standard (Pulito)', ''))).upper() == addr_clean), None)
                        if neg and neg.get('Latitudine'):
                            try: return get_road_distance(st.session_state.my_lat, st.session_state.my_lon, float(str(neg['Latitudine']).replace(',','.')), float(str(neg['Longitudine']).replace(',','.')))
                            except: return 888
                        return 999

                    res['KM'] = res.apply(add_dist, axis=1)
                    
                    # 6. Ordinamento e Pulizia
                    res = res.sort_values(by=['PREZZO_AL_L_KG', 'KM'])
                    
                    # Top risultato
                    best = res.iloc[0]
                    u = best['UNITA']
                    st.success(f"🏆 Migliore offerta: **{best['NOME_NORMALIZZATO']}** a **{best['PREZZO_AL_L_KG']:.2f} €/{u}**")
                    st.caption(f"Presso {best['Negozio']} ({best['Data']}) - Prezzo cartellino: €{best['Prezzo_Unitario']}")
                    
                    # Tabella risultati
                    show_cols = ['Data', 'NOME_NORMALIZZATO', 'Prezzo_Unitario', 'PREZZO_AL_L_KG', 'Negozio', 'Indirizzo', 'KM', 'In_Offerta']
                    renames = {'NOME_NORMALIZZATO': 'Prodotto', 'Prezzo_Unitario': 'Prezzo Conf.', 'PREZZO_AL_L_KG': f'Prezzo/{u}'}
                    
                    st.dataframe(
                        res[show_cols].rename(columns=renames), 
                        use_container_width=True, 
                        hide_index=True,
                        column_config={
                            f"Prezzo/{u}": st.column_config.NumberColumn(format="%.2f €"),
                            "Prezzo Conf.": st.column_config.NumberColumn(format="%.2f €"),
                            "KM": st.column_config.NumberColumn(format="%.1f km")
                        }
                    )
                else:
                    st.warning("Nessun prodotto trovato nel database normalizzato.")
            else:
                st.info("Database vuoto.")
