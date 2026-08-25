import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import time
import re
from datetime import datetime

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


def _normalize_name_tokens(name_str: str) -> set:
    """Extract clean lowercase name tokens excluding titles/honorifics."""
    if not name_str:
        return set()
    cleaned = re.sub(r'[^\w\s]', ' ', str(name_str).lower())
    ignore_titles = {'mr', 'mrs', 'ms', 'dr', 'prof', 'shri', 'shree', 'smt', 'sir', 'madam', 'guest'}
    tokens = {t for t in cleaned.split() if t and t not in ignore_titles}
    return tokens


def _names_match(name_a: str, name_b: str) -> bool:
    """Check if two guest names refer to the same person."""
    tokens_a = _normalize_name_tokens(name_a)
    tokens_b = _normalize_name_tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    if tokens_a == tokens_b:
        return True
    if tokens_a.issubset(tokens_b) or tokens_b.issubset(tokens_a):
        return True
    return False


def _parse_date_to_tuple(date_str: str):
    """Normalize date to (year, month, day) tuple for comparison."""
    if not date_str:
        return None
    cleaned = str(date_str).strip().lstrip("'").strip()
    explicit_formats = ["%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for fmt in explicit_formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return (dt.year, dt.month, dt.day)
        except ValueError:
            pass
    try:
        from dateutil import parser as date_parser
        dt = date_parser.parse(cleaned, dayfirst=True)
        return (dt.year, dt.month, dt.day)
    except Exception:
        return None


def _normalize_time_str(time_str: str) -> str:
    """Normalize time string to standard 'HH:MM AM/PM' for comparison."""
    if not time_str:
        return ""
    cleaned = str(time_str).strip().lstrip("'").strip().upper()
    m = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', cleaned)
    if m:
        hr = int(m.group(1))
        mn = m.group(2)
        ampm = m.group(3) or ""
        return f"{hr:02d}:{mn} {ampm}".strip()
    return cleaned.lower()


def cancel_booking_in_google_sheet(
    guest_name: str,
    pickup_date: str,
    pickup_time: str = "",
    pickup_point: str = "",
    drop_point: str = "",
    cancellation_time: str = "",
    mob_no: str = "",
    booked_by: str = "",
    special_details: str = "",
    duty_type: str = "",
    cab_type: str = "",
    bill_to: str = "",
    state: str = "",
    city: str = "",
    cab_number: str = ""
) -> str:
    """
    Finds the matching active booking in Google Sheets, moves it to the
    'Cancelled Bookings' worksheet tab (preserving full records with cancellation timestamp),
    and deletes the original row from the active bookings sheet.

    If ambiguous (multiple matching active bookings with same name and date, but no distinguishing time/route),
    it logs an entry in 'Cancelled Bookings' and preserves the active rows for manual review without guessing.
    """
    credentials_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../credentials.json"))

    if not os.path.exists(credentials_path):
        return f"CRITICAL ERROR: {credentials_path} not found. Please follow the Google Setup Guide."

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(creds)

        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        try:
            if sheet_id:
                workbook = client.open_by_key(sheet_id)
            else:
                workbook = client.open("Master Cab Bookings")
        except gspread.exceptions.SpreadsheetNotFound:
            return "CRITICAL ERROR: Could not find the Google Sheet. Did you share it with the service account email?"

        active_sheet = workbook.sheet1

        # 1. Ensure "Cancelled Bookings" worksheet exists
        try:
            cancelled_sheet = workbook.worksheet("Cancelled Bookings")
        except gspread.exceptions.WorksheetNotFound:
            cancelled_sheet = workbook.add_worksheet(title="Cancelled Bookings", rows=1000, cols=20)
            cancelled_headers = [
                "S No.",
                "Cancellation Time",
                "Original Booking Time",
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
                "special details",
                "Cancellation Status"
            ]
            cancelled_sheet.append_row(cancelled_headers)
            time.sleep(1)

        # 2. Search active bookings for matches
        all_records = active_sheet.get_all_values()
        target_date_tuple = _parse_date_to_tuple(pickup_date)
        target_time_norm = _normalize_time_str(pickup_time)

        # Candidate format: (row_1_indexed, row_data, score)
        candidates = []

        # Skip header row (row index 1 in sheet, index 0 in all_records)
        for idx, row in enumerate(all_records[1:], start=2):
            if len(row) < 5:
                continue

            row_date_str = row[3] if len(row) > 3 else ""
            row_guest_str = row[4] if len(row) > 4 else ""
            row_mob_str = row[5] if len(row) > 5 else ""
            row_time_str = row[6] if len(row) > 6 else ""
            row_pickup_str = row[7] if len(row) > 7 else ""
            row_drop_str = row[8] if len(row) > 8 else ""

            row_date_tuple = _parse_date_to_tuple(row_date_str)
            dates_matched = (target_date_tuple is not None and row_date_tuple is not None and target_date_tuple == row_date_tuple)
            names_matched = _names_match(guest_name, row_guest_str)

            if dates_matched and names_matched:
                score = 10  # base match for name + date

                # Time match bonus
                if target_time_norm and _normalize_time_str(row_time_str) == target_time_norm:
                    score += 50

                # Mobile number bonus
                if mob_no and str(mob_no).strip() and str(mob_no).strip() in row_mob_str:
                    score += 30

                # Location keyword match bonuses
                if pickup_point and str(pickup_point).strip():
                    p_tokens = {t for t in re.sub(r'[^\w\s]', '', str(pickup_point).lower()).split() if len(t) > 2}
                    row_p_tokens = {t for t in re.sub(r'[^\w\s]', '', str(row_pickup_str).lower()).split() if len(t) > 2}
                    if p_tokens & row_p_tokens:
                        score += 20

                if drop_point and str(drop_point).strip():
                    d_tokens = {t for t in re.sub(r'[^\w\s]', '', str(drop_point).lower()).split() if len(t) > 2}
                    row_d_tokens = {t for t in re.sub(r'[^\w\s]', '', str(row_drop_str).lower()).split() if len(t) > 2}
                    if d_tokens & row_d_tokens:
                        score += 20

                candidates.append((idx, row, score))

        # Determine next S No. for Cancelled Bookings tab
        all_cancelled = cancelled_sheet.get_all_values()
        next_cancel_s_no = str(len(all_cancelled)) if all_cancelled else "1"
        if next_cancel_s_no == "0":
            next_cancel_s_no = "1"

        safe_date = f"'{pickup_date}" if pickup_date else ""
        safe_time = f"'{pickup_time}" if pickup_time else ""

        # Case 1: Exactly 1 candidate or 1 clear winner among multiple
        selected_candidate = None

        if len(candidates) == 1:
            selected_candidate = candidates[0]
        elif len(candidates) > 1:
            # Sort by score descending
            candidates.sort(key=lambda c: c[2], reverse=True)
            top_score = candidates[0][2]
            second_score = candidates[1][2]
            # If top candidate is significantly higher (e.g. exact time match vs different time)
            if top_score >= 60 and top_score > second_score:
                selected_candidate = candidates[0]

        if selected_candidate is not None:
            row_idx, matched_row, _ = selected_candidate

            # Prepare row for Cancelled Bookings tab
            cancel_row_data = [
                next_cancel_s_no,
                cancellation_time,
                matched_row[1] if len(matched_row) > 1 else "Not Given",  # Original booking creation time
                matched_row[2] if len(matched_row) > 2 else booked_by,    # Booked by
                matched_row[3] if len(matched_row) > 3 else safe_date,    # pickup date
                matched_row[4] if len(matched_row) > 4 else guest_name,   # guest name
                matched_row[5] if len(matched_row) > 5 else mob_no,       # mob no
                matched_row[6] if len(matched_row) > 6 else safe_time,    # pickup time
                matched_row[7] if len(matched_row) > 7 else pickup_point, # pickup point
                matched_row[8] if len(matched_row) > 8 else drop_point,   # drop point
                matched_row[9] if len(matched_row) > 9 else cab_number,   # cab number
                matched_row[10] if len(matched_row) > 10 else cab_type,   # cab type
                matched_row[11] if len(matched_row) > 11 else duty_type,  # duty type
                matched_row[12] if len(matched_row) > 12 else bill_to,    # bill to
                matched_row[13] if len(matched_row) > 13 else state,      # state
                matched_row[14] if len(matched_row) > 14 else city,       # city
                matched_row[15] if len(matched_row) > 15 else special_details, # special details
                "Successfully cancelled & removed from active bookings"
            ]

            safe_cancel_data = [str(item) if item is not None else "" for item in cancel_row_data]
            cancelled_sheet.insert_row(safe_cancel_data, index=2)
            time.sleep(1)

            # Delete the matched row from active bookings
            active_sheet.delete_rows(row_idx)

            return (f"Successfully cancelled booking for {guest_name} on {pickup_date} "
                    f"(Row {row_idx} moved to 'Cancelled Bookings' and deleted from active sheet).")

        elif len(candidates) > 1:
            # Case 2: Ambiguous (multiple matching active bookings without distinguishing time/route)
            cancel_row_data = [
                next_cancel_s_no,
                cancellation_time,
                "Multiple Matches in Sheet",
                booked_by,
                safe_date,
                guest_name,
                mob_no,
                safe_time,
                pickup_point,
                drop_point,
                cab_number,
                cab_type,
                duty_type,
                bill_to,
                state,
                city,
                special_details,
                f"AMBIGUOUS CANCELLATION: Found {len(candidates)} matching active bookings on {pickup_date} for {guest_name}. Active rows preserved for manual check."
            ]
            safe_cancel_data = [str(item) if item is not None else "" for item in cancel_row_data]
            cancelled_sheet.insert_row(safe_cancel_data, index=2)

            return (f"WARNING: Ambiguous cancellation for {guest_name} on {pickup_date} — {len(candidates)} "
                    f"matching active trips found. Preserved active trips and logged to 'Cancelled Bookings' for manual review.")

        else:
            # Case 3: No matching row found in active sheet
            cancel_row_data = [
                next_cancel_s_no,
                cancellation_time,
                "Not Found in Active Sheet",
                booked_by,
                safe_date,
                guest_name,
                mob_no,
                safe_time,
                pickup_point,
                drop_point,
                cab_number,
                cab_type,
                duty_type,
                bill_to,
                state,
                city,
                special_details,
                "RECORDED ONLY: Original booking was not found in active sheet (possibly already removed or date differs)."
            ]
            safe_cancel_data = [str(item) if item is not None else "" for item in cancel_row_data]
            cancelled_sheet.insert_row(safe_cancel_data, index=2)

            return (f"Recorded cancellation for {guest_name} on {pickup_date} in 'Cancelled Bookings' "
                    f"(Original booking was not found in active sheet).")

    except Exception as e:
        return f"CRITICAL ERROR interacting with Google Sheets during cancellation: {str(e)}"


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