"""Data models.

`ReportIn` is what a user is allowed to submit: every form submission is
checked against it before it reaches a database. PostgreSQL enforces the same
rules again with CHECK constraints (defense in depth).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(StrEnum):
    PHISHING = "phishing"
    COMPROMISED_ACCOUNT = "compromised_account"
    LOST_DEVICE = "lost_device"
    MALWARE = "malware"
    DATA_LEAK = "data_leak"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _CATEGORY_LABELS[self]


_CATEGORY_LABELS = {
    Category.PHISHING: "Phishing",
    Category.COMPROMISED_ACCOUNT: "Compromised account",
    Category.LOST_DEVICE: "Lost or stolen device",
    Category.MALWARE: "Malware",
    Category.DATA_LEAK: "Data leak",
    Category.HARDWARE: "Hardware",
    Category.SOFTWARE: "Software",
    Category.OTHER: "Other",
}


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def label(self) -> str:
        return self.value.capitalize()


TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 2000


class ReportIn(BaseModel):
    """A report submitted through the form."""

    # "   " counts as empty; unknown fields are refused.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    category: Category
    severity: Severity
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)


class StoredReport(ReportIn):
    """A report as stored, with its id and creation date."""

    model_config = ConfigDict(extra="ignore")

    id: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def to_public(self) -> dict[str, Any]:
        """JSON-ready view, used by the live feed."""
        data = self.model_dump(mode="json")
        data["category_label"] = self.category.label
        data["severity_label"] = self.severity.label
        return data
