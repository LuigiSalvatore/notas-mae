# Plataforma de Notas e Frequência — Product Spec

**Purpose:** A self-hosted web app for a single teacher to manage her class rosters, grades, and attendance, replacing manual spreadsheets built from university-issued PDF rosters. This document is written to be handed to a coding agent (e.g. Claude Code) as a build brief.

**User:** One teacher, non-technical, older adult. Single user, no multi-tenant login system needed. Optimize every screen for *fewest clicks* and *hard to break*.

**Hosting:** Self-hosted on a personal Debian server.

---

## 1. Source data reality (from sample rosters)

The university PDF ("Lista de Chamada / Frequência") has:
- **A header block** with: Atividade de Ensino (subject name), Turma, Período Letivo (e.g. `2025/1`), Créditos, Nível, Professor(es), Data Emissão.
- **A table**: Seq, Aluno (type, e.g. GRAD), Cartão (student ID number), Nome, then a row of **empty checkbox/grid cells** under "Avaliação" and "Freq."

Important implication: **the PDF only ever contains the roster (names + IDs), never grades or attendance.** Those blank grid columns are for paper use — all grade/attendance data lives only inside this app. The import step is roster-only.

Other things observed in the sample data:
- The same subject reappears across periods (`2025/1`, `2026/1`) as a **different class instance** — same subject/turma structure, mostly different students, occasional repeats (matched by Cartão).
- PDFs are sometimes password-protected, sometimes not.
- Names have accents and inconsistent capitalization (some ALL CAPS, some Title Case) — don't rely on exact-casing matching anywhere (search, dedup).

---

## 2. Suggested tech stack (agent can adjust)

Not decided yet — the following is a reasonable default to hand to a build agent, optimized for solo maintenance on a small Debian box:

- **Backend:** Python (FastAPI or Flask) — good PDF/table-parsing ecosystem (`pdfplumber`, `pikepdf`/`qpdf` for password removal, `reportlab` or `weasyprint` for PDF export).
- **DB:** SQLite (simple, file-based, trivial to back up; fine for single-user scale). Postgres only if the agent expects to scale beyond one user later.
- **Frontend:** Server-rendered pages (Jinja2) with a light sprinkle of JS, or a small React/Vue SPA — either is fine given this is a single low-traffic user; prioritize simplicity over framework sophistication.
- **Auth:** Single login (username + password), session cookie, HTTPS via Let's Encrypt/Certbot.
- **Process management:** systemd service + nginx reverse proxy (standard Debian pattern).
- **Backups:** nightly cron job dumping the SQLite file (or `pg_dump`) to a second location.

---

## 3. Data model (entities)

- **Teacher** — single row, login credentials.
- **ClassOffering** ("card") — subject name, turma, período letivo, créditos, nível, professores, created_at, archived flag. Uniqueness key: (subject + turma + período letivo).
- **Student** — cartão (unique ID from university), nome, notes/comments field.
- **Enrollment** — links Student ↔ ClassOffering; status flag (active / removed-but-kept-for-history).
- **AssessmentComponent** — belongs to a ClassOffering. Fields: name (e.g. "Prova 1", "Trabalho Final"), weight (%), scale_type (`numeric_0_10` or `letter_A_F`), max_value, optional `covers_component_id` (self-reference used only by PS components, pointing to the original exam they substitute for in the average).
- **Grade** — Enrollment × AssessmentComponent → value (stored on one internal canonical scale, e.g. 0–10, with a conversion table for A–F display). Also carries a status: `graded` / `missed_justified` (eligible for PS) / `missed_unjustified` (counts as 0).
- **AttendanceSession** — belongs to ClassOffering; a date.
- **AttendanceRecord** — Enrollment × AttendanceSession → one of: `unmarked` ("/", the default before she takes attendance), `present`, `absent`, `justified_absence`.
- **ClassSettings** — per ClassOffering: minimum attendance % to pass (default 75%), minimum average grade to pass (default **7.0**, configurable), passing scale, default grade-aggregation method (see 4.4).
- **ActionLog** — simplified audit trail: timestamp, action type (e.g. "grade edited," "attendance marked," "student removed"), a short human-readable description of what changed and the previous value, and which entity it applies to. Powers both the audit view and the undo feature below.
- **BackupJob** — timestamp, trigger reason ("grade edit finished," manual), file paths (xlsx + pdf), size, status (sent / failed). Used to enforce the 200MB retention cap (see 4.7).

