import os
import tempfile
import pytest
from src.db import DatabaseTracker

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tracker = DatabaseTracker(db_path=path)
    tracker.init_db()
    yield tracker
    if os.path.exists(path):
        os.remove(path)

def test_db_init_and_application_record(temp_db):
    assert not temp_db.is_already_applied("job-123")
    temp_db.record_application(
        job_id="job-123",
        title="Software Engineer",
        company="Acme Corp",
        location="Remote",
        status="APPLIED"
    )
    assert temp_db.is_already_applied("job-123")
    assert temp_db.get_session_applied_count() == 1

def test_qa_cache(temp_db):
    assert temp_db.get_cached_answer("hash123") is None
    temp_db.cache_answer("hash123", "Are you authorized to work in the US?", "radio", '{"target_value": "Yes", "confidence": 1.0}')
    cached = temp_db.get_cached_answer("hash123")
    assert cached is not None
    assert cached["target_value"] == "Yes"
