import sys
import os
# Ensure Python can find the 'mst_cabs' package inside the 'src' directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import time
import json
import re
from dotenv import load_dotenv

from mst_cabs.crew import MstCabsCrew
from mst_cabs.tools.email_tools import check_new_emails, read_godaddy_emails, set_last_uid

def run_bot():
    """
    Run the background loop that fetches emails and processes them using the crew.
    """
    load_dotenv()
    
    print("Starting background cab booking automation...")
    print("Listening for new emails...")
    
    cab_crew = MstCabsCrew().crew()
    
    while True:
        try:
            if check_new_emails():
                print("\n[+] New email detected! Fetching content...")
                
                email_text, current_uid = read_godaddy_emails("ALL")
                
                if "No new emails found" not in email_text and current_uid != 0:
                    print("\n[+] Waking up the agent to parse the email...")
                    
                    # --- NEW FAILOVER LOGIC ---
                    api_keys = [
                        os.getenv("GROQ_API_KEY"),
                        os.getenv("GROQ_API_KEY_2"),
                        os.getenv("GROQ_API_KEY_3")
                    ]
                    valid_keys = [k for k in api_keys if k and k.strip()]
                    if not valid_keys:
                        print("[-] ERROR: No GROQ_API_KEY found in .env file!")
                        continue
                        
                    result_obj = None
                    last_exception = None
                    
                    for attempt, key in enumerate(valid_keys):
                        os.environ["GROQ_API_KEY"] = key
                        try:
                            # Kickoff the process with the email content
                            result_obj = cab_crew.kickoff(inputs={"email_content": email_text})
                            break # Success! Break out of the fallback loop
                        except Exception as e:
                            last_exception = e
                            print(f"[-] Groq API Key {attempt + 1} failed! Trying backup key if available...")
                            time.sleep(2) # Short pause before retry
                    
                    if result_obj is None:
                        print(f"[-] ALL Groq API Keys failed! Skipping this email for now. Error: {last_exception}")
                        continue
                        
                    result = str(result_obj)
                    
                    try:
                        # Clean up potential markdown formatting like ```json ... ```
                        json_str = result.strip()
                        json_match = re.search(r'\{.*\}', json_str, re.DOTALL)
                        if json_match:
                            json_str = json_match.group(0)
                            
                        import json_repair
                        data = json_repair.loads(json_str)
                        
                        # Check the strict boolean spam filter
                        is_booking = data.get("is_cab_booking")
                        
                        if is_booking is False or str(is_booking).lower() == "false":
                            print("\n[-] Irrelevant email skipped (AI marked it as spam/newsletter).")
                            set_last_uid(current_uid) # Mark as successfully processed (spam)
                        else:
                            print("\n[+] Extraction successful! Pushing to Google Sheets Cloud Database...")
                            from mst_cabs.tools.google_sheets_tools import append_to_google_sheet
                            from datetime import datetime
                            import zoneinfo
                            
                            # Cloud servers usually run on UTC time. We MUST force it to Indian Standard Time (IST) 
                            # so your client doesn't get confused by bookings showing up 5.5 hours in the past!
                            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                            booking_creation_time = datetime.now(ist_tz).strftime("%d-%m-%Y %I:%M %p")
                            
                            def flatten_data(value):
                                if value is None:
                                    return ""
                                if isinstance(value, str):
                                    return value
                                if isinstance(value, list):
                                    return " | ".join(flatten_data(v) for v in value)
                                if isinstance(value, dict):
                                    return ", ".join(f"{flatten_data(v)}" for k, v in value.items())
                                return str(value)
                            
                            save_status = append_to_google_sheet(
                                customer_name=flatten_data(data.get('customer_name', '')),
                                pickup=flatten_data(data.get('pickup', '')),
                                dropoff=flatten_data(data.get('dropoff', '')),
                                date_time=flatten_data(data.get('date_time', '')),
                                booking_creation_time=booking_creation_time,
                                contact_info=flatten_data(data.get('contact_info', '')),
                                pickup_type=flatten_data(data.get('pickup_type', '')),
                                cars_required=flatten_data(data.get('cars_required', '')),
                                booked_by=flatten_data(data.get('booked_by', '')),
                                special_details=flatten_data(data.get('special_details', '')),
                                billing_to=flatten_data(data.get('billing_to', '')),
                                state=flatten_data(data.get('state', '')),
                                city=flatten_data(data.get('city', ''))
                            )
                            
                            # SAFETY CHECK: If Google Sheets fails, we MUST crash this block so it doesn't save the UID!
                            if "CRITICAL ERROR" in save_status:
                                raise Exception(save_status)
                                
                            print(save_status)
                            
                            # ONLY increase the count if it successfully reaches the end!
                            set_last_uid(current_uid)
                            print(f"[SUCCESS] Email UID {current_uid} fully processed and marked as complete.")
                            
                    except Exception as je:
                        print(f"[-] Failed to parse JSON or push to Sheets: {je}\nRaw output: {result}")
                        print("[-] Will retry this email on the next loop since it failed.")
                            
                    print("\n[+] Finished processing block. Going back to sleep...")
            else:
                pass
                
        except Exception as e:
            print(f"Error during polling: {e}")
            
        time.sleep(60)

from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is active!"

def run():
    # 1. Start the main cab-booking infinite loop in a background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # 2. Start the dummy web server on the main thread to keep Render happy
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting dummy web server on port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    run()
