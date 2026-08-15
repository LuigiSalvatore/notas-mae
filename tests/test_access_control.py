import pytest
from app import create_app, db
from app.models import Teacher, ClassOffering, ClassTeacher, Grade, Student, Enrollment, AssessmentComponent
from app.utils import get_accessible_class, is_owner
from werkzeug.exceptions import Forbidden, NotFound

@pytest.fixture
def test_app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True, "WTF_CSRF_ENABLED": False})
    with app.app_context():
        # Setup two teachers
        t1 = Teacher(username="teacher1", password_hash="hash", display_name="T1", is_super_admin=False)
        t2 = Teacher(username="teacher2", password_hash="hash", display_name="T2", is_super_admin=False)
        t_admin = Teacher(username="superadmin", password_hash="hash", display_name="Admin", is_super_admin=True)
        db.session.add_all([t1, t2, t_admin])
        db.session.commit()
        yield app

def test_class_access_control(test_app):
    with test_app.app_context():
        t1 = db.session.query(Teacher).filter_by(username="teacher1").one()
        t2 = db.session.query(Teacher).filter_by(username="teacher2").one()
        t_admin = db.session.query(Teacher).filter_by(username="superadmin").one()

        # Teacher 1 creates a class
        co = ClassOffering(subject_name="Biologia Celular", turma="A", owner_id=t1.id)
        db.session.add(co)
        db.session.flush()

        ct1 = ClassTeacher(class_offering_id=co.id, teacher_id=t1.id, role="owner")
        db.session.add(ct1)
        db.session.commit()

        # 1. Teacher 1 should have access
        accessible_co = get_accessible_class(co.id, teacher=t1)
        assert accessible_co.id == co.id
        assert is_owner(co.id, teacher=t1) is True

        # 2. Teacher 2 should NOT have access (raises Forbidden 403)
        with pytest.raises(Forbidden):
            get_accessible_class(co.id, teacher=t2)
        assert is_owner(co.id, teacher=t2) is False

        # 3. Super admin should have access
        admin_co = get_accessible_class(co.id, teacher=t_admin)
        assert admin_co.id == co.id
        assert is_owner(co.id, teacher=t_admin) is True

        # 4. Add Teacher 2 as collaborator
        ct2 = ClassTeacher(class_offering_id=co.id, teacher_id=t2.id, role="collaborator")
        db.session.add(ct2)
        db.session.commit()

        # Teacher 2 should now have access but NOT be owner
        accessible_co_t2 = get_accessible_class(co.id, teacher=t2)
        assert accessible_co_t2.id == co.id
        assert is_owner(co.id, teacher=t2) is False
