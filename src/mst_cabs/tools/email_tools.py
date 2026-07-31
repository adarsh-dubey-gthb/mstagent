import imaplib
import email
import os
from dotenv import load_dotenv

load_dotenv()

from mst_cabs.tools.google_sheets_tools import get_cloud_uid, set_cloud_uid

def get_last_uid() -> int:
    return get_cloud_uid()

def set_last_uid(uid: int):
    set_cloud_uid(uid)

def read_godaddy_emails(query: str = "ALL"):
    """
    Reads the next unprocessed email from the IMAP server based on UID tracking.
    Returns a tuple: (email_text, target_uid)
    """
    try:
        imap_host = os.getenv("IMAP_SERVER", "outlook.office365.com")
        imap_port = int(os.getenv("IMAP_PORT", 993))
        mail = imaplib.IMAP4_SSL(host=imap_host, port=imap_port)
        mail.login(os.getenv("EMAIL_ACCOUNT"), os.getenv("EMAIL_PASSWORD"))
        mail.select('inbox')
        
        last_uid = get_last_uid()
        
        if last_uid == 0:
            status, messages = mail.uid('SEARCH', None, 'ALL')
        else:
            # OPTIMIZATION: Only ask the server for UIDs greater than our last processed one!
            status, messages = mail.uid('SEARCH', None, f'UID {last_uid + 1}:*')
            
        if not messages[0]:
            return ("No new emails found.", 0)
            
        uids = [int(u) for u in messages[0].split()]
        
        new_uids = [u for u in uids if u > last_uid]
        
        if not new_uids:
            return ("No new emails found.", 0)
            
        # Process the oldest new email first (FIFO - First In, First Out)
        target_uid = new_uids[0]
            
        emails_content = []
        status, msg_data = mail.uid('FETCH', str(target_uid), '(RFC822)')
        
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                subject_raw = msg.get('Subject', '')
                try:
                    subject = email.header.decode_header(subject_raw)[0][0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(errors='ignore')
                except:
                    subject = str(subject_raw)
                
                sender = msg.get('From', '')
                body = ""
                
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors='ignore')
                        elif part.get_content_type() == "text/html" and not body:
                            body = part.get_payload(decode=True).decode(errors='ignore')
                else:
                    body = msg.get_payload(decode=True).decode(errors='ignore')
                
                import re
                # PRESERVE TABLE STRUCTURE FOR BATCH BOOKINGS
                body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
                body = re.sub(r'</tr>', '\n', body, flags=re.IGNORECASE)
                body = re.sub(r'</p>', '\n', body, flags=re.IGNORECASE)
                body = re.sub(r'</td>', ' | ', body, flags=re.IGNORECASE)
                
                # Strip out all remaining HTML tags
                body = re.sub(r'<[^>]+>', ' ', body)
                
                # Remove excessive blank spaces but preserve newlines
                body = re.sub(r'[ \t]+', ' ', body)
                body = re.sub(r'\n\s*\n', '\n', body).strip()
                
                # Reduce max characters to 2500 to prevent edge-case data loss
                if len(body) > 2500:
                    body = body[:2500] + "\n...[TRUNCATED]"
                
                emails_content.append(f"From: {sender}\nSubject: {subject}\nBody: {body}\n---")
        
        mail.logout()
        # Return the email text AND the UID, so main.py can save it later!
        return ("\n".join(emails_content), target_uid)
    except Exception as e:
        return (f"Error reading emails: {str(e)}", 0)

def check_new_emails(query: str = "ALL") -> bool:
    """
    Checks if there are any new emails with a UID greater than our last processed UID.
    """
    try:
        imap_host = os.getenv("IMAP_SERVER", "outlook.office365.com")
        imap_port = int(os.getenv("IMAP_PORT", 993))
        mail = imaplib.IMAP4_SSL(host=imap_host, port=imap_port)
        mail.login(os.getenv("EMAIL_ACCOUNT"), os.getenv("EMAIL_PASSWORD"))
        mail.select('inbox')
        
        last_uid = get_last_uid()
        
        if last_uid == 0:
            status, messages = mail.uid('SEARCH', None, 'ALL')
            if not messages[0]:
                return False
            uids = [int(u) for u in messages[0].split()]
            if len(uids) > 0:
                # First time ever running the bot! We don't want to process thousands of old emails.
                set_last_uid(uids[-1])
            mail.logout()
            return False
        else:
            status, messages = mail.uid('SEARCH', None, f'UID {last_uid + 1}:*')
            if not messages[0]:
                return False
            uids = [int(u) for u in messages[0].split()]
            
        new_uids = [u for u in uids if u > last_uid]
        
        mail.logout()
        return len(new_uids) > 0
    except Exception as e:
        print(f"Error checking for new emails: {str(e)}")
        return False
