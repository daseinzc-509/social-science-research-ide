# SRA Desktop — Windows + macOS bundled release (0.2 alpha)

This phase combines **Avalonia/.NET 8 self-contained desktop + bundled Python/FastAPI research backend**.
It does **not** rewrite the Python research engine or change SQLite's schema. One program launches
both processes; a per-process session token protects the private localhost API.

## Platform packages

| Platform | Architecture | CI runner | Download |
| --- | --- | --- | --- |
| Windows | x64 | `windows-latest` | `SRA-Desktop-Setup-win-x64-*.exe`, `SRA-win-x64-*.zip` |
| macOS Apple Silicon | ARM64 | `macos-26` | `SRA-osx-arm64-*.dmg`, `.zip` |

> **macOS support:** Apple Silicon (ARM64), macOS 14 or newer.
> **Intel Mac (x86_64) is not supported or packaged.** There is no
> Intel-specific PyMuPDF variant, and release CI does not start Intel runners.
> Compatibility can be reconsidered later if there is demand and dependable
> Docling/PyTorch support, but no additional maintenance branch exists now.
>
> Linux is not yet part of this release matrix. A universal macOS binary is not created: the
> architectures are built separately and must not be mixed. Distribution uses per-OS PyInstaller,
> not cross-compilation of Python native libraries.

## User experience

1. Install Windows Setup or drag `SRA.app` from the appropriate Mac DMG into Applications.
2. Double-click SRA. **Do not open `sra api` manually** for these new bundled releases.
3. Avalonia starts the bundled `sra-backend` child and waits for `/api/v1/health` over
   a randomly chosen `127.0.0.1` port; the endpoint requires a fresh 256-bit token.
4. Use Settings > Model Connection to enter *your own* Lite/Pro API key and endpoints.
5. The program stores PDFs, SQLite, notes and secret credentials in **your account's user directory**;
   updates/uninstallers do not package them or delete them.

Development checkouts without a frozen backend retain compatibility with the manually started
`conda run -p .conda-env sra api` on port 8766. This *developer* API is legacy and is not
session-authenticated: don't expose it beyond 127.0.0.1. Bundled desktop services always use
session authentication, including PDF fetches, settings, and `/docs`.

## Privacy layout

| Platform | Private data | Model credentials |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\SRA\data` | Windows DPAPI, `%LOCALAPPDATA%\SRA\config\model-keys.dpapi` |
| Mac | `~/Library/Application Support/SRA/data` | macOS Keychain (`org.daseinzc509.sra.model-credentials`) |

The existing `config.py` still handles legacy ignored `.env` files for developers. Never bundle
`.env`, a local database, cached model responses or imported PDFs. A GitHub Action checks tracked
source and the generated `.app`/Windows installer payload **before publishing**.

The imported PDF *text excerpts* are still sent to the configured external Lite/Pro model provider
during online analysis. Packaged binaries may fetch **Docling model weights at first use**;
"bundled Python runtime" does **not** mean "fully offline model inference".

## Updates and uninstall

- **Windows:** install newer setup over the existing stable AppId. Uninstall through Windows
  Installed apps. Program files are removed; your private user data is preserved.
- **macOS:** replace `SRA.app` by dragging the new app into Applications. To uninstall, drag
  `SRA.app` to Trash. Data and Keychain entries remain by default so that an accidental
  uninstall does not destroy research or model settings.
- **Both:** the in-app version checker queries public GitHub Releases, displays available
  versions, and links to the official release page. It does not silently execute downloaded
  installers or overwrite a running app. Pre-release version comparison is supported.
- **Erase personal data:** only after backing up, users may explicitly delete the private
  `SRA/data` and `SRA/config` directories (and Keychain credentials on Mac). Automatic
  uninstall deliberately never performs this destructive action.

## macOS Gatekeeper and signing (important)

The automated macOS output is a **preview ad-hoc-signed build**; it is not yet signed with an
Apple Developer ID and not notarized. Gatekeeper may refuse the first launch. Users should
confirm the GitHub Release origin and follow System Settings > Privacy & Security instructions.
For a frictionless public release, configure Apple Developer ID signing plus `notarytool`
notarization and stapling. An unsigned/ad-hoc preview is *not* equivalent to notarization.

## CI lifecycle

- `ci.yml`: Python/API/privacy regressions and Release C#/AXAML compilation on both supported OS/arch targets,
  on push and PR. These quick jobs **do not build the enormous Docling backend**.
- `release-desktop.yml`: run manually for a pre-release preview or push a tag `v0.2.0-alpha.1`.
  This freezes PyInstaller Python on both native runners, smoke-tests its authenticated
  health endpoint, publishes self-contained .NET, assembles OS-specific packages, scans the
  outputs and uploads GitHub Actions artifacts.
- A **tagged run** creates one GitHub Release containing both supported targets only after every job
  passes. A manual run just produces private workflow artifacts for testing.
- `workflow_dispatch` can select `core` to debug bundling with PyMuPDF only. Normal tagged
  releases select `full` on Windows and macOS ARM64. Intel Mac is not packaged.
- Native PyTorch, Docling parser, OCR and app signing may require extra disk and time. The
  generated bundles have not been cross-platform executed in this development environment;
  GitHub Actions is their real build/boot smoke gate, followed by human GUI/real-PDF testing.

### Publishing a preview

1. Copy all files from the upgrade ZIP into the project root, preserving paths and taking a
   Git snapshot first. This overlay builds on the previous secure-distribution version.
2. Ensure the previous v0.1 API, Services, and Desktop files are present. The ZIP is *not*
   a complete copy of the entire repository.
3. Run local Python regression tests and `dotnet build` on Windows.
4. Commit, push to GitHub. Inspect CI on Windows and macOS Apple Silicon.
5. In **Actions > Release / Windows + macOS bundled Desktop > Run workflow**, choose
   `0.2.0-alpha.1`, backend profile `full`. Download the two bundles from Artifacts.
6. Test on a real Apple Silicon Mac before tagging a public release.
7. After both pass, `git tag v0.2.0-alpha.1; git push origin v0.2.0-alpha.1`.

Do **not** tag v0.1.0 to trigger a supposedly stable full installer; use a clear alpha tag until
Docling real-document tests, Apple notarization and all native UI interactions pass.

## Limitations that remain

- Windows ARM64 / Linux bundles are not included.
- macOS packages are **ad-hoc-signed**. Signing, notarization and auto-update installation are future work.
- Heavy Docling dependencies, model weights and macOS architecture-specific packages may still
  fail PyInstaller hooks: release CI intentionally blocks the asset if smoke testing fails.
- A packed binary is not a guarantee of fully offline OCR. First-use Docling downloads may require internet.
- Per-user database migration from a legacy project checkout remains a user-controlled operation:
  don't blindly overwrite existing stores.
