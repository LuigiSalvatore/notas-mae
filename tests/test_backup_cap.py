import os
import time
from pathlib import Path
from app.backup.jobs import enforce_backup_cap

def test_backup_fifo_cap(monkeypatch):
    # Set a tiny cap of 100 bytes for testing
    monkeypatch.setattr("app.backup.jobs.BACKUP_CAP_BYTES", 100)
    # Mock db save callback as noop
    monkeypatch.setattr("app.backup.jobs._mark_file_deleted", lambda path: None)

    # Use a real temp folder
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = Path(tmpdir) / "backup_old.xlsx"
        f2 = Path(tmpdir) / "backup_mid.xlsx"
        f3 = Path(tmpdir) / "backup_new.xlsx"
        
        f1.write_bytes(b"A" * 40) # 40 bytes
        f2.write_bytes(b"B" * 40) # 40 bytes
        f3.write_bytes(b"C" * 40) # 40 bytes
        
        # total: 120 bytes (exceeds 100)
        
        # Set modification times using os.utime to control order
        now = time.time()
        os.utime(f1, (now - 300, now - 300)) # Oldest
        os.utime(f2, (now - 200, now - 200)) # Mid
        os.utime(f3, (now - 100, now - 100)) # Newest
        
        # Run cap enforcement
        enforce_backup_cap(tmpdir)
        
        # backup_old.xlsx should be deleted (leaving mid + new = 80 bytes)
        assert not f1.exists()
        assert f2.exists()
        assert f3.exists()
