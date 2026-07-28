import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import time

def sync_from_google_sheets():
    """
    Downloads new rows from Google Sheets and injects them live into the local Excel file!
    """
    print("Starting Local PC Sync App...")
    credentials_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "credentials.json"))
    
    if not os.path.exists(credentials_path):
        print(f"CRITICAL ERROR: {credentials_path} not found.")
        return

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)
        sheet = client.open("Master Cab Bookings").sheet1
    except Exception as e:
        print(f"Could not connect to Google Sheets: {e}")
        return

    last_processed_row = 1 # Start by assuming we only have headers synced

    # Let's see how many rows are already in the local Excel file to know where to resume downloading
    # We will import the existing append logic which handles pywin32 COM injection!
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))
    from mst_cabs.tools.excel_tools import append_to_excel
    import openpyxl

    file_path = os.path.abspath(os.getenv("EXCEL_FILE_PATH", "bookings.xlsx"))
    
    while True:
        try:
            print("\nChecking Google Sheets for new bookings...")
            # Get local row count to find out what we need to download
            local_row_count = 1
            if os.path.exists(file_path):
                wb = openpyxl.load_workbook(file_path, read_only=True)
                local_row_count = wb.active.max_row
                wb.close()
                
            all_cloud_records = sheet.get_all_values()
            cloud_row_count = len(all_cloud_records)
            
            if cloud_row_count > local_row_count:
                print(f"Found {cloud_row_count - local_row_count} new bookings to sync!")
                # Sync the missing rows
                for i in range(local_row_count, cloud_row_count):
                    row_data = all_cloud_records[i] # i is 0-indexed
                    
                    # Pad the row if it's missing columns (now 14 columns)
                    while len(row_data) < 14:
                        row_data.append("")
                        
                    print(f"Syncing booking {row_data[0]} for {row_data[4]}...")
                    status = append_to_excel(
                        customer_name=row_data[4],
                        pickup=row_data[5],
                        dropoff=row_data[6],
                        date_time=row_data[1],
                        booking_creation_time=row_data[2],
                        pickup_type=row_data[7],
                        cars_required=row_data[8],
                        contact_info=row_data[9],
                        special_details=row_data[10],
                        billing_to=row_data[11],
                        state=row_data[12],
                        city=row_data[13],
                        booked_by=row_data[3]
                    )
                    print(f"  -> {status}")
            else:
                print("Everything is up to date.")
                
        except Exception as e:
            print(f"Sync Error: {e}")
            
        print("Waiting 60 seconds before next sync...")
        time.sleep(60)

if __name__ == "__main__":
    sync_from_google_sheets()
