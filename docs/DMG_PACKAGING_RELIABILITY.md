# Reliable macOS DMG packaging

This change fixes an intermittent macOS GitHub-hosted runner failure:

```
hdiutil: create failed - Resource busy
```

The error occurs **after** the Python backend and self-contained Avalonia build
have succeeded. It is not caused by the PDF parser, Lite/Pro or the safe-pruning
allowlist. Both Windows and macOS use the same privacy/safety checks.

## What changes

`release-desktop.yml` now calls `scripts/create_macos_dmg.py` instead of running
one `hdiutil create -srcfolder` command directly. The script:

1. Attempts the normal compressed `UDZO` DMG creation with **up to four** bounded
   attempts, retrying only transient `Resource busy` responses.
2. If all retries fail, creates a writable HFS+ staging image with a unique
   private mountpoint, copies the already signed app and `/Applications`
   shortcut with `ditto`, calls `sync`, detaches **only that image**, and
   converts it to compressed `UDZO`.
3. Verifies the image with `hdiutil verify` before moving it to its final path.
4. Uses per-run scratch directories under the existing build `dist/` folder and
   never deletes user data or detaches unrelated mounted volumes.

**No change** to Avalonia, Python/Docling dependencies, the installer, signing
or privacy checks. This repair does not turn ad-hoc Mac signatures into Apple
notarization.

## Test and limitations

On Windows or Linux:

```powershell
conda run -p .conda-env python -m pytest -q tests\test_macos_dmg_packaging.py
```

Unit tests simulate busy creation, transient errors, fallback, verification and
source protection with a mocked macOS command runner. Real image mounting and
conversion must still be checked by the next `Bundle osx-arm64` GitHub job.

If it still fails, open the *Assemble, sign preview and package macOS app + DMG*
step. The wrapper now prints which attempt failed and whether it reached the
isolated writable-image fallback. Do not force-delete other mounted images.

## Git commit

```text
fix(release): retry busy macOS DMG creation with isolated fallback
```

```text
- retry transient hdiutil Resource busy errors on macOS runners
- fall back to a private writable image and mountpoint
- verify the final DMG before publishing
- add packaging error and safety regression tests
- keep Windows bundling and release privacy gates unchanged
```
