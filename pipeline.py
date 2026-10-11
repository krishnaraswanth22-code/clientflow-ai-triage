# pipeline.py
"""
================================================================================
AI-POWERED REAL ESTATE EMAIL TRIAGE ENGINE (PIPELINE PROCESSOR)
================================================================================
"""

import os
import re
import difflib
import logging
from datetime import datetime
import pytz

import database
from gmail_client import GmailService
from classifier import classify_email

logger = logging.getLogger(__name__)

# Polished, client-facing operational labels
LABEL_AI_HANDLED = "🟢 AI Handled"
LABEL_REVIEW_DRAFTS = "🟡 Review Drafts"
LABEL_ACTION_NEEDED = "🔴 Action Needed"

# Compiled regex patterns for Stage 2 marketing, newsletter, and automated system noise
PROMOTIONAL_REGEX_PATTERNS = [
    r"no-?reply@",
    r"donotreply@",
    r"info(rmational)?@",
    r"updates?@",
    r"advertising@",
    r"marketing@",
    r"promotions?@",
    r"newsletter@",
    r"connect@",
    r"notifications?@",
    r"@hello\.",
    r"@.*mail\.grammarly\.com",
    r"@.*email\.openai\.com",
    r"@.*discover\.pinterest\.com",
    r"@.*explore\.pinterest\.com",
    r"@.*email\.snapchat\.com",
    r"@.*imdb\.com",
    r"@.*spotify\.com",
    r"@.*creators\.suno\.com",
    r"@.*thesouledstore\.com",
    r"@.*link\.com",
    r"@.*stripe\.com",
    r"@.*bandcamp\.com",
    r"@.*myntra\.com",
    r"@.*yousician\.com"
]

# Text tokens commonly found in commercial bulk footers
COMMERCIAL_FOOTER_TOKENS = [
    "unsubscribe",
    "opt out",
    "opt-out",
    "manage preferences",
    "privacy policy",
    "terms of service",
    "view in browser",
    "mailing list"
]

def is_after_hours(config: dict) -> bool:
    """Evaluates whether current execution timestamp is outside configured business hours."""
    tz_name = config.get("timezone", "UTC")
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC

    now = datetime.now(tz)
    bh = config.get("business_hours", {})
    work_days = bh.get("work_days", [0, 1, 2, 3, 4])

    if now.weekday() not in work_days:
        return True

    start_h, start_m = map(int, bh.get("start", "09:00").split(":"))
    end_h, end_m = map(int, bh.get("end", "18:00").split(":"))

    start_time = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return not (start_time <= now <= end_time)

def is_promotional_or_vendor_noise(sender: str, raw_sender: str, subject: str, body: str, config: dict, headers: dict = None) -> bool:
    """Deterministic Stage 2 heuristic to catch automated vendor emails and retail blasts."""
    lower_sender = raw_sender.lower()
    lower_subject = subject.lower()
    lower_body = body.lower()

    if headers:
        if "list-unsubscribe" in headers or headers.get("precedence") in ["bulk", "list", "junk"]:
            return True
        if headers.get("auto-submitted") and headers.get("auto-submitted") != "no":
            return True

    for domain in config.get("vendor_domains", []):
        if domain.lower() in lower_sender:
            return True

    for pattern in config.get("noise_patterns", []):
        if pattern.lower() in lower_sender or pattern.lower() in lower_body:
            return True

    for regex in PROMOTIONAL_REGEX_PATTERNS:
        if re.search(regex, lower_sender):
            return True

    has_footer_token = any(token in lower_body for token in COMMERCIAL_FOOTER_TOKENS)
    if has_footer_token:
        real_estate_signals = ["listing", "property", "showing", "tour", "offer", "bedroom", "sqft", "hoa", "closing"]
        if not any(signal in lower_subject or signal in lower_body for signal in real_estate_signals):
            return True

    return False

