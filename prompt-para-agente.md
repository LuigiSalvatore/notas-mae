Build "Plataforma de Notas e Frequência" — a self-hosted web app for a single teacher (non-technical, older adult) to manage class rosters, grades, and attendance, replacing manual spreadsheets built from university PDF rosters.

I'm giving you two attached documents — use them together, not as competing sources:

1. **`plataforma-notas-frequencia-spec.md`** — the product spec. This is the source of truth for *what the app must do and why* (features, data model, defaults, UX rules).
2. **The implementation plan** (Flask/Jinja2/Alpine.js/SQLite/pdfplumber/pikepdf/openpyxl/WeasyPrint/Flask-Login/systemd+Gunicorn+nginx, with the file structure, SQLAlchemy schema, and phase breakdown) — this is the source of truth for *how to build it* (architecture, file layout, tech choices). Follow it as the base plan unless it conflicts with a decision below, in which case the decision below wins.

A few decisions were finalized after that plan was written — apply these, overriding any default or open question in the two documents where they conflict:

- **Frequência % formula**: `presente / (presente + ausente) × 100`. Justified absences (`justified_absence`) are excluded entirely from the calculation — they neither help nor hurt. Unmarked (`/`) sessions don't count until she marks them. Only unjustified absences reduce frequência.
- **PS (Prova de Substituição)**: always its own `AssessmentComponent` (e.g. "PS — Prova 1"), linked via `covers_component_id` to the exam it substitutes for. Never overwrite the original missed exam's grade cell. In averaging, the PS value stands in only for the student(s) whose original exam is `missed_justified`; it's a no-op for everyone else.
- **Default minimum average grade to pass**: **7.0** (not 6.0), configurable per class in Class Settings.
- **Email backups, credential setup**: no interactive/OAuth login flow needed. Support either a Gmail **App Password** (SMTP+SSL, pasted once into Settings) or a transactional email API key (Resend/Mailgun/Brevo — pick one as the documented default in the setup script, but keep the SMTP path as a fallback). This is a one-time Settings-page configuration, not part of the per-backup flow.
- **Backup storage cap**: hard 200MB cap on local backup files (the per-class email backups from §4.7 of the spec), enforced FIFO (delete oldest first). This is separate from and does **not** apply to the nightly full-database backup, which has no cap.
- **PT/EN toggle**: explicitly out of scope for this build. Ship Portuguese-only. Don't build any i18n scaffolding for it now.

Build order: follow the phase breakdown in the implementation plan (Foundation → Grading → Attendance → Student View & Status → Export → Audit/Undo/Backup → Settings). Within each phase, implement exactly what the spec describes — don't add scope beyond it. If something in the spec is ambiguous and blocks you from proceeding on a specific phase, ask; otherwise use your judgment and note the assumption in a comment or commit message rather than stopping.

Before writing code: read both attached documents fully, then propose the SQLAlchemy models and file structure (largely already given in the implementation plan) so I can confirm before you scaffold the project.
