# onboard_server.py
"""
================================================================================
SELF-SERVE CLIENT ONBOARDING GATEWAY (OAUTH 2.0 WEB FLOW)
================================================================================
"""

import os
import re
import json
import logging
import datetime
import urllib.request
import urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from google_auth_oauthlib.flow import Flow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OnboardGateway")

# Essential Gmail scopes for triage, drafting, labeling, and replies
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKENS_DIR = "tokens"
CLIENTS_DIR = "clients"
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://127.0.0.1:8000/oauth2callback")

# Allows local testing over HTTP on 127.0.0.1
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# In-memory dictionary to hold the active flow and PKCE code_verifier per client session
ACTIVE_FLOWS = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler replacing deprecated on_event."""
    print("\n" + "=" * 76)
    print("🚀 CLIENTFLOW ONBOARDING GATEWAY ACTIVE")
    print("=" * 76)
    print("👉 Gateway is listening for trial form submissions on port 8000.")
    print("👉 Direct Manual Test Link:")
    print("   http://127.0.0.1:8000/onboard/connect?client_id=client_primary")
    print("=" * 76 + "\n")
    yield
    ACTIVE_FLOWS.clear()


app = FastAPI(title="Email Triage Onboarding Gateway", lifespan=lifespan)


@app.post("/api/register-trial")
async def register_trial(
    access_key: str = Form(...),
    client_name: str = Form(...),
    work_email: str = Form(...),
    brokerage_region: str = Form(...),
    calendly_link: str = Form(...),
    listings_and_hoa: str = Form(...),
    custom_guardrails: str = Form("")
):
    """
    Step 1: Client submits the form on index.html.
    - Sends an email alert to you via Web3Forms (with browser headers to prevent 403).
    - Creates clients/<slug>.json with their rules.
    - Redirects them directly to the Google OAuth consent screen.
    """
    # 1. Forward lead data cleanly to Web3Forms with valid browser headers
    try:
        web3_payload = json.dumps({
            "access_key": access_key,
            "subject": f"🚨 New 7-Day Pilot Request - {client_name}",
            "from_name": "ClientFlow AI Web Portal",
            "client_name": client_name,
            "work_email": work_email,
            "brokerage_region": brokerage_region,
            "calendly_link": calendly_link,
            "listings_and_hoa": listings_and_hoa,
            "custom_guardrails": custom_guardrails
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.web3forms.com/submit",
            data=web3_payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            logger.info("Web3Forms lead alert dispatched successfully.")
    except Exception as e:
        logger.warning(f"Web3Forms notification failed (non-blocking): {e}")

    # 2. Derive a clean client slug (e.g., 'David Sterling' -> 'david_sterling')
    clean_slug = re.sub(r"[^a-zA-Z0-9]+", "_", client_name.strip().lower()).strip("_")
    if not clean_slug:
        clean_slug = re.sub(r"[^a-zA-Z0-9]+", "_", work_email.split("@")[0].lower())

    # 3. Create the starter configuration file in clients/
    os.makedirs(CLIENTS_DIR, exist_ok=True)
    client_config_path = os.path.join(CLIENTS_DIR, f"{clean_slug}.json")

    config_data = {
        "client_id": clean_slug,
        "business_name": client_name,
        "admin_email": work_email,
        "brokerage_region": brokerage_region,
        "booking_link": calendly_link,
        "listings_and_hoa": listings_and_hoa,
        "custom_guardrails": custom_guardrails,
        "trial_start_date": datetime.date.today().isoformat(),
        "paid": False
    }

    with open(client_config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

    logger.info(f"Created configuration for '{clean_slug}' at {client_config_path}")

    # 4. Redirect into the Google Consent Flow using HTTP 303 (See Other)
    return RedirectResponse(url=f"/onboard/connect?client_id={clean_slug}", status_code=303)


@app.get("/onboard/connect")
def start_client_oauth(client_id: str):
    """
    Step 2: Builds the Google OAuth URL with prompt='consent' and forwards the browser.
    """
    if not client_id or not client_id.strip():
        raise HTTPException(status_code=400, detail="Missing required query parameter: 'client_id'")

    if not os.path.exists(CREDENTIALS_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"Server missing credentials file at '{CREDENTIALS_PATH}'."
        )

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_PATH,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=client_id.strip()
    )

    # Retain flow in memory to validate the PKCE verifier on callback
    ACTIVE_FLOWS[state] = flow

    logger.info(f"Redirecting '{client_id}' to Google consent screen...")
    return RedirectResponse(authorization_url)


@app.get("/oauth2callback")
async def oauth2_callback(request: Request):
    """
    Step 3: Google redirects here with authorization code and state.
    Exchanges code for permanent refresh token, saves tokens/token_<client_id>.json,
    and returns the confirmation page.
    """
    code = request.query_params.get("code")
    client_id = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        logger.warning(f"OAuth failed or was canceled by user: {error}")
        return HTMLResponse(
            content=f"<div style='font-family:sans-serif;padding:30px;color:#c0392b;'>"
                    f"<h2>Authorization Cancelled</h2><p>{error}</p></div>",
            status_code=400
        )

    if not code or not client_id:
        return HTMLResponse(
            content="<div style='font-family:sans-serif;padding:30px;color:#c0392b;'>"
                    "<h2>Missing Parameters</h2><p>Invalid authorization response received from Google.</p></div>",
            status_code=400
        )

    try:
        flow = ACTIVE_FLOWS.pop(client_id, None)

        if not flow:
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_PATH,
                scopes=SCOPES,
                redirect_uri=REDIRECT_URI
            )

        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save credentials to tokens/
        os.makedirs(TOKENS_DIR, exist_ok=True)
        token_filepath = os.path.join(TOKENS_DIR, f"token_{client_id}.json")

        with open(token_filepath, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        logger.info(f"[SUCCESS] OAuth token for '{client_id}' successfully saved to {token_filepath}")

        success_html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Inbox Connected</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: #f8fafc;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }
                .card {{
                    background: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 12px;
                    padding: 40px;
                    max-width: 420px;
                    text-align: center;
                    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.05);
                }}
                .badge {{
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    width: 56px;
                    height: 56px;
                    border-radius: 50%;
                    background: #dcfce7;
                    color: #16a34a;
                    font-size: 28px;
                    margin-bottom: 20px;
                }}
                h2 {{
                    margin: 0 0 10px 0;
                    color: #0f172a;
                    font-size: 22px;
                }}
                p {{
                    color: #64748b;
                    font-size: 15px;
                    line-height: 1.5;
                    margin: 0 0 24px 0;
                }}
                .footer-text {{
                    font-size: 13px;
                    color: #94a3b8;
                    margin: 0;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="badge">✓</div>
                <h2>Inbox Connected!</h2>
                <p>Your Google Workspace account has been safely linked to your 24/7 lead concierge. Your 7-day Draft-Only trial is now active.</p>
                <p class="footer-text">You may safely close this tab.</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=success_html)

    except Exception as exc:
        logger.exception(f"Error handling OAuth callback for client {client_id}: {exc}")
        return HTMLResponse(
            content=f"<div style='font-family:sans-serif;padding:30px;color:#c0392b;'>"
                    f"<h2>Authentication Failed</h2><p>{str(exc)}</p></div>",
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("onboard_server:app", host="127.0.0.1", port=8000, reload=True)