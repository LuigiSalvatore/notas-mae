"""
Classes blueprint — class CRUD, PDF import, Excel import/export, settings.
"""

import io
import json
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, send_file, current_app,
)
from flask_login import login_required

from app import db
from app.models import (
    ClassOffering, ClassSettings, Student, Enrollment,
    AssessmentComponent, ActionLog,
)
from app.utils import strip_accents, log_action, get_accessible_class, is_owner
from app.classes.import_pdf import parse_pdf, parse_pdf_multi, compute_diff
from app.classes.import_xlsx import parse_xlsx_grades
from app.classes.export import export_class_xlsx, export_full_system_xlsx, export_class_pdf

classes_bp = Blueprint("classes", __name__, url_prefix="/class")


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_class_or_404(class_id: int) -> ClassOffering:
    return get_accessible_class(class_id)


# ─────────────────────────────────────────────────────────────────────────────
# New class (manual creation)
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_class():
    from flask_login import current_user
    teacher = current_user

    if request.method == "POST":
        subject_name   = request.form.get("subject_name", "").strip()
        turma          = request.form.get("turma", "").strip()
        periodo_letivo = request.form.get("periodo_letivo", "").strip()
        creditos       = request.form.get("creditos", "").strip()
        nivel          = request.form.get("nivel", "").strip()
        professores    = request.form.get("professores", "").strip()
        department     = request.form.get("department", "").strip()

        if not subject_name:
            flash("Nome da atividade de ensino é obrigatório.", "danger")
            return redirect(url_for("classes.new_class"))

        # Check uniqueness
        existing = db.session.query(ClassOffering).filter(
            db.func.lower(ClassOffering.subject_name) == subject_name.lower(),
            ClassOffering.turma          == turma,
            ClassOffering.periodo_letivo == periodo_letivo,
        ).first()

        if existing:
            flash("Já existe uma turma com esse nome, turma e período.", "warning")
            return redirect(url_for("classes.new_class"))

        co = ClassOffering(
            subject_name=subject_name,
            turma=turma,
            periodo_letivo=periodo_letivo,
            creditos=int(creditos) if creditos.isdigit() else None,
            nivel=nivel,
            professores=professores,
            department=department,
            owner_id=teacher.id,
        )
        db.session.add(co)
        db.session.flush()  # get co.id

        from app.models import ClassTeacher
        ct = ClassTeacher(
            class_offering_id=co.id,
            teacher_id=teacher.id,
            role="owner"
        )
        db.session.add(ct)

        settings = ClassSettings(
            class_offering_id=co.id,
            min_attendance_pct=teacher.default_min_attendance_pct,
            min_avg_grade=teacher.default_min_avg_grade,
        )
        db.session.add(settings)
        db.session.commit()
        flash(f"Turma '{subject_name}' criada.", "success")
        return redirect(url_for("students.list_students", class_id=co.id))

    return render_template("classes/new.html")


# ─────────────────────────────────────────────────────────────────────────────
# Archive / Unarchive
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/archive", methods=["POST"])
@login_required
def archive_class(class_id):
    co = _get_class_or_404(class_id)
    if not is_owner(co):
        flash("Apenas o proprietário da turma pode arquivá-la.", "danger")
        return redirect(url_for("dashboard.index"))
    co.archived = not co.archived
    db.session.commit()
    state = "arquivada" if co.archived else "reativada"
    flash(f"Turma {state}.", "info")
    return redirect(url_for("dashboard.index"))