---

## 4. Core features

### 4.1 PDF Roster Import
- Upload a PDF; if encrypted, prompt for password inline before proceeding.
- Parse header block → auto-suggest matching an existing ClassOffering or creating a new one, using (subject + turma + período letivo) as the match key.
- Parse the roster table → extract Cartão + Nome for each row.
- **Show a preview/diff before committing**: new students to add, students missing from the new PDF (never hard-delete — mark as "removed" but keep their historical grades/attendance), and no-change students.
- Manual "add student" option for late enrollments not on any PDF.

### 4.2 Dashboard (class cards)
- One card per ClassOffering: subject, turma, período letivo, créditos, professor(es), student count, at-a-glance risk indicator (e.g. "3 students at risk").
- Filter/group by período letivo; toggle between "current" and "archived" semesters so old classes don't clutter the view.

### 4.3 Inside a class — student list
- First-letter filter + free-text searchbar (as requested).
- Sortable by name, cartão, current average, attendance %.
- Auto-computed status badge per student: **Aprovado**, **Reprovado por nota**, **Reprovado por falta**, **Em risco**. Status coloring is **toggleable** — a switch to turn color-coding on/off, for when she wants a plainer view (e.g. before exporting a screenshot, or if colors are visually noisy for her).
- Dedicated "Reprovados" view listing everyone failing for any reason (grade or attendance), with the reason shown.
- Optional: "what grade does this student need on the remaining assessments to pass" calculator, using the weighted-average config.

### 4.4 Dynamic grading system
- Per class, teacher defines her own AssessmentComponents (name, weight, scale — numeric 0.0–10.0 or letter A–F).
- **Default aggregation is a simple average of all exam components** (equal weight) — matches how most of her classes are actually graded. Weighted averages remain available as an override per class for the classes that need it.
- Grade entry as a **spreadsheet-style grid**: students as rows, assessment components as columns, tab/enter navigation for fast bulk entry.
- **Missed-exam handling**: any grade cell can be marked "missed" instead of given a value. If marked *justified*, the student is flagged as eligible for **PS (Prova de Substituição)** and excluded from the average until a PS grade is entered. If marked *unjustified*, the system automatically counts it as 0 in the average.
- **PS as its own component**: the PS grade is **not** overwritten into the original exam's cell — it's tracked as its own AssessmentComponent (e.g. "PS — Prova 1"), linked to the exam it covers, so both the missed original and the PS result stay visible in her records. For averaging, the PS component's grade substitutes for that student's missing original-exam grade (the original stays recorded as "missed" rather than being deleted); students who weren't missing that exam simply have no value in the PS column and it doesn't affect their average.
- A **"# students who missed this exam"** counter shown per assessment column, split into justified (PS-eligible) vs unjustified, so she can see at a glance who needs to take a PS.
- Weighted/simple average auto-calculated and shown live as she enters grades.
- Internal storage on one canonical scale with a conversion table, but displayed per the class's chosen scale.

### 4.5 Attendance
- Calendar of class sessions (dates); create a new session with one click.
- **Default state for every student is "/" (unmarked)** — not a specific one-click "mark all present" — since she wants explicit control. Two clear tap targets per student: **Presente** and **Ausente** (with a third option for justified absence), so nothing is assumed until she marks it.
- Auto-rolls into a frequency % per student, compared against the class's configured minimum. **Formula**: `frequência % = presente / (presente + ausente) × 100` — justified absences (`justified_absence`) are **excluded from the calculation entirely** (neither help nor hurt), only unjustified absences count against her. Unmarked (`/`) sessions don't count in either direction until she marks them.

