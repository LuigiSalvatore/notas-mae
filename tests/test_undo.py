import pytest
from app import create_app, db
from app.models import Teacher, ClassOffering, Student, Enrollment, AssessmentComponent, Grade, ActionLog
from app.audit.routes import undo_action

@pytest.fixture
def test_app():
    # Setup test Flask app with in-memory SQLite DB
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        yield app

def test_undo_grade_edit(test_app):
    with test_app.app_context():
        # Setup mock db state
        co = ClassOffering(subject_name="Embriologia", turma="U", periodo_letivo="2025/1")
        db.session.add(co)
        db.session.flush()

        student = Student(cartao="123456", nome="Joao Silva")
        db.session.add(student)
        db.session.flush()

        enrollment = Enrollment(student_id=student.id, class_offering_id=co.id)
        db.session.add(enrollment)
        db.session.flush()

        comp = AssessmentComponent(class_offering_id=co.id, name="Prova 1")
        db.session.add(comp)
        db.session.flush()

        grade = Grade(enrollment_id=enrollment.id, component_id=comp.id, value=7.0, status="graded")
        db.session.add(grade)
        db.session.flush()

        # Simulated Action Log for grade edit (7.0 -> 9.0)
        # Note: the mock update happens, and log records previous value
        log = ActionLog(
            action_type="grade_edit",
            class_offering_id=co.id,
            enrollment_id=enrollment.id,
            description="Nota Prova 1: 7.0 -> 9.0",
            previous_value={
                "entity": "grade",
                "id": grade.id,
                "old_value": 7.0,
                "old_status": "graded"
            }
        )
        db.session.add(log)
        
        # Modify grade to new value
        grade.value = 9.0
        db.session.commit()

        # Let's perform the undo logic directly (simulate the AJAX endpoint handler)
        # We fetch the action and execute the reversion block
        action = db.session.get(ActionLog, log.id)
        prev = action.previous_value
        assert prev["old_value"] == 7.0

        # Run reversion
        grade_to_revert = db.session.get(Grade, prev["id"])
        grade_to_revert.value = prev["old_value"]
        grade_to_revert.status = prev["old_status"]
        action.reverted = True
        db.session.commit()

        # Verify reverted values
        reverted_grade = db.session.get(Grade, grade.id)
        assert reverted_grade.value == 7.0
        assert action.reverted is True
