import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import time

def append_to_google_sheet(
    guest_name: str, 
    pickup_point: str, 
    drop_point: str, 
    pickup_date: str,       # Changed from date_time
    pickup_time: str,       # Added as separate parameter
    booking_creation_time: str, 
    mob_no: str, 
    duty_type: str, 
    cab_type: str, 
    booked_by: str, 
    bill_to: str, 
    state: str,
    city: str,
    special_details: str,
    cab_number: str = ""
) -> str:
    """
    Appends a new booking record to the Google Sheet.
    Requires credentials.json to be present in the directory.
    """
    credentials_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../credentials.json"))
    
    if not os.path.exists(credentials_path):
        return f"CRITICAL ERROR: {credentials_path} not found. Please follow the Google Setup Guide."

    try:
        # 1. Authorize the gspread client
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)
        
        # 2. Open the spreadsheet by name
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        try:
            if sheet_id:
                workbook = client.open_by_key(sheet_id)
            else:
                workbook = client.open("Master Cab Bookings")
        except gspread.exceptions.SpreadsheetNotFound:
            return f"CRITICAL ERROR: Could not find the Google Sheet. Did you share it with the service account email?"
            
        sheet = workbook.sheet1
        
        # 3. Check if headers exist, if not, create them
        first_row = sheet.row_values(1)
        if not first_row:
            headers = [
                "S No.",
                "booking creation time",
                "Booked by",
                "pickup date",
                "guest name",
                "mob no.",
                "pickup time",
                "pickup point",
                "drop point",
                "cab number",
                "cab type",
                "duty type",
                "bill to",
                "state",
                "city",
                "special details"
            ]
            sheet.append_row(headers)
            time.sleep(1) # Small delay to ensure Google registers the row
        
        # 4. Generate the next S No. based on row count
        all_records = sheet.get_all_values()
        s_no = str(len(all_records)) # If 1 row of headers exists, len is 1, next booking is 1.
        
        if s_no == "0":
            s_no = "1"
            
        # FORCE Google Sheets to treat date and time as text by prepending an apostrophe.
        # This prevents the bug where Sheets turns "09:30 AM" into just "AM".
        safe_date = f"'{pickup_date}" if pickup_date else ""
        safe_time = f"'{pickup_time}" if pickup_time else ""
        
        # 5. Append the data in the new column order!
        data = [
            s_no,
            booking_creation_time,
            booked_by,
            safe_date,          # Uses the safe text-forced date
            guest_name,
            mob_no,
            safe_time,          # Uses the safe text-forced time
            pickup_point,
            drop_point,
            cab_number,
            cab_type,
            duty_type,
            bill_to,
            state,
            city,
            special_details
        ]
        
        # Google Sheets crashes if we try to insert a Python list or dictionary into a single cell.
        safe_data = [str(item) if item is not None else "" for item in data]
        
        # Insert at index 2 (Row 2, right below the headers) to keep newest bookings on top!
        sheet.insert_row(safe_data, index=2)
        
        return f"Successfully saved booking {s_no} for {guest_name} to Google Sheets!"
    except Exception as e:
        return f"CRITICAL ERROR interacting with Google Sheets: {str(e)}"

def get_cloud_uid() -> int:
    credentials_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../credentials.json"))
    if not os.path.exists(credentials_path):
        return 0
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)
        
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        if sheet_id:
            workbook = client.open_by_key(sheet_id)
        else:
            workbook = client.open("Master Cab Bookings")
        
        try:
            settings_sheet = workbook.worksheet("Settings")
        except gspread.exceptions.WorksheetNotFound:
            settings_sheet = workbook.add_worksheet(title="Settings", rows=10, cols=10)
            settings_sheet.update_acell('A1', 'Last Processed Email UID:')
            settings_sheet.update_acell('B1', '0')
            
        val = settings_sheet.acell('B1').value
        return int(val) if val else 0
    except Exception as e:
        print(f"Cloud UID Fetch Error: {e}")
        return 0

def set_cloud_uid(uid: int):
    credentials_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../credentials.json"))
    if not os.path.exists(credentials_path):
        return
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)
        
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        if sheet_id:
            workbook = client.open_by_key(sheet_id)
        else:
            workbook = client.open("Master Cab Bookings")
            
        settings_sheet = workbook.worksheet("Settings")
        settings_sheet.update_acell('B1', str(uid))
    except Exception as e:
        print(f"Cloud UID Update Error: {e}")