### 4.6 PDF export builder
- Custom export: checkboxes to choose which columns are included (Cartão, Nome, each assessment's grade, weighted average, frequência %, status).
- **Anonymized preset**: Cartão + grades only, no names — matches her specific stated need.
- Additional presets: "Lista de reprovados," "Boletim individual" (one student, full detail).
- Output styled reasonably close to the university's own PDF header format if she wants an "official" look.

### 4.7 Automated backups (Excel + PDF, sent by email)
- **Trigger**: automatically fires when she finishes a grade-editing session for a class (e.g. on save / on leaving the grade-entry grid after making changes). Also available as a manual "back up now" button.
- **Contents**: names, cartões, all grades, and attendance/frequência for that class, generated as both a `.xlsx` file and a `.pdf` in the same run.
- **Delivery**: emailed to an address she configures once in settings (not asked each time). No interactive login is required at send-time — she authorizes it once during setup, either via a **Gmail App Password** (free, generated once in her Google Account security settings, pasted into the app's SMTP config) or via a **transactional email API key** (e.g. Resend, Mailgun, Brevo — free tier is plenty for this volume, and tends to be more reliable than SMTP-from-a-home-server for deliverability). Either credential goes in the Settings page (§7) and is reused silently for every backup.
- **Storage cap**: keep backups on disk but enforce a **hard 200MB total cap** — oldest backups are deleted first (FIFO) once the cap would be exceeded by a new one. The email copy is the durable one; local copies are just a short-term safety net, not the archive.
- Log every backup attempt (success/failure, size, timestamp) in the BackupJob table so failures are visible somewhere, since silent email failures would be easy to miss.

---

## 5. UX principles (important given the user)

- Big touch targets, minimal steps per action, avoid multi-page wizards where a single screen will do.
- Confirm before anything destructive (deleting a student, overwriting existing grades, removing a session).
- **Undo**: every meaningful action (grade edit, attendance mark, student removal, roster import) is written to the ActionLog with enough detail (previous value) to revert it. Provide a visible "recent actions" panel with an **Undo** button per entry — not just the single last action, but a short history (e.g. last 20) so she can roll back a few steps if she notices a mistake later, not just immediately after.
- **Simplified audit log**: a readable list of "what changed" per class — e.g. "14/07 14:02 — Nota de Prova 1 alterada para João Silva (7.0 → 8.5)" — the same underlying ActionLog data, just surfaced as a log view rather than only as undo buttons.
- Mobile-friendly attendance screen — she should be able to take attendance from her phone while walking around the classroom.
- Avoid jargon in the UI — use the same Portuguese terms she already knows from the university PDFs (Cartão, Avaliação, Frequência, Turma, Período Letivo).
- **Login**: single username/password login screen for her account, session-based, "remember me" option so she isn't re-entering credentials constantly. A password-reset flow can piggyback on the backup email address once that's configured.
- **PT / EN language toggle** for the whole interface — build this **last**, after everything else is functional, since it's explicitly a nice-to-have rather than core.

---

## 6. Non-functional requirements

- HTTPS (Let's Encrypt), single login behind auth for the whole app — student grades are sensitive personal data.
- Automated nightly backup of the database file to a separate location (this is distinct from the per-class email backups in 4.7 — this one is a full-system safety net, not capped at 200MB since it's her own server storage, not a user-facing feature).
- No public indexing (robots.txt / server not exposed beyond what's needed).
- **Full data export to Excel**: one-click export of everything (all classes, students, grades, attendance) into a single workbook, as a safety net independent of the per-class PDF/Excel exports.
- **Partial Excel import**: she can upload a `.xlsx` that's already partially filled in with grades (e.g. one she's been keeping manually) and have it matched to students by Cartão, filling in the corresponding AssessmentComponent columns. Should tolerate a partially-filled sheet (blank cells = not yet graded, not zero) and show a preview/diff before committing, same pattern as the PDF roster import.

---

## 7. Nice-to-haves for later phases (not MVP)

- Cross-semester student history (matched by Cartão) — see attendance/grade trends for a returning student across years.
- Class-level statistics: grade distribution histogram, average attendance trend over the semester.
- Free-text notes per student (e.g. "talked to student about attendance on [date]").
