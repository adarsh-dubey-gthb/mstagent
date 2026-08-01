import sys
import time
import os
import builtins
import zoneinfo
from datetime import datetime, timedelta
# Ensure Python can find the 'mst_cabs' package inside the 'src' directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Force all print statements to flush instantly, bypassing cloud buffering delays
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    builtins.print(*args, **kwargs)

import json
import re
from dotenv import load_dotenv

from mst_cabs.crew import MstCabsCrew
from mst_cabs.tools.email_tools import check_new_emails, read_godaddy_emails, set_last_uid


def _parse_date_flexible(date_str):
    """
    Parse a date string in whatever format the LLM happened to return.
    Strategy:
      1. Try the explicit formats we most expect (fast, no ambiguity).
      2. Fall back to dateutil's flexible parser, which can handle almost any
         human-readable date string (e.g. '1st August 2026', 'Aug 1 2026',
         '2026/08/01', etc.) without us having to enumerate every format.
    dayfirst=True because this system deals with Indian-format dates (DD-MM-YYYY),
    so an ambiguous string like '01-02-2026' is read as 1st Feb, not Jan 2nd.
    """
    date_str = date_str.strip()

    explicit_formats = [
        "%d-%b-%Y",    # 01-Aug-2026
        "%d-%B-%Y",    # 01-August-2026
        "%d-%m-%Y",    # 01-08-2026
        "%d/%m/%Y",    # 01/08/2026
        "%Y-%m-%d",    # 2026-08-01
    ]
    for fmt in explicit_formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Fallback: dateutil handles nearly anything else the model might produce
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(date_str, dayfirst=True)
    except Exception:
        raise ValueError(f"Could not parse date: '{date_str}'")


