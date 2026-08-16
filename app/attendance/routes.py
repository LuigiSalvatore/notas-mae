"""
Attendance blueprint — session management + per-session marking.
"""

from datetime import date, datetime

from flask import (
    Blueprint, render_template, request, jsonify,
    redirect, url_for, flash,
)
from flask_login import login_required

from app import db
from app.models import (
    ClassOffering, AttendanceSession, AttendanceRecord, Enrollment,
)
from app.utils import compute_student_freq, log_action, get_accessible_class

attendance_bp = Blueprint("attendance", __name__, url_prefix="/class")


def _get_class_or_404(class_id):
    return get_accessible_class(class_id)


# ─────────────────────────────────────────────────────────────────────────────
# Session list
# ─────────────────────────────────────────────────────────────────────────────

@attendance_bp.route("/<int:class_id>/attendance")
@login_required
def sessions_list(class_id):
    co = _get_class_or_404(class_id)
    sessions = sorted(co.sessions, key=lambda s: s.date)

    # Compute per-session summary (how many marked present/absent)
    summaries = {}
    for session in sessions:
        present  = sum(1 for r in session.records if r.status == "present")
        absent   = sum(1 for r in session.records if r.status == "absent")
        justified = sum(1 for r in session.records if r.status == "justified_absence")
        unmarked = sum(1 for r in session.records if r.status == "unmarked")
        total    = len(co.active_enrollments)
        summaries[session.id] = {
            "present":   present,
            "absent":    absent,
            "justified": justified,
            "unmarked":  unmarked if unmarked else (total - present - absent - justified),
        }

    return render_template(
        "attendance/sessions.html",
        co=co,
        sessions=sessions,
        summaries=summaries,
        today=date.today().isoformat(),
    )


@attendance_bp.route("/<int:class_id>/attendance/new", methods=["POST"])
@login_required
def new_session(class_id):
    co = _get_class_or_404(class_id)

    date_str = request.form.get("date", "").strip()
    label    = request.form.get("label", "").strip()

    try:
        session_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        flash("Data inválida.", "danger")
        return redirect(url_for("attendance.sessions_list", class_id=class_id))

    # Check for duplicate date
    existing = db.session.query(AttendanceSession).filter_by(
        class_offering_id=class_id, date=session_date
    ).first()
    if existing:
        flash(f"Já existe uma aula em {session_date.strftime('%d/%m/%Y')}.", "warning")
        return redirect(url_for("attendance.sessions_list", class_id=class_id))

    session_obj = AttendanceSession(
        class_offering_id=class_id,
        date=session_date,
        label=label or None,
    )
    db.session.add(session_obj)
    db.session.flush()

    # Create 'unmarked' records for all active enrollments
    for enrollment in co.active_enrollments:
        record = AttendanceRecord(
            enrollment_id=enrollment.id,
            session_id=session_obj.id,
            status="unmarked",
        )
        db.session.add(record)

    db.session.commit()
    flash(f"Aula de {session_date.strftime('%d/%m/%Y')} criada.", "success")
    return redirect(url_for("attendance.session_detail", class_id=class_id, session_id=session_obj.id))


@attendance_bp.route("/<int:class_id>/attendance/<int:session_id>/delete", methods=["POST"])
@login_required
def delete_session(class_id, session_id):
    session_obj = db.session.get(AttendanceSession, session_id)
    if not session_obj or session_obj.class_offering_id != class_id:
        flash("Aula não encontrada.", "danger")
        return redirect(url_for("attendance.sessions_list", class_id=class_id))

    date_str = session_obj.date.strftime("%d/%m/%Y")
    db.session.delete(session_obj)
    db.session.commit()
    flash(f"Aula de {date_str} excluída.", "info")
    return redirect(url_for("attendance.sessions_list", class_id=class_id))


# ─────────────────────────────────────────────────────────────────────────────
# Per-session attendance marking (mobile-optimized)
# ─────────────────────────────────────────────────────────────────────────────

