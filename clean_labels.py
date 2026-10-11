import os
import sys
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

# Labels to target and remove
TARGET_OLD_LABELS = [
    "AI_Handled",
    "Pending_Approval",
    "Needs_Human_Review",
    "Needs_Human_Review — Sensitive",
    "AI_Drafted"
]

def get_service(credentials_path="credentials.json", token_path="token.json"):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def clean_old_labels():
    service = get_service()
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])
    
    deleted_count = 0
    print("\nScanning Gmail for legacy developer labels...")

    for label in labels:
        name = label["name"]
        # Match case-insensitively against the target list
        if any(name.strip().lower() == old.strip().lower() for old in TARGET_OLD_LABELS):
            label_id = label["id"]
            try:
                service.users().labels().delete(userId="me", id=label_id).execute()
                print(f" -> Successfully deleted legacy label: '{name}' (ID: {label_id})")
                deleted_count += 1
            except Exception as e:
                print(f" -> Failed to delete '{name}': {e}")

    if deleted_count == 0:
        print("No legacy developer labels found in Gmail.")
    else:
        print(f"\nCleanup complete. Successfully removed {deleted_count} legacy label(s).")
    
    print("\nActive client-facing folders preserved:")
    print(" - 🔴 Action Needed")
    print(" - 🟡 Review Drafts")
    print(" - 🟢 AI Handled")

if __name__ == "__main__":
    clean_old_labels()