def expand_bookings(data_list):
    """
    Takes the raw list of 'group' objects from the LLM and expands them into
    individual flat booking dicts.

    Each group uses ONE of two shapes:
      - "trips": explicit list of {date, pickup_time, pickup_point, drop_point}.
        Each trip becomes exactly one row — no multiplication. Used for
        non-recurring / different-per-date bookings (e.g. a round trip where
        outbound and return are on different, non-adjacent dates).
      - "date_ranges" + "legs": a cross-product — every leg is applied to every
        date in every range. Used only for genuine daily-recurring commutes.

    Backward compatible: if a group has none of the above (old flat format),
    it is passed through unchanged.
    """
    flat_bookings = []
    for group in data_list:
        trips = group.get("trips")
        date_ranges = group.get("date_ranges")
        legs = group.get("legs")

        common_fields = {k: v for k, v in group.items() if k not in ("date_ranges", "legs", "trips")}

        if trips:
            # SHAPE 2: explicit per-date trips, one row each, no cross-multiplication.
            for trip in trips:
                date_str = str(trip.get("date", "")).strip()
                try:
                    trip_date = _parse_date_flexible(date_str)
                except ValueError:
                    print(f"[-] Skipping malformed trip date: {trip}")
                    continue
                booking = dict(common_fields)
                booking["pickup_date"] = trip_date.strftime("%d-%b-%Y")
                booking["pickup_time"] = trip.get("pickup_time", "Not Given")
                booking["pickup_point"] = trip.get("pickup_point", "Not Given")
                booking["drop_point"] = trip.get("drop_point", "Not Given")
                flat_bookings.append(booking)
            continue

        if not date_ranges or not legs:
            # Old flat format fallback: pass the group through unchanged.
            flat_bookings.append(group)
            continue

        # SHAPE 1: recurring daily commute — cross-product of dates x legs.
        for date_range in date_ranges:
            start_str = str(date_range.get("start_date", "")).strip()
            end_str = str(date_range.get("end_date", "") or start_str).strip()
            try:
                start = _parse_date_flexible(start_str)
                end = _parse_date_flexible(end_str)
            except ValueError:
                print(f"[-] Skipping malformed date range: {date_range}")
                continue

            if end < start:
                start, end = end, start

            current = start
            while current <= end:
                for leg in legs:
                    booking = dict(common_fields)
                    booking["pickup_date"] = current.strftime("%d-%b-%Y")
                    booking["pickup_time"] = leg.get("pickup_time", "Not Given")
                    booking["pickup_point"] = leg.get("pickup_point", "Not Given")
                    booking["drop_point"] = leg.get("drop_point", "Not Given")
                    flat_bookings.append(booking)
                current += timedelta(days=1)

    return flat_bookings


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
                            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                            now_str = datetime.now(ist_tz).strftime("%A, %d-%b-%Y at %I:%M %p")
                            result_obj = cab_crew.kickoff(inputs={
                                "email_content": email_text,
                                "current_date": now_str
                            })
                            break  # Success! Break out of the fallback loop
                        except Exception as e:
                            last_exception = e
                            print(f"[-] Groq API Key {attempt + 1} failed! Trying backup key if available...")
                            time.sleep(2)  # Short pause before retry

                    if result_obj is None:
                        print(f"[-] ALL Groq API Keys failed! Skipping this email for now. Error: {last_exception}")
                        continue

                    result = str(result_obj)

                    try:
                        # Clean up potential markdown formatting like ```json ... ```
                        json_str = result.strip()

                        # extract_booking_task returns a raw JSON ARRAY of group objects.
                        if re.search(r'\[.*\]', json_str, re.DOTALL):
                            json_str = re.search(r'\[.*\]', json_str, re.DOTALL).group(0)
                        elif re.search(r'\{.*\}', json_str, re.DOTALL):
                            json_str = re.search(r'\{.*\}', json_str, re.DOTALL).group(0)

                        import json_repair
                        data_list = json_repair.loads(json_str)

                        # Fallback: if AI disobeyed and returned a single dict instead of a list, wrap it
                        if isinstance(data_list, dict):
                            data_list = [data_list]

                        if not data_list:
                            raise Exception("AI returned empty JSON array")

                        # Check the strict boolean spam filter on the first object
                        is_booking = data_list[0].get("is_cab_booking")

                        if is_booking is False or str(is_booking).lower() == "false":
                            print("\n[-] Irrelevant email skipped (AI marked it as spam/newsletter).")
                            set_last_uid(current_uid)  # Mark as successfully processed (spam)

                        else:
                            # Expand date_ranges + legs into individual flat bookings
                            # (one row per day per leg, morning leg before evening leg)
                            data_list = expand_bookings(data_list)

                            # SAFETY CHECK: if expansion produced nothing (e.g. every date
                            # range failed to parse), do NOT mark this email as successfully
                            # processed — raise so it's retried and logged as a real failure.
                            if not data_list:
                                raise Exception(
                                    "expand_bookings() produced 0 bookings — likely a date "
                                    "format the parser doesn't recognize. Check the "
                                    "'[-] Skipping malformed date range' lines above."
                                )

                            print(f"\n[+] Extraction successful! Found {len(data_list)} distinct booking(s). Pushing to Google Sheets...")
                            from mst_cabs.tools.google_sheets_tools import append_to_google_sheet

                            # Cloud servers usually run on UTC time. We MUST force it to Indian Standard Time (IST)
                            # so your client doesn't get confused by bookings showing up 5.5 hours in the past!
                            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                            booking_creation_time = datetime.now(ist_tz).strftime("%d-%m-%Y %I:%M %p")

                            def flatten_data(value):
                                if value is None or str(value).strip() == "":
                                    return "Not Given"
                                if isinstance(value, str):
                                    return value
                                if isinstance(value, list):
                                    if not value: return "Not Given"
                                    return " | ".join(flatten_data(v) for v in value)
                                if isinstance(value, dict):
                                    if not value: return "Not Given"
                                    return ", ".join(f"{flatten_data(v)}" for k, v in value.items())
                                return str(value)

                            for i, data in enumerate(data_list):
                                print(f"  -> Saving booking {i+1} of {len(data_list)}...")
                                save_status = append_to_google_sheet(
                                    guest_name=flatten_data(data.get('guest_name', '')),
                                    pickup_point=flatten_data(data.get('pickup_point', '')),
                                    drop_point=flatten_data(data.get('drop_point', '')),
                                    pickup_date=flatten_data(data.get('pickup_date', '')),
                                    booking_creation_time=booking_creation_time,
                                    mob_no=flatten_data(data.get('mob_no', '')),
                                    pickup_time=flatten_data(data.get('pickup_time', '')),
                                    duty_type=flatten_data(data.get('duty_type', '')),
                                    cab_type=flatten_data(data.get('cab_type', '')),
                                    booked_by=flatten_data(data.get('booked_by', '')),
                                    bill_to=flatten_data(data.get('bill_to', '')),
                                    state=flatten_data(data.get('state', '')),
                                    city=flatten_data(data.get('city', '')),
                                    special_details=flatten_data(data.get('special_details', '')),
                                    cab_number=flatten_data(data.get('cab_number', ''))
                                )

                                # SAFETY CHECK: If Google Sheets fails, we MUST crash this block!
                                if "CRITICAL ERROR" in save_status:
                                    raise Exception(f"Google Sheets API Error on Booking {i+1}: {save_status}")

                                print(f"    {save_status}")

                                # If there are more bookings in the array, wait 2 seconds to avoid API rate limits
                                if i < len(data_list) - 1:
                                    time.sleep(2)

                            # ONLY increase the count if ALL bookings successfully saved!
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