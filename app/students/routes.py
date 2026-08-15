"""
Students blueprint — student list (filter/sort/search/badges), detail, manual add.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from app import db
from app.models import ClassOffering, Student, Enrollment, AttendanceRecord
from app.utils import (
    compute_student_average,
    compute_student_freq,
    compute_student_status,
    build_grade_map,
    build_attendance_map,
    log_action,
    STATUS_LABELS,
    STATUS_COLORS,
    strip_accents,
    get_accessible_class,
)

students_bp = Blueprint("students", __name__, url_prefix="/class")


def _get_class_or_404(class_id):
    return get_accessible_class(class_id)


# ─────────────────────────────────────────────────────────────────────────────
# Student list
# ─────────────────────────────────────────────────────────────────────────────

@students_bp.route("/<int:class_id>/students")
@login_required
def list_students(class_id):
    co          = _get_class_or_404(class_id)
    components  = co.components
    settings    = co.settings
    enrollments = co.active_enrollments

    att_map = build_attendance_map(co)

    rows = []
    for enrollment in sorted(enrollments, key=lambda e: strip_accents(e.student.nome)):
        gmap    = build_grade_map(enrollment)
        recs    = list(att_map.get(enrollment.id, {}).values())
        avg     = compute_student_average(enrollment, components, gmap)
        freq    = compute_student_freq(recs)

        has_pending_grades = any(gmap.get(c.id) is None for c in components if not c.is_ps)
        has_pending_freq   = any(
            att_map.get(enrollment.id, {}).get(s.id) is None
            or att_map.get(enrollment.id, {}).get(s.id).status == "unmarked"
            for s in co.sessions
        )
        status = compute_student_status(avg, freq, settings,
                                        has_pending_grades, has_pending_freq)

        # "What grade does this student need to pass?" calculator
        needed_grade = None
        if settings and avg is not None:
            ungraded = [c for c in components if not c.is_ps and gmap.get(c.id) is None]
            if ungraded:
                graded_comps = [c for c in components if not c.is_ps and gmap.get(c.id) is not None]
                graded_vals  = []
                for c in graded_comps:
                    g = gmap[c.id]
                    if g.status == "graded":
                        graded_vals.append(g.value or 0)
                    elif g.status == "missed_unjustified":
                        graded_vals.append(0.0)

                n_total   = len([c for c in components if not c.is_ps])
                n_graded  = len(graded_vals)
                n_missing = n_total - n_graded
                if n_missing > 0:
                    needed = (settings.min_avg_grade * n_total - sum(graded_vals)) / n_missing
                    needed_grade = max(0.0, min(10.0, needed))

        rows.append({
            "enrollment":   enrollment,
            "avg":          avg,
            "freq":         freq,
            "status":       status,
            "needed_grade": needed_grade,
        })

    # Letter filter
    letter   = request.args.get("letter", "")
    q        = request.args.get("q", "").strip()
    tab      = request.args.get("tab", "all")    # 'all' or 'failing'
    sort_by  = request.args.get("sort", "nome")
    sort_dir = request.args.get("dir", "asc")

    if letter:
        rows = [r for r in rows
                if strip_accents(r["enrollment"].student.nome).startswith(strip_accents(letter))]

    if q:
        qn = strip_accents(q)
        rows = [r for r in rows
                if qn in strip_accents(r["enrollment"].student.nome)
                or qn in r["enrollment"].student.cartao]

    if tab == "failing":
        rows = [r for r in rows
                if r["status"] in ("reprovado_nota", "reprovado_falta", "reprovado_ambos")]

    # Sort
    reverse = sort_dir == "desc"
    if sort_by == "nome":
        rows.sort(key=lambda r: strip_accents(r["enrollment"].student.nome), reverse=reverse)
    elif sort_by == "cartao":
        rows.sort(key=lambda r: r["enrollment"].student.cartao, reverse=reverse)
    elif sort_by == "avg":
        rows.sort(key=lambda r: r["avg"] if r["avg"] is not None else -1, reverse=reverse)
    elif sort_by == "freq":
        rows.sort(key=lambda r: r["freq"] if r["freq"] is not None else -1, reverse=reverse)

    # Collect unique first letters for filter bar
    all_letters = sorted({
        strip_accents(e.student.nome[0]).upper()
        for e in enrollments if e.student.nome
    })

    return render_template(
        "students/list.html",
        co=co,
        rows=rows,
        all_letters=all_letters,
        letter=letter,
        q=q,
        tab=tab,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Student detail
# ─────────────────────────────────────────────────────────────────────────────

@students_bp.route("/<int:class_id>/students/<int:student_id>")
@login_required
def student_detail(class_id, student_id):
    co      = _get_class_or_404(class_id)
    student = db.session.get(Student, student_id)
    if student is None:
        from flask import abort
        abort(404)

    enrollment = db.session.query(Enrollment).filter_by(
        class_offering_id=class_id, student_id=student_id
    ).first()
    if enrollment is None:
        flash("Aluno não matriculado nesta turma.", "warning")
        return redirect(url_for("students.list_students", class_id=class_id))

    components = co.components
    gmap       = build_grade_map(enrollment)
    recs       = sorted(enrollment.attendance_records, key=lambda r: r.session.date)
    avg        = compute_student_average(enrollment, components, gmap)
    freq       = compute_student_freq(enrollment.attendance_records)

    has_pending_grades = any(gmap.get(c.id) is None for c in components if not c.is_ps)
    status = compute_student_status(avg, freq, co.settings, has_pending_grades)

    from app.models import ActionLog
    history = (
        db.session.query(ActionLog)
        .filter_by(enrollment_id=enrollment.id)
        .order_by(ActionLog.timestamp.desc())
        .limit(30)
        .all()
    )

    return render_template(
        "students/detail.html",
        co=co,
        student=student,
        enrollment=enrollment,
        components=components,
        gmap=gmap,
        recs=recs,
        avg=avg,
        freq=freq,
        status=status,
        history=history,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Student detail — update notes
# ─────────────────────────────────────────────────────────────────────────────

@students_bp.route("/<int:class_id>/students/<int:student_id>/notes", methods=["POST"])
@login_required
def update_notes(class_id, student_id):
    student = db.session.get(Student, student_id)
    if student:
        student.notes = request.form.get("notes", "")
        db.session.commit()
    return redirect(url_for("students.student_detail", class_id=class_id, student_id=student_id))


# ─────────────────────────────────────────────────────────────────────────────
# Manual add student
# ─────────────────────────────────────────────────────────────────────────────

@students_bp.route("/<int:class_id>/students/add", methods=["POST"])
@login_required
def add_student(class_id):
    co     = _get_class_or_404(class_id)
    cartao = request.form.get("cartao", "").strip()
    nome   = request.form.get("nome", "").strip()

    if not cartao or not nome:
        flash("Cartão e nome são obrigatórios.", "danger")
        return redirect(url_for("students.list_students", class_id=class_id))

    # Check for existing enrollment in this class
    existing_enr = (
        db.session.query(Enrollment)
        .join(Student)
        .filter(Student.cartao == cartao, Enrollment.class_offering_id == class_id)
        .first()
    )
    if existing_enr:
        if existing_enr.status == "removed":
            existing_enr.status = "active"
            db.session.commit()
            flash(f"Aluno {nome} reativado na turma.", "success")
        else:
            flash(f"Aluno com cartão {cartao} já está nesta turma.", "warning")
        return redirect(url_for("students.list_students", class_id=class_id))

    # Upsert student record
    student = db.session.query(Student).filter_by(cartao=cartao).first()
    if not student:
        student = Student(cartao=cartao, nome=nome)
        db.session.add(student)
        db.session.flush()

    # Get next seq
    max_seq = max((e.seq or 0 for e in co.enrollments), default=0)
    enrollment = Enrollment(
        student_id=student.id,
        class_offering_id=class_id,
        status="active",
        seq=max_seq + 1,
        aluno_type="GRAD",
    )
    db.session.add(enrollment)
    db.session.flush()

    log_action(
        "student_added",
        f"Aluno {nome} (cartão {cartao}) adicionado manualmente",
        previous_value={"entity": "enrollment", "id": enrollment.id, "action": "add"},
        class_offering_id=class_id,
        enrollment_id=enrollment.id,
    )
    db.session.commit()
    flash(f"Aluno {nome} adicionado.", "success")
    return redirect(url_for("students.list_students", class_id=class_id))


# ─────────────────────────────────────────────────────────────────────────────
# Remove student (soft-delete: marks enrollment as 'removed')
# ─────────────────────────────────────────────────────────────────────────────

@students_bp.route("/<int:class_id>/students/<int:student_id>/remove", methods=["POST"])
@login_required
def remove_student(class_id, student_id):
    co = _get_class_or_404(class_id)
    enrollment = db.session.query(Enrollment).filter_by(
        class_offering_id=class_id, student_id=student_id
    ).first()

    if not enrollment:
        flash("Aluno não encontrado nesta turma.", "warning")
        return redirect(url_for("students.list_students", class_id=class_id))

    prev = {
        "entity":     "enrollment",
        "id":         enrollment.id,
        "old_status": enrollment.status,
    }
    enrollment.status = "removed"
    log_action(
        "student_removed",
        f"Aluno {enrollment.student.nome} removido da turma (histórico preservado)",
        previous_value=prev,
        class_offering_id=class_id,
        enrollment_id=enrollment.id,
    )
    db.session.commit()
    flash(f"Aluno {enrollment.student.nome} removido. Histórico preservado.", "info")
    return redirect(url_for("students.list_students", class_id=class_id))