# ─────────────────────────────────────────────────────────────────────────────
# Class settings
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/settings", methods=["GET", "POST"])
@login_required
def class_settings(class_id):
    co = _get_class_or_404(class_id)
    if co.settings is None:
        s = ClassSettings(class_offering_id=co.id)
        db.session.add(s)
        db.session.commit()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_settings":
            s = co.settings
            try:
                s.min_attendance_pct = float(request.form.get("min_attendance_pct", 75.0))
                s.min_avg_grade      = float(request.form.get("min_avg_grade", 7.0))
            except ValueError:
                flash("Valores inválidos.", "danger")
                return redirect(url_for("classes.class_settings", class_id=class_id))

            s.aggregation_method = request.form.get("aggregation_method", "simple_avg")
            db.session.commit()
            log_action("settings_changed", f"Configurações da turma atualizadas",
                       class_offering_id=class_id)
            db.session.commit()
            flash("Configurações salvas.", "success")

        elif action == "add_component":
            name       = request.form.get("comp_name", "").strip()
            weight     = request.form.get("comp_weight", "1.0")
            scale_type = request.form.get("comp_scale", "numeric_0_10")
            covers_id  = request.form.get("covers_component_id", "").strip()

            if not name:
                flash("Nome do componente é obrigatório.", "danger")
                return redirect(url_for("classes.class_settings", class_id=class_id))

            max_order = db.session.query(db.func.max(AssessmentComponent.display_order))\
                .filter_by(class_offering_id=class_id).scalar() or 0

            comp = AssessmentComponent(
                class_offering_id=class_id,
                name=name,
                weight=float(weight) if weight else 1.0,
                scale_type=scale_type,
                covers_component_id=int(covers_id) if covers_id else None,
                display_order=max_order + 1,
            )
            db.session.add(comp)
            db.session.commit()
            log_action("component_added", f"Componente '{name}' adicionado",
                       class_offering_id=class_id)
            db.session.commit()
            flash(f"Componente '{name}' adicionado.", "success")

        elif action == "delete_component":
            comp_id = int(request.form.get("comp_id", 0))
            comp = db.session.get(AssessmentComponent, comp_id)
            if comp and comp.class_offering_id == class_id:
                name = comp.name
                db.session.delete(comp)
                db.session.commit()
                log_action("component_removed", f"Componente '{name}' removido",
                           class_offering_id=class_id)
                db.session.commit()
                flash(f"Componente '{name}' removido.", "info")

        elif action == "reorder_components":
            order_json = request.form.get("order", "[]")
            try:
                order = json.loads(order_json)  # list of component IDs
                for idx, comp_id in enumerate(order):
                    comp = db.session.get(AssessmentComponent, int(comp_id))
                    if comp and comp.class_offering_id == class_id:
                        comp.display_order = idx
                db.session.commit()
            except Exception:
                pass

        elif action == "add_teacher":
            if not is_owner(co):
                flash("Apenas o proprietário pode gerenciar professores nesta turma.", "danger")
                return redirect(url_for("classes.class_settings", class_id=class_id))
            username = request.form.get("username", "").strip()
            from app.models import Teacher, ClassTeacher
            teacher = db.session.query(Teacher).filter_by(username=username, is_active=True).first()
            if not teacher:
                flash(f"Professor '{username}' não encontrado ou inativo.", "danger")
            else:
                exists = db.session.query(ClassTeacher).filter_by(class_offering_id=class_id, teacher_id=teacher.id).first()
                if exists:
                    flash(f"Professor '{username}' já tem acesso a esta turma.", "warning")
                else:
                    ct = ClassTeacher(
                        class_offering_id=class_id,
                        teacher_id=teacher.id,
                        role="collaborator"
                    )
                    db.session.add(ct)
                    db.session.commit()
                    log_action("collaborator_added", f"Professor '{username}' adicionado como colaborador",
                               class_offering_id=class_id)
                    db.session.commit()
                    flash(f"Professor '{username}' adicionado com sucesso.", "success")

        elif action == "remove_teacher":
            if not is_owner(co):
                flash("Apenas o proprietário pode gerenciar professores nesta turma.", "danger")
                return redirect(url_for("classes.class_settings", class_id=class_id))
            teacher_id = int(request.form.get("teacher_id", 0))
            from app.models import ClassTeacher
            ct = db.session.query(ClassTeacher).filter_by(class_offering_id=class_id, teacher_id=teacher_id).first()
            if ct:
                if ct.role == "owner" or ct.teacher_id == co.owner_id:
                    flash("Não é possível remover o proprietário da turma.", "danger")
                else:
                    username = ct.teacher.username
                    db.session.delete(ct)
                    db.session.commit()
                    log_action("collaborator_removed", f"Acesso do professor '{username}' removido",
                               class_offering_id=class_id)
                    db.session.commit()
                    flash(f"Acesso do professor '{username}' removido.", "info")

        return redirect(url_for("classes.class_settings", class_id=class_id))

    return render_template("classes/settings.html", co=co, is_owner=is_owner(co))


