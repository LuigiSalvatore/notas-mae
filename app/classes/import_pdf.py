"""
PDF roster import pipeline.

Steps:
  1. Open the PDF (pikepdf strips encryption if a password is supplied).
  2. Extract header fields from per-page text via regex.
  3. Parse the student table (pdfplumber) — columns: Seq, Aluno, Cartão, Nome, ...blanks
  4. Return a list[ParseResult] — one entry per class block in the PDF.
     Single-class PDFs return a list of length 1 (backward-compatible).

Multi-class detection:
  A new class block starts whenever the (subject_name, turma) combination seen
  at the top of a page differs from the current block's values.
"""

import io
import re
from dataclasses import dataclass, field

import pikepdf
import pdfplumber

from app.utils import strip_accents


@dataclass
class ParsedHeader:
    subject_name:   str = ""
    turma:          str = ""
    periodo_letivo: str = ""
    creditos:       int | None = None
    nivel:          str = ""
    professores:    str = ""
    department:     str = ""
    data_emissao:   str = ""


@dataclass
class ParsedStudent:
    seq:        int
    aluno_type: str   # e.g. "GRAD"
    cartao:     str
    nome:       str


@dataclass
class ParseResult:
    header:   ParsedHeader
    students: list[ParsedStudent]
    error:    str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry points
# ─────────────────────────────────────────────────────────────────────────────

def parse_pdf(file_bytes: bytes, password: str = "") -> ParseResult:
    """
    Parse a university roster PDF (single-class or multi-class).

    For backward compatibility, returns only the FIRST ParseResult.
    Use parse_pdf_multi() to get all classes.

    Returns a ParseResult. On encryption error without a password,
    result.error will be set to 'encrypted'.
    """
    results = parse_pdf_multi(file_bytes, password=password)
    return results[0]


def parse_pdf_multi(file_bytes: bytes, password: str = "") -> list[ParseResult]:
    """
    Parse a university roster PDF that may contain one or more class blocks.

    Returns a list of ParseResult objects, one per class.
    On error, returns a list with a single ParseResult where .error is set.
    """
    # Step 1: strip encryption with pikepdf
    try:
        pk_pdf = pikepdf.open(io.BytesIO(file_bytes), password=password or "")
    except pikepdf.PasswordError:
        return [ParseResult(header=ParsedHeader(), students=[], error="encrypted")]
    except Exception as exc:
        return [ParseResult(header=ParsedHeader(), students=[], error=str(exc))]

    buf = io.BytesIO()
    pk_pdf.save(buf)
    buf.seek(0)

    # Step 2 + 3: extract per-page headers and student rows, then segment
    try:
        with pdfplumber.open(buf) as pdf:
            results = _extract_all_classes(pdf)
    except Exception as exc:
        return [ParseResult(header=ParsedHeader(), students=[], error=str(exc))]

    return results if results else [ParseResult(header=ParsedHeader(), students=[])]


# ─────────────────────────────────────────────────────────────────────────────
# Multi-class segmentation
# ─────────────────────────────────────────────────────────────────────────────

def _class_key(header: ParsedHeader) -> tuple[str, str]:
    """Canonical key for deduplicating class blocks."""
    return (
        strip_accents(header.subject_name or "").upper().strip(),
        (header.turma or "").upper().strip(),
    )


def _extract_all_classes(pdf: pdfplumber.PDF) -> list[ParseResult]:
    """
    Walk every page; when the class header changes, start a new ParseResult block.
    Each page carries its own header info so we can detect class boundaries.
    """
    results: list[ParseResult] = []
    current_header: ParsedHeader | None = None
    current_students: list[ParsedStudent] = []
    seen_cartaos: set[str] = set()

    for page in pdf.pages:
        page_text  = page.extract_text() or ""
        page_header = _extract_header_from_text(page_text)

        # Determine if this page starts a new class block
        if current_header is None:
            # First page
            current_header = page_header
        elif _class_key(page_header) != _class_key(current_header) and page_header.subject_name:
            # New class detected — flush current block
            results.append(ParseResult(header=current_header, students=current_students))
            current_header  = page_header
            current_students = []
            seen_cartaos    = set()

        # Extract students from this page's tables
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                if not row or len(row) < 4:
                    continue
                seq_val = (row[0] or "").strip()
                if not seq_val.isdigit():
                    continue   # header row or empty

                try:
                    seq        = int(seq_val)
                    aluno_type = (row[1] or "").strip()
                    cartao     = (row[2] or "").strip()
                    nome       = (row[3] or "").strip()
                except (IndexError, ValueError):
                    continue

                if not cartao or not nome:
                    continue
                if cartao in seen_cartaos:
                    continue   # duplicate across pages (header repeat)

                seen_cartaos.add(cartao)
                current_students.append(ParsedStudent(
                    seq=seq,
                    aluno_type=aluno_type,
                    cartao=cartao,
                    nome=nome,
                ))

    # Flush last block
    if current_header is not None:
        results.append(ParseResult(header=current_header, students=current_students))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Header extraction
