"""
SQLAlchemy models for Plataforma de Notas e Frequência.

Table overview:
  teacher              — user credentials + SMTP config + role (super_admin / normal)
  class_teacher        — many-to-many join: which teachers can access which classes
  class_offering       — one card per subject/turma/período
  class_settings       — 1:1 per class_offering (thresholds, aggregation)
  student              — canonical student record (cartao is the business key)
  enrollment           — student ↔ class_offering join; keeps removed students
  assessment_component — grade columns per class; PS components link via covers_component_id
  grade                — enrollment × component → value + status
  attendance_session   — a single class date
  attendance_record    — enrollment × session → status
  action_log           — audit trail + undo payload (tracks which teacher acted)
  backup_job           — per-class email backup log
  system_setting       — key-value store for server-wide toggles
"""

from datetime import datetime, timezone
from app import db


def _now():
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Teacher
# ─────────────────────────────────────────────────────────────────────────────

class Teacher(db.Model):
    __tablename__ = "teacher"

    id               = db.Column(db.Integer, primary_key=True)
    username         = db.Column(db.String(80), unique=True, nullable=False)
    display_name     = db.Column(db.String(128))                  # shown in UI and audit log
    password_hash    = db.Column(db.String(256), nullable=False)
    email            = db.Column(db.String(256))
    # Roles
    is_super_admin   = db.Column(db.Boolean, default=False)       # can see/edit ALL classes
    is_active        = db.Column(db.Boolean, default=True)        # deactivated users cannot login
    # SMTP config — smtp_pass_enc is Fernet-encrypted at rest
    smtp_host        = db.Column(db.String(256))
    smtp_port        = db.Column(db.Integer, default=465)
    smtp_user        = db.Column(db.String(256))
    smtp_pass_enc    = db.Column(db.String(512))                   # encrypted
    smtp_from        = db.Column(db.String(256))
    smtp_use_tls     = db.Column(db.Boolean, default=True)        # True=SSL, False=STARTTLS
    backup_email     = db.Column(db.String(256))
    remember_me_days = db.Column(db.Integer, default=30)
    first_run        = db.Column(db.Boolean, default=True)        # cleared after first login
    # Teacher-level defaults applied to newly created classes
    default_min_attendance_pct = db.Column(db.Float, default=75.0)
    default_min_avg_grade      = db.Column(db.Float, default=7.0)

    # Relationships
    class_memberships = db.relationship("ClassTeacher", back_populates="teacher",
                                         cascade="all, delete-orphan")

    # Flask-Login interface
    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    # Note: is_active is a real column, not a property — Flask-Login reads it directly.

    def __repr__(self):
        return f"<Teacher {self.username!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# ClassTeacher  (many-to-many access table)
# ─────────────────────────────────────────────────────────────────────────────

