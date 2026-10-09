SRA AGPL-3.0-only license kit
============================

1. Copy LICENSE, NOTICE, TRADEMARKS.md, CONTRIBUTING.md, and docs/
   LICENSING.md into your repository root, preserving directory names.
2. Optional: review README.md in this ZIP and use it only if it does
   not overwrite newer local README changes. Alternatively, copy the
   License and AI-assisted development paragraphs into your own README.
3. Confirm the copyright/maintainer identification in NOTICE. The
   GitHub username is a placeholder for whoever holds applicable rights.
4. Check existing third-party dependency/asset licensing before release.
5. Check git status and diff before staging files. Do not push .env,
   local PDF files, SQLite databases, caches or Conda environments.

PowerShell examples:

  git status --short
  git add LICENSE NOTICE TRADEMARKS.md CONTRIBUTING.md docs/LICENSING.md README.md
  git diff --cached
  git commit -m "docs: adopt AGPL-3.0-only and contribution policy"
  git push

IMPORTANT:
- AGPL permits commercial use; it does not permit keeping covered
  distributed modified versions closed source without meeting obligations.
- This kit is not a lawyer's opinion or confirmation of title.
- README.md is a documentation candidate, not a requirement to overwrite
  local work. LICENSE contains the unmodified AGPLv3 text.
