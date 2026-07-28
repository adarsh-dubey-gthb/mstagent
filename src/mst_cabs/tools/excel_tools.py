import openpyxl
import os
from dotenv import load_dotenv

load_dotenv()

def append_to_excel(customer_name: str, pickup: str, dropoff: str, date_time: str, booking_creation_time: str, contact_info: str, pickup_type: str, cars_required: str, booked_by: str, special_details: str, billing_to: str, state: str, city: str) -> str:
    """
    Appends a new booking record to the Excel sheet.
    """
    file_path = os.path.abspath(os.getenv("EXCEL_FILE_PATH", "bookings.xlsx"))
    
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
                
                # Find the total number of rows to calculate the next booking number
                last_row = sheet.Cells(sheet.Rows.Count, 1).End(-4162).Row # xlUp
                
                # If the sheet is totally blank, write headers first
                if last_row == 1 and sheet.Cells(1,1).Value is None:
                    headers = ["Booking Number", "Pickup Date & Time", "Booking Creation Time", "Booked By", "Customer Name", "Pickup Location", "Drop-off Location", "Pickup Type", "Cars Required", "Contact Info", "Special Details", "Billing To", "State", "City"]
                    for c_idx, h_val in enumerate(headers, start=1):
                        sheet.Cells(1, c_idx).Value = h_val
                    
                booking_number = str(last_row) # Auto-increment
                data = [booking_number, date_time, booking_creation_time, booked_by, customer_name, pickup, dropoff, pickup_type, cars_required, contact_info, special_details, billing_to, state, city]
                
                # We want to insert the new booking at Row 2 (pushing old bookings down)
                sheet.Rows(2).Insert()
                
                # Insert the data in real-time!
                for col_idx, value in enumerate(data, start=1):
                    sheet.Cells(2, col_idx).Value = value
                    
                # Automatically expand the columns to fit the new text perfectly!
                sheet.Columns.AutoFit()
                
                # Save the workbook so the changes persist
                target_wb.Save()
                
                return f"Successfully LIVE-UPDATED booking {booking_number} for {customer_name} directly in your open Excel window!"
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
            booking_number = str(sheet.max_row)
        else:
            wb = openpyxl.Workbook()
            sheet = wb.active
            # Write headers
            sheet.append(["Booking Number", "Pickup Date & Time", "Booking Creation Time", "Booked By", "Customer Name", "Pickup Location", "Drop-off Location", "Pickup Type", "Cars Required", "Contact Info", "Special Details", "Billing To", "State", "City"])
            booking_number = "1"
            
            # Auto-expand columns for better visibility
            for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N']:
                sheet.column_dimensions[col].width = 30
            
        # Insert data row at row 2
        data = [booking_number, date_time, booking_creation_time, booked_by, customer_name, pickup, dropoff, pickup_type, cars_required, contact_info, special_details, billing_to, state, city]
        sheet.insert_rows(2)
        for col_idx, value in enumerate(data, start=1):
            sheet.cell(row=2, column=col_idx, value=value)
            
        wb.save(file_path)
        return f"Successfully saved booking {booking_number} for {customer_name} to Excel."
    except Exception as e:
        # If the file is locked by Excel (PermissionError) or any other issue, save to a backup CSV!
        backup_file = "backup_bookings.csv"
        import csv
        file_exists = os.path.exists(backup_file)
        
        # If saving to backup, we might not know the true booking number if the excel read failed, 
        # so we will use a fallback timestamp or just sequential row count of the CSV.
        if 'booking_number' not in locals():
            import time
            booking_number = f"BACKUP_{int(time.time())}"
            
        try:
            with open(backup_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Booking Number", "Booked By", "Customer Name", "Pickup Location", "Drop-off Location", "Date/Time", "Pickup Type", "Cars Required", "Contact Info", "Special Details", "Billing To", "State", "City"])
                writer.writerow([booking_number, booked_by, customer_name, pickup, dropoff, date_time, pickup_type, cars_required, contact_info, special_details, billing_to, state, city])
            return f"WARNING: Could not write to Excel (file might be open). Data was saved safely to {backup_file} instead with ID {booking_number}!"
        except Exception as backup_e:
            return f"CRITICAL ERROR: Failed to write to Excel ({str(e)}) AND failed to write to backup ({str(backup_e)})."
