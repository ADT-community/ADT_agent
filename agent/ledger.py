"""Payout ledger for tracking bounties and payments."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LedgerEntry:
    """A single entry in the payout ledger."""
    issue_number: int
    title: str
    amount_usd: float
    paid_at: Optional[str] = None
    paid_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "issue_number": self.issue_number,
            "title": self.title,
            "amount_usd": self.amount_usd,
            "paid_at": self.paid_at,
            "paid_by": self.paid_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LedgerEntry":
        return cls(
            issue_number=data["issue_number"],
            title=data["title"],
            amount_usd=data["amount_usd"],
            paid_at=data.get("paid_at"),
            paid_by=data.get("paid_by"),
        )


class PayoutLedger:
    """Manages the payout ledger state."""

    def __init__(self, path: str = "ledger.json"):
        self.path = path
        self.entries: dict[int, LedgerEntry] = {}
        self._load()

    def _load(self):
        """Load ledger from disk if it exists."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for num_str, entry_data in data.items():
                        num = int(num_str)
                        self.entries[num] = LedgerEntry.from_dict(entry_data)
            except (json.JSONDecodeError, KeyError, ValueError):
                # If the file is corrupted, start fresh but keep the old file
                pass

    def save(self):
        """Save ledger to disk."""
        data = {str(num): entry.to_dict() for num, entry in self.entries.items()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def is_paid(self, issue_number: int) -> bool:
        """Check if an issue is already marked as paid."""
        entry = self.entries.get(issue_number)
        return entry is not None and entry.paid_at is not None

    def mark_paid(self, issue_number: int, title: str, amount_usd: float, paid_by: str = "maintainer"):
        """Mark an issue as paid in the ledger."""
        if self.is_paid(issue_number):
            return

        now = datetime.now(timezone.utc).isoformat()
        self.entries[issue_number] = LedgerEntry(
            issue_number=issue_number,
            title=title,
            amount_usd=amount_usd,
            paid_at=now,
            paid_by=paid_by,
        )
        self.save()

    def get_entry(self, issue_number: int) -> Optional[LedgerEntry]:
        """Get a specific ledger entry."""
        return self.entries.get(issue_number)

    def get_all_entries(self) -> list[LedgerEntry]:
        """Get all ledger entries sorted by issue number."""
        return [self.entries[k] for k in sorted(self.entries.keys())]
