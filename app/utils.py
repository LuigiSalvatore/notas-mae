"""
Shared utility functions.

Key responsibilities:
  - strip_accents / normalize_name  — accent-insensitive comparison
  - get_accessible_class            — 404/403 guard respecting ClassTeacher membership
  - is_owner                        — bool check for class ownership
  - compute_student_average         — simple/weighted avg with PS substitution
  - compute_student_freq            — presente/(presente+ausente)×100
  - compute_student_status          — Aprovado/Reprovado/Em risco badge
  - letter_to_numeric / numeric_to_letter — A–F ↔ 0–10 scale conversion
  - log_action                      — write to ActionLog (with teacher attribution)
"""

import unicodedata
from datetime import datetime, timezone

from flask import abort
from flask_login import current_user

from app import db
from app.models import ActionLog, AttendanceRecord, ClassOffering, ClassTeacher, Grade


# ─────────────────────────────────────────────────────────────────────────────
# Access control helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_accessible_class(class_id: int, teacher=None) -> ClassOffering:
    """
    Return a ClassOffering if `teacher` has access to it.

    - Super admins can access every class.
    - Other teachers must be in class_teacher (owner or collaborator).
    - Raises 404 if the class doesn't exist, 403 if no access.
    """
    if teacher is None:
        teacher = current_user

    co = db.session.get(ClassOffering, class_id)
    if co is None:
        abort(404)

    if teacher.is_super_admin:
        return co

    role = co.get_role_for(teacher)
    if role is None:
        abort(403)

    return co


def is_owner(co, teacher=None) -> bool:
    """
    Return True if `teacher` is the owner of `co` (or is super admin).
    Accepts a ClassOffering or class_id integer.
    """
    if teacher is None:
        teacher = current_user

    if teacher.is_super_admin:
        return True

    if isinstance(co, int):
        co = db.session.get(ClassOffering, co)
        if co is None:
            return False

    return co.get_role_for(teacher) == "owner"


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation
# ─────────────────────────────────────────────────────────────────────────────

def strip_accents(text: str) -> str:
    """Return text with accents removed and lowercased, for comparison."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def names_match(a: str, b: str) -> bool:
    """Case- and accent-insensitive name equality."""
    return strip_accents(a) == strip_accents(b)


# ─────────────────────────────────────────────────────────────────────────────
# Grade scale conversion  (A–F ↔ 0–10)
# ─────────────────────────────────────────────────────────────────────────────

# Mapping: letter → canonical numeric midpoint
_LETTER_TO_NUM = {
    "A": 9.5,
    "B": 8.0,
    "C": 6.5,
    "D": 5.0,
    "F": 2.0,
}

_NUM_TO_LETTER = [
    (9.0, "A"),
    (7.5, "B"),
    (6.0, "C"),
    (5.0, "D"),
    (0.0, "F"),
]


def letter_to_numeric(letter: str) -> float | None:
    """Convert a letter grade (A/B/C/D/F) to its canonical 0–10 numeric value."""
    return _LETTER_TO_NUM.get(letter.upper().strip()) if letter else None


def numeric_to_letter(value: float) -> str:
    """Convert a 0–10 numeric value to the nearest letter grade."""
    for threshold, letter in _NUM_TO_LETTER:
        if value >= threshold:
            return letter
    return "F"


def format_grade(value: float | None, scale_type: str) -> str:
    """Format a canonical value for display according to the component's scale."""
    if value is None:
        return "—"
    if scale_type == "letter_A_F":
        return numeric_to_letter(value)
    # numeric_0_10: show one decimal place
    return f"{value:.1f}"


