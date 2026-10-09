# Licensing and distribution notes

> Informational guidance, not individualized legal advice.

## Project code

SRA's original, copyrightable source code is offered under the GNU
Affero General Public License, version 3 **only** (`AGPL-3.0-only`), to
the extent the contributors own or control the rights needed to license
it. The complete, unmodified license text is in [`../LICENSE`](../LICENSE).

This is a strong copyleft open-source license. Commercial use, including
charging for distribution or services, is **allowed**. Covered modified
versions that are distributed must follow the license's source-code and
notice obligations. The AGPL also has obligations for modified programs
that interact with users remotely over a network.

The AGPL is **not** a ban on all commercial use and does not monopolize
the underlying idea of a social-science research assistant. Independently
developed software that does not copy protected material is not governed
by this project's license just because it performs similar tasks.

## AI-assisted development

SRA has been developed with substantial AI assistance. Human maintainers
set goals, select designs, evaluate behavior, integrate and revise output,
and make release decisions. Copyright treatment of particular AI-generated
material varies with the degree of human authorship and governing law.
This project's license and notices do not assert exclusive rights in
material that is not copyright-protected or that the project does not own.

## Branding and graphical assets

Review [`../TRADEMARKS.md`](../TRADEMARKS.md) before distributing an
unofficial build under the SRA name or reusing its visual identity.
The code license does not automatically resolve rights in logos, fonts,
third-party artwork, or AI-generated visual material.

## Dependencies and bundled installers

Bundled Python packages, .NET libraries, Docling/PyTorch components,
models, fonts, and runtime assets may carry separate licenses. For every
released Windows installer and macOS application bundle:

1. Retain and ship required third-party license and attribution notices.
2. Check compatibility with AGPL-3.0-only where software is combined.
3. Ensure notices and corresponding source availability obligations are
   satisfied for the parts that require them.
4. Verify third-party model weights and download terms separately; do not
   assume a pip installable package gives unlimited redistribution rights.

## Private user materials

User-imported PDFs, their copyright, locally stored API keys, research
notes, and SQLite libraries are **not** redistributed or licensed by
this repository. Do not copy personal user data into release artifacts.

## Switching from another license

If you previously shared a version under Apache-2.0 or another
irrevocable license, simply editing `LICENSE` cannot retroactively
revoke grants already made. Confirm the repository history before
changing licensing terms, especially when external contributors exist.
