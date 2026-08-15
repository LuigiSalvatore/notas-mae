import os
from app.classes.import_pdf import parse_pdf, compute_diff, ParsedStudent
from app.models import Enrollment, Student

def test_parse_decrypted_pdf():
    # Verify parsing on freq_decrypted.pdf if it was saved
    pdf_path = "E:\\projects\\mae\\chamada provas\\freq_decrypted.pdf"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            res = parse_pdf(f.read())
        assert res.error is None
        assert res.header.subject_name == "INTRODUÇÃO À EMBRIOLOGIA"
        assert res.header.turma == "U"
        assert res.header.periodo_letivo == "2025/1"
        assert len(res.students) == 75
        assert res.students[0].nome == "Alana Gabriele Gomes"
        assert res.students[0].cartao == "600290"

def test_parse_regular_pdf():
    # Verify parsing on unencrypted lista26.1.pdf
    pdf_path = "E:\\projects\\mae\\chamada provas\\lista26.1.pdf"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            res = parse_pdf(f.read())
        assert res.error is None
        assert "EMBRIOLOGIA" in res.header.subject_name.upper()
        assert res.header.turma == "U"
        assert res.header.periodo_letivo == "2026/1"
        assert len(res.students) == 83
        assert res.students[0].nome == "Amanda Moraes Hackenhaar"
        assert res.students[0].cartao == "614049"


def test_compute_diff():
    # Test diff computation
    parsed = [
        ParsedStudent(seq=1, aluno_type="GRAD", cartao="111", nome="Joao"),
        ParsedStudent(seq=2, aluno_type="GRAD", cartao="222", nome="Maria")
    ]
    # mock existing active enrollment
    e1 = Enrollment()
    e1.student = Student(cartao="222", nome="Maria")
    e1.status = "active"

    e2 = Enrollment()
    e2.student = Student(cartao="333", nome="Jose")
    e2.status = "active"

    diff = compute_diff(parsed, [e1, e2])
    assert len(diff.new_students) == 1
    assert diff.new_students[0].cartao == "111"
    assert len(diff.removed_students) == 1
    assert diff.removed_students[0].student.cartao == "333"
    assert len(diff.unchanged) == 1
    assert diff.unchanged[0][0].cartao == "222"
