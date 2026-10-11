import os
import base64
import logging
from email.message import EmailMessage
from typing import List, Dict, Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

class GmailService:
    def __init__(self, credentials_path: str, token_path: str = "token.json"):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()
        self.label_cache = {}

    def _authenticate(self):
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None
            if not creds:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w") as token:
                token.write(creds.to_json())

        return build("gmail", "v1", credentials=creds)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def get_or_create_label(self, label_name: str) -> str:
        if label_name in self.label_cache:
            return self.label_cache[label_name]

        results = self.service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        for lbl in labels:
            if lbl["name"].lower() == label_name.lower():
                self.label_cache[label_name] = lbl["id"]
                return lbl["id"]

        # Keep internal noise and duplicate suppression out of the user's primary sidebar
        is_hidden = any(k in label_name.lower() for k in ["noise", "vendor", "duplicate", "suppressed"])
        body = {
            "name": label_name,
            "labelListVisibility": "labelHide" if is_hidden else "labelShow",
            "messageListVisibility": "show"
        }
        created = self.service.users().labels().create(userId="me", body=body).execute()
        self.label_cache[label_name] = created["id"]
        return created["id"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def fetch_unread_messages(self) -> List[Dict[str, Any]]:
        results = self.service.users().messages().list(userId="me", q="is:unread").execute()
        return results.get("messages", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def get_message_detail(self, msg_id: str) -> Optional[Dict[str, Any]]:
        return self.service.users().messages().get(userId="me", id=msg_id, format="full").execute()

    def extract_email_data(self, message: Dict[str, Any]) -> Dict[str, str]:
        headers = message.get("payload", {}).get("headers", [])
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
        subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "")
        thread_id = message.get("threadId", "")

        body = ""
        payload = message.get("payload", {})

        def parse_parts(parts):
            nonlocal body
            for part in parts:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    try:
                        body += base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                elif "parts" in part:
                    parse_parts(part["parts"])

        if "parts" in payload:
            parse_parts(payload["parts"])
        elif "data" in payload.get("body", {}):
            try:
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            except Exception:
                body = ""

        clean_sender = sender
        if "<" in sender and ">" in sender:
            clean_sender = sender.split("<")[1].split(">")[0]

        return {
            "raw_sender": sender,
            "sender_email": clean_sender.strip().lower(),
            "subject": subject,
            "thread_id": thread_id,
            "body": body
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def send_reply(self, thread_id: str, to_email: str, subject: str, reply_text: str) -> Dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg.set_content(reply_text)
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return self.service.users().messages().send(userId="me", body={"raw": encoded, "threadId": thread_id}).execute()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def create_draft(self, thread_id: str, to_email: str, subject: str, draft_text: str) -> Dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg.set_content(draft_text)
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        draft_body = {"message": {"raw": encoded, "threadId": thread_id}}
        return self.service.users().drafts().create(userId="me", body=draft_body).execute()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def send_notification(self, to_email: str, subject: str, html_content: str) -> Dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content("HTML content required.")
        msg.add_alternative(html_content, subtype="html")
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return self.service.users().messages().send(userId="me", body={"raw": encoded}).execute()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(HttpError))
    def apply_labels_and_mark_read(self, msg_id: str, add_labels: List[str], archive: bool = False):
        add_ids = [self.get_or_create_label(lbl) for lbl in add_labels]
        remove_ids = ["UNREAD"]
        if archive:
            remove_ids.append("INBOX")
        body = {"ids": [msg_id], "addLabelIds": add_ids, "removeLabelIds": remove_ids}
        self.service.users().messages().batchModify(userId="me", body=body).execute()