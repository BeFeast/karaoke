#!/usr/bin/env bash
set -euo pipefail

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo="$(cd "$ios_dir/../.." && pwd)"
team="${SONERELI_TEAM_ID:-LA9V2K2376}"
bundle=com.befeast.sonereli
group=group.com.befeast.sonereli
upload=1
case "${1:-}" in
    --no-upload) upload=0 ;;
    "") ;;
    *) printf 'Usage: %s [--no-upload]\n' "$0" >&2; exit 2 ;;
esac
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == Darwin ]] || die "TestFlight requires macOS with Xcode."
sha="$(git -C "$repo" rev-parse HEAD)"
[[ "$sha" == "${SONERELI_SOURCE_SHA:-$sha}" ]] || die "The checkout differs from SONERELI_SOURCE_SHA."
[[ -z "$(git -C "$repo" status --porcelain --untracked-files=all)" ]] || die "The release checkout must be clean."
build="${SONERELI_BUILD_NUMBER:-$(git -C "$repo" rev-list --count HEAD)}"
[[ "$build" =~ ^[1-9][0-9]*$ ]] || die "SONERELI_BUILD_NUMBER must be a positive integer."

# Metadata only. The key remains in altool's private-key directory.
key_id="${SONERELI_ASC_KEY_ID:?Set the App Store Connect API key ID authorized for Sonereli}"
issuer="${SONERELI_ASC_ISSUER_ID:?Set the App Store Connect API issuer ID}"
[[ "$key_id" =~ ^[A-Z0-9]+$ ]] || die "Invalid App Store Connect API key ID."
[[ "$issuer" =~ ^[0-9A-Fa-f-]{36}$ ]] || die "Invalid App Store Connect API issuer ID."
key_file=""
for key_dir in "$PWD/private_keys" "$HOME/private_keys" "$HOME/.private_keys" "$HOME/.appstoreconnect/private_keys"; do
    if [[ -r "$key_dir/AuthKey_$key_id.p8" ]]; then
        key_file="$key_dir/AuthKey_$key_id.p8"
        break
    fi
done
[[ -n "$key_file" ]] || die "Install the decoded Sonereli ASC .p8 in altool's private_keys directory."
[[ "$(stat -f '%Lp' "$key_file")" =~ ^[46]00$ ]] || die "Restrict the ASC private key to its owner."
auth=(-authenticationKeyPath "$key_file" -authenticationKeyID "$key_id" -authenticationKeyIssuerID "$issuer")

"$ios_dir/scripts/ci-simulator.sh" generate
build_dir="$ios_dir/.build-release"
mkdir -p "$build_dir"
out="$(mktemp -d "$build_dir/testflight-${sha:0:12}-$build.XXXXXX")"
archive="$out/Sonereli.xcarchive"
options="$out/ExportOptions.plist"
cp "$ios_dir/ExportOptions-AppStore.plist" "$options"
plutil -replace teamID -string "$team" "$options"

xcodebuild -project "$ios_dir/Sonereli.xcodeproj" -scheme Sonereli -configuration Release \
    -destination generic/platform=iOS -archivePath "$archive" -derivedDataPath "$ios_dir/.build-xcode" \
    -allowProvisioningUpdates "${auth[@]}" SONERELI_TEAM_ID="$team" CURRENT_PROJECT_VERSION="$build" \
    archive 2>&1 | tee "$out/archive.log"

xcodebuild -exportArchive -archivePath "$archive" -exportOptionsPlist "$options" \
    -exportPath "$out/export" -allowProvisioningUpdates "${auth[@]}" 2>&1 | tee "$out/export.log"

ipa="$out/export/Sonereli.ipa"
[[ -f "$ipa" ]] || die "The export did not contain Sonereli.ipa."
unzip -q "$ipa" -d "$out/ipa"
app="$out/ipa/Payload/Sonereli.app"
extension="$app/PlugIns/SonereliShare.appex"
[[ -d "$app" && -d "$extension" ]] || die "The export must contain the app and embedded share extension."

for entry in "$app|$bundle|app" "$extension|$bundle.share|share"; do
    IFS='|' read -r product product_id label <<< "$entry"
    entitlements="$out/$label-entitlements.plist"
    codesign --verify --strict "$product"
    codesign -d --entitlements - --xml "$product" >"$entitlements" 2>>"$out/codesign.log"
    codesign -dvv "$product" 2>&1 | tee -a "$out/codesign.log" | grep -F 'Authority=Apple Distribution:' >/dev/null
    [[ "$(plutil -extract application-identifier raw -o - "$entitlements")" == "$team.$product_id" ]] ||
        die "Unexpected application identifier on $label."
    [[ "$(/usr/libexec/PlistBuddy -c "Print :com.apple.developer.team-identifier" "$entitlements")" == "$team" ]] ||
        die "Unexpected team on $label."
    /usr/libexec/PlistBuddy -c "Print :com.apple.security.application-groups" "$entitlements" |
        grep -F "$group" >/dev/null || die "Missing share App Group on $label."
    [[ "$(/usr/libexec/PlistBuddy -c 'Print :get-task-allow' "$entitlements" 2>/dev/null || true)" != true ]] ||
        die "The distribution build allows debugging on $label."
    [[ "$(plutil -extract CFBundleVersion raw -o - "$product/Info.plist")" == "$build" ]] ||
        die "Unexpected build number on $label."
done

marketing="$(plutil -extract CFBundleShortVersionString raw -o - "$app/Info.plist")"
status=exported
receipt="$out/receipt.txt"
write_receipt() {
    {
        printf 'Sonereli %s (%s)\nCommit: %s\nBundle ID: %s\nTeam: %s\nStatus: %s\n' \
            "$marketing" "$build" "$sha" "$bundle" "$team" "$status"
        xcodebuild -version
        printf 'IPA SHA-256: '
        shasum -a 256 "$ipa" | awk '{print $1}'
    } >"$receipt"
}
write_receipt
asc=(--type ios --file "$ipa" --apiKey "$key_id" --apiIssuer "$issuer")
xcrun altool --validate-app "${asc[@]}" 2>&1 | tee "$out/validate.log"
status=validated
write_receipt
if [[ "$upload" == 1 ]]; then
    xcrun altool --upload-app "${asc[@]}" 2>&1 | tee "$out/upload.log"
    status=uploaded_waiting_for_processing
    write_receipt
fi
cat "$receipt"
printf 'Evidence: %s\n' "$out"
