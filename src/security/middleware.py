"""
Security module — PII detection, prompt injection defense, audit logging.

Usage:
    from src.security.middleware import SecurityMiddleware
    sec = SecurityMiddleware()
    result = sec.process_input("Check John Smith's account john@acme.com")
    # result.allowed = True, result.cleaned_query = "Check <PERSON>'s account <EMAIL_ADDRESS>"
"""

import json
import os
import re
import time
import hashlib
from datetime import datetime
from pathlib import Path

from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

from src.utils.config import CONFIG


# ═══════════════════════════════════════════════════════════════
# PII DETECTION
# ═══════════════════════════════════════════════════════════════

PII_ENTITIES = [
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
    "US_SSN", "IP_ADDRESS", "IBAN_CODE", "US_PASSPORT",
    "US_BANK_NUMBER", "LOCATION",
]


class PIIGuard:
    """Detect and redact PII from text."""

    def __init__(self, score_threshold=0.5):
        self.analyzer = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()
        self.score_threshold = score_threshold

    def scan(self, text):
        """Detect PII in text. Returns list of findings."""
        results = self.analyzer.analyze(
            text=text, language='en',
            entities=PII_ENTITIES,
            score_threshold=self.score_threshold,
        )
        return [{"type": r.entity_type, "start": r.start, "end": r.end,
                 "score": r.score, "value": text[r.start:r.end]} for r in results]

    def redact(self, text):
        """Redact PII from text. Returns (redacted_text, findings)."""
        results = self.analyzer.analyze(
            text=text, language='en',
            entities=PII_ENTITIES,
            score_threshold=self.score_threshold,
        )
        if not results:
            return text, []

        anonymized = self.anonymizer.anonymize(text=text, analyzer_results=results)
        findings = [{"type": r.entity_type, "original": text[r.start:r.end],
                      "score": round(r.score, 2)} for r in results]
        return anonymized.text, findings


# ═══════════════════════════════════════════════════════════════
# PROMPT INJECTION DEFENSE
# ═══════════════════════════════════════════════════════════════

INJECTION_PATTERNS = [
    (r'ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)', "instruction_override", 0.95),
    (r'disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)', "instruction_override", 0.95),
    (r'forget\s+(all\s+)?(previous|your)\s+(instructions?|rules?|training)', "instruction_override", 0.90),
    (r'you\s+are\s+now\s+(an?\s+)?(\w+\s+)?(ai|assistant|bot)', "role_hijack", 0.90),
    (r'act\s+as\s+(an?\s+)?(\w+\s+)?(ai|assistant|expert|hacker)', "role_hijack", 0.85),
    (r'pretend\s+(to\s+be|you\s+are)', "role_hijack", 0.85),
    (r'(show|reveal|display|print|repeat)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions?|rules?)', "prompt_extraction", 0.95),
    (r'what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?)', "prompt_extraction", 0.90),
    (r'(DAN|developer\s+mode|god\s+mode|unrestricted|unfiltered)', "jailbreak", 0.85),
    (r'(bypass|override|disable|remove)\s+(your\s+)?(safety|filter|guardrail)', "jailbreak", 0.90),
    (r'<\|?(system|im_start|endoftext)\|?>', "delimiter_attack", 0.95),
]


def check_injection(text):
    """Check if text contains a prompt injection attempt."""
    text_lower = text.lower().strip()
    highest_score = 0
    attack_type = None

    for pattern, atype, score in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            if score > highest_score:
                highest_score = score
                attack_type = atype

    return {
        "is_injection": highest_score >= 0.7,
        "confidence": highest_score,
        "attack_type": attack_type,
        "blocked": highest_score >= 0.7,
    }


# ═══════════════════════════════════════════════════════════════
# TOPIC BOUNDARY CHECKER
# ═══════════════════════════════════════════════════════════════

BLOCKED_TOPICS = [
    (r'(how\s+to\s+)?(make|build|create)\s+(a\s+)?(bomb|weapon|explosive|poison)', "weapons"),
    (r'(how\s+to\s+)?(hack|exploit|crack|break\s+into)', "hacking"),
    (r'(write|generate)\s+(malware|virus|ransomware|exploit)', "malware"),
]