class ClassTeacher(db.Model):
    __tablename__ = "class_teacher"

    id                = db.Column(db.Integer, primary_key=True)
    class_offering_id = db.Column(db.Integer,
                                   db.ForeignKey("class_offering.id", ondelete="CASCADE"),
                                   nullable=False)
    teacher_id        = db.Column(db.Integer,
                                   db.ForeignKey("teacher.id", ondelete="CASCADE"),
                                   nullable=False)
    # 'owner' — creator, can manage members and archive
    # 'collaborator' — full read/write on grades, attendance, students
    role              = db.Column(db.String(16), default="collaborator", nullable=False)
    added_at          = db.Column(db.DateTime, default=_now)

    teacher       = db.relationship("Teacher",       back_populates="class_memberships")
    class_offering = db.relationship("ClassOffering", back_populates="teacher_memberships")

    __table_args__ = (
        db.UniqueConstraint("class_offering_id", "teacher_id", name="uq_class_teacher"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ClassOffering
# ─────────────────────────────────────────────────────────────────────────────

class ClassOffering(db.Model):
    __tablename__ = "class_offering"

    id             = db.Column(db.Integer, primary_key=True)
    subject_name   = db.Column(db.String(256), nullable=False)
    turma          = db.Column(db.String(32))
    periodo_letivo = db.Column(db.String(16))
    creditos       = db.Column(db.Integer)
    nivel          = db.Column(db.String(64))
    professores    = db.Column(db.String(512))
    department     = db.Column(db.String(256))
    created_at     = db.Column(db.DateTime, default=_now)
    archived       = db.Column(db.Boolean, default=False)
    # Owner FK — the teacher who created this class
    owner_id       = db.Column(db.Integer,
                                db.ForeignKey("teacher.id", ondelete="SET NULL"),
                                nullable=True)

    # Relationships
    owner           = db.relationship("Teacher", foreign_keys=[owner_id])
    teacher_memberships = db.relationship("ClassTeacher", back_populates="class_offering",
                                           cascade="all, delete-orphan")
    settings        = db.relationship("ClassSettings",       back_populates="class_offering",
                                       uselist=False, cascade="all, delete-orphan")
    enrollments     = db.relationship("Enrollment",           back_populates="class_offering",
                                       cascade="all, delete-orphan",
                                       order_by="Enrollment.seq")
    components      = db.relationship("AssessmentComponent",  back_populates="class_offering",
                                       cascade="all, delete-orphan",
                                       order_by="AssessmentComponent.display_order")
    sessions        = db.relationship("AttendanceSession",    back_populates="class_offering",
                                       cascade="all, delete-orphan",
                                       order_by="AttendanceSession.date")
    action_logs     = db.relationship("ActionLog",            back_populates="class_offering",
                                       cascade="all, delete-orphan")
    backup_jobs     = db.relationship("BackupJob",            back_populates="class_offering")

    __table_args__ = (
        db.UniqueConstraint("subject_name", "turma", "periodo_letivo",
                             name="uq_class_offering"),
    )

    @property
    def active_enrollments(self):
        return [e for e in self.enrollments if e.status == "active"]

    def get_role_for(self, teacher) -> str | None:
        """Return 'owner', 'collaborator', or None for this teacher."""
        if teacher.is_super_admin:
            return "owner"   # super admin has full control everywhere
        for m in self.teacher_memberships:
            if m.teacher_id == teacher.id:
                return m.role
        return None

    def __repr__(self):
        return f"<ClassOffering {self.subject_name!r} {self.turma} {self.periodo_letivo}>"


# ─────────────────────────────────────────────────────────────────────────────
# ClassSettings (1:1 with ClassOffering)
# ─────────────────────────────────────────────────────────────────────────────

class ClassSettings(db.Model):
    __tablename__ = "class_settings"

    class_offering_id  = db.Column(db.Integer,
                                    db.ForeignKey("class_offering.id", ondelete="CASCADE"),
                                    primary_key=True)
    min_attendance_pct = db.Column(db.Float, default=75.0)
    min_avg_grade      = db.Column(db.Float, default=7.0)
    aggregation_method = db.Column(db.String(16), default="simple_avg")

    class_offering = db.relationship("ClassOffering", back_populates="settings")


# ─────────────────────────────────────────────────────────────────────────────
# Student
# ─────────────────────────────────────────────────────────────────────────────

class Student(db.Model):
    __tablename__ = "student"

    id     = db.Column(db.Integer, primary_key=True)
    cartao = db.Column(db.String(32), unique=True, nullable=False)
    nome   = db.Column(db.String(256), nullable=False)
    notes  = db.Column(db.Text)

    enrollments = db.relationship("Enrollment", back_populates="student",
                                   cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Student {self.cartao} {self.nome!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# Enrollment  (student ↔ class_offering, never hard-deleted)
# ─────────────────────────────────────────────────────────────────────────────

class Enrollment(db.Model):
    __tablename__ = "enrollment"

    id                = db.Column(db.Integer, primary_key=True)
    student_id        = db.Column(db.Integer, db.ForeignKey("student.id"),        nullable=False)
    class_offering_id = db.Column(db.Integer, db.ForeignKey("class_offering.id"), nullable=False)
    status            = db.Column(db.String(16), default="active")
    seq               = db.Column(db.Integer)
    aluno_type        = db.Column(db.String(16))

    student         = db.relationship("Student",        back_populates="enrollments")
    class_offering  = db.relationship("ClassOffering",  back_populates="enrollments")
    grades          = db.relationship("Grade",          back_populates="enrollment",
                                       cascade="all, delete-orphan")
    attendance_records = db.relationship("AttendanceRecord", back_populates="enrollment",
                                          cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("student_id", "class_offering_id", name="uq_enrollment"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AssessmentComponent
# ─────────────────────────────────────────────────────────────────────────────

class AssessmentComponent(db.Model):
    __tablename__ = "assessment_component"

    id                  = db.Column(db.Integer, primary_key=True)
    class_offering_id   = db.Column(db.Integer, db.ForeignKey("class_offering.id"), nullable=False)
    name                = db.Column(db.String(128), nullable=False)
    weight              = db.Column(db.Float, default=1.0)
    scale_type          = db.Column(db.String(16), default="numeric_0_10")
    max_value           = db.Column(db.Float, default=10.0)
    covers_component_id = db.Column(db.Integer,
                                     db.ForeignKey("assessment_component.id",
                                                   ondelete="SET NULL"),
                                     nullable=True)
    display_order       = db.Column(db.Integer, default=0)

    class_offering  = db.relationship("ClassOffering", back_populates="components")
    grades          = db.relationship("Grade", back_populates="component",
                                       cascade="all, delete-orphan")
    covers_component = db.relationship("AssessmentComponent",
                                        remote_side="AssessmentComponent.id",
                                        foreign_keys=[covers_component_id])

    @property
    def is_ps(self):
        return self.covers_component_id is not None

    def __repr__(self):
        return f"<Component {self.name!r} class={self.class_offering_id}>"


# ─────────────────────────────────────────────────────────────────────────────
# Grade
# ─────────────────────────────────────────────────────────────────────────────

class Grade(db.Model):
    __tablename__ = "grade"

    id            = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollment.id"), nullable=False)
    component_id  = db.Column(db.Integer, db.ForeignKey("assessment_component.id"), nullable=False)
    value         = db.Column(db.Float, nullable=True)
    status        = db.Column(db.String(24), default="graded")
    updated_at    = db.Column(db.DateTime, default=_now, onupdate=_now)

    enrollment = db.relationship("Enrollment",          back_populates="grades")
    component  = db.relationship("AssessmentComponent", back_populates="grades")

    __table_args__ = (
        db.UniqueConstraint("enrollment_id", "component_id", name="uq_grade"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AttendanceSession
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceSession(db.Model):
    __tablename__ = "attendance_session"

    id                = db.Column(db.Integer, primary_key=True)
    class_offering_id = db.Column(db.Integer, db.ForeignKey("class_offering.id"), nullable=False)
    date              = db.Column(db.Date, nullable=False)
    label             = db.Column(db.String(128))

    class_offering = db.relationship("ClassOffering", back_populates="sessions")
    records        = db.relationship("AttendanceRecord", back_populates="session",
                                      cascade="all, delete-orphan")


# ─────────────────────────────────────────────────────────────────────────────
# AttendanceRecord
# ─────────────────────────────────────────────────────────────────────────────

class AttendanceRecord(db.Model):
    __tablename__ = "attendance_record"

    id            = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(db.Integer, db.ForeignKey("enrollment.id"),          nullable=False)
    session_id    = db.Column(db.Integer, db.ForeignKey("attendance_session.id"),  nullable=False)
    status        = db.Column(db.String(24), default="unmarked")

    enrollment = db.relationship("Enrollment",       back_populates="attendance_records")
    session    = db.relationship("AttendanceSession", back_populates="records")

    __table_args__ = (
        db.UniqueConstraint("enrollment_id", "session_id", name="uq_attendance_record"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ActionLog  (audit trail + undo payload)
# ─────────────────────────────────────────────────────────────────────────────

class ActionLog(db.Model):
    __tablename__ = "action_log"

    id                = db.Column(db.Integer, primary_key=True)
    timestamp         = db.Column(db.DateTime, default=_now)
    action_type       = db.Column(db.String(64))
    class_offering_id = db.Column(db.Integer, db.ForeignKey("class_offering.id"), nullable=True)
    enrollment_id     = db.Column(db.Integer, db.ForeignKey("enrollment.id"),     nullable=True)
    # Which teacher performed this action
    teacher_id        = db.Column(db.Integer, db.ForeignKey("teacher.id"),        nullable=True)
    description       = db.Column(db.String(512))
    previous_value    = db.Column(db.JSON)
    reverted          = db.Column(db.Boolean, default=False)

    class_offering = db.relationship("ClassOffering", back_populates="action_logs")
    teacher        = db.relationship("Teacher", foreign_keys=[teacher_id])


# ─────────────────────────────────────────────────────────────────────────────
# BackupJob
# ─────────────────────────────────────────────────────────────────────────────

class BackupJob(db.Model):
    __tablename__ = "backup_job"

    id                = db.Column(db.Integer, primary_key=True)
    timestamp         = db.Column(db.DateTime, default=_now)
    trigger_reason    = db.Column(db.String(32))
    class_offering_id = db.Column(db.Integer, db.ForeignKey("class_offering.id"), nullable=True)
    xlsx_path         = db.Column(db.String(512))
    pdf_path          = db.Column(db.String(512))
    total_size_bytes  = db.Column(db.Integer)
    status            = db.Column(db.String(16))
    error_message     = db.Column(db.Text)

    class_offering = db.relationship("ClassOffering", back_populates="backup_jobs")


# ─────────────────────────────────────────────────────────────────────────────
# SystemSetting  (key-value server-wide config toggles)
# ─────────────────────────────────────────────────────────────────────────────

class SystemSetting(db.Model):
    __tablename__ = "system_setting"

    key   = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(512))

    @staticmethod
    def get(key: str, default: str = "") -> str:
        row = db.session.get(SystemSetting, key)
        return row.value if row else default

    @staticmethod
    def set(key: str, value: str) -> None:
        row = db.session.get(SystemSetting, key)
        if row:
            row.value = value
        else:
            db.session.add(SystemSetting(key=key, value=value))