# ─────────────────────────────────────────────────────────────────────────────
# PDF Import — upload step
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/import/pdf", methods=["GET", "POST"])
@login_required
def import_pdf_upload(class_id):
    co = _get_class_or_404(class_id)

    if request.method == "POST":
        file    = request.files.get("pdf_file")
        password = request.form.get("pdf_password", "")

        if not file or not file.filename:
            flash("Selecione um arquivo PDF.", "danger")
            return redirect(url_for("classes.import_pdf_upload", class_id=class_id))

        file_bytes = file.read()
        result     = parse_pdf(file_bytes, password=password)

        if result.error == "encrypted":
            flash("Este PDF está protegido por senha. Informe a senha.", "warning")
            return render_template("classes/import_pdf.html", co=co,
                                   needs_password=True, filename=file.filename)

        if result.error:
            flash(f"Erro ao ler PDF: {result.error}", "danger")
            return redirect(url_for("classes.import_pdf_upload", class_id=class_id))

        # Compute diff against existing enrollments
        existing = (
            db.session.query(Enrollment)
            .filter_by(class_offering_id=class_id)
            .join(Student)
            .all()
        )
        diff = compute_diff(result.students, existing)

        # Store parsed data in session for the confirm step
        from flask import session as flask_session
        flask_session["import_pdf_header"]   = {
            "subject_name":   result.header.subject_name,
            "turma":          result.header.turma,
            "periodo_letivo": result.header.periodo_letivo,
            "creditos":       result.header.creditos,
            "nivel":          result.header.nivel,
            "professores":    result.header.professores,
            "department":     result.header.department,
        }
        flask_session["import_pdf_students"] = [
            {"seq": s.seq, "aluno_type": s.aluno_type, "cartao": s.cartao, "nome": s.nome}
            for s in result.students
        ]
        flask_session["import_pdf_class_id"] = class_id

        return render_template(
            "classes/import_pdf_diff.html",
            co=co,
            header=result.header,
            diff=diff,
        )

    return render_template("classes/import_pdf.html", co=co, needs_password=False)


# ─────────────────────────────────────────────────────────────────────────────
# PDF Import — confirm (commit diff)
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/import/pdf/confirm", methods=["POST"])
@login_required
def import_pdf_confirm(class_id):
    from flask import session as flask_session
    co = _get_class_or_404(class_id)

    students_data = flask_session.get("import_pdf_students", [])
    if not students_data:
        flash("Sessão expirada. Faça o upload novamente.", "warning")
        return redirect(url_for("classes.import_pdf_upload", class_id=class_id))

    added = 0
    removed = 0

    pdf_cartaos = {s["cartao"]: s for s in students_data}
    existing_enrollments = (
        db.session.query(Enrollment)
        .filter_by(class_offering_id=class_id, status="active")
        .join(Student)
        .all()
    )
    existing_cartaos = {e.student.cartao: e for e in existing_enrollments}

    # Add new students
    for cartao, s_data in pdf_cartaos.items():
        if cartao not in existing_cartaos:
            # Upsert student (may exist from another class)
            student = db.session.query(Student).filter_by(cartao=cartao).first()
            if not student:
                student = Student(cartao=cartao, nome=s_data["nome"])
                db.session.add(student)
                db.session.flush()
            enrollment = Enrollment(
                student_id=student.id,
                class_offering_id=class_id,
                status="active",
                seq=s_data["seq"],
                aluno_type=s_data["aluno_type"],
            )
            db.session.add(enrollment)
            added += 1

    # Mark removed students
    for cartao, enrollment in existing_cartaos.items():
        if cartao not in pdf_cartaos:
            enrollment.status = "removed"
            removed += 1

    db.session.flush()

    log_action(
        "roster_import",
        f"Importação de lista PDF: {added} adicionados, {removed} removidos",
        previous_value={"added": added, "removed": removed},
        class_offering_id=class_id,
    )
    db.session.commit()

    flask_session.pop("import_pdf_students", None)
    flask_session.pop("import_pdf_header",   None)

    flash(f"Importação concluída: {added} aluno(s) adicionado(s), {removed} removido(s).", "success")
    return redirect(url_for("students.list_students", class_id=class_id))