# ─────────────────────────────────────────────────────────────────────────────
# Grade average computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_student_average(
    enrollment,
    components: list,
    grade_map: dict,           # {component_id: Grade}
) -> float | None:
    """
    Compute weighted or simple average for one student (enrollment).

    PS substitution rules (finalized):
      - For each non-PS component with status='missed_justified':
          look for a PS component whose covers_component_id == this component's id.
          If a graded PS grade exists for this enrollment → use PS value.
          If no PS grade yet → exclude this component from the average entirely.
      - PS components contribute ONLY for students whose original exam is missed_justified.
        For all other students the PS column is ignored.
      - missed_unjustified → counts as 0.
      - No grade row at all → excluded (not yet graded).

    Returns None when there are no gradeable data points at all.
    """
    settings = enrollment.class_offering.settings
    method = settings.aggregation_method if settings else "simple_avg"

    # Build {component_id → Grade} for quick lookup
    # (grade_map is passed in to avoid N+1 queries)

    # Separate PS components from regular components
    ps_map = {}  # original_component_id → ps_component
    for comp in components:
        if comp.is_ps and comp.covers_component_id:
            ps_map[comp.covers_component_id] = comp

    values = []    # list of (numeric_value, weight)

    for comp in components:
        if comp.is_ps:
            continue   # PS handled via the original component logic below

        grade = grade_map.get(comp.id)

        if grade is None:
            # Not yet entered — exclude (not counted as 0)
            continue

        if grade.status == "graded":
            if grade.value is None:
                continue
            values.append((grade.value, comp.weight))

        elif grade.status == "missed_unjustified":
            # Counts as 0
            values.append((0.0, comp.weight))

        elif grade.status == "missed_justified":
            # Look for a PS grade
            ps_comp = ps_map.get(comp.id)
            if ps_comp:
                ps_grade = grade_map.get(ps_comp.id)
                if ps_grade and ps_grade.status == "graded" and ps_grade.value is not None:
                    # Use PS value instead
                    values.append((ps_grade.value, comp.weight))
                    continue
            # No PS yet → exclude from denominator entirely
            continue

    if not values:
        return None

    if method == "weighted_avg":
        total_weight = sum(w for _, w in values)
        if total_weight == 0:
            return None
        return sum(v * w for v, w in values) / total_weight
    else:  # simple_avg (default)
        return sum(v for v, _ in values) / len(values)


# ─────────────────────────────────────────────────────────────────────────────
# Frequency computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_student_freq(records: list[AttendanceRecord]) -> float | None:
    """
    Compute frequência % for one student.

    Formula (finalized):
        freq = present / (present + absent) × 100

    - justified_absence: excluded entirely (neither helps nor hurts)
    - unmarked:          excluded entirely (not yet marked)
    - Returns None when present+absent == 0 (no countable sessions yet → displayed as "—")
    """
    present = sum(1 for r in records if r.status == "present")
    absent  = sum(1 for r in records if r.status == "absent")
    total   = present + absent
    if total == 0:
        return None
    return (present / total) * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Student status badge
# ─────────────────────────────────────────────────────────────────────────────

# Status codes and display labels
STATUS_LABELS = {
    "aprovado":         "Aprovado",
    "reprovado_nota":   "Reprovado por Nota",
    "reprovado_falta":  "Reprovado por Falta",
    "reprovado_ambos":  "Reprovado (Nota e Falta)",
    "em_risco":         "Em Risco",
    "pendente":         "Pendente",
}

STATUS_COLORS = {
    "aprovado":         "success",
    "reprovado_nota":   "danger",
    "reprovado_falta":  "warning",
    "reprovado_ambos":  "danger",
    "em_risco":         "warning",
    "pendente":         "neutral",
}


