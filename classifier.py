# classifier.py
import os
import json
import logging
import time
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from groq import Groq, RateLimitError

logger = logging.getLogger(__name__)

class EmailIntentClassification(BaseModel):
    is_real_estate_related: bool
    intent_category: str
    confidence_score: float
    extracted_address_or_listing_id: Optional[str] = None
    suggested_reply_draft: str

def build_system_prompt(config: dict) -> str:
    # 1. Accepts both raw web form text ("listings_and_hoa") and structured JSON lists
    raw_listings = config.get("listings_and_hoa") or config.get("sample_listings") or config.get("listings") or "No active listings specified."
    listings_context = raw_listings if isinstance(raw_listings, str) else json.dumps(raw_listings, indent=2)

    # 2. Supports both key names for the scheduling link
    calendly_link = config.get("booking_link") or config.get("calendly_link", "https://calendly.com/apex-realty/tour")
    business_name = config.get("business_name", "Apex Boutique Realty")

    return f"""
You are the AI Operations Concierge for {business_name}.
You must analyze incoming real estate inquiries and respond strictly in valid JSON format.

YOUR FIRST AND MOST CRITICAL JOB:
Determine if this email is a GENUINE REAL ESTATE INQUIRY from a prospective home buyer, seller, or client.

RULES FOR NON-REAL ESTATE / NOISE:
- If the email is a newsletter, receipt, job application, social media update, course notification, marketing pitch, or general system alert, it is NOT REAL ESTATE.
- Even if it uses words like "exclusive offer", "tour our app", or "browse properties", if it is from an automated platform or brand, classify it as "non_real_estate_noise".

ACTIVE PROPERTY INVENTORY:
{listings_context}

SCHEDULING CALENDLY LINK:
{calendly_link}

ALLOWED INTENT CATEGORIES:
- property_inquiry: Questions regarding standard listing specs, price, beds/baths, HOA fees, school district.
- showing_request: Inquiries asking to view, tour, or see a specific property.
- financing_question: Questions about mortgage rates, lenders, down payments, pre-approval qualifications.
- price_negotiation: A CONCRETE below-asking offer with a specific dollar figure, or an explicit request for a specific concession.
- price_flexibility_inquiry: A VAGUE question about whether there's room on price, with NO specific number attached.
- seller_valuation: Homeowner requesting a home valuation or Comparative Market Analysis (CMA).
- buyer_detail_request: Inquiries requesting disclosure packets, HOA rules, or surveys.
- general_vague_inquiry: Ambiguous or off-topic questions.
- non_real_estate_noise: ANY email that is not an actual client asking about real estate services or properties.

DRAFTING RULES:
1. For property_inquiry: Pull the exact HOA fee, notes, and school district details directly from the listing data. Mention that specific school attendance zones should be confirmed with the district. Include the scheduling link: {calendly_link}. Set confidence_score to 0.95.
2. For showing_request: Provide the booking link ({calendly_link}) and confirm the address. Set confidence_score to 0.95.
3. For financing_question: State that we partner with local preferred mortgage specialists who can review financing options and offer a lender introduction, while avoiding specific loan advice or interest rates. Set confidence_score to 0.80.
4. For price_negotiation: Set confidence_score to 0.40. NEVER use bracketed placeholders. Set suggested_reply_draft to: "Thank you for reaching out with your offer details. I have received this and will review it directly with the seller before following up shortly."
5. For price_flexibility_inquiry: Draft a warm, brief reply: "The seller is open to reviewing all written offers. I would be happy to connect you with our lead agent to discuss terms, or schedule a walkthrough first." Include the scheduling link: {calendly_link}. Set confidence_score to 0.90.
6. For non_real_estate_noise: Set is_real_estate_related to false, confidence_score to 1.0, and leave suggested_reply_draft empty.

Keep suggested_reply_draft concise — 120 words or fewer.

CRITICAL REQUIREMENT:
Output a single, raw, valid JSON object with NO extra commentary or markdown tags:
{{
  "is_real_estate_related": true,
  "intent_category": "property_inquiry",
  "confidence_score": 0.95,
  "extracted_address_or_listing_id": "402 West Avenue",
  "suggested_reply_draft": "Your drafted response here"
}}
"""

MAX_OUTPUT_TOKENS = 700
MAX_RATE_LIMIT_RETRIES = 3
BASE_BACKOFF_SECONDS = 20

def classify_email(subject: str, body: str, config: dict) -> Dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("[CLASSIFIER ERROR] GROQ_API_KEY is missing from environment.")
        return get_fallback_result("Missing Groq API Key")

    client = Groq(api_key=api_key)
    system_prompt = build_system_prompt(config)
    user_prompt = f"Subject: {subject}\n\nEmail Body:\n{body}"
    model_name = "qwen/qwen3.8-27b"

    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 1):
        try:
            completion = client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                max_tokens=MAX_OUTPUT_TOKENS,
                extra_body={"reasoning_effort": "none"},
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            raw_content = completion.choices[0].message.content
            logger.info(f"[GROQ RAW RESPONSE]:\n{raw_content}")

            data = json.loads(raw_content)
            score = float(data.get("confidence_score", 0.0))
            score = max(0.0, min(1.0, score))

            return {
                "is_real_estate_related": data.get("is_real_estate_related", True),
                "intent_category": data.get("intent_category", "general_vague_inquiry"),
                "confidence_score": score,
                "extracted_address_or_listing_id": data.get("extracted_address_or_listing_id"),
                "suggested_reply_draft": data.get("suggested_reply_draft", "Thank you for contacting us."),
                "classification_failed": False,
            }

        except RateLimitError as err:
            last_err = err
            if attempt < MAX_RATE_LIMIT_RETRIES:
                wait_s = BASE_BACKOFF_SECONDS * attempt
                logger.warning(f"[GROQ RATE LIMIT] Retrying in {wait_s}s. Error: {err}")
                time.sleep(wait_s)
                continue
            logger.error(f"[GROQ ERROR] Exhausted retries on rate limit: {err}", exc_info=True)
            return get_fallback_result(f"Rate limit exceeded: {err}")

        except Exception as err:
            logger.error(f"[GROQ ERROR] Classification failed: {err}", exc_info=True)
            return get_fallback_result(str(err))

    return get_fallback_result(str(last_err) if last_err else "Unknown classifier failure")

def get_fallback_result(reason: str) -> Dict[str, Any]:
    logger.warning(f"[CLASSIFIER FALLBACK] Routing to human review. Reason: {reason}")
    return {
        "is_real_estate_related": True,
        "intent_category": "classification_failed",
        "confidence_score": 0.0,
        "extracted_address_or_listing_id": None,
        "suggested_reply_draft": None,
        "classification_failed": True,
        "failure_reason": reason,
    }