# ─────────────────────────────────────────────────────────────────────────────
# PDF Import — dashboard-level (no class yet → auto-create or match)
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/import/pdf", methods=["GET", "POST"])
@login_required
def import_pdf_new():
    """Import PDF from dashboard — handles both single-class and multi-class PDFs."""
    from flask import session as flask_session
    from flask_login import current_user
    from app.models import ClassTeacher

    if request.method == "POST":
        file     = request.files.get("pdf_file")
        password = request.form.get("pdf_password", "")
        action   = request.form.get("action", "upload")

        # ── STEP 1: Upload & parse ─────────────────────────────────────────
        if action == "upload":
            if not file or not file.filename:
                flash("Selecione um arquivo PDF.", "danger")
                return redirect(url_for("classes.import_pdf_new"))

            file_bytes = file.read()
            results    = parse_pdf_multi(file_bytes, password=password)

            # Check the first result for errors (errors affect the whole file)
            if results[0].error == "encrypted":
                flash("PDF protegido por senha. Informe a senha.", "warning")
                return render_template("classes/import_pdf_new.html", needs_password=True)

            if results[0].error:
                flash(f"Erro ao ler PDF: {results[0].error}", "danger")
                return redirect(url_for("classes.import_pdf_new"))

            # ── Multi-class PDF (≥2 classes) ──────────────────────────────
            if len(results) > 1:
                # Classify each parsed class: new vs. already-existing
                classes_info = []
                for r in results:
                    h = r.header
                    matched_co = db.session.query(ClassOffering).filter(
                        db.func.lower(ClassOffering.subject_name) == (h.subject_name or "").lower(),
                        ClassOffering.turma          == h.turma,
                        ClassOffering.periodo_letivo == h.periodo_letivo,
                    ).first()
                    classes_info.append({
                        "header": {
                            "subject_name":   h.subject_name,
                            "turma":          h.turma,
                            "periodo_letivo": h.periodo_letivo,
                            "creditos":       h.creditos,
                            "nivel":          h.nivel,
                            "professores":    h.professores,
                            "department":     h.department,
                        },
                        "students": [
                            {"seq": s.seq, "aluno_type": s.aluno_type,
                             "cartao": s.cartao, "nome": s.nome}
                            for s in r.students
                        ],
                        "student_count": len(r.students),
                        "existing_id":   matched_co.id if matched_co else None,
                        "existing_name": str(matched_co) if matched_co else None,
                    })

                flask_session["import_pdf_multi"] = classes_info
                return render_template(
                    "classes/import_pdf_multi_confirm.html",
                    classes_info=classes_info,
                )

            # ── Single-class PDF ──────────────────────────────────────────
            result = results[0]
            h = result.header

            matched_co = db.session.query(ClassOffering).filter(
                db.func.lower(ClassOffering.subject_name) == (h.subject_name or "").lower(),
                ClassOffering.turma          == h.turma,
                ClassOffering.periodo_letivo == h.periodo_letivo,
            ).first()

            flask_session["import_pdf_students"] = [
                {"seq": s.seq, "aluno_type": s.aluno_type, "cartao": s.cartao, "nome": s.nome}
                for s in result.students
            ]
            flask_session["import_pdf_header"] = {
                "subject_name":   h.subject_name,
                "turma":          h.turma,
                "periodo_letivo": h.periodo_letivo,
                "creditos":       h.creditos,
                "nivel":          h.nivel,
                "professores":    h.professores,
                "department":     h.department,
            }

            if matched_co:
                existing = (
                    db.session.query(Enrollment)
                    .filter_by(class_offering_id=matched_co.id)
                    .join(Student)
                    .all()
                )
                diff = compute_diff(result.students, existing)
                flask_session["import_pdf_class_id"] = matched_co.id
                return render_template(
                    "classes/import_pdf_diff.html",
                    co=matched_co,
                    header=result.header,
                    diff=diff,
                )
            else:
                return render_template(
                    "classes/import_pdf_new_confirm.html",
                    header=result.header,
                    student_count=len(result.students),
                )

        # ── STEP 2a: Confirm & create (single-class) ───────────────────────
        elif action == "create_and_import":
            teacher       = current_user
            hdr           = flask_session.get("import_pdf_header", {})
            students_data = flask_session.get("import_pdf_students", [])

            co = ClassOffering(
                subject_name=hdr.get("subject_name", "Nova Turma"),
                turma=hdr.get("turma", ""),
                periodo_letivo=hdr.get("periodo_letivo", ""),
                creditos=hdr.get("creditos"),
                nivel=hdr.get("nivel", ""),
                professores=hdr.get("professores", ""),
                department=hdr.get("department", ""),
                owner_id=teacher.id,
            )
            db.session.add(co)
            db.session.flush()

            ct = ClassTeacher(class_offering_id=co.id, teacher_id=teacher.id, role="owner")
            db.session.add(ct)
            settings = ClassSettings(
                class_offering_id=co.id,
                min_attendance_pct=teacher.default_min_attendance_pct,
                min_avg_grade=teacher.default_min_avg_grade,
            )
            db.session.add(settings)
            db.session.flush()

            added = 0
            for s in students_data:
                student = db.session.query(Student).filter_by(cartao=s["cartao"]).first()
                if not student:
                    student = Student(cartao=s["cartao"], nome=s["nome"])
                    db.session.add(student)
                    db.session.flush()
                enrollment = Enrollment(
                    student_id=student.id, class_offering_id=co.id,
                    status="active", seq=s["seq"], aluno_type=s["aluno_type"],
                )
                db.session.add(enrollment)
                added += 1

            log_action("roster_import", f"Nova turma criada via PDF com {added} alunos",
                       previous_value={"added": added}, class_offering_id=co.id)
            db.session.commit()
            flask_session.pop("import_pdf_students", None)
            flask_session.pop("import_pdf_header", None)

            flash(f"Turma criada com {added} aluno(s).", "success")
            return redirect(url_for("students.list_students", class_id=co.id))

        # ── STEP 2b: Confirm & create (multi-class) ────────────────────────
        elif action == "create_and_import_multi":
            teacher      = current_user
            classes_info = flask_session.get("import_pdf_multi", [])

            if not classes_info:
                flash("Sessão expirada. Faça o upload novamente.", "warning")
                return redirect(url_for("classes.import_pdf_new"))

            total_added   = 0
            total_classes = 0
            last_class_id = None

            for ci in classes_info:
                hdr           = ci["header"]
                students_data = ci["students"]
                existing_id   = ci.get("existing_id")

                if existing_id:
                    # Class already exists — just add missing students
                    co = db.session.get(ClassOffering, existing_id)
                    existing_cartaos = {
                        e.student.cartao
                        for e in db.session.query(Enrollment)
                        .filter_by(class_offering_id=existing_id, status="active")
                        .join(Student).all()
                    }
                    added = 0
                    for s in students_data:
                        if s["cartao"] in existing_cartaos:
                            continue
                        student = db.session.query(Student).filter_by(cartao=s["cartao"]).first()
                        if not student:
                            student = Student(cartao=s["cartao"], nome=s["nome"])
                            db.session.add(student)
                            db.session.flush()
                        enrollment = Enrollment(
                            student_id=student.id, class_offering_id=existing_id,
                            status="active", seq=s["seq"], aluno_type=s["aluno_type"],
                        )
                        db.session.add(enrollment)
                        added += 1
                    log_action("roster_import",
                               f"Atualização via PDF multi-turma: {added} alunos adicionados",
                               previous_value={"added": added},
                               class_offering_id=existing_id)
                    total_added  += added
                    last_class_id = existing_id
                else:
                    # Create new class
                    co = ClassOffering(
                        subject_name=hdr.get("subject_name", "Nova Turma"),
                        turma=hdr.get("turma", ""),
                        periodo_letivo=hdr.get("periodo_letivo", ""),
                        creditos=hdr.get("creditos"),
                        nivel=hdr.get("nivel", ""),
                        professores=hdr.get("professores", ""),
                        department=hdr.get("department", ""),
                        owner_id=teacher.id,
                    )
                    db.session.add(co)
                    db.session.flush()

                    ct = ClassTeacher(class_offering_id=co.id, teacher_id=teacher.id, role="owner")
                    db.session.add(ct)
                    settings = ClassSettings(
                        class_offering_id=co.id,
                        min_attendance_pct=teacher.default_min_attendance_pct,
                        min_avg_grade=teacher.default_min_avg_grade,
                    )
                    db.session.add(settings)
                    db.session.flush()

                    added = 0
                    for s in students_data:
                        student = db.session.query(Student).filter_by(cartao=s["cartao"]).first()
                        if not student:
                            student = Student(cartao=s["cartao"], nome=s["nome"])
                            db.session.add(student)
                            db.session.flush()
                        enrollment = Enrollment(
                            student_id=student.id, class_offering_id=co.id,
                            status="active", seq=s["seq"], aluno_type=s["aluno_type"],
                        )
                        db.session.add(enrollment)
                        added += 1

                    log_action("roster_import",
                               f"Nova turma criada via PDF multi-turma com {added} alunos",
                               previous_value={"added": added},
                               class_offering_id=co.id)
                    total_added  += added
                    total_classes += 1
                    last_class_id  = co.id

            db.session.commit()
            flask_session.pop("import_pdf_multi", None)

            flash(
                f"Importação concluída: {total_classes} turma(s) criada(s), "
                f"{total_added} aluno(s) importado(s) no total.",
                "success"
            )
            return redirect(url_for("classes.list_classes"))

    return render_template("classes/import_pdf_new.html", needs_password=False)


