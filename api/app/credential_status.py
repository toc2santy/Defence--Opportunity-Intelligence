"""
Pure logic for credential expiry status — deliberately kept out of
main.py and DB-free, same separation habit as every other piece of
real logic in this project, even though it's small.
"""

from datetime import date
from typing import TypedDict

WARNING_THRESHOLD_DAYS = 14


class CredentialStatus(TypedDict):
    expires_at: str
    days_remaining: int
    status: str      # 'ok' | 'warning' | 'expired'
    message: str


def compute_status(expires_at: date, today: date) -> CredentialStatus:
    days_remaining = (expires_at - today).days

    if days_remaining < 0:
        status = "expired"
        message = (
            f"EXPIRED {abs(days_remaining)} day(s) ago. Ingestion calls will fail until renewed. "
            "Get a new key from SAM.gov Account Details, set SAM_GOV_API_KEY, restart the API, "
            "then update this record via PATCH /ingestion/sam-gov/key-status."
        )
    elif days_remaining <= WARNING_THRESHOLD_DAYS:
        status = "warning"
        message = (
            f"Expires in {days_remaining} day(s). Renew soon at SAM.gov Account Details "
            "to avoid an ingestion outage."
        )
    else:
        status = "ok"
        message = f"Valid for {days_remaining} more day(s)."

    return {
        "expires_at": expires_at.isoformat(),
        "days_remaining": days_remaining,
        "status": status,
        "message": message,
    }
