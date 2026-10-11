# save as: get_token.py
import os
import argparse
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

def generate_token_for_client(client_id: str):
    os.makedirs("tokens", exist_ok=True)
    token_path = os.path.join("tokens", f"token_{client_id}.json")
    
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)
    
    with open(token_path, "w", encoding="utf-8") as token:
        token.write(creds.to_json())
    print(f"\n[SUCCESS] Token for '{client_id}' saved to: {token_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate OAuth Token for a Client Inbox")
    parser.add_argument("--client", required=True, help="Unique identifier for the client (e.g., client_02, apex_realty)")
    args = parser.parse_args()
    
    generate_token_for_client(args.client)