def process_email_message(msg_id: str, gmail: GmailService, config: dict):
    """Orchestrates Stages 1 through 6 for a single incoming email."""
    msg_raw = gmail.get_message_detail(msg_id)
    if not msg_raw:
        logger.warning(f"Could not retrieve payload for message {msg_id}. Skipping.")
        return

    data = gmail.extract_email_data(msg_raw)
    sender, raw_sender = data["sender_email"], data["raw_sender"]
    subject, body, thread_id = data["subject"], data["body"], data["thread_id"]
    headers = data.get("headers", {})
    client_id = config.get("client_id", "Unknown Client")
    admin_email = config.get("admin_email") or os.getenv("ADMIN_NOTIFICATION_EMAIL")

    logger.info(f"[{client_id}] --- Triaging: '{subject}' from {sender} ---")

    # ==========================================
    # STAGE 1 — Known Client Check
    # ==========================================
    known = database.get_known_client(client_id, sender)
    if known and known[0] == "active_deal":
        logger.info(f"[{client_id}] [STAGE 1] Active deal client detected: {sender}")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=[LABEL_ACTION_NEEDED, "Existing Client — Active Deal"], archive=False)
        return

    # ==========================================
    # STAGE 2 — Noise / Vendor Filter
    # ==========================================
    if is_promotional_or_vendor_noise(sender, raw_sender, subject, body, config, headers):
        logger.info(f"[{client_id}] [STAGE 2] Noise filtered")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=["Vendor_Noise"], archive=True)
        return

    lower_body, lower_subject = body.lower(), subject.lower()
    if any(kw in lower_subject or kw in lower_body for kw in config.get("agent_to_agent_keywords", [])):
        logger.info(f"[{client_id}] [STAGE 2] Agent-to-Agent matched")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=["Agent_To_Agent"], archive=True)
        return

    # ==========================================
    # STAGE 3 — Dedupe Check
    # ==========================================
    for prev_subj, _ in database.get_recent_inquiries_for_sender(client_id, sender, minutes=15):
        similarity = difflib.SequenceMatcher(None, subject.lower(), prev_subj.lower()).ratio()
        if similarity > 0.85:
            logger.info(f"[{client_id}] [STAGE 3] Duplicate inquiry suppressed from {sender}")
            gmail.apply_labels_and_mark_read(msg_id, add_labels=["Duplicate_Suppressed"], archive=False)
            database.log_inquiry(client_id, sender, subject, intent_category="duplicate_suppressed")
            return

    # ==========================================
    # STAGE 4 — Sensitive Signal Check 
    # ==========================================
    # Clean the keywords list to strip out any empty strings that cause false-positives
    sensitive_kws = [kw.lower() for kw in config.get("sensitive_trigger_keywords", []) if kw.strip()]
    
    if sensitive_kws and any(kw in lower_body or kw in lower_subject for kw in sensitive_kws):
        logger.info(f"[{client_id}] [STAGE 4] Sensitive keywords matched")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=[LABEL_ACTION_NEEDED], archive=False)
        database.log_inquiry(client_id, sender, subject, intent_category="sensitive_escalation")
        return

    # ==========================================
    # STAGE 5 — AI Intent Classification & Relevance Gate
    # ==========================================
    truncated_body = body[:2000] if body else "[No text content]"
    result = classify_email(subject, truncated_body, config)

    is_re_related = result.get("is_real_estate_related", True)
    intent = result["intent_category"]
    confidence = result["confidence_score"]
    draft = result["suggested_reply_draft"]
    after_hours = is_after_hours(config)
    
    # --- THE RELEVANCE GATE ---
    if not is_re_related or intent == "non_real_estate_noise":
        logger.info(f"[{client_id}] Non-real estate email detected by AI. Suppressing reply.")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=["Vendor_Noise"], archive=True)
        return

    logger.info(f"[{client_id}] [STAGE 5] Verified Real Estate Lead | Intent: {intent} | Confidence: {confidence:.2f}")

    # ==========================================
    # STAGE 6 — Execution Tier Routing
    # ==========================================
    # 6.A: Establish the default tier based purely on confidence & intent
    tier = "FLAGGED_HUMAN"
    
    if intent in ["showing_request", "property_inquiry", "buyer_detail_request", "price_flexibility_inquiry"] and confidence >= 0.85:
        tier = "AUTO_SEND"
    elif (intent in ["financing_question", "general_vague_inquiry"]) or (0.60 <= confidence < 0.85):
        tier = "DRAFT_AND_NOTIFY"

    # 6.B: Apply Trial Safety Gate (The "Draft Mode" Override)
    if config.get("draft_mode", False) and tier == "AUTO_SEND":
        logger.info(f"[{client_id}] [STAGE 6 OVERRIDE] Trial draft-mode active: converting AUTO_SEND to DRAFT_AND_NOTIFY")
        tier = "DRAFT_AND_NOTIFY"

    # 6.C: Execute the Final Selected Tier
    
    # Path A: Instant Autonomous Send
    if tier == "AUTO_SEND":
        logger.info(f"[{client_id}] Executing Autonomous Auto-Reply to {sender}")
        gmail.send_reply(thread_id, sender, subject, draft)
        gmail.apply_labels_and_mark_read(msg_id, add_labels=[LABEL_AI_HANDLED], archive=False)
        database.log_inquiry(client_id, sender, subject, intent_category=intent)
        database.log_audit_reply(client_id, msg_id, thread_id, sender, subject, draft, intent, confidence, "AUTO_SENT", after_hours)

    # Path B: Draft in Gmail for 1-Click Review
    elif tier == "DRAFT_AND_NOTIFY":
        logger.info(f"[{client_id}] Saving generated reply as Gmail Draft for {sender}")
        gmail.create_draft(thread_id, sender, subject, draft)
        gmail.apply_labels_and_mark_read(msg_id, add_labels=[LABEL_REVIEW_DRAFTS], archive=False)
        database.log_inquiry(client_id, sender, subject, intent_category=intent)
        database.log_audit_reply(client_id, msg_id, thread_id, sender, subject, draft, intent, confidence, "PENDING_APPROVAL_DRAFT", after_hours)

        if admin_email and admin_email.lower() != sender.lower():
            html = f"""
            <h3>Draft Ready for Review & Send</h3>
            <p><strong>From:</strong> {raw_sender}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Classification:</strong> {intent} (Confidence: {confidence:.2f})</p>
            <blockquote style="background:#f4f4f4; padding:10px; border-left:4px solid #007bff;">
                {draft.replace(chr(10), '<br/>') if draft else ''}
            </blockquote>
            <p><a href="https://mail.google.com/mail/u/0/#drafts">Open Gmail to Send</a></p>
            """
            gmail.send_notification(admin_email, f"[{client_id}] Review Needed: {subject}", html)

    # Path C: Hard Escalation Flag
    else:
        logger.info(f"[{client_id}] Escalating {sender} for manual review")
        gmail.apply_labels_and_mark_read(msg_id, add_labels=[LABEL_ACTION_NEEDED], archive=False)
        database.log_inquiry(client_id, sender, subject, intent_category=intent)
        database.log_audit_reply(client_id, msg_id, thread_id, sender, subject, "", intent, confidence, "FLAGGED_HUMAN", after_hours)

        if admin_email and admin_email.lower() != sender.lower():
            html = f"""
            <h3>Lead Escalated for Personal Action</h3>
            <p><strong>From:</strong> {raw_sender}</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Reason:</strong> High-risk intent category ({intent}) or confidence under 0.60 ({confidence:.2f}).</p>
            <blockquote style="background:#fff3cd; padding:10px; border-left:4px solid #ffc107;">
                {truncated_body.replace(chr(10), '<br/>')}
            </blockquote>
            """
            gmail.send_notification(admin_email, f"[{client_id}] Action Needed: {subject}", html)