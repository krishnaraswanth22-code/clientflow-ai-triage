import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

DB_PATH = "triage_system.db"

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes tables scoped by client_id. Strips hardcoded test fixtures."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Stage 1: Known contacts per client
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_clients (
            client_id TEXT NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            status TEXT NOT NULL,
            deal_stage TEXT,
            PRIMARY KEY (client_id, email)
        )
    """)

    # Stage 3: Deduplication per client
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recent_inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            sender_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            received_at TIMESTAMP NOT NULL,
            intent_category TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_inquiries_client_sender ON recent_inquiries(client_id, sender_email)")

    # Sent replies & monthly audit metrics per client
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sent_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            sent_body TEXT NOT NULL,
            intent_category TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            action_taken TEXT NOT NULL,
            is_after_hours INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sent_replies_client_date ON sent_replies(client_id, created_at)")

    conn.commit()
    conn.close()

def get_known_client(client_id: str, email: str) -> Optional[Tuple[str, str]]:
    """Checks if sender belongs to known contacts for THIS specific client."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT status, deal_stage 
        FROM known_clients 
        WHERE client_id = ? AND email = ?
    """, (client_id, email.strip().lower()))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["status"], row["deal_stage"]
    return None

def get_recent_inquiries_for_sender(client_id: str, email: str, minutes: int = 15) -> List[Tuple[str, str]]:
    """Retrieves recent inquiries from this sender strictly for this client within N minutes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    cursor.execute("""
        SELECT subject, received_at 
        FROM recent_inquiries 
        WHERE client_id = ? AND sender_email = ? AND received_at >= ?
    """, (client_id, email.strip().lower(), cutoff.isoformat()))
    rows = cursor.fetchall()
    conn.close()
    return [(r["subject"], r["received_at"]) for r in rows]

def log_inquiry(client_id: str, email: str, subject: str, intent_category: Optional[str] = None):
    """Logs an incoming inquiry tagged with client_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recent_inquiries (client_id, sender_email, subject, received_at, intent_category)
        VALUES (?, ?, ?, ?, ?)
    """, (client_id, email.strip().lower(), subject, datetime.utcnow().isoformat(), intent_category))
    conn.commit()
    conn.close()

def log_audit_reply(
    client_id: str,
    message_id: str,
    thread_id: str,
    recipient_email: str,
    subject: str,
    sent_body: str,
    intent_category: str,
    confidence_score: float,
    action_taken: str,
    is_after_hours: bool
):
    """Logs executed response actions tagged by client_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sent_replies (
            client_id, message_id, thread_id, recipient_email, subject, sent_body, 
            intent_category, confidence_score, action_taken, is_after_hours, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        client_id, message_id, thread_id, recipient_email, subject, sent_body,
        intent_category, confidence_score, action_taken,
        1 if is_after_hours else 0, datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()

def get_monthly_report_data(client_id: str, year: int, month: int) -> Dict[str, Any]:
    """Generates monthly metrics strictly isolated for ONE client_id."""
    conn = get_db_connection()
    cursor = conn.cursor()

    start_date = f"{year:04d}-{month:02d}-01T00:00:00"
    end_date = f"{year+1:04d}-01-01T00:00:00" if month == 12 else f"{year:04d}-{month+1:02d}-01T00:00:00"

    cursor.execute("""
        SELECT 
            COUNT(*) as total_leads,
            SUM(CASE WHEN action_taken = 'AUTO_SENT' THEN 1 ELSE 0 END) as auto_sent_count,
            SUM(CASE WHEN action_taken IN ('FLAGGED_HUMAN', 'PENDING_APPROVAL_DRAFT') THEN 1 ELSE 0 END) as human_flagged_count,
            SUM(CASE WHEN is_after_hours = 1 THEN 1 ELSE 0 END) as after_hours_count
        FROM sent_replies
        WHERE client_id = ? AND created_at >= ? AND created_at < ?
    """, (client_id, start_date, end_date))

    row = cursor.fetchone()
    conn.close()
    return {
        "total_leads": row["total_leads"] or 0,
        "auto_sent_count": row["auto_sent_count"] or 0,
        "human_flagged_count": row["human_flagged_count"] or 0,
        "after_hours_count": row["after_hours_count"] or 0
    }