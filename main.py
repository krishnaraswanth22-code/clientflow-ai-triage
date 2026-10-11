"""
================================================================================
AI-POWERED REAL ESTATE EMAIL TRIAGE ENGINE (MULTI-TENANT DYNAMIC RUNNER)
================================================================================
"""

import os
import sys
import glob
import json
import time
import logging
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv

import database
from gmail_client import GmailService
import pipeline

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("TriageEngine")

CLIENTS_DIR = "clients"
TOKENS_DIR = "tokens"


def get_client_status(config: dict) -> tuple[str, int]:
    """
    Evaluates client subscription status based on config dates.
    Returns: (STATUS, days_elapsed)
    STATUS values: 'ACTIVE_PAID', 'ACTIVE_TRIAL', 'TRIAL_EXPIRED'
    """
    if config.get("paid", False):
        return "ACTIVE_PAID", 0

    trial_start_str = config.get("trial_start_date")
    if not trial_start_str:
        return "TRIAL_EXPIRED", 0

    start_date = datetime.strptime(trial_start_str, "%Y-%m-%d")
    days_elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - start_date).days

    if days_elapsed <= 7:
        return "ACTIVE_TRIAL", days_elapsed
    return "TRIAL_EXPIRED", days_elapsed


def process_single_client(config_file: str):
    """Processes unread emails for a single client with safe batching."""
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as err:
        logger.error(f"Failed to read config file {config_file}: {err}")
        return

    client_id = config.get("client_id", os.path.splitext(os.path.basename(config_file))[0])
    token_path = os.path.join(TOKENS_DIR, f"token_{client_id}.json")
    creds_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")

    # Gatekeeper: Check Trial / Paid Status
    status, days_elapsed = get_client_status(config)

    if status == "TRIAL_EXPIRED":
        logger.info(f"[{client_id}] 7-Day trial expired ({days_elapsed} days elapsed). Skipping inbox entirely.")
        return

    if status == "ACTIVE_TRIAL":
        logger.info(f"[{client_id}] Trial Day {days_elapsed}/7 active. Operating strictly in DRAFT-ONLY mode.")
        config["draft_mode"] = True
    else:
        logger.info(f"[{client_id}] Paid subscription active. Autonomous auto-send enabled.")
        config["draft_mode"] = False

    # Authentication
    if not os.path.exists(token_path):
        # Fallback to root token.json if running single-client mode
        if os.path.exists("token.json"):
            token_path = "token.json"
        else:
            logger.warning(f"[{client_id}] Missing OAuth token at {token_path}. Skipping client.")
            return

    try:
        gmail = GmailService(credentials_path=creds_path, token_path=token_path)
    except Exception as err:
        logger.error(f"[{client_id}] Authentication failed: {err}")
        return

    # Inbox Triage (10-Message Safety Throttle)
    messages = gmail.fetch_unread_messages()
    if not messages:
        logger.info(f"[{client_id}] Inbox clean: No unread messages.")
        return

    logger.info(f"[{client_id}] Discovered {len(messages)} unread email(s). Processing batch of up to 10...")
    for msg in messages[:10]:
        try:
            pipeline.process_email_message(msg["id"], gmail, config)
            time.sleep(0.5)
        except Exception as err:
            logger.exception(f"[{client_id}] Error handling message {msg['id']}: {err}")
            try:
                gmail.apply_labels_and_mark_read(msg["id"], add_labels=["Needs_Human_Review"], archive=False)
            except Exception:
                pass


def send_monthly_reports():
    """Generates monthly reports for all ACTIVE_PAID clients with strict client_id scoping."""
    config_files = glob.glob(os.path.join(CLIENTS_DIR, "*.json"))
    
    # Fallback to root config.json if clients/ directory has not been populated yet
    if not config_files and os.path.exists("config.json"):
        config_files = ["config.json"]

    for config_file in config_files:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            continue

        status, _ = get_client_status(config)
        if status != "ACTIVE_PAID":
            continue

        client_id = config.get("client_id", os.path.splitext(os.path.basename(config_file))[0])
        admin_email = config.get("admin_email") or os.getenv("ADMIN_NOTIFICATION_EMAIL")
        if not admin_email:
            continue

        try:
            token_path = os.path.join(TOKENS_DIR, f"token_{client_id}.json")
            if not os.path.exists(token_path) and os.path.exists("token.json"):
                token_path = "token.json"

            gmail = GmailService(
                credentials_path=os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json"),
                token_path=token_path
            )

            now = datetime.now(timezone.utc)
            target_year = now.year if now.month > 1 else now.year - 1
            target_month = now.month - 1 if now.month > 1 else 12

            # Scoped strictly by client_id to prevent multi-tenant data leaks
            stats = database.get_monthly_report_data(
                client_id=client_id,
                year=target_year,
                month=target_month
            )
            business_name = config.get("business_name", client_id)

            html = f"""
            <html>
              <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <h2 style="color: #2c3e50;">Monthly Inbound Triage Report: {target_year}-{target_month:02d}</h2>
                <p>Operational metrics for <strong>{business_name}</strong>:</p>
                <table style="border-collapse: collapse; width: 100%; max-width: 500px; margin-top: 15px;">
                  <tr style="background:#f8f9fa;">
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Metric</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: right;">Total</th>
                  </tr>
                  <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Total Inbound Leads</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right;"><strong>{stats['total_leads']}</strong></td>
                  </tr>
                  <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Auto-Replies Sent</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: green;">{stats['auto_sent_count']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">Flagged for Review / Drafted</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #d9534f;">{stats['human_flagged_count']}</td>
                  </tr>
                  <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;">After-Hours Leads Captured</td>
                    <td style="padding: 10px; border: 1px solid #ddd; text-align: right; color: #0275d8;">{stats['after_hours_count']}</td>
                  </tr>
                </table>
                <p style="font-size: 12px; color: #777; margin-top: 20px;">
                  Generated automatically by Apex AI Operations Hub.
                </p>
              </body>
            </html>
            """
            gmail.send_notification(admin_email, f"Monthly Triage Summary: {target_year}-{target_month:02d}", html)
            logger.info(f"[{client_id}] Monthly report dispatched to {admin_email}")
        except Exception as e:
            logger.error(f"[{client_id}] Failed to send monthly report: {e}")


def run_all_clients():
    """Scans the directory for all clients and processes them."""
    os.makedirs(CLIENTS_DIR, exist_ok=True)
    os.makedirs(TOKENS_DIR, exist_ok=True)

    config_files = glob.glob(os.path.join(CLIENTS_DIR, "*.json"))
    
    # Fallback to root config.json if no multi-tenant files exist
    if not config_files and os.path.exists("config.json"):
        process_single_client("config.json")
        return

    if not config_files:
        logger.info(f"No client configurations found in {CLIENTS_DIR}/ directory.")
        return

    for config_file in config_files:
        process_single_client(config_file)


def main():
    parser = argparse.ArgumentParser(description="Multi-Tenant Email Triage Engine")
    parser.add_argument("--once", action="store_true", help="Run a single pass across all clients and exit.")
    parser.add_argument("--monthly-report", action="store_true", help="Send monthly summary report to paid clients.")
    parser.add_argument("--interval", type=int, default=120, help="Polling interval in seconds.")
    args = parser.parse_args()

    database.init_db()

    if args.monthly_report:
        send_monthly_reports()
        return

    if args.once:
        run_all_clients()
        return

    logger.info(f"Worker running every {args.interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            run_all_clients()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Worker stopped gracefully.")


if __name__ == "__main__":
    main()