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

# Fields that actually matter for what gets written to Google Sheets.
# Two agents' outputs are compared ONLY on these fields — differences in ordering,
# whitespace, or wording of unrelated internal keys don't count as a mismatch.
COMPARE_FIELDS = [
    'guest_name', 'pickup_point', 'drop_point', 'pickup_date', 'pickup_time',
    'duty_type', 'cab_type', 'booked_by', 'bill_to', 'state', 'city',
    'cab_number', 'mob_no'
]

MAX_MISMATCH_RETRIES = 3


def clean_email_content(raw_text):
    """
    Strips boilerplate that adds token bloat but zero booking information:
    - Embedded image placeholders like [cid:image001.png@...]
    - Legal/confidentiality disclaimer paragraphs
    - Repeated corporate signature blocks (name, title, company, address, phone,
      email, website, fax) that often appear 2-3x in a forwarded email thread

    This runs on the SAME email content sent to BOTH agents, so it doesn't bias
    one agent's result vs the other — it just reduces the total token count on
    every call, which directly reduces rate-limit pressure on long/forwarded emails.

    Deliberately conservative: only strips well-known boilerplate patterns, never
    touches anything that looks like it could be booking-relevant (dates, times,
    addresses, names in a "Guest Name -" style line, table rows, etc.).
    """
    text = raw_text

    # Remove embedded image content-id placeholders — pure noise, no information.
    text = re.sub(r'\[cid:[^\]]+\]', '', text)

    # Remove disclaimer paragraphs (common corporate boilerplate about
    # confidentiality). Stops at the next blank line or common thread markers.
    text = re.sub(
        r'Disclaimer:.*?(?=\n\s*\n|\nFrom:|\nSent:|\Z)',
        '',
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Collapse a repeated "Thanks & Regards," signature block: name, title,
    # company, address, phone/email/website/fax lines that follow it. Keeps only
    # the FIRST occurrence (in case the sender's identity is useful context once)
    # and strips subsequent repeats from earlier messages in the same thread.
    signature_pattern = re.compile(
        r'Thanks\s*&\s*Regards,.*?(?=\n\s*\n|\nFrom:|\nSent:|\Z)',
        flags=re.DOTALL | re.IGNORECASE
    )
    matches = list(signature_pattern.finditer(text))
    if len(matches) > 1:
        # Remove all but the first match, working backwards so earlier match
        # positions in `text` don't shift.
        for m in reversed(matches[1:]):
            text = text[:m.start()] + text[m.end():]

    # Collapse 3+ consecutive blank lines down to a single blank line.
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

    return text.strip()


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


def filter_past_bookings(flat_bookings, cutoff_date):
    """
    Drops any booking whose pickup_date is before cutoff_date. Per business rule:
    keeps yesterday, today, and future dates — only drops anything OLDER than
    yesterday (e.g. a historical booking mentioned in an earlier message of a
    forwarded email thread).

    Deliberately done here in code, not left to the LLM: date-vs-cutoff
    comparison is exactly the kind of date-math task LLMs get wrong in subtle,
    inconsistent ways (see the various date-format bugs fixed earlier in this
    file) — and applying it uniformly in code also means both agents get
    filtered identically, so this can't become a new source of disagreement
    between them.
    """
    kept = []
    for booking in flat_bookings:
        date_str = str(booking.get("pickup_date", "")).strip()
        try:
            booking_date = _parse_date_flexible(date_str)
        except ValueError:
            # Can't tell the date — keep it rather than silently dropping a
            # possibly-real booking due to a parse issue. Any genuine parsing
            # problem is already caught earlier by the 0-bookings safety check.
            kept.append(booking)
            continue

        if booking_date.date() >= cutoff_date.date():
            kept.append(booking)
        else:
            print(f"[-] Dropping historical booking (before cutoff {cutoff_date.date()}): "
                  f"{booking.get('guest_name', '?')} on {date_str}")

    return kept


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


def parse_crew_json(raw_result):
    """
    Parses a single agent's raw text output into a Python list of group dicts.
    Shared by both agent outputs so the parsing logic only lives in one place.
    Raises Exception if nothing usable could be parsed.
    """
    json_str = raw_result.strip()

    if re.search(r'\[.*\]', json_str, re.DOTALL):
        json_str = re.search(r'\[.*\]', json_str, re.DOTALL).group(0)
    elif re.search(r'\{.*\}', json_str, re.DOTALL):
        json_str = re.search(r'\{.*\}', json_str, re.DOTALL).group(0)

    import json_repair
    data_list = json_repair.loads(json_str)

    if isinstance(data_list, dict):
        data_list = [data_list]

    if not data_list:
        raise Exception("AI returned empty JSON array")

    return data_list


def normalize_booking(booking):
    """
    Turns one flat booking dict into a normalized, hashable tuple for comparison —
    trims whitespace and lowercases so trivial formatting differences between the
    two agents (extra spaces, capitalization) don't count as a real mismatch.
    """
    return tuple(
        flatten_data(booking.get(field, '')).strip().lower()
        for field in COMPARE_FIELDS
    )


def bookings_match(list_a, list_b):
    """
    Compares two expanded booking lists for agreement, ignoring row order.
    Returns (True, None) if they match, or (False, reason) if not.
    """
    if len(list_a) != len(list_b):
        return False, f"Agent A produced {len(list_a)} booking(s), Agent B produced {len(list_b)} booking(s)."

    norm_a = sorted(normalize_booking(b) for b in list_a)
    norm_b = sorted(normalize_booking(b) for b in list_b)

    if norm_a != norm_b:
        return False, "Booking details differ between the two agents (see raw outputs above)."

    return True, None


def run_bot():
    """
    Run the background loop that fetches emails and processes them using the crew.
    """
    load_dotenv()

    print("Starting background cab booking automation...")
    print("Listening for new emails...")

    cab_crew = MstCabsCrew().crew()

    # Tracks how many times each UID has failed due to agent mismatch, so we retry
    # a bounded number of times instead of either (a) giving up after one try, or
    # (b) retrying a genuinely ambiguous email forever and burning API calls.
    mismatch_retry_counts = {}

    while True:
        try:
            if check_new_emails():
                print("\n[+] New email detected! Fetching content...")

                email_text, current_uid = read_godaddy_emails("ALL")

                if "No new emails found" not in email_text and current_uid != 0:
                    original_length = len(email_text)
                    email_text = clean_email_content(email_text)
                    print(f"\n[+] Cleaned email content: {original_length} -> {len(email_text)} characters "
                          f"({original_length - len(email_text)} chars of boilerplate removed)")
                    print("\n[+] Waking up BOTH agents to independently parse the email...")

                    key_a = os.getenv("GROQ_API_KEY")
                    key_b = os.getenv("GROQ_API_KEY_2")
                    key_backup = os.getenv("GROQ_API_KEY_3")

                    result_obj = None
                    last_exception = None

                    if not key_a or not key_b:
                        print("[-] ERROR: GROQ_API_KEY and/or GROQ_API_KEY_2 not found in .env file! "
                              "Agent A needs GROQ_API_KEY, Agent B needs GROQ_API_KEY_2.")
                        last_exception = "Missing GROQ_API_KEY or GROQ_API_KEY_2"
                    else:
                        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                        now_str = datetime.now(ist_tz).strftime("%A, %d-%b-%Y at %I:%M %p")
                        kickoff_inputs = {"email_content": email_text, "current_date": now_str}

                        # Primary attempt: Agent A on its own key, Agent B on its own key.
                        try:
                            result_obj = cab_crew.kickoff(inputs=kickoff_inputs)
                        except Exception as e:
                            last_exception = e
                            error_str = str(e).lower()
                            is_rate_limit = "rate_limit" in error_str or "429" in error_str or "rate limit" in error_str
                            if is_rate_limit:
                                print(f"[-] Primary keys hit a RATE LIMIT: {e}")
                            else:
                                print(f"[-] Primary keys failed: {e}")

                            # Backup attempt: if we have a spare 3rd key, retry ONCE using it
                            # for BOTH agents (temporary — not their normal fixed assignment).
                            # We swap the env vars that crew.py's @agent methods read from,
                            # construct a FRESH MstCabsCrew() so the agents pick up the swapped
                            # keys, then restore the originals immediately after — success or fail.
                            if key_backup:
                                print("[-] Retrying once with backup key (GROQ_API_KEY_3) for both agents...")
                                time.sleep(20 if is_rate_limit else 2)
                                os.environ["GROQ_API_KEY"] = key_backup
                                os.environ["GROQ_API_KEY_2"] = key_backup
                                try:
                                    backup_crew = MstCabsCrew().crew()
                                    result_obj = backup_crew.kickoff(inputs=kickoff_inputs)
                                except Exception as e2:
                                    last_exception = e2
                                    print(f"[-] Backup key also failed: {e2}")
                                finally:
                                    # Always restore the real keys, even if the backup attempt
                                    # raised — otherwise every future email would silently keep
                                    # using the backup key instead of the intended primary ones.
                                    os.environ["GROQ_API_KEY"] = key_a
                                    os.environ["GROQ_API_KEY_2"] = key_b
                            else:
                                print("[-] No GROQ_API_KEY_3 configured — no backup available for this attempt.")

                    if result_obj is None:
                        # Deliberately NOT using `continue` here — that would skip past the
                        # time.sleep(60) at the very end of the outer while loop below, causing
                        # the bot to hammer the Groq/IMAP APIs with zero delay on repeated
                        # failures. Falling through naturally preserves the pause.
                        if key_a and key_b:
                            print(f"[-] ALL Groq attempts failed (primary + backup)! Skipping this email for now. Error: {last_exception}")
                    else:
                        try:
                            # The crew ran TWO tasks (extract_booking_task_a and _b), each on a
                            # different independent agent. Pull both raw outputs.
                            outputs = result_obj.tasks_output
                            if len(outputs) < 2:
                                raise Exception(
                                    f"Expected 2 task outputs (two extraction agents), got {len(outputs)}. "
                                    "Check that crew.py defines both extract_booking_task_a and _b."
                                )

                            raw_a = getattr(outputs[0], 'raw', str(outputs[0]))
                            raw_b = getattr(outputs[1], 'raw', str(outputs[1]))

                            print("\n[Agent A raw output]\n" + raw_a)
                            print("\n[Agent B raw output]\n" + raw_b)

                            data_list_a = parse_crew_json(raw_a)
                            data_list_b = parse_crew_json(raw_b)

                            is_booking_a = data_list_a[0].get("is_cab_booking")
                            is_booking_b = data_list_b[0].get("is_cab_booking")
                            a_is_spam = is_booking_a is False or str(is_booking_a).lower() == "false"
                            b_is_spam = is_booking_b is False or str(is_booking_b).lower() == "false"

                            if a_is_spam and b_is_spam:
                                print("\n[-] Both agents agree: irrelevant email (spam/newsletter). Skipping.")
                                set_last_uid(current_uid)
                                mismatch_retry_counts.pop(current_uid, None)

                            elif a_is_spam != b_is_spam:
                                # The two agents disagree on whether this is even a booking at
                                # all — definitely not safe to auto-push. Retry a bounded number
                                # of times before giving up (in case it's just LLM run-to-run
                                # variance rather than a genuinely ambiguous email).
                                print("\n[-] MISMATCH: agents disagree on whether this is a real booking "
                                      f"(Agent A: {is_booking_a}, Agent B: {is_booking_b}).")
                                mismatch_retry_counts[current_uid] = mismatch_retry_counts.get(current_uid, 0) + 1
                                attempts = mismatch_retry_counts[current_uid]
                                if attempts < MAX_MISMATCH_RETRIES:
                                    print(f"[-] Will retry this email next loop (attempt {attempts}/{MAX_MISMATCH_RETRIES}). "
                                          "UID NOT advanced — nothing pushed yet.")
                                    # Deliberately NOT calling set_last_uid() here. Falls through
                                    # normally (no `continue`) so time.sleep(60) still runs.
                                else:
                                    print(f"[-] Gave up after {MAX_MISMATCH_RETRIES} attempts — this email appears "
                                          "genuinely ambiguous. Marking as processed for MANUAL REVIEW. "
                                          "Nothing was pushed to Google Sheets.")
                                    mismatch_retry_counts.pop(current_uid, None)
                                    set_last_uid(current_uid)

                            else:
                                # Both agents agree it's a real booking — expand and compare details.
                                expanded_a = expand_bookings(data_list_a)
                                expanded_b = expand_bookings(data_list_b)

                                if not expanded_a or not expanded_b:
                                    raise Exception(
                                        "One or both agents produced 0 bookings after expansion — "
                                        "likely a date format issue. Check the "
                                        "'[-] Skipping malformed date range/trip date' lines above."
                                    )

                                # Filter out anything older than yesterday (per business rule),
                                # applied identically to both agents' output so this can't become
                                # a new source of disagreement between them.
                                ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                                cutoff_date = datetime.now(ist_tz).replace(tzinfo=None) - timedelta(days=1)

                                pre_filter_count_a = len(expanded_a)
                                expanded_a = filter_past_bookings(expanded_a, cutoff_date)
                                expanded_b = filter_past_bookings(expanded_b, cutoff_date)

                                if not expanded_a and not expanded_b:
                                    # Legitimate outcome, NOT an error: every booking both agents
                                    # extracted was historical (e.g. an old thread with no
                                    # upcoming trips). Nothing to push — mark processed and move on.
                                    print(f"\n[-] All {pre_filter_count_a} booking(s) were historical "
                                          f"(older than {cutoff_date.date()}). Nothing to push. Skipping email.")
                                    set_last_uid(current_uid)
                                    mismatch_retry_counts.pop(current_uid, None)

                                elif not expanded_a or not expanded_b:
                                    # One agent found upcoming bookings, the other found NONE
                                    # after filtering — that's a real mismatch, not agreement.
                                    print(f"\n[-] MISMATCH after filtering past bookings: Agent A has "
                                          f"{len(expanded_a)} upcoming booking(s), Agent B has {len(expanded_b)}.")
                                    mismatch_retry_counts[current_uid] = mismatch_retry_counts.get(current_uid, 0) + 1
                                    attempts = mismatch_retry_counts[current_uid]
                                    if attempts < MAX_MISMATCH_RETRIES:
                                        print(f"[-] Will retry this email next loop (attempt {attempts}/{MAX_MISMATCH_RETRIES}). "
                                              "UID NOT advanced — nothing pushed yet.")
                                    else:
                                        print(f"[-] Gave up after {MAX_MISMATCH_RETRIES} attempts. Marking as "
                                              "processed for MANUAL REVIEW. Nothing was pushed.")
                                        mismatch_retry_counts.pop(current_uid, None)
                                        set_last_uid(current_uid)

                                else:
                                    match, reason = bookings_match(expanded_a, expanded_b)

                                    if not match:
                                        print(f"\n[-] MISMATCH between the two agents: {reason}")
                                        mismatch_retry_counts[current_uid] = mismatch_retry_counts.get(current_uid, 0) + 1
                                        attempts = mismatch_retry_counts[current_uid]
                                        if attempts < MAX_MISMATCH_RETRIES:
                                            print(f"[-] Will retry this email next loop (attempt {attempts}/{MAX_MISMATCH_RETRIES}). "
                                                  "UID NOT advanced — nothing pushed yet.")
                                            # No set_last_uid(), no `continue` — falls through
                                            # normally so time.sleep(60) still runs below.
                                        else:
                                            print(f"[-] Gave up after {MAX_MISMATCH_RETRIES} attempts — this email "
                                                  "appears genuinely ambiguous. Marking as processed for MANUAL "
                                                  "REVIEW. Please check the raw outputs above manually. "
                                                  "Nothing was pushed.")
                                            mismatch_retry_counts.pop(current_uid, None)
                                            set_last_uid(current_uid)

                                    else:
                                        data_list = expanded_a  # both agree, either one is fine to use

                                        print(f"\n[+] Both agents AGREE. Found {len(data_list)} distinct upcoming "
                                              f"booking(s). Pushing to Google Sheets...")
                                        from mst_cabs.tools.google_sheets_tools import append_to_google_sheet

                                        booking_creation_time = datetime.now(ist_tz).strftime("%d-%m-%Y %I:%M %p")

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

                                            if i < len(data_list) - 1:
                                                time.sleep(2)

                                        # ONLY increase the count if ALL bookings successfully saved!
                                        set_last_uid(current_uid)
                                        mismatch_retry_counts.pop(current_uid, None)
                                        print(f"[SUCCESS] Email UID {current_uid} fully processed and marked as complete.")

                        except Exception as je:
                            print(f"[-] Failed to parse/compare/push: {je}")
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