# ─────────────────────────────────────────────────────────────────────────────
# Excel Grade Import
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/import/xlsx", methods=["GET", "POST"])
@login_required
def import_xlsx_upload(class_id):
    from flask import session as flask_session
    co = _get_class_or_404(class_id)

    if request.method == "POST":
        file = request.files.get("xlsx_file")
        if not file or not file.filename:
            flash("Selecione uma planilha Excel.", "danger")
            return redirect(url_for("classes.import_xlsx_upload", class_id=class_id))

        from app.utils import build_class_grade_maps
        grade_maps = build_class_grade_maps(co)
        result     = parse_xlsx_grades(file.read(), co, grade_maps)

        if result.error:
            flash(f"Erro na planilha: {result.error}", "danger")
            return redirect(url_for("classes.import_xlsx_upload", class_id=class_id))

        if not result.diffs:
            flash("Nenhuma alteração encontrada na planilha.", "info")
            return redirect(url_for("grades.grade_grid", class_id=class_id))

        flask_session["xlsx_diffs"] = [
            {
                "component_name": d.component_name,
                "cartao":         d.cartao,
                "student_name":   d.student_name,
                "old_value":      d.old_value,
                "old_status":     d.old_status,
                "new_value":      d.new_value,
            }
            for d in result.diffs
        ]
        flask_session["xlsx_class_id"] = class_id

        return render_template(
            "classes/import_xlsx_diff.html",
            co=co,
            diffs=result.diffs,
            matched_components=result.matched_components,
            unmatched_columns=result.unmatched_columns,
        )

    return render_template("classes/import_xlsx.html", co=co)


