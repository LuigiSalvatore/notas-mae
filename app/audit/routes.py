"""
Audit blueprint — action log view + undo AJAX endpoint.
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required

from app import db
from app.models import ClassOffering, ActionLog, Grade, AttendanceRecord, Enrollment

from app.utils import get_accessible_class

audit_bp = Blueprint("audit", __name__, url_prefix="/class")


def _get_class_or_404(class_id):
    return get_accessible_class(class_id)


# ─────────────────────────────────────────────────────────────────────────────
# Audit log view
# ─────────────────────────────────────────────────────────────────────────────

@audit_bp.route("/<int:class_id>/log")
@login_required
def action_log(class_id):
    co = _get_class_or_404(class_id)

    action_type_filter = request.args.get("type", "")
    date_from          = request.args.get("date_from", "")
    date_to            = request.args.get("date_to", "")

    query = (
        db.session.query(ActionLog)
        .filter_by(class_offering_id=class_id)
        .order_by(ActionLog.timestamp.desc())
    )

    if action_type_filter:
        query = query.filter_by(action_type=action_type_filter)

    if date_from:
        try:
            from datetime import datetime
            query = query.filter(ActionLog.timestamp >= datetime.fromisoformat(date_from))
        except ValueError:
            pass

    if date_to:
        try:
            from datetime import datetime
            query = query.filter(ActionLog.timestamp <= datetime.fromisoformat(date_to + "T23:59:59"))
        except ValueError:
            pass

    logs = query.limit(200).all()

    action_types = [
        ("grade_edit",        "Nota"),
        ("attendance_mark",   "Frequência"),
        ("student_added",     "Aluno adicionado"),
        ("student_removed",   "Aluno removido"),
        ("roster_import",     "Importação"),
        ("component_added",   "Componente"),
        ("settings_changed",  "Configurações"),
    ]

    return render_template(
        "audit/log.html",
        co=co,
        logs=logs,
        action_types=action_types,
        active_type=action_type_filter,
        date_from=date_from,
        date_to=date_to,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Undo AJAX endpoint
# ─────────────────────────────────────────────────────────────────────────────

@audit_bp.route("/<int:class_id>/undo/<int:action_id>", methods=["POST"])
@login_required
def undo_action(class_id, action_id):
    """
    Revert a single ActionLog entry using its previous_value payload.

    Supported entities:
      - grade            → restore value + status
      - attendance_record → restore status
      - enrollment       → restore status (undo student_removed / student_added)
    """
    log_entry = db.session.get(ActionLog, action_id)
    if not log_entry or log_entry.class_offering_id != class_id:
        return jsonify(ok=False, error="Ação não encontrada"), 404

    if log_entry.reverted:
        return jsonify(ok=False, error="Esta ação já foi desfeita"), 409

    prev = log_entry.previous_value
    if not prev:
        return jsonify(ok=False, error="Sem dados para desfazer"), 400

    entity = prev.get("entity")
    eid    = prev.get("id")

    try:
        if entity == "grade":
            grade = db.session.get(Grade, eid)
            if grade:
                grade.value  = prev.get("old_value")
                grade.status = prev.get("old_status") or "graded"
            else:
                return jsonify(ok=False, error="Nota não encontrada"), 404

        elif entity == "attendance_record":
            record = db.session.get(AttendanceRecord, eid)
            if record:
                record.status = prev.get("old_status") or "unmarked"
            else:
                return jsonify(ok=False, error="Registro não encontrado"), 404

        elif entity == "enrollment":
            enrollment = db.session.get(Enrollment, eid)
            if enrollment:
                enrollment.status = prev.get("old_status") or "active"
            else:
                return jsonify(ok=False, error="Matrícula não encontrada"), 404

        else:
            return jsonify(ok=False, error=f"Tipo '{entity}' não suportado para desfazer"), 400

        log_entry.reverted = True
        db.session.commit()
        return jsonify(ok=True, message="Ação desfeita com sucesso.")

    except Exception as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 500
