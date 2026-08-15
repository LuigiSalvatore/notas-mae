from app.utils import compute_student_average
from app.models import AssessmentComponent, Grade, Enrollment, ClassOffering, ClassSettings

def test_simple_average():
    co = ClassOffering(subject_name="Test")
    co.settings = ClassSettings(aggregation_method="simple_avg")
    e = Enrollment(class_offering=co)

    c1 = AssessmentComponent(id=1, name="P1", weight=1.0)
    c2 = AssessmentComponent(id=2, name="P2", weight=1.0)
    comps = [c1, c2]

    # Both graded
    gmap = {
        1: Grade(component_id=1, value=8.0, status="graded"),
        2: Grade(component_id=2, value=6.0, status="graded")
    }
    assert compute_student_average(e, comps, gmap) == 7.0

def test_unjustified_miss_is_zero():
    co = ClassOffering(subject_name="Test")
    co.settings = ClassSettings(aggregation_method="simple_avg")
    e = Enrollment(class_offering=co)

    c1 = AssessmentComponent(id=1, name="P1", weight=1.0)
    c2 = AssessmentComponent(id=2, name="P2", weight=1.0)
    comps = [c1, c2]

    # One graded, one unjustified miss
    gmap = {
        1: Grade(component_id=1, value=8.0, status="graded"),
        2: Grade(component_id=2, value=None, status="missed_unjustified")
    }
    assert compute_student_average(e, comps, gmap) == 4.0

def test_justified_miss_with_ps():
    co = ClassOffering(subject_name="Test")
    co.settings = ClassSettings(aggregation_method="simple_avg")
    e = Enrollment(class_offering=co)

    c1 = AssessmentComponent(id=1, name="P1", weight=1.0)
    c2 = AssessmentComponent(id=2, name="P2", weight=1.0)
    ps = AssessmentComponent(id=3, name="PS", weight=1.0, covers_component_id=1)
    comps = [c1, c2, ps]

    # P1 is justified miss, PS is graded to 9.0, P2 is 5.0
    gmap = {
        1: Grade(component_id=1, value=None, status="missed_justified"),
        2: Grade(component_id=2, value=5.0, status="graded"),
        3: Grade(component_id=3, value=9.0, status="graded")
    }
    # Avg should be (9.0 [PS] + 5.0 [P2]) / 2 = 7.0
    assert compute_student_average(e, comps, gmap) == 7.0

def test_justified_miss_without_ps_excludes():
    co = ClassOffering(subject_name="Test")
    co.settings = ClassSettings(aggregation_method="simple_avg")
    e = Enrollment(class_offering=co)

    c1 = AssessmentComponent(id=1, name="P1", weight=1.0)
    c2 = AssessmentComponent(id=2, name="P2", weight=1.0)
    ps = AssessmentComponent(id=3, name="PS", weight=1.0, covers_component_id=1)
    comps = [c1, c2, ps]

    # P1 is justified miss, no PS grade entered yet, P2 is 6.0
    gmap = {
        1: Grade(component_id=1, value=None, status="missed_justified"),
        2: Grade(component_id=2, value=6.0, status="graded")
    }
    # P1 should be excluded completely, so avg = 6.0
    assert compute_student_average(e, comps, gmap) == 6.0
