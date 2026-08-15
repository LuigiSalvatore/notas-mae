"""
Backup jobs — per-class email backup + FIFO 200MB cap + nightly DB backup.

Called from:
  - backup/routes.py (AJAX trigger + manual)
  - A cron/systemd timer for nightly DB backup
"""

import os
import smtplib
import ssl
import shutil
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

from flask import current_app

from app import db
from app.models import BackupJob, ClassOffering, Teacher
from app.classes.export import export_class_xlsx, export_class_pdf

# Hard cap for per-class backup files (bytes)
BACKUP_CAP_BYTES = 200 * 1024 * 1024   # 200 MB


# ─────────────────────────────────────────────────────────────────────────────
# FIFO cap enforcement
# ─────────────────────────────────────────────────────────────────────────────

def enforce_backup_cap(backup_dir: str) -> None:
    """
    Delete oldest backup files in backup_dir until total size < BACKUP_CAP_BYTES.
    Files are deleted in order of modification time (oldest first).
    """
    files = sorted(
        [f for f in Path(backup_dir).iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
    )
    total = sum(f.stat().st_size for f in files)

    while total > BACKUP_CAP_BYTES and files:
        oldest = files.pop(0)
        size   = oldest.stat().st_size
        oldest.unlink(missing_ok=True)
        # Mark the corresponding BackupJob as having its file deleted
        _mark_file_deleted(str(oldest))
        total -= size


def _mark_file_deleted(path: str) -> None:
    """Update BackupJob rows whose file path matches the deleted file."""
    jobs = db.session.query(BackupJob).filter(
        (BackupJob.xlsx_path == path) | (BackupJob.pdf_path == path)
    ).all()
    for job in jobs:
        if job.xlsx_path == path:
            job.xlsx_path = None
        if job.pdf_path == path:
            job.pdf_path = None
    if jobs:
        db.session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Per-class backup
# ─────────────────────────────────────────────────────────────────────────────

def run_class_backup(class_id: int, trigger_reason: str = "manual") -> BackupJob:
    """
    Generate xlsx + pdf for a class, save locally, email them, enforce 200MB cap.
    Returns the BackupJob row (committed).
    """
    co      = db.session.get(ClassOffering, class_id)
    teacher = db.session.query(Teacher).first()

    if co is None or teacher is None:
        raise ValueError("Class or teacher not found")

    backup_dir = os.path.join(current_app.config["BACKUP_DIR"], "class")
    os.makedirs(backup_dir, exist_ok=True)

    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = (
        f"{co.subject_name[:20]}_{co.turma}_{co.periodo_letivo}"
        .replace("/", "-").replace(" ", "_")
    )
    xlsx_filename = f"{safe_name}_{ts_str}.xlsx"
    pdf_filename  = f"{safe_name}_{ts_str}.pdf"
    xlsx_path     = os.path.join(backup_dir, xlsx_filename)
    pdf_path      = os.path.join(backup_dir, pdf_filename)

    job = BackupJob(
        trigger_reason=trigger_reason,
        class_offering_id=class_id,
        status="pending",
    )
    db.session.add(job)
    db.session.commit()

    try:
        # Generate Excel
        xlsx_data = export_class_xlsx(co)
        with open(xlsx_path, "wb") as f:
            f.write(xlsx_data)

        # Generate PDF (optional — skip if WeasyPrint not installed)
        pdf_data = None
        try:
            pdf_data = export_class_pdf(co)
            with open(pdf_path, "wb") as f:
                f.write(pdf_data)
        except (RuntimeError, ImportError):
            pdf_path = None   # WeasyPrint not available — skip PDF

        total_size = os.path.getsize(xlsx_path)
        if pdf_path and os.path.exists(pdf_path):
            total_size += os.path.getsize(pdf_path)

        job.xlsx_path        = xlsx_path
        job.pdf_path         = pdf_path
        job.total_size_bytes = total_size
        db.session.commit()

        # Send email
        if teacher.backup_email and teacher.smtp_host:
            _send_backup_email(teacher, co, xlsx_path, pdf_path)

        # Enforce 200MB cap
        enforce_backup_cap(backup_dir)

        job.status = "sent" if teacher.backup_email else "sent"
        db.session.commit()

    except Exception as exc:
        job.status        = "failed"
        job.error_message = str(exc)
        db.session.commit()
        raise

    return job


# ─────────────────────────────────────────────────────────────────────────────
# Email delivery
# ─────────────────────────────────────────────────────────────────────────────

def _decrypt_smtp_password(teacher: Teacher) -> str:
    """Decrypt the stored SMTP password using the Fernet key."""
    if not teacher.smtp_pass_enc:
        return ""
    try:
        from cryptography.fernet import Fernet
        key = current_app.config.get("FERNET_KEY", "").encode()
        if not key:
            return ""
        f = Fernet(key)
        return f.decrypt(teacher.smtp_pass_enc.encode()).decode()
    except Exception:
        return ""


def _send_backup_email(teacher: Teacher, co: ClassOffering, xlsx_path: str, pdf_path: str | None) -> None:
    """Send backup files via SMTP to the configured backup_email address."""
    password = _decrypt_smtp_password(teacher)

    msg = MIMEMultipart()
    msg["From"]    = teacher.smtp_from or teacher.smtp_user
    msg["To"]      = teacher.backup_email
    msg["Subject"] = (
        f"Backup — {co.subject_name} {co.turma} "
        f"{co.periodo_letivo} — "
        f"{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC"
    )

    body = (
        f"Backup automático da turma:\n\n"
        f"  Disciplina: {co.subject_name}\n"
        f"  Turma:      {co.turma}\n"
        f"  Período:    {co.periodo_letivo}\n"
        f"  Gerado em:  {datetime.now(timezone.utc).strftime('%d/%m/%Y às %H:%M')} UTC\n\n"
        f"Arquivos em anexo:\n"
        f"  - Planilha Excel (.xlsx)\n"
        + (f"  - PDF de notas (.pdf)\n" if pdf_path else "")
        + f"\nEste email foi gerado automaticamente pela Plataforma de Notas e Frequência."
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for filepath in [xlsx_path, pdf_path]:
        if not filepath or not os.path.exists(filepath):
            continue
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(filepath)}"',
        )
        msg.attach(part)

    ctx = ssl.create_default_context()
    if teacher.smtp_use_tls:
        with smtplib.SMTP_SSL(teacher.smtp_host, teacher.smtp_port or 465, context=ctx) as server:
            server.login(teacher.smtp_user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(teacher.smtp_host, teacher.smtp_port or 587) as server:
            server.starttls(context=ctx)
            server.login(teacher.smtp_user, password)
            server.send_message(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Nightly DB backup (called by systemd timer / cron)
# ─────────────────────────────────────────────────────────────────────────────

def nightly_db_backup(app) -> None:
    """
    Copy the SQLite database file to backups/db/ with a date-stamped filename.
    Keeps last 30 copies. No 200MB cap (it's the teacher's server storage).
    """
    with app.app_context():
        db_uri  = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        # Extract file path from sqlite:///...
        if not db_uri.startswith("sqlite:///"):
            return
        db_path = db_uri.replace("sqlite:///", "")
        if not os.path.exists(db_path):
            return

        db_backup_dir = os.path.join(app.config["BACKUP_DIR"], "db")
        os.makedirs(db_backup_dir, exist_ok=True)

        ts_str  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dst     = os.path.join(db_backup_dir, f"chamada_{ts_str}.db")
        shutil.copy2(db_path, dst)

        # Keep only the 30 most recent
        backups = sorted(Path(db_backup_dir).glob("chamada_*.db"))
        while len(backups) > 30:
            backups.pop(0).unlink(missing_ok=True)
