"""
Excel grade import — partial fill-in matched by Cartão.

Sheet format expected:
  Row 1: headers — "Cartão", "Nome", then one column per AssessmentComponent name
  Row 2+: data rows, Cartão in col 0, blank cells = not yet graded (not zero)
"""

from dataclasses import dataclass, field

import openpyxl


@dataclass
class XlsxGradeDiff:
    component_name: str
    cartao:         str
    student_name:   str
    old_value:      float | None
    old_status:     str | None
    new_value:      float | None   # None means blank in xlsx = skip


@dataclass
class XlsxImportResult:
    matched_components: list[str] = field(default_factory=list)
    unmatched_columns:  list[str] = field(default_factory=list)
    diffs:              list[XlsxGradeDiff] = field(default_factory=list)
    error:              str | None = None


def parse_xlsx_grades(
    file_bytes: bytes,
    class_offering,
    grade_maps: dict,   # {enrollment_id: {component_id: Grade}}
) -> XlsxImportResult:
    """
    Parse an Excel file and return a diff of grade changes.

    Tolerates:
      - Missing component columns (they are reported as unmatched)
      - Blank cells (skipped — not treated as 0)
      - Case-insensitive component name matching
      - Extra columns (ignored)
    """
    import io
    from app.utils import strip_accents

    result = XlsxImportResult()

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
    except Exception as exc:
        result.error = str(exc)
        return result

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        result.error = "Planilha vazia."
        return result

    header_row = [str(c).strip() if c is not None else "" for c in rows[0]]

    # Build component lookup: normalised_name → AssessmentComponent
    comp_lookup = {
        strip_accents(c.name): c
        for c in class_offering.components
    }

    # Map xlsx column index → AssessmentComponent (skip Cartão/Nome cols)
    col_to_comp = {}
    for idx, col_name in enumerate(header_row):
        if idx < 2:
            continue   # Cartão and Nome columns
        key = strip_accents(col_name)
        if key in comp_lookup:
            col_to_comp[idx] = comp_lookup[key]
            result.matched_components.append(col_name)
        elif col_name:
            result.unmatched_columns.append(col_name)

    # Build cartão → enrollment lookup
    cartao_to_enrollment = {
        e.student.cartao.strip(): e
        for e in class_offering.active_enrollments
    }

    for row in rows[1:]:
        if not row or row[0] is None:
            continue
        cartao = str(row[0]).strip()
        enrollment = cartao_to_enrollment.get(cartao)
        if not enrollment:
            continue   # student not in this class

        gmap = grade_maps.get(enrollment.id, {})

        for col_idx, comp in col_to_comp.items():
            if col_idx >= len(row):
                continue
            cell_val = row[col_idx]
            if cell_val is None or str(cell_val).strip() == "":
                continue   # blank = not yet graded, skip

            try:
                new_value = float(cell_val)
            except (ValueError, TypeError):
                continue   # non-numeric, skip

            # Clamp to 0–10
            new_value = max(0.0, min(10.0, new_value))

            existing_grade = gmap.get(comp.id)
            old_value  = existing_grade.value  if existing_grade else None
            old_status = existing_grade.status if existing_grade else None

            # Only include in diff if value is different
            if old_value is not None and abs(old_value - new_value) < 0.001:
                continue

            result.diffs.append(XlsxGradeDiff(
                component_name=comp.name,
                cartao=cartao,
                student_name=enrollment.student.nome,
                old_value=old_value,
                old_status=old_status,
                new_value=new_value,
            ))

    return result
