#!/usr/bin/env bash
set -euo pipefail

ios_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo="$(cd "$ios_dir/../.." && pwd)"
version=2.43.0
archive_sha=a4847ed77d3341a4d24049bc4424a3babca4c94ff1dcaaee923eaca2b32c678f
binary_sha=4db2eac610504024d143ddef609a118e71310d513fadbe0427400a77d175223a
tools_dir="${SONERELI_TOOLS_DIR:-$ios_dir/.tools}"
xcodegen="$tools_dir/xcodegen/bin/xcodegen"
out="${SONERELI_IOS_EVIDENCE:-$ios_dir/.build-ci}"
derived="${SONERELI_IOS_DERIVED_DATA:-$ios_dir/.build-xcode}"
action="${1:-all}"
case "$action" in
  generate|build|test|all|ios-stage|ios-share|ios-live) ;;
  *) printf 'Usage: %s [generate|build|test|all|ios-stage|ios-share|ios-live]\n' "$0" >&2; exit 2 ;;
esac

[[ "$(uname -s)" == Darwin ]] || { echo "iOS verification requires macOS with Xcode." >&2; exit 1; }

if [[ ! -x "$xcodegen" ]]; then
  mkdir -p "$tools_dir"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  archive="$work/xcodegen.zip"
  mirror="${SONERELI_XCODEGEN_MIRROR:-}"
  if [[ -z "$mirror" ]] || ! curl -fsSL --max-time 90 "$mirror/xcodegen/$version/xcodegen.zip" -o "$archive"; then
    curl -fsSL --retry 2 "https://github.com/yonaskolb/XcodeGen/releases/download/$version/xcodegen.zip" -o "$archive"
  fi
  printf '%s  %s\n' "$archive_sha" "$archive" | shasum -a 256 -c -
  unzip -q "$archive" -d "$work"
  printf '%s  %s\n' "$binary_sha" "$work/xcodegen/bin/xcodegen" | shasum -a 256 -c -
  mv "$work/xcodegen" "$tools_dir/"
fi
printf '%s  %s\n' "$binary_sha" "$xcodegen" | shasum -a 256 -c -
"$xcodegen" generate --spec "$ios_dir/project.yml" --project "$ios_dir"
if [[ "$action" == generate ]]; then exit 0; fi

mkdir -p "$out"
out="$(cd "$out" && pwd)"
project="$ios_dir/Sonereli.xcodeproj"
destination="${SONERELI_SIM_DESTINATION:-platform=iOS Simulator,name=iPhone 17 Pro,OS=latest}"
args=(-project "$project" -scheme Sonereli -derivedDataPath "$derived" -destination "$destination")
{
  git -C "$repo" rev-parse HEAD
  xcodebuild -version
  printf 'Destination: %s\nApp version: %s (%s)\n' "$destination" \
    "$(awk '/MARKETING_VERSION:/ {print $2; exit}' "$ios_dir/project.yml")" \
    "$(awk '/CURRENT_PROJECT_VERSION:/ {print $2; exit}' "$ios_dir/project.yml")"
} | tee "$out/source.txt"

if [[ "$action" == all || "$action" == build ]]; then
  xcodebuild "${args[@]}" -configuration Release CODE_SIGNING_ALLOWED=NO build 2>&1 | tee "$out/build.log"
fi
if [[ "$action" == build ]]; then exit 0; fi

live_test=SonereliUITests/SonereliLiveUITests
signing=(CODE_SIGNING_ALLOWED=NO)
selection=(-skip-testing:"$live_test")
case "$action" in
  ios-live)
    : "${SONERELI_QA_TICKET_FILE:?Set the path to a one-use Clerk QA ticket file}"
    : "${SONERELI_QA_SUBJECT:?Set the Clerk QA user ID}"
    : "${SONERELI_QA_YOUTUBE_URL:?Set an authorized source for the one live job}"
    TEST_RUNNER_SONERELI_QA_TICKET="$(cat "$SONERELI_QA_TICKET_FILE")"
    export TEST_RUNNER_SONERELI_QA_TICKET
    export TEST_RUNNER_SONERELI_QA_SUBJECT="$SONERELI_QA_SUBJECT"
    export TEST_RUNNER_SONERELI_QA_YOUTUBE_URL="$SONERELI_QA_YOUTUBE_URL"
    selection=(-only-testing:"$live_test")
    # Clerk uses the simulator Keychain. Ad-hoc signing supplies the
    # simulated application identifier without an Apple certificate.
    signing=(CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- "SONERELI_TEAM_ID=${SONERELI_TEAM_ID:-LA9V2K2376}")
    ;;
  ios-stage) selection=(-only-testing:SonereliUITests/SonereliUITests/testStagePlaybackAndHebrewLyrics) ;;
  ios-share) selection=(-only-testing:SonereliUITests/SonereliUITests/testShareConfirmationProgressAndSing) ;;
esac
bundle="$out/SonereliUITests.xcresult"
rm -rf "$bundle" "$out/screenshots"
# Bash 3.2 treats an empty array as unset with nounset.
xcodebuild "${args[@]}" ${selection[@]+"${selection[@]}"} -configuration Debug \
  -parallel-testing-enabled NO -resultBundlePath "$bundle" \
  "${signing[@]}" test 2>&1 | tee "$out/test.log"
grep -E 'Executed [1-9][0-9]* tests?, with 0 failures' "$out/test.log" >/dev/null
if grep -Eq 'Test Case .* skipped' "$out/test.log"; then
  echo "The iOS acceptance suite reported skipped tests." >&2
  exit 1
fi
mkdir -p "$out/screenshots"
xcrun xcresulttool export attachments --path "$bundle" --output-path "$out/screenshots"
# The xcresult manifest keeps the relation between the test name and the exported PNG.
printf 'Evidence: %s\n' "$out"
