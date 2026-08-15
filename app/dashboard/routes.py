from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app import db
from app.models import ClassOffering, ClassTeacher
from app.utils import count_at_risk

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    show_archived = False
    if current_user.is_super_admin:
        query = db.session.query(ClassOffering)
    else:
        query = (
            db.session.query(ClassOffering)
            .join(ClassOffering.teacher_memberships)
            .filter(ClassTeacher.teacher_id == current_user.id)
        )
    all_classes = (
        query.order_by(ClassOffering.periodo_letivo.desc(), ClassOffering.subject_name)
        .all()
    )

    # Group by periodo_letivo
    periods = {}
    for co in all_classes:
        if co.archived and not show_archived:
            continue
        p = co.periodo_letivo or "Sem período"
        periods.setdefault(p, []).append(co)

    # Build risk badges (cheap: computed per card)
    risk_map = {}
    for co in all_classes:
        if not co.archived or show_archived:
            risk_map[co.id] = count_at_risk(co)

    return render_template(
        "dashboard/index.html",
        periods=periods,
        risk_map=risk_map,
        show_archived=show_archived,
        all_classes=all_classes,
    )


@dashboard_bp.route("/toggle-archived")
@login_required
def toggle_archived():
    # Client-side toggle via Alpine.js; this route is kept for no-JS fallback
    from flask import redirect, url_for, request
    show = request.args.get("show", "0") == "1"
    return redirect(url_for("dashboard.index", show_archived=int(show)))
