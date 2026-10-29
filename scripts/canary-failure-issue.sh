#!/usr/bin/env bash
# Open or update a canary failure issue through the Forgejo issues API (#332).
#
# Usage: canary-failure-issue.sh <title> <body>
#
# Comments on the open issue whose title matches exactly, otherwise opens a
# new one. Called by the canary workflows instead of actions/github-script,
# which the Forgejo runner cannot resolve (it looks short action names up on
# data.forgejo.org).
#
# Env: GITHUB_API_URL (Forgejo sets it to <server>/api/v1), GITHUB_REPOSITORY
# (owner/repo), CANARY_ISSUE_TOKEN (the workflow's automatic token).
set -euo pipefail

title="${1:?usage: canary-failure-issue.sh <title> <body>}"
body="${2:?usage: canary-failure-issue.sh <title> <body>}"
api="${GITHUB_API_URL:?GITHUB_API_URL is required}/repos/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
auth="Authorization: token ${CANARY_ISSUE_TOKEN:?CANARY_ISSUE_TOKEN is required}"

# `q` is a keyword search, so keep only the exact-title match.
number="$(
  curl -fsS -G -H "$auth" "$api/issues" \
    --data-urlencode state=open \
    --data-urlencode type=issues \
    --data-urlencode limit=50 \
    --data-urlencode "q=$title" \
    | jq -r --arg title "$title" 'map(select(.title == $title)) | first | .number // empty'
)"

if [ -n "$number" ]; then
  jq -n --arg body "$body" '{body: $body}' \
    | curl -fsS -o /dev/null -X POST -H "$auth" -H "Content-Type: application/json" \
      --data-binary @- "$api/issues/$number/comments"
  echo "Commented on issue #$number: $title"
else
  number="$(
    jq -n --arg title "$title" --arg body "$body" '{title: $title, body: $body}' \
      | curl -fsS -X POST -H "$auth" -H "Content-Type: application/json" \
        --data-binary @- "$api/issues" \
      | jq -r '.number'
  )"
  echo "Opened issue #$number: $title"
fi