def compute_student_status(
    avg: float | None,
    freq: float | None,
    settings,
    has_pending_grades: bool = False,
    has_pending_freq: bool = False,
) -> str:
    """
    Return a status code string for a student.

    Parameters:
      avg              — computed average (None = no grades yet)
      freq             — frequência % (None = no countable sessions yet)
      settings         — ClassSettings instance
      has_pending_grades — True if any non-PS component still has no grade
      has_pending_freq   — True if any session is still 'unmarked'

    Status hierarchy:
      Reprovado por Falta  > Reprovado por Nota > Em Risco > Aprovado > Pendente
    """
    if settings is None:
        return "pendente"

    min_grade = settings.min_avg_grade      # default 7.0
    min_freq  = settings.min_attendance_pct # default 75.0

    grade_fail  = avg  is not None and avg  < min_grade and not has_pending_grades
    freq_fail   = freq is not None and freq < min_freq  and not has_pending_freq

    if grade_fail and freq_fail:
        return "reprovado_ambos"
    if freq_fail:
        return "reprovado_falta"
    if grade_fail:
        return "reprovado_nota"

    # Not definitively failed — check for risk
    grade_risky = avg  is not None and avg  < min_grade                      # failing but still has pending grades
    freq_risky  = freq is not None and freq < min_freq                        # failing but sessions still unmarked
    grade_close = avg  is not None and avg  < (min_grade + 1.5) and not grade_fail
    freq_close  = freq is not None and freq < (min_freq + 10.0) and not freq_fail

    if grade_risky or freq_risky or grade_close or freq_close:
        return "em_risco"

    if avg is None and freq is None:
        return "pendente"

    return "aprovado"


# ─────────────────────────────────────────────────────────────────────────────
# ActionLog helper
# ─────────────────────────────────────────────────────────────────────────────

def log_action(
    action_type: str,
    description: str,
    previous_value: dict | None = None,
    class_offering_id: int | None = None,
    enrollment_id: int | None = None,
    teacher_id: int | None = None,
) -> ActionLog:
    """Create and add an ActionLog row (caller must db.session.commit())."""
    if teacher_id is None:
        try:
            if current_user and current_user.is_authenticated:
                teacher_id = current_user.id
        except Exception:
            pass

    entry = ActionLog(
        action_type=action_type,
        description=description,
        previous_value=previous_value,
        class_offering_id=class_offering_id,
        enrollment_id=enrollment_id,
        teacher_id=teacher_id,
    )
    db.session.add(entry)
    return entry



# ─────────────────────────────────────────────────────────────────────────────
# Grade map builder (avoids N+1 in templates / route helpers)
# ─────────────────────────────────────────────────────────────────────────────

def build_grade_map(enrollment) -> dict:
    """Return {component_id: Grade} for a single enrollment."""
    return {g.component_id: g for g in enrollment.grades}


def build_class_grade_maps(class_offering) -> dict:
    """Return {enrollment_id: {component_id: Grade}} for a full class."""
    result = {}
    for enrollment in class_offering.enrollments:
        result[enrollment.id] = build_grade_map(enrollment)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Attendance record map builder
# ─────────────────────────────────────────────────────────────────────────────

def build_attendance_map(class_offering) -> dict:
    """Return {enrollment_id: {session_id: AttendanceRecord}} for a full class."""
    result = {}
    for enrollment in class_offering.enrollments:
        result[enrollment.id] = {r.session_id: r for r in enrollment.attendance_records}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Risk indicator for dashboard card
# ─────────────────────────────────────────────────────────────────────────────

def count_at_risk(class_offering) -> dict:
    """
    Return {'at_risk': N, 'failing': N} for a ClassOffering.
    Used for the dashboard card badge.
    """
    at_risk = 0
    failing = 0
    components = class_offering.components
    grade_maps = build_class_grade_maps(class_offering)
    att_map    = build_attendance_map(class_offering)
    settings   = class_offering.settings

    for enrollment in class_offering.active_enrollments:
        gmap   = grade_maps.get(enrollment.id, {})
        recs   = list(att_map.get(enrollment.id, {}).values())
        avg    = compute_student_average(enrollment, components, gmap)
        freq   = compute_student_freq(recs)

        has_pending_grades = any(
            gmap.get(c.id) is None for c in components if not c.is_ps
        )
        has_pending_freq = any(
            att_map.get(enrollment.id, {}).get(s.id) is None
            or att_map.get(enrollment.id, {}).get(s.id).status == "unmarked"
            for s in class_offering.sessions
        )

        status = compute_student_status(avg, freq, settings,
                                        has_pending_grades, has_pending_freq)
        if status in ("reprovado_nota", "reprovado_falta", "reprovado_ambos"):
            failing += 1
        elif status == "em_risco":
            at_risk += 1

    return {"at_risk": at_risk, "failing": failing}
