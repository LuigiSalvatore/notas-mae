"""
Settings blueprint — SMTP configuration, backup email destination, defaults, database export.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, current_app
from flask_login import login_required, current_user
from cryptography.fernet import Fernet
import io

from app import db
from app.models import Teacher, ClassOffering
from app.utils import log_action
from app.classes.export import export_full_system_xlsx

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

@settings_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    teacher = current_user
    fernet_key = current_app.config.get("FERNET_KEY")
    f = Fernet(fernet_key.encode()) if fernet_key else None

    # Decrypt password for display in field if available
    decrypted_password = ""
    if teacher.smtp_pass_enc and f:
        try:
            decrypted_password = f.decrypt(teacher.smtp_pass_enc.encode()).decode()
        except Exception:
            pass

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_profile":
            teacher.display_name = request.form.get("display_name", "").strip()
            db.session.commit()
            flash("Perfil atualizado.", "success")
            return redirect(url_for("settings.index"))

        elif action == "save_smtp":
            teacher.smtp_host = request.form.get("smtp_host", "").strip()
            try:
                teacher.smtp_port = int(request.form.get("smtp_port", "465").strip())
            except ValueError:
                teacher.smtp_port = 465

            teacher.smtp_user = request.form.get("smtp_user", "").strip()
            teacher.smtp_from = request.form.get("smtp_from", "").strip()
            teacher.smtp_use_tls = request.form.get("smtp_use_tls") == "1"
            teacher.backup_email = request.form.get("backup_email", "").strip()

            password = request.form.get("smtp_password", "")
            if password and f:
                teacher.smtp_pass_enc = f.encrypt(password.encode()).decode()
            elif not password:
                teacher.smtp_pass_enc = None

            db.session.commit()
            log_action("settings_changed", "Configurações de SMTP e backup salvas")
            db.session.commit()
            flash("Configurações de SMTP salvas com sucesso.", "success")
            return redirect(url_for("settings.index"))

        elif action == "save_defaults":
            try:
                teacher.default_min_attendance_pct = float(request.form.get("default_min_attendance_pct", "75.0").strip())
                teacher.default_min_avg_grade = float(request.form.get("default_min_avg_grade", "7.0").strip())
            except ValueError:
                flash("Valores padrão inválidos.", "danger")
                return redirect(url_for("settings.index"))

            db.session.commit()
            log_action("settings_changed", "Configurações de valores padrão salvas")
            db.session.commit()
            flash("Valores padrão atualizados.", "success")
            return redirect(url_for("settings.index"))

        elif action == "test_smtp":
            # Quick test of SMTP settings by sending a test email
            test_email = request.form.get("test_email", "").strip() or teacher.backup_email
            if not test_email:
                flash("E-mail de teste ou e-mail de destino do backup não configurado.", "danger")
                return redirect(url_for("settings.index"))

            from email.mime.text import MIMEText
            import smtplib
            import ssl

            msg = MIMEText("Este é um e-mail de teste da Plataforma de Notas e Frequência.")
            msg["Subject"] = "Plataforma de Notas e Frequência — Teste de Configuração"
            msg["From"] = teacher.smtp_from or teacher.smtp_user
            msg["To"] = test_email

            password = ""
            if teacher.smtp_pass_enc and f:
                try:
                    password = f.decrypt(teacher.smtp_pass_enc.encode()).decode()
                except Exception:
                    pass

            try:
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
                flash(f"E-mail de teste enviado com sucesso para {test_email}!", "success")
            except Exception as e:
                flash(f"Falha ao enviar e-mail de teste: {e}", "danger")

            return redirect(url_for("settings.index"))

        elif action == "add_teacher":
            if not current_user.is_super_admin:
                flash("Apenas administradores podem gerenciar outros professores.", "danger")
                return redirect(url_for("settings.index"))
            username = request.form.get("username", "").strip()
            display_name = request.form.get("display_name", "").strip()
            password = request.form.get("password", "").strip()
            if not username or not password:
                flash("Nome de usuário e senha são obrigatórios.", "danger")
                return redirect(url_for("settings.index"))
            existing = db.session.query(Teacher).filter_by(username=username).first()
            if existing:
                flash("Já existe um professor com este nome de usuário.", "danger")
                return redirect(url_for("settings.index"))

            import bcrypt
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            new_t = Teacher(
                username=username,
                display_name=display_name or username,
                password_hash=password_hash,
                is_active=True,
                is_super_admin=request.form.get("is_super_admin") == "1",
                first_run=False
            )
            db.session.add(new_t)
            db.session.commit()
            flash(f"Professor '{username}' adicionado com sucesso.", "success")
            return redirect(url_for("settings.index"))

        elif action == "toggle_active":
            if not current_user.is_super_admin:
                flash("Apenas administradores podem gerenciar outros professores.", "danger")
                return redirect(url_for("settings.index"))
            t_id = int(request.form.get("teacher_id", 0))
            t = db.session.get(Teacher, t_id)
            if t:
                if t.id == current_user.id:
                    flash("Você não pode desativar seu próprio usuário.", "danger")
                else:
                    t.is_active = not t.is_active
                    db.session.commit()
                    status_str = "ativado" if t.is_active else "desativado"
                    flash(f"Professor '{t.username}' foi {status_str}.", "info")
            return redirect(url_for("settings.index"))

        elif action == "toggle_super_admin":
            if not current_user.is_super_admin:
                flash("Apenas administradores podem gerenciar outros professores.", "danger")
                return redirect(url_for("settings.index"))
            t_id = int(request.form.get("teacher_id", 0))
            t = db.session.get(Teacher, t_id)
            if t:
                if t.id == current_user.id:
                    flash("Você não pode alterar seu próprio privilégio de administrador.", "danger")
                else:
                    t.is_super_admin = not t.is_super_admin
                    db.session.commit()
                    admin_str = "promovido a administrador" if t.is_super_admin else "removido dos administradores"
                    flash(f"Professor '{t.username}' foi {admin_str}.", "info")
            return redirect(url_for("settings.index"))

        elif action == "reset_teacher_password":
            if not current_user.is_super_admin:
                flash("Apenas administradores podem gerenciar outros professores.", "danger")
                return redirect(url_for("settings.index"))
            t_id = int(request.form.get("teacher_id", 0))
            new_password = request.form.get("new_password", "").strip()
            if not new_password:
                flash("A senha não pode estar em branco.", "danger")
                return redirect(url_for("settings.index"))
            t = db.session.get(Teacher, t_id)
            if t:
                import bcrypt
                t.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                db.session.commit()
                flash(f"Senha do professor '{t.username}' alterada com sucesso.", "success")
            return redirect(url_for("settings.index"))

    all_teachers = []
    if teacher.is_super_admin:
        all_teachers = db.session.query(Teacher).order_by(Teacher.username).all()

    return render_template(
        "settings/index.html",
        teacher=teacher,
        decrypted_password=decrypted_password,
        all_teachers=all_teachers
    )

@settings_bp.route("/__init__")
def init():
    return ""