@classes_bp.route("/<int:class_id>/import/xlsx/confirm", methods=["POST"])
@login_required
def import_xlsx_confirm(class_id):
    from flask import session as flask_session
    from app.models import Grade

    co = _get_class_or_404(class_id)
    diffs_data = flask_session.get("xlsx_diffs", [])

    if not diffs_data:
        flash("Sessão expirada. Faça o upload novamente.", "warning")
        return redirect(url_for("classes.import_xlsx_upload", class_id=class_id))

    comp_lookup  = {c.name: c for c in co.components}
    cartao_to_enrollment = {e.student.cartao: e for e in co.active_enrollments}
    applied = 0

    for d in diffs_data:
        comp       = comp_lookup.get(d["component_name"])
        enrollment = cartao_to_enrollment.get(d["cartao"])
        if not comp or not enrollment:
            continue

        grade = db.session.query(Grade).filter_by(
            enrollment_id=enrollment.id,
            component_id=comp.id,
        ).first()

        if grade:
            prev = {"entity": "grade", "id": grade.id,
                    "old_value": grade.value, "old_status": grade.status}
            grade.value  = d["new_value"]
            grade.status = "graded"
        else:
            prev = {"entity": "grade", "id": None,
                    "old_value": None, "old_status": None}
            grade = Grade(
                enrollment_id=enrollment.id,
                component_id=comp.id,
                value=d["new_value"],
                status="graded",
            )
            db.session.add(grade)

        old_str = f"{d['old_value']:.1f}" if d["old_value"] is not None else "—"
        log_action(
            "grade_edit",
            f"Nota {d['component_name']} importada para {d['student_name']}: {old_str} → {d['new_value']:.1f}",
            previous_value=prev,
            class_offering_id=class_id,
            enrollment_id=enrollment.id,
        )
        applied += 1

    db.session.commit()
    flask_session.pop("xlsx_diffs", None)
    flash(f"{applied} nota(s) importada(s).", "success")
    return redirect(url_for("grades.grade_grid", class_id=class_id))


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

