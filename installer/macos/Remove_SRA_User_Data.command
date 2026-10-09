#!/bin/zsh
# Optional explicit purge, supplied with the SRA macOS DMG.
# Drag SRA.app from Applications to the Trash to remove the program itself.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'This cleaner only runs on macOS.\n'
  exit 1
fi
if pgrep -x 'SRA.Desktop' >/dev/null || pgrep -x 'sra-backend' >/dev/null; then
  printf 'Quit SRA Desktop and its backend before removing any user data.\n'
  exit 1
fi

root="$HOME/Library/Application Support/SRA"
old_prefs="$HOME/.local/share/SRA/desktop-preferences.json"

printf '\nSRA optional personal-data cleanup\n'
printf 'Normal removal: simply move SRA.app to the Trash. Your PDFs/notes remain.\n\n'
printf 'This extra operation PERMANENTLY DELETES:\n  %s\n' "$root"
printf '  macOS Keychain entries for SRA Lite / Pro model credentials\n'
printf '  old desktop preferences at %s (if present)\n' "$old_prefs"
printf '\nOriginal PDFs outside SRA, custom SRA_HOME/SRA_DATA_DIR, and shared external\nmodel caches are NOT deleted. This operation cannot be undone.\n\n'
printf 'Type DELETE-SRA-DATA to proceed, or press Enter to cancel: '
read -r reply
if [[ "$reply" != "DELETE-SRA-DATA" ]]; then
  printf 'Cancelled; no files changed.\n'
  exit 0
fi

# Reject symlinked SRA roots; a private application-data path is all we own.
if [[ -L "$root" ]]; then
  printf 'SRA root is a symlink. Refusing automatic removal: %s\n' "$root"
  exit 1
fi
if [[ "$root" != "$HOME/Library/Application Support/SRA" ]]; then
  printf 'Unexpected cleanup destination. No files removed.\n'
  exit 1
fi
if [[ -d "$root" ]]; then
  /bin/rm -rf -- "$root"
fi
# This old preferences file was used by a previous .NET macOS preview.
if [[ -f "$old_prefs" && ! -L "$old_prefs" ]]; then
  /bin/rm -- "$old_prefs"
fi
for account in SRA_API_KEY SRA_LITE_API_KEY SRA_PRO_API_KEY; do
  /usr/bin/security delete-generic-password -s 'org.daseinzc509.sra.model-credentials' -a "$account" >/dev/null 2>&1 || true
done
printf '\nSRA private data cleanup completed. System usage history or shared caches may remain.\n'
printf 'You can now close this window.\n'
