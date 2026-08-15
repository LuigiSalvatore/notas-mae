from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from pathlib import Path
import bcrypt

from app import db
from app.models import Teacher

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").encode()
        remember = bool(request.form.get("remember_me"))

        teacher = db.session.query(Teacher).filter_by(username=username).first()
        if teacher and bcrypt.checkpw(password, teacher.password_hash.encode()):
            login_user(teacher, remember=remember,
                       duration=None if not remember else
                       __import__("datetime").timedelta(days=teacher.remember_me_days))

            # Delete first_run credentials file on first successful login
            if teacher.first_run:
                teacher.first_run = False
                db.session.commit()
                creds = Path(current_app.instance_path).parent / "first_run_credentials.txt"
                if creds.exists():
                    creds.unlink()

            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        else:
            error = "Usuário ou senha incorretos."

    return render_template("auth/login.html", error=error)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    current_pw  = request.form.get("current_password", "").encode()
    new_pw      = request.form.get("new_password", "").strip()
    confirm_pw  = request.form.get("confirm_password", "").strip()

    teacher = current_user

    if not bcrypt.checkpw(current_pw, teacher.password_hash.encode()):
        flash("Senha atual incorreta.", "danger")
    elif len(new_pw) < 8:
        flash("A nova senha deve ter ao menos 8 caracteres.", "danger")
    elif new_pw != confirm_pw:
        flash("As senhas não coincidem.", "danger")
    else:
        teacher.password_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        db.session.commit()
        flash("Senha alterada com sucesso.", "success")

    return redirect(url_for("settings.index"))
