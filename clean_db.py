import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
import os
import json

def run_cleanup():
    try:
        # Recuperiamo le credenziali dai Secrets di GitHub
        google_info = json.loads(os.environ['GOOGLE_SHEETS_JSON'])
        
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(google_info, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # Apriamo il foglio
        sh = gc.open("Database_Prezzi")
        worksheet = sh.get_worksheet(0)
        
        # Leggiamo i dati
        data = worksheet.get_all_records()
        if not data:
            print("Database vuoto. Nulla da pulire.")
            return
            
        df = pd.DataFrame(data)
        count_prima = len(df)
        
        # Identifichiamo le colonne per i duplicati (adattale se i nomi variano)
        # Cerchiamo colonne che contengano queste parole
        col_data = next((c for c in df.columns if 'DATA' in c.upper()), None)
        col_super = next((c for c in df.columns if 'SUPERMERCATO' in c.upper()), None)
        col_prod = next((c for c in df.columns if 'PRODOTTO' in c.upper()), None)
        col_prezzo = next((c for c in df.columns if 'NETTO' in c.upper() or 'UNITARIO' in c.upper()), None)
        
        subset_cols = [c for c in [col_data, col_super, col_prod, col_prezzo] if c is not None]
        
        # Rimuoviamo i duplicati mantenendo l'ultima occorrenza
        df_pulito = df.drop_duplicates(subset=subset_cols, keep='last')
        count_dopo = len(df_pulito)
        
        if count_dopo < count_prima:
            # Svuota e riscrivi
            worksheet.clear()
            worksheet.update([df_pulito.columns.values.tolist()] + df_pulito.values.tolist())
            print(f"Pulizia completata: rimosse {count_prima - count_dopo} righe duplicate.")
        else:
            print("Nessun duplicato trovato.")
            
    except Exception as e:
        print(f"Errore durante la pulizia: {e}")

if __name__ == "__main__":
    run_cleanup()
