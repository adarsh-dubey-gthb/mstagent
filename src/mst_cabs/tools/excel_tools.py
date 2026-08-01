import openpyxl
import os
from dotenv import load_dotenv

load_dotenv()

def append_to_excel(
    guest_name: str, 
    pickup_point: str, 
    drop_point: str, 
    date_time: str, 
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
    Appends a new booking record to the Excel sheet.
    """
    file_path = os.path.abspath(os.getenv("EXCEL_FILE_PATH", "bookings.xlsx"))
    
    # Define the exact headers requested
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
    
    # Split date_time into separate date and time parts
    date_time_parts = date_time.strip().rsplit(' ', 1)
    pickup_date = date_time_parts[0] if len(date_time_parts) > 1 else date_time
    pickup_time = date_time_parts[1] if len(date_time_parts) > 1 else "Not Given"

    # -------------------------------------------------------------------------
    # MAGIC FEATURE: LIVE EXCEL UPDATING!
    # If the user has Excel actively open on their screen, inject the data live!
    # -------------------------------------------------------------------------
    try:
        import win32com.client
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
            target_wb = None
            for wb in excel.Workbooks:
                if wb.FullName == file_path:
                    target_wb = wb
                    break
            
            if target_wb:
                # The file is currently open on the user's screen!
                sheet = target_wb.ActiveSheet
                
                # Find the total number of rows to calculate the next S No.
                last_row = sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row # xlUp
                
                # If the sheet is totally blank, write headers first
                if last_row == 1 and sheet.Cells(1,1).Value is None:
                    for c_idx, h_val in enumerate(headers, start=1):
                        sheet.Cells(1, c_idx).Value = h_val
                
                s_no = str(last_row) # Auto-increment
                
                data = [
                    s_no,
                    booking_creation_time,
                    booked_by,
                    pickup_date,
                    guest_name,
                    mob_no,
                    pickup_time,
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
                
                # We want to insert the new booking at Row 2 (pushing old bookings down)
                sheet.Rows(2).Insert()
                
                # Insert the data in real-time!
                for col_idx, value in enumerate(data, start=1):
                    sheet.Cells(2, col_idx).Value = str(value) if value is not None else ""
                    
                # Automatically expand the columns to fit the new text perfectly!
                sheet.Columns.AutoFit()
                
                # Save the workbook so the changes persist
                target_wb.Save()
                
                return f"Successfully LIVE-UPDATED booking {s_no} for {guest_name} directly in your open Excel window!"
        except Exception:
            pass # Excel is not running or the file isn't open. Fallback to normal.
    except ImportError:
        pass # pywin32 is not installed. Fallback to normal.

    # -------------------------------------------------------------------------
    # NORMAL FALLBACK: Save silently in the background using openpyxl
    # -------------------------------------------------------------------------
    try:
        if os.path.exists(file_path):
            wb = openpyxl.load_workbook(file_path)
            sheet = wb.active
            s_no = str(sheet.max_row)
        else:
            wb = openpyxl.Workbook()
            sheet = wb.active
            # Write headers
            sheet.append(headers)
            s_no = "1"
            
            # Auto-expand columns for better visibility
            for col_idx in range(1, len(headers) + 1):
                col_letter = openpyxl.utils.get_column_letter(col_idx)
                sheet.column_dimensions[col_letter].width = 30
            
        # Insert data row at row 2
        data = [
            s_no,
            booking_creation_time,
            booked_by,
            pickup_date,
            guest_name,
            mob_no,
            pickup_time,
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
        
        safe_data = [str(item) if item is not None else "" for item in data]
        
        sheet.insert_rows(2)
        for col_idx, value in enumerate(safe_data, start=1):
            sheet.cell(row=2, column=col_idx, value=value)
            
        wb.save(file_path)
        return f"Successfully saved booking {s_no} for {guest_name} to Excel."
    except Exception as e:
        # If the file is locked by Excel (PermissionError) or any other issue, save to a backup CSV!
        backup_file = "backup_bookings.csv"
        import csv
        file_exists = os.path.exists(backup_file)
        
        if 's_no' not in locals():
            import time
            s_no = f"BACKUP_{int(time.time())}"
            
        try:
            with open(backup_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(headers)
                writer.writerow([
                    s_no,
                    booking_creation_time,
                    booked_by,
                    pickup_date,
                    guest_name,
                    mob_no,
                    pickup_time,
                    pickup_point,
                    drop_point,
                    cab_number,
                    cab_type,
                    duty_type,
                    bill_to,
                    state,
                    city,
                    special_details
                ])
            return f"WARNING: Could not write to Excel (file might be open). Data was saved safely to {backup_file} instead with ID {s_no}!"
        except Exception as backup_e:
            return f"CRITICAL ERROR: Failed to write to Excel ({str(e)}) AND failed to write to backup ({str(backup_e)})."