@attendance_bp.route("/<int:class_id>/attendance/<int:session_id>")
@login_required
def session_detail(class_id, session_id):
    co          = _get_class_or_404(class_id)
    session_obj = db.session.get(AttendanceSession, session_id)
    if not session_obj or session_obj.class_offering_id != class_id:
        from flask import abort
        abort(404)

    enrollments = sorted(co.active_enrollments, key=lambda e: e.student.nome)
    record_map  = {r.enrollment_id: r for r in session_obj.records}

    # Ensure records exist for all active enrollments
    for enrollment in enrollments:
        if enrollment.id not in record_map:
            r = AttendanceRecord(
                enrollment_id=enrollment.id,
                session_id=session_id,
                status="unmarked",
            )
            db.session.add(r)
            record_map[enrollment.id] = r
    db.session.commit()

    # Pre-compute accumulated frequency per enrollment so the template doesn't
    # need to call compute_student_freq as a filter (it's a global, not a filter).
    freq_map = {}
    for enrollment in enrollments:
        all_records = list(enrollment.attendance_records)
        freq_map[enrollment.id] = compute_student_freq(all_records)

    return render_template(
        "attendance/session.html",
        co=co,
        session=session_obj,
        enrollments=enrollments,
        record_map=record_map,
        freq_map=freq_map,
    )


@attendance_bp.route("/<int:class_id>/attendance/<int:session_id>/mark", methods=["POST"])
@login_required
def mark_attendance(class_id, session_id):
    """
    AJAX endpoint: mark a single student's attendance for a session.
    Payload: { enrollment_id, status }
    status: 'present' | 'absent' | 'justified_absence' | 'unmarked'
    """
    co          = _get_class_or_404(class_id)
    session_obj = db.session.get(AttendanceSession, session_id)
    if not session_obj or session_obj.class_offering_id != class_id:
        return jsonify(ok=False, error="Aula não encontrada"), 404

    data          = request.get_json(force=True)
    enrollment_id = int(data.get("enrollment_id", 0))
    new_status    = data.get("status", "unmarked")

    valid_statuses = {"unmarked", "present", "absent", "justified_absence"}
    if new_status not in valid_statuses:
        return jsonify(ok=False, error="Status inválido"), 400

    enrollment = db.session.get(Enrollment, enrollment_id)
    if not enrollment or enrollment.class_offering_id != class_id:
        return jsonify(ok=False, error="Aluno não encontrado"), 404

    record = db.session.query(AttendanceRecord).filter_by(
        enrollment_id=enrollment_id,
        session_id=session_id,
    ).first()

    if record:
        old_status = record.status
        prev_payload = {
            "entity":     "attendance_record",
            "id":         record.id,
            "old_status": old_status,
        }
        record.status = new_status
    else:
        old_status   = "unmarked"
        prev_payload = {
            "entity":     "attendance_record",
            "id":         None,
            "old_status": "unmarked",
        }
        record = AttendanceRecord(
            enrollment_id=enrollment_id,
            session_id=session_id,
            status=new_status,
        )
        db.session.add(record)
        db.session.flush()
        prev_payload["id"] = record.id

    _STATUS_PT = {
        "unmarked":          "/",
        "present":           "Presente",
        "absent":            "Ausente",
        "justified_absence": "Falta Justificada",
    }
    description = (
        f"Frequência {session_obj.date.strftime('%d/%m/%Y')} "
        f"para {enrollment.student.nome}: "
        f"{_STATUS_PT.get(old_status, old_status)} → {_STATUS_PT.get(new_status, new_status)}"
    )
    log_action(
        "attendance_mark",
        description,
        previous_value=prev_payload,
        class_offering_id=class_id,
        enrollment_id=enrollment_id,
    )
    db.session.commit()

    # Return updated freq for this student
    all_records = enrollment.attendance_records
    freq = compute_student_freq(all_records)

    return jsonify(
        ok=True,
        new_status=new_status,
        freq=round(freq, 1) if freq is not None else None,
    )