@classes_bp.route("/<int:class_id>/export", methods=["GET", "POST"])
@login_required
def export_page(class_id):
    co = _get_class_or_404(class_id)

    if request.method == "POST":
        fmt        = request.form.get("format", "xlsx")
        preset     = request.form.get("preset", "custom")
        anonymized = preset == "anonymized" or bool(request.form.get("anonymized"))
        failing    = preset == "failing"

        filename_base = (
            f"{co.subject_name[:30]}_{co.turma}_{co.periodo_letivo}"
            .replace("/", "-").replace(" ", "_")
        )

        if fmt == "xlsx":
            data     = export_class_xlsx(co, anonymized=anonymized)
            filename = f"{filename_base}.xlsx"
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            try:
                data     = export_class_pdf(co, anonymized=anonymized, failing_only=failing)
                filename = f"{filename_base}.pdf"
                mimetype = "application/pdf"
            except RuntimeError as e:
                flash(str(e), "danger")
                return redirect(url_for("classes.export_page", class_id=class_id))

        return send_file(
            io.BytesIO(data),
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename,
        )

    return render_template("classes/export.html", co=co)


@classes_bp.route("/export/full", methods=["GET"])
@login_required
def export_full_xlsx():
    from flask_login import current_user
    from app.models import ClassOffering, ClassTeacher
    if current_user.is_super_admin:
        query = db.session.query(ClassOffering)
    else:
        query = (
            db.session.query(ClassOffering)
            .join(ClassOffering.teacher_memberships)
            .filter(ClassTeacher.teacher_id == current_user.id)
        )
    all_classes = query.order_by(
        ClassOffering.periodo_letivo.desc(), ClassOffering.subject_name
    ).all()
    data = export_full_system_xlsx(all_classes)
    return send_file(
        io.BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="exportacao_completa.xlsx",
    )
