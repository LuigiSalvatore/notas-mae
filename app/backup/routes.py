"""
Backup blueprint — AJAX trigger + manual backup routes.
"""

from flask import Blueprint, jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.models import BackupJob, ClassOffering, ClassTeacher
from app.utils import get_accessible_class

backup_bp = Blueprint("backup", __name__, url_prefix="/backup")


@backup_bp.route("/trigger/<int:class_id>", methods=["POST"])
@login_required
def trigger_backup(class_id):
    """
    AJAX endpoint called by grade_grid.js via navigator.sendBeacon()
    when the user leaves the grade grid after making changes.
    """
    get_accessible_class(class_id)
    from app.backup.jobs import run_class_backup
    try:
        job = run_class_backup(class_id, trigger_reason="grade_session_end")
        return jsonify(ok=True, job_id=job.id, status=job.status)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 500


@backup_bp.route("/manual/<int:class_id>", methods=["POST"])
@login_required
def manual_backup(class_id):
    """Manual 'Fazer backup agora' button."""
    get_accessible_class(class_id)
    from app.backup.jobs import run_class_backup
    try:
        job = run_class_backup(class_id, trigger_reason="manual")
        if job.status == "sent":
            flash("Backup enviado com sucesso por email.", "success")
        else:
            flash(f"Backup gerado mas não enviado: {job.error_message}", "warning")
    except Exception as exc:
        flash(f"Erro ao gerar backup: {exc}", "danger")

    return redirect(url_for("students.list_students", class_id=class_id))


@backup_bp.route("/jobs")
@login_required
def job_list():
    """List recent backup jobs (for settings page)."""
    if current_user.is_super_admin:
        query = db.session.query(BackupJob)
    else:
        # Get class IDs user has access to
        accessible_class_ids = (
            db.session.query(ClassTeacher.class_offering_id)
            .filter(ClassTeacher.teacher_id == current_user.id)
            .all()
        )
        class_ids = [r[0] for r in accessible_class_ids]
        query = db.session.query(BackupJob).filter(
            (BackupJob.class_offering_id.in_(class_ids)) | (BackupJob.class_offering_id == None)
        )

    jobs = (
        query.order_by(BackupJob.timestamp.desc())
        .limit(50)
        .all()
    )
    return jsonify([
        {
            "id":         j.id,
            "timestamp":  j.timestamp.strftime("%d/%m/%Y %H:%M") if j.timestamp else "",
            "class":      j.class_offering.subject_name if j.class_offering else "Sistema",
            "trigger":    j.trigger_reason,
            "status":     j.status,
            "size_kb":    round(j.total_size_bytes / 1024, 1) if j.total_size_bytes else 0,
            "error":      j.error_message or "",
        }
        for j in jobs
    ])
