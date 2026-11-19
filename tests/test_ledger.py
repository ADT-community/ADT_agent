"""Tests for the payout ledger."""
import json
import os
import tempfile
import unittest

from agent.ledger import PayoutLedger, LedgerEntry


class PayoutLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "ledger.json")
        self.ledger = PayoutLedger(path=self.ledger_path)

    def tearDown(self):
        if os.path.exists(self.ledger_path):
            os.remove(self.ledger_path)
        os.rmdir(self.temp_dir)

    def test_initial_state_empty(self):
        self.assertEqual(len(self.ledger.get_all_entries()), 0)

    def test_mark_paid(self):
        self.ledger.mark_paid(1, "Test Issue", 100.0, "alice")
        self.assertTrue(self.ledger.is_paid(1))
        entry = self.ledger.get_entry(1)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "Test Issue")
        self.assertEqual(entry.amount_usd, 100.0)
        self.assertEqual(entry.paid_by, "alice")
        self.assertIsNotNone(entry.paid_at)

    def test_prevent_duplicate_payment(self):
        self.ledger.mark_paid(1, "Test Issue", 100.0, "alice")
        first_paid_at = self.ledger.get_entry(1).paid_at
        
        # Try to mark again with different data
        self.ledger.mark_paid(1, "Updated Issue", 200.0, "bob")
        
        entry = self.ledger.get_entry(1)
        self.assertEqual(entry.title, "Test Issue")
        self.assertEqual(entry.amount_usd, 100.0)
        self.assertEqual(entry.paid_by, "alice")
        self.assertEqual(entry.paid_at, first_paid_at)

    def test_persistence(self):
        self.ledger.mark_paid(1, "Test Issue", 100.0, "alice")
        
        # Create a new ledger instance from the same file
        new_ledger = PayoutLedger(path=self.ledger_path)
        self.assertTrue(new_ledger.is_paid(1))
        entry = new_ledger.get_entry(1)
        self.assertEqual(entry.title, "Test Issue")
        self.assertEqual(entry.amount_usd, 100.0)

    def test_multiple_entries(self):
        self.ledger.mark_paid(1, "Issue 1", 50.0)
        self.ledger.mark_paid(2, "Issue 2", 150.0)
        
        entries = self.ledger.get_all_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].issue_number, 1)
        self.assertEqual(entries[1].issue_number, 2)

    def test_unpaid_issue(self):
        self.assertFalse(self.ledger.is_paid(999))
        self.assertIsNone(self.ledger.get_entry(999))

    def test_corrupted_file_handling(self):
        # Write invalid JSON
        with open(self.ledger_path, "w") as f:
            f.write("{invalid json")
        
        # Should not crash, should start fresh
        ledger = PayoutLedger(path=self.ledger_path)
        self.assertEqual(len(ledger.get_all_entries()), 0)


if __name__ == "__main__":
    unittest.main()
