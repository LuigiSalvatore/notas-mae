"""
Flask application factory.

First-run behaviour:
  If no Teacher row exists, a random password is generated, written to
  first_run_credentials.txt in the project root, and printed to stdout.
  The file is deleted automatically the first time the teacher logs in.
"""

import os
import secrets
import string
from pathlib import Path

import bcrypt
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db      = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf    = CSRFProtect()


def create_app(config_object=None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # ── Default configuration ─────────────────────────────────────────────
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{Path(app.instance_path) / 'chamada.db'}",
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        WTF_CSRF_TIME_LIMIT=None,   # never expire CSRF tokens mid-session
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,   # 32 MB upload limit
        BACKUP_DIR=str(Path(app.instance_path).parent / "backups"),
        FERNET_KEY=os.environ.get("FERNET_KEY", None),
    )

    # ── Load instance config (overrides above) ────────────────────────────
    if config_object:
        if isinstance(config_object, dict):
            app.config.from_mapping(config_object)
        else:
            app.config.from_object(config_object)
    else:
        app.config.from_pyfile("config.py", silent=True)


    # Ensure instance + backup directories exist
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["BACKUP_DIR"], exist_ok=True)
    os.makedirs(os.path.join(app.config["BACKUP_DIR"], "class"), exist_ok=True)
    os.makedirs(os.path.join(app.config["BACKUP_DIR"], "db"),    exist_ok=True)

    # If no FERNET_KEY configured, generate + persist one
    _ensure_fernet_key(app)

    # ── Extensions ────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Por favor, faça login para continuar."
    login_manager.login_message_category = "info"

    # ── User loader ───────────────────────────────────────────────────────
    from app.models import Teacher

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Teacher, int(user_id))

    # ── Blueprints ────────────────────────────────────────────────────────
    from app.auth.routes      import auth_bp
    from app.dashboard.routes import dashboard_bp
    from app.classes.routes   import classes_bp
    from app.grades.routes    import grades_bp
    from app.attendance.routes import attendance_bp
    from app.students.routes  import students_bp
    from app.audit.routes     import audit_bp
    from app.backup.routes    import backup_bp
    from app.settings.routes  import settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(classes_bp)
    app.register_blueprint(grades_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(settings_bp)

    # ── Jinja2 globals / filters ──────────────────────────────────────────
    from app.utils import (
        format_grade, STATUS_LABELS, STATUS_COLORS,
        compute_student_freq, compute_student_average, compute_student_status,
    )

    app.jinja_env.globals.update(
        STATUS_LABELS=STATUS_LABELS,
        STATUS_COLORS=STATUS_COLORS,
    )
    app.jinja_env.filters["format_grade"] = format_grade

    @app.template_filter("pt_date")
    def pt_date(value):
        """Format a date as DD/MM/YYYY."""
        if value is None:
            return "—"
        if hasattr(value, "strftime"):
            return value.strftime("%d/%m/%Y")
        return str(value)

    @app.template_filter("pt_datetime")
    def pt_datetime(value):
        """Format a datetime as DD/MM HH:MM."""
        if value is None:
            return "—"
        if hasattr(value, "strftime"):
            return value.strftime("%d/%m %H:%M")
        return str(value)

    # ── First-run setup ───────────────────────────────────────────────────
    with app.app_context():
        _migrate_database(app)
        _first_run_setup(app)

    return app


def _migrate_database(app: Flask) -> None:
    """Dynamically add new tables and columns to support multi-tenancy in SQLite."""
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    
    # 1. db.create_all() creates any completely new tables (class_teacher, system_setting)
    db.create_all()
    
    # 2. Add columns to existing tables using raw SQLite queries if missing
    conn = db.engine.raw_connection()
    try:
        cursor = conn.cursor()
        
        # Check teacher table
        columns = [col['name'] for col in inspector.get_columns('teacher')]
        if 'display_name' not in columns:
            cursor.execute("ALTER TABLE teacher ADD COLUMN display_name VARCHAR(128)")
        if 'is_super_admin' not in columns:
            cursor.execute("ALTER TABLE teacher ADD COLUMN is_super_admin BOOLEAN DEFAULT 0")
        if 'is_active' not in columns:
            cursor.execute("ALTER TABLE teacher ADD COLUMN is_active BOOLEAN DEFAULT 1")
            
        # Check class_offering table
        columns = [col['name'] for col in inspector.get_columns('class_offering')]
        if 'owner_id' not in columns:
            cursor.execute("ALTER TABLE class_offering ADD COLUMN owner_id INTEGER REFERENCES teacher(id) ON DELETE SET NULL")
            
        # Check action_log table
        columns = [col['name'] for col in inspector.get_columns('action_log')]
        if 'teacher_id' not in columns:
            cursor.execute("ALTER TABLE action_log ADD COLUMN teacher_id INTEGER REFERENCES teacher(id)")
            
        conn.commit()
    except Exception as e:
        app.logger.error(f"Migration error: {e}")
        conn.rollback()
    finally:
        conn.close()


def _ensure_fernet_key(app: Flask) -> None:
    """Generate and persist a Fernet key if none exists."""
    if app.config.get("FERNET_KEY"):
        return
    config_path = Path(app.instance_path) / "config.py"
    if config_path.exists():
        return   # already written
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    app.config["FERNET_KEY"] = key
    # Write minimal instance config
    config_path.write_text(
        f"# Auto-generated — do not commit this file.\n"
        f"FERNET_KEY = {key!r}\n"
        f"SECRET_KEY = {secrets.token_hex(32)!r}\n",
        encoding="utf-8",
    )


def _first_run_setup(app: Flask) -> None:
    """Create the default Teacher account on first run and migration link."""
    from app.models import Teacher, ClassOffering, ClassTeacher

    admin = db.session.query(Teacher).filter_by(username="admin").first()
    if not admin:
        # Check if there's any teacher, if so use the first one
        admin = db.session.query(Teacher).first()

    creds_path = Path(app.instance_path).parent / "first_run_credentials.txt"
    
    # If admin exists but has never logged in (first_run is True) and the file is missing, recreate it!
    regenerate_creds = False
    if admin and admin.first_run and not creds_path.exists():
        regenerate_creds = True

    if not admin or regenerate_creds:
        # Generate a random password
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(16))
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        if not admin:
            admin = Teacher(
                username="admin",
                display_name="Administrador",
                password_hash=password_hash,
                is_super_admin=True,
                first_run=True,
            )
            db.session.add(admin)
        else:
            admin.password_hash = password_hash
            admin.is_super_admin = True
            
        from sqlalchemy.exc import IntegrityError
        try:
            db.session.commit()
        except IntegrityError:
            # Another Gunicorn worker beat us to creating the admin user.
            # Rollback and exit setup for this worker.
            db.session.rollback()
            return

        # Write credentials file
        creds_path.write_text(
            f"=== Plataforma de Notas e Frequência — Primeiro Acesso ===\n"
            f"Usuário: admin\n"
            f"Senha:   {password}\n\n"
            f"Altere a senha após o primeiro login em Configurações.\n"
            f"Este arquivo será excluído automaticamente após o login.\n",
            encoding="utf-8",
        )
        print(f"\n{'='*55}")
        print(f"  PRIMEIRO ACESSO")
        print(f"  Usuário: admin")
        print(f"  Senha:   {password}")
        print(f"  Credenciais salvas em: {creds_path}")
        print(f"{'='*55}\n")
    else:
        # Make sure admin is super_admin and active
        if not admin.is_super_admin:
            admin.is_super_admin = True
        if not admin.display_name:
            admin.display_name = "Administrador"
        db.session.commit()

    # Link any unowned ClassOfferings to the admin
    unowned_classes = db.session.query(ClassOffering).filter_by(owner_id=None).all()
    for co in unowned_classes:
        co.owner_id = admin.id
        # Also ensure ClassTeacher row exists for owner
        exists = db.session.query(ClassTeacher).filter_by(class_offering_id=co.id, teacher_id=admin.id).first()
        if not exists:
            db.session.add(ClassTeacher(class_offering_id=co.id, teacher_id=admin.id, role="owner"))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()

