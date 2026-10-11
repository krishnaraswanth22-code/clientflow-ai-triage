# ⚡ ClientFlow AI — Autonomous Inbox Speed-to-Lead Engine

> **A headless, multi-tenant AI triage pipeline for Google Workspace & Gmail that classifies inbound leads in <3 seconds with deterministic human handoff.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Google Workspace API](https://img.shields.io/badge/Google%20API-OAuth%202.0-yellow.svg)](https://developers.google.com/gmail/api)
[![Groq Llama-3](https://img.shields.io/badge/LLM-Groq%20Llama--3-orange.svg)](https://groq.com/)
[![Live Portfolio Demo](https://img.shields.io/badge/Live-Demo-emerald.svg)](https://krishnaraswanth22-code.github.io/clientflow-ai-triage/)

---

### ⏱️ The 30-Second Overview
* **The Problem:** 78% of service clients buy from whoever replies first[cite: 22]. High-intent leads arriving after-hours or on weekends wait 14–48 hours, leaking revenue to faster competitors[cite: 22].
* **The Engine:** An event-driven microservice utilizing the Gmail REST API and Groq LLM inference to parse and triage client inquiries in under 3 seconds[cite: 13, 21, 23].
* **The Safety Gate:** Unlike raw chatbots that hallucinate pricing, strict confidence thresholds (`< 0.75`) halt automated replies and escalate negotiation queries to human engineers[cite: 15, 21].

---

### 🛡️ 3-Tier Execution Matrix

| Status Badge | Routing Criteria | Deterministic Action |
| :--- | :--- | :--- |
| **🟢 AI Handled** | Confidence ≥ 0.85 (Standard FAQs, tours, pricing) | Auto-sends response & Calendly booking links in <3s via Gmail API[cite: 15, 21] |
| **🟡 Review Drafts** | Confidence 0.60–0.84 (Complex inquiries, scope) | Generates pre-written Gmail Draft for 1-click human review[cite: 15, 21] |
| **🔴 Action Needed** | Cash offers, price cuts, legal/refund threats | Halts automation entirely; tags thread for manual takeover[cite: 15, 21] |

---

### 🚀 Key Technical Highlights
* **Multi-Tenant Configuration Scanner:** Business rules and active listings are decoupled into `clients/*.json` config files[cite: 13, 15]. Scale to new clients without modifying backend code[cite: 15, 17].
* **Ephemeral In-Memory Processing:** Payloads are evaluated in volatile RAM and purged immediately (Zero Data Retention / GDPR aligned)[cite: 15, 21, 22].
* **Pre-LLM Noise Filtering:** String inspection and regex blocks promotional blasts and retail newsletters before invoking high-speed model tokens[cite: 13, 18, 20].

---

### 🛠️ Quickstart (Fork & Run)

```bash
# 1. Clone your fork
git clone [https://github.com/krishnaraswanth22-code/clientflow-ai-triage.git](https://github.com/krishnaraswanth22-code/clientflow-ai-triage.git)
cd clientflow-ai-triage

# 2. Virtual environment setup
python -m venv venv
# Windows:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Place your Google Cloud OAuth credentials.json in root

# 5. Run a single triage sweep
python main.py --once
