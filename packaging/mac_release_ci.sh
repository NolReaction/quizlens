#!/bin/bash
# Run only on a GitHub-hosted macOS runner in the tag release job.
set -euo pipefail

required=(MAC_CERTIFICATE_P12_BASE64 MAC_CERTIFICATE_PASSWORD APPLE_ID APPLE_TEAM_ID APPLE_APP_PASSWORD)
missing=()
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then missing+=("$name"); fi
done
if [[ "${#missing[@]}" -eq "${#required[@]}" ]]; then
  python3 packaging/package_mac.py --output dist/QuizLens-Mac.zip
  echo 'signing=unsigned' >> "$GITHUB_OUTPUT"
  exit 0
fi
if [[ "${#missing[@]}" -gt 0 ]]; then
  printf 'Incomplete Apple signing configuration. Missing: %s\n' "${missing[*]}" >&2
  exit 1
fi

umask 077
stage=$(mktemp -d "$RUNNER_TEMP/quizlens-sign.XXXXXXXX")
cert="$stage/identity.p12"
keychain="$stage/quizlens.keychain-db"
# The search list is per user; restore GitHub's runner configuration when exiting.
original=()
while IFS= read -r line; do
  line=${line#*\"}
  original+=("${line%\"*}")
done < <(/usr/bin/security list-keychains -d user)
cleanup() {
  /usr/bin/security list-keychains -d user -s "${original[@]}" >/dev/null 2>&1 || true
  /usr/bin/security delete-keychain "$keychain" >/dev/null 2>&1 || true
  rm -rf "$stage"
}
trap cleanup EXIT
password=$(/usr/bin/openssl rand -hex 32)
printf '%s' "$MAC_CERTIFICATE_P12_BASE64" | /usr/bin/base64 -D > "$cert"
/usr/bin/security create-keychain -p "$password" "$keychain"
/usr/bin/security set-keychain-settings -lut 3600 "$keychain"
/usr/bin/security unlock-keychain -p "$password" "$keychain"
/usr/bin/security import "$cert" -k "$keychain" -P "$MAC_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
rm -f "$cert"
/usr/bin/security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" "$keychain" >/dev/null
/usr/bin/security list-keychains -d user -s "$keychain" "${original[@]}"
# Select the certificate actually imported into this isolated keychain, including its Team ID.
identities=$(/usr/bin/security find-identity -v -p codesigning "$keychain")
QUIZLENS_SIGN_IDENTITY=$(printf '%s\n' "$identities" | python3 -c '
import os, re, sys
valid = re.findall(r"^[ ]*\d+\) [0-9A-Fa-f]{40} \"(Developer ID Application: [^\"]+)\"[ ]*$", sys.stdin.read(), re.M)
matches = [name for name in valid if name.endswith("(" + os.environ["APPLE_TEAM_ID"] + ")")]
if len(matches) != 1:
    sys.exit("Expected one valid Developer ID Application identity for APPLE_TEAM_ID.")
print(matches[0])
')
export QUIZLENS_SIGN_IDENTITY
export QUIZLENS_KEYCHAIN="$keychain" QUIZLENS_NOTARY_PROFILE=QuizLensCI
/usr/bin/xcrun notarytool store-credentials "$QUIZLENS_NOTARY_PROFILE" --keychain "$keychain" \
  --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD"
unset MAC_CERTIFICATE_P12_BASE64 MAC_CERTIFICATE_PASSWORD APPLE_APP_PASSWORD APPLE_ID APPLE_TEAM_ID
python3 packaging/package_mac.py --notarize --output dist/QuizLens-Mac.zip
echo 'signing=notarized' >> "$GITHUB_OUTPUT"
