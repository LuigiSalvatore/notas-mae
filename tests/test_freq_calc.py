from app.utils import compute_student_freq
from app.models import AttendanceRecord

def test_attendance_freq():
    # 2 present, 1 absent, 1 justified, 1 unmarked
    records = [
        AttendanceRecord(status="present"),
        AttendanceRecord(status="present"),
        AttendanceRecord(status="absent"),
        AttendanceRecord(status="justified_absence"),
        AttendanceRecord(status="unmarked")
    ]
    # Freq: 2 / (2 + 1) * 100 = 66.666...%
    freq = compute_student_freq(records)
    assert abs(freq - 66.6666) < 0.01

def test_attendance_empty():
    # Only unmarked and justified
    records = [
        AttendanceRecord(status="justified_absence"),
        AttendanceRecord(status="unmarked")
    ]
    assert compute_student_freq(records) is None
