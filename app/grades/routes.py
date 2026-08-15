"""
Grades blueprint — spreadsheet-style grade grid + assessment component management.
"""

import json
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, jsonify,
    redirect, url_for, flash,
)
from flask_login import login_required

from app import db
from app.models import (
    ClassOffering, AssessmentComponent, Grade, Enrollment,
)
from app.utils import (
    compute_student_average,
    build_class_grade_maps,
    format_grade,
    log_action,
    get_accessible_class,
)

grades_bp = Blueprint("grades", __name__, url_prefix="/class")


def _get_class_or_404(class_id):
    return get_accessible_class(class_id)


# ─────────────────────────────────────────────────────────────────────────────
# Grade grid view
# ─────────────────────────────────────────────────────────────────────────────

@grades_bp.route("/<int:class_id>/grades")
@login_required
def grade_grid(class_id):
    co = _get_class_or_404(class_id)

    components   = co.components
    enrollments  = sorted(co.active_enrollments, key=lambda e: e.seq or 0)
    grade_maps   = build_class_grade_maps(co)

    # Build per-component miss counters (for column footer)
    miss_counts = {}
    for comp in components:
        if comp.is_ps:
            continue
        justified   = 0
        unjustified = 0
        for enrollment in enrollments:
            g = grade_maps.get(enrollment.id, {}).get(comp.id)
            if g:
                if g.status == "missed_justified":
                    justified += 1
                elif g.status == "missed_unjustified":
                    unjustified += 1
        miss_counts[comp.id] = {"justified": justified, "unjustified": unjustified}

    # Pre-compute averages for the live column
    averages = {}
    for enrollment in enrollments:
        gmap = grade_maps.get(enrollment.id, {})
        averages[enrollment.id] = compute_student_average(enrollment, components, gmap)

    return render_template(
        "grades/grid.html",
        co=co,
        components=components,
        enrollments=enrollments,
        grade_maps=grade_maps,
        miss_counts=miss_counts,
        averages=averages,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AJAX: save a single grade cell
# ─────────────────────────────────────────────────────────────────────────────

@grades_bp.route("/<int:class_id>/grades/cell", methods=["POST"])
@login_required
def save_grade_cell(class_id):
    """
    AJAX endpoint called by grade_grid.js on every cell change.

    Payload (JSON):
      { enrollment_id, component_id, value, status }
      status: 'graded' | 'missed_justified' | 'missed_unjustified'
      value:  float or null (null when status != 'graded')

    Returns:
      { ok: true, avg: float|null, component_misses: {justified, unjustified} }
    """
    co = _get_class_or_404(class_id)
    data = request.get_json(force=True)

    enrollment_id = int(data.get("enrollment_id", 0))
    component_id  = int(data.get("component_id", 0))
    new_status    = data.get("status", "graded")
    raw_value     = data.get("value")

    enrollment = db.session.get(Enrollment, enrollment_id)
    component  = db.session.get(AssessmentComponent, component_id)

    if not enrollment or not component:
        return jsonify(ok=False, error="Não encontrado"), 404

    if enrollment.class_offering_id != class_id or component.class_offering_id != class_id:
        return jsonify(ok=False, error="Acesso negado"), 403

    # Parse value
    try:
        new_value = float(raw_value) if raw_value is not None and raw_value != "" else None
        if new_value is not None:
            new_value = max(0.0, min(component.max_value, new_value))
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Valor inválido"), 400

    if new_status != "graded":
        new_value = None

    # Get or create grade row
    grade = db.session.query(Grade).filter_by(
        enrollment_id=enrollment_id,
        component_id=component_id,
    ).first()

    if grade:
        old_value  = grade.value
        old_status = grade.status
        prev_payload = {
            "entity":     "grade",
            "id":         grade.id,
            "old_value":  old_value,
            "old_status": old_status,
        }
        grade.value  = new_value
        grade.status = new_status
    else:
        old_value  = None
        old_status = None
        prev_payload = {
            "entity":     "grade",
            "id":         None,
            "old_value":  None,
            "old_status": None,
        }
        grade = Grade(
            enrollment_id=enrollment_id,
            component_id=component_id,
            value=new_value,
            status=new_status,
        )
        db.session.add(grade)

    db.session.flush()
    prev_payload["id"] = grade.id  # fill in if new

    # Build human-readable description
    student_name = enrollment.student.nome
    comp_name    = component.name
    old_str = format_grade(old_value, component.scale_type) if old_status == "graded" else (old_status or "—")
    new_str = format_grade(new_value, component.scale_type) if new_status == "graded" else new_status

    description = f"Nota {comp_name} para {student_name}: {old_str} → {new_str}"
    log_action(
        "grade_edit",
        description,
        previous_value=prev_payload,
        class_offering_id=class_id,
        enrollment_id=enrollment_id,
    )
    db.session.commit()

    # Recompute average for this student
    all_comps = co.components
    gmap      = {g.component_id: g for g in enrollment.grades}
    avg       = compute_student_average(enrollment, all_comps, gmap)

    # Recompute miss counts for this component
    justified   = 0
    unjustified = 0
    for enr in co.active_enrollments:
        g = db.session.query(Grade).filter_by(
            enrollment_id=enr.id, component_id=component_id
        ).first()
        if g:
            if g.status == "missed_justified":
                justified += 1
            elif g.status == "missed_unjustified":
                unjustified += 1

    return jsonify(
        ok=True,
        avg=round(avg, 2) if avg is not None else None,
        component_misses={"justified": justified, "unjustified": unjustified},
    )


# ─────────────────────────────────────────────────────────────────────────────
# AJAX: get recent action logs for undo panel
# ─────────────────────────────────────────────────────────────────────────────

@grades_bp.route("/<int:class_id>/grades/recent-actions")
@login_required
def recent_actions(class_id):
    from app.models import ActionLog
    logs = (
        db.session.query(ActionLog)
        .filter_by(class_offering_id=class_id, reverted=False)
        .order_by(ActionLog.timestamp.desc())
        .limit(20)
        .all()
    )
    return jsonify([
        {
            "id":          l.id,
            "timestamp":   l.timestamp.strftime("%d/%m %H:%M") if l.timestamp else "",
            "description": l.description,
            "reverted":    l.reverted,
        }
        for l in logs
    ])