def check_topic(text):
    """Check if query violates topic boundaries."""
    text_lower = text.lower()
    for pattern, topic in BLOCKED_TOPICS:
        if re.search(pattern, text_lower):
            return {"allowed": False, "blocked_topic": topic}
    return {"allowed": True, "blocked_topic": None}


# ═══════════════════════════════════════════════════════════════
# AUDIT LOGGER
# ═══════════════════════════════════════════════════════════════

class AuditLogger:
    """Immutable audit log for all LLM interactions."""

    def __init__(self, log_path=None):
        if log_path is None:
            log_path = CONFIG["paths"].get("audit_log", "./logs/audit_log.jsonl")
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.session_id = hashlib.md5(
            f"{datetime.now().isoformat()}_{os.getpid()}".encode()
        ).hexdigest()[:12]
        self.count = 0

    def log(self, event_type, data):
        """Append an event to the audit log."""
        self.count += 1
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "interaction_id": self.count,
            "event_type": event_type,
            "data": data,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
        return entry

    def get_stats(self):
        """Read log and return statistics."""
        stats = {"total": 0, "security_alerts": 0, "pii_detections": 0, "injection_blocks": 0}
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        stats["total"] += 1
                        if entry["event_type"] == "security_alert":
                            stats["security_alerts"] += 1
        except FileNotFoundError:
            pass
        return stats


# ═══════════════════════════════════════════════════════════════
# RATE LIMITER
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, max_requests=10, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = {}

    def check(self, user_id="default"):
        now = time.time()
        self.requests.setdefault(user_id, [])
        self.requests[user_id] = [t for t in self.requests[user_id] if now - t < self.window]

        if len(self.requests[user_id]) >= self.max_requests:
            return {"allowed": False, "reason": f"Rate limit: {self.max_requests} req/{self.window}s"}

        self.requests[user_id].append(now)
        return {"allowed": True, "remaining": self.max_requests - len(self.requests[user_id])}


# ═══════════════════════════════════════════════════════════════
# COMBINED SECURITY MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class SecurityMiddleware:
    """Chains all security checks into one pipeline."""

    def __init__(self):
        sec_config = CONFIG.get("security", {})
        self.pii_guard = PIIGuard(score_threshold=sec_config.get("pii_score_threshold", 0.5))
        self.rate_limiter = RateLimiter(
            max_requests=sec_config.get("rate_limit_requests", 10),
            window_seconds=sec_config.get("rate_limit_window", 60),
        )
        self.audit = AuditLogger()
        print(f"  Security middleware initialized (session: {self.audit.session_id})")

    def process_input(self, query, user_id="default"):
        """Run all input security checks. Returns dict with allowed, cleaned_query, etc."""
        result = {"allowed": True, "cleaned_query": query, "block_reason": None,
                  "pii_findings": [], "injection_check": {}, "topic_check": {}}

        # Rate limit
        rate = self.rate_limiter.check(user_id)
        if not rate["allowed"]:
            result["allowed"] = False
            result["block_reason"] = rate["reason"]
            return result

        # PII
        cleaned, findings = self.pii_guard.redact(query)
        result["cleaned_query"] = cleaned
        result["pii_findings"] = findings

        # Injection
        inj = check_injection(cleaned)
        result["injection_check"] = inj
        if inj["blocked"]:
            result["allowed"] = False
            result["block_reason"] = f"Prompt injection: {inj['attack_type']} ({inj['confidence']:.0%})"
            self.audit.log("security_alert", {"type": "injection", "detail": inj["attack_type"]})
            return result

        # Topic
        topic = check_topic(cleaned)
        result["topic_check"] = topic
        if not topic["allowed"]:
            result["allowed"] = False
            result["block_reason"] = f"Blocked topic: {topic['blocked_topic']}"
            self.audit.log("security_alert", {"type": "topic_violation", "detail": topic["blocked_topic"]})
            return result

        return result

    def process_output(self, response):
        """Scan model output for PII leakage."""
        cleaned, findings = self.pii_guard.redact(response)
        if findings:
            self.audit.log("security_alert", {"type": "output_pii", "entities": [f["type"] for f in findings]})
        return cleaned, findings
