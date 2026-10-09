# Contributing to SRA

Thank you for considering a contribution. SRA is an early-stage
research software project with a focus on auditable academic reading.

## Before you contribute

- Open an issue before proposing a major architectural or product change.
- Respect the distinction between author claims, AI review, and user notes.
- Preserve paper provenance, claim/evidence traceability, and failure states.
- Never submit personal API keys, imported PDFs, private notes, databases,
  `.env` files, or generated model caches.
- Only submit code and other material that you are entitled to license.
  Verify third-party compatibility and preserve applicable notices.

## Working on the project

- Python core and local API: `src/sociology_research/`.
- Avalonia desktop: `desktop/SRA.Desktop/`.
- Run existing Python tests with `python -m pytest -q` from your configured
  development environment.
- Build the desktop with:
  `dotnet build desktop/SRA.Desktop/SRA.Desktop.csproj`.
- Add regression tests for parser, evidence, schema, API, security,
  packaging, or UI behavior changes.

## AI-assisted contributions

AI-assisted code and documentation are welcome. Review and test every
proposed change yourself; identify external material that was copied or
adapted and ensure you have the right to submit it. AI assistance is
not a substitute for testing, attribution, or license compliance.

## License of contributions

By contributing, you confirm that you have the necessary rights to
submit your contribution under the project's `AGPL-3.0-only` license,
subject to any separate license explicitly indicated for third-party
material. There is no contributor license agreement or assignment of
exclusive copyright in this starter policy.

Repository: https://github.com/daseinzc-509/social-science-research-ide
