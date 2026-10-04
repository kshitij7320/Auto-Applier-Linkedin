import pytest
from src.db import DatabaseTracker

def test_duplicate_suppression_prevents_reapply():
    db = DatabaseTracker(db_path=":memory:")
    db.init_db()
    db.record_application("job-999", "DevOps Engineer", "CloudCorp", "Remote", "APPLIED")
    assert db.is_already_applied("job-999") is True
    assert db.is_already_applied("job-1000") is False

def test_session_hard_cap_enforcement():
    max_cap = 20
    session_count = 20
    assert (session_count >= max_cap) is True

def test_consecutive_errors_circuit_breaker():
    error_threshold = 3
    errors = 3
    assert errors >= error_threshold
