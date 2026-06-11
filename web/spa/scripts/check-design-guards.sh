#!/usr/bin/env bash
# Marquee design guards (#152). Run from web/spa (any cwd works):
#   bash scripts/check-design-guards.sh     (or: bun run guards)
#
# Five guards keep the route ports from drifting off the design system:
#   1. no raw framework color names (tailwind-style `amber-500`) in src
#   2. raw color literals (#hex / rgb( / rgba( / hsl() allowed ONLY as the 3
#      readWaveColors fallbacks in src/player/KaraokePlayer.tsx and inside the
#      marked MARQUEE TOKENS / MARQUEE RECIPES blocks of src/styles.css
#   3. recipe selectors (.m-btn, .m-chip, ...) defined ONLY inside the
#      MARQUEE RECIPES block of src/styles.css — no re-definition anywhere
#   4. no nested same-recipe (.m-sign inside .m-sign)
#   5. no window.alert( / window.confirm( in src (window.prompt clipboard
#      fallbacks stay sanctioned — navigator.clipboard is unavailable on the
#      http LAN origin)
#
# Comments are stripped (replaced by blanks, line numbers preserved) before
# matching so issue references like "(#113)" and prose mentioning
# window.confirm() don't false-positive. Digit-only "(#NNN)" issue refs are
# also dropped from string literals (noFollower.test.ts names #113 in a
# describe() title). Portable on purpose: GNU grep + awk + perl, no ripgrep —
# GitHub's ubuntu runners don't ship rg.
set -uo pipefail
cd "$(dirname "$0")/.."

status=0
fail() {
  printf 'GUARD FAIL — %s\n%s\n\n' "$1" "$2"
  status=1
}

# Blank out /* ... */ block comments (and, for TS, // line comments that are
# not part of a URL "://"), preserving newlines so line numbers stay true.
strip_ts_comments() {
  perl -0777 -pe 's{/\*.*?\*/}{($x = $&) =~ tr/\n//cd; $x}ges; s{(?<!:)//[^\n]*}{}g'
}
strip_css_comments() {
  perl -0777 -pe 's{/\*.*?\*/}{($x = $&) =~ tr/\n//cd; $x}ges'
}

# Blank every line inside the named marked block(s) of styles.css, keeping
# line numbers; $1 is an ERE naming the block(s), e.g. 'TOKENS|RECIPES'.
blank_marked_blocks() {
  awk -v which="$1" '
    $0 ~ ("END MARQUEE (" which ")") { inblk = 0; print ""; next }
    $0 ~ ("MARQUEE (" which ")")     { inblk = 1 }
    inblk { print ""; next }
    { print }
  ' src/styles.css
}

COLOR_RE='#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\('
FALLBACK_RE='get\("--[a-z-]+", "#[0-9a-fA-F]{6}"\)'
RECIPE_RE='^[[:space:]]*\.m-(mono|btn|chip|dot|wipe|wipebar|bulbs|sign|stem)\b[^{]*\{'

# ── guard 1: raw framework color names ──────────────────────────────────────
g1=$(grep -rnE '\b(cyan|sky|teal|slate|indigo|emerald|rose|amber|fuchsia|violet|blue|green|yellow|red|orange|pink|purple|stone|zinc|neutral|gray)-[0-9]{2,3}\b' src || true)
if [ -n "$g1" ]; then
  fail "guard 1: raw framework color names in src" "$g1"
else
  echo "guard 1 OK — no raw framework color names"
fi

# ── guard 2: raw color literals outside sanctioned zones ────────────────────
g2=""
while IFS= read -r f; do
  hits=$(strip_ts_comments < "$f" | grep -nE "$COLOR_RE" | perl -pe 's/\(#[0-9]{1,5}\)//g' | grep -E "$COLOR_RE" || true)
  if [ "$f" = "src/player/KaraokePlayer.tsx" ]; then
    nfallback=$(printf '%s\n' "$hits" | grep -cE "$FALLBACK_RE" || true)
    hits=$(printf '%s\n' "$hits" | grep -vE "$FALLBACK_RE" || true)
    if [ "$nfallback" -gt 3 ]; then
      fail "guard 2: more than the 3 sanctioned readWaveColors fallbacks in $f" "$nfallback fallback-shaped literals found"
    fi
  fi
  [ -n "$hits" ] && g2="$g2$(printf '%s\n' "$hits" | sed "s|^|$f:|")
"
done < <(find src -name '*.ts' -o -name '*.tsx' | sort)

# styles.css: literals live only inside the marked TOKENS/RECIPES blocks.
css_hits=$(blank_marked_blocks 'TOKENS|RECIPES' | strip_css_comments | grep -nE "$COLOR_RE" || true)
[ -n "$css_hits" ] && g2="$g2$(printf '%s\n' "$css_hits" | sed 's|^|src/styles.css:|')
"
# any other stylesheet has no sanctioned zone at all
while IFS= read -r f; do
  hits=$(strip_css_comments < "$f" | grep -nE "$COLOR_RE" || true)
  [ -n "$hits" ] && g2="$g2$(printf '%s\n' "$hits" | sed "s|^|$f:|")
"
done < <(find src -name '*.css' ! -path 'src/styles.css' | sort)

if [ -n "$g2" ]; then
  fail "guard 2: raw color literals outside sanctioned zones" "$g2"
else
  echo "guard 2 OK — no raw color literals outside sanctioned zones"
fi

# ── guard 3: recipe definitions only inside the MARQUEE RECIPES block ───────
g3=$(blank_marked_blocks 'RECIPES' | grep -nE "$RECIPE_RE" | sed 's|^|src/styles.css:|' || true)
g3b=$(grep -rnE "$RECIPE_RE" src --include='*.css' --include='*.ts' --include='*.tsx' | grep -v '^src/styles.css:' || true)
if [ -n "$g3$g3b" ]; then
  fail "guard 3: recipe selector defined outside the MARQUEE RECIPES block" "$g3${g3b:+
$g3b}"
else
  echo "guard 3 OK — recipes defined only in the MARQUEE RECIPES block"
fi

# ── guard 4: nested same-recipe ──────────────────────────────────────────────
g4a=$(grep -rnE 'm-sign[^"]*m-sign' src || true)
g4b=$(grep -nE '\.m-sign[^{]*\.m-sign' src/styles.css | sed 's|^|src/styles.css:|' || true)
if [ -n "$g4a$g4b" ]; then
  fail "guard 4: nested same-recipe (m-sign inside m-sign)" "$g4a${g4b:+
$g4b}"
else
  echo "guard 4 OK — no nested same-recipe"
fi

# ── guard 5: forbidden primitives ────────────────────────────────────────────
g5=""
while IFS= read -r f; do
  hits=$(strip_ts_comments < "$f" | grep -nE 'window\.(alert|confirm)\(' || true)
  [ -n "$hits" ] && g5="$g5$(printf '%s\n' "$hits" | sed "s|^|$f:|")
"
done < <(find src -name '*.ts' -o -name '*.tsx' | sort)
if [ -n "$g5" ]; then
  fail "guard 5: window.alert()/window.confirm() in src" "$g5"
else
  echo "guard 5 OK — no forbidden primitives"
fi

if [ "$status" -ne 0 ]; then
  echo "design guards FAILED" >&2
else
  echo "all design guards passed"
fi
exit "$status"