# ─────────────────────────────────────────────────────────────────────────────

_HEADER_PATTERNS = {
    "department":     re.compile(r"^DEPARTAMENTO\s+.+", re.IGNORECASE),
    "subject_name":   re.compile(r"Atividade de Ensino:\s*(.+)", re.IGNORECASE),
    "turma":          re.compile(r"Turma:\s*(\S+)", re.IGNORECASE),
    "periodo_letivo": re.compile(r"Per[ií]odo Letivo:\s*(\S+)", re.IGNORECASE),
    "creditos":       re.compile(r"Cr[ée]ditos:\s*(\d+)", re.IGNORECASE),
    "nivel":          re.compile(r"N[ií]vel:\s*(.+?)(?:\s{2,}|Professor)", re.IGNORECASE),
    "professores":    re.compile(r"Professor\(es\):\s*(.+)", re.IGNORECASE),
    "data_emissao":   re.compile(r"Data Emiss[aã]o:\s*(\S+)", re.IGNORECASE),
}


def _extract_header_from_text(text: str) -> ParsedHeader:
    """Parse header fields out of a single page's text."""
    lines = text.splitlines()
    header = ParsedHeader()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if not header.department and _HEADER_PATTERNS["department"].match(line):
            header.department = line.strip()
            continue

        m = _HEADER_PATTERNS["subject_name"].search(line)
        if m and not header.subject_name:
            header.subject_name = m.group(1).strip()
            continue

        m = _HEADER_PATTERNS["turma"].search(line)
        if m and not header.turma:
            header.turma = m.group(1).strip()

        m = _HEADER_PATTERNS["periodo_letivo"].search(line)
        if m and not header.periodo_letivo:
            header.periodo_letivo = m.group(1).strip()

        m = _HEADER_PATTERNS["creditos"].search(line)
        if m and header.creditos is None:
            try:
                header.creditos = int(m.group(1))
            except ValueError:
                pass

        m = _HEADER_PATTERNS["data_emissao"].search(line)
        if m and not header.data_emissao:
            header.data_emissao = m.group(1).strip()

        m = _HEADER_PATTERNS["professores"].search(line)
        if m and not header.professores:
            header.professores = m.group(1).strip()

        m = _HEADER_PATTERNS["nivel"].search(line)
        if m and not header.nivel:
            header.nivel = m.group(1).strip()

    return header


def _extract_header(pdf: pdfplumber.PDF) -> ParsedHeader:
    """Backward-compat: extract header from the first page only."""
    first_page_text = pdf.pages[0].extract_text() or ""
    return _extract_header_from_text(first_page_text)


def _extract_students(pdf: pdfplumber.PDF) -> list[ParsedStudent]:
    """
    Backward-compat: extract student rows from all pages (single-class assumed).
    """
    seen_cartaos: set[str] = set()
    students: list[ParsedStudent] = []

    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                if not row or len(row) < 4:
                    continue
                seq_val = (row[0] or "").strip()
                if not seq_val.isdigit():
                    continue   # header row or empty

                try:
                    seq        = int(seq_val)
                    aluno_type = (row[1] or "").strip()
                    cartao     = (row[2] or "").strip()
                    nome       = (row[3] or "").strip()
                except (IndexError, ValueError):
                    continue

                if not cartao or not nome:
                    continue
                if cartao in seen_cartaos:
                    continue   # duplicate across pages (header repeat)

                seen_cartaos.add(cartao)
                students.append(ParsedStudent(
                    seq=seq,
                    aluno_type=aluno_type,
                    cartao=cartao,
                    nome=nome,
                ))

    return students


# ─────────────────────────────────────────────────────────────────────────────
# Diff computation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiffResult:
    new_students:     list[ParsedStudent] = field(default_factory=list)
    removed_students: list  = field(default_factory=list)   # list of Enrollment
    unchanged:        list  = field(default_factory=list)   # list of (ParsedStudent, Enrollment)


def compute_diff(parsed_students: list[ParsedStudent], existing_enrollments: list) -> DiffResult:
    """
    Compare parsed PDF students against existing active enrollments.

    Matching is by cartao (case-insensitive strip).
    Returns:
      new_students     — in PDF but not in DB (will be added)
      removed_students — in DB (active) but not in PDF (will be marked 'removed')
      unchanged        — in both (no action)
    """
    pdf_cartaos = {s.cartao.strip(): s for s in parsed_students}
    db_cartaos  = {e.student.cartao.strip(): e
                   for e in existing_enrollments if e.status == "active"}

    new_students     = [s for c, s in pdf_cartaos.items() if c not in db_cartaos]
    removed_students = [e for c, e in db_cartaos.items() if c not in pdf_cartaos]
    unchanged        = [(pdf_cartaos[c], e)
                        for c, e in db_cartaos.items() if c in pdf_cartaos]

    return DiffResult(
        new_students=new_students,
        removed_students=removed_students,
        unchanged=unchanged,
    )
