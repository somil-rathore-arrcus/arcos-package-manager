#!/bin/bash
# Report whether an ARCoS fork shares history with candidate upstreams.
#
# Emits one JSON object. Run on a host with access to the private ARCoS forks.
# Usage: ancestry_probe.sh <workdir> <arcos_url> <arcos_ref> <cand_url> <cand_ref> [...]
set -u
WORK=$1; AU=$2; AR=$3; shift 3

rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK" || { echo '{"error":"workdir"}'; exit 0; }
git init -q .
git config --local advice.detachedHead false

if ! git fetch -q --filter=blob:none --no-tags "$AU" "$AR" 2>/dev/null; then
    printf '{"arcos_ok":false,"candidates":[]}\n'; exit 0
fi
ARCOS=$(git rev-parse FETCH_HEAD)

printf '{"arcos_ok":true,"arcos_sha":"%s","candidates":[' "$ARCOS"
FIRST=1
while [ $# -ge 2 ]; do
    CU=$1; CR=$2; shift 2
    [ $FIRST -eq 0 ] && printf ','
    FIRST=0
    if ! git fetch -q --filter=blob:none --no-tags "$CU" "$CR" 2>/dev/null; then
        printf '{"url":"%s","ref":"%s","reachable":false,"shared":false}' "$CU" "$CR"
        continue
    fi
    CAND=$(git rev-parse FETCH_HEAD)
    MB=$(git merge-base "$ARCOS" "$CAND" 2>/dev/null || true)
    if [ -z "$MB" ]; then
        printf '{"url":"%s","ref":"%s","reachable":true,"shared":false,"candidate_sha":"%s"}' "$CU" "$CR" "$CAND"
    else
        # --no-merges, to match the comparison engine: a merge commit cannot be
        # cherry-picked, so counting merges here would report a backlog larger
        # than the number of patches the tool can actually offer.
        BEHIND=$(git rev-list --count --no-merges "$MB".."$CAND" 2>/dev/null || echo 0)
        ONLY=$(git rev-list --count --no-merges "$MB".."$ARCOS" 2>/dev/null || echo 0)
        printf '{"url":"%s","ref":"%s","reachable":true,"shared":true,"candidate_sha":"%s","merge_base":"%s","behind":%s,"arcos_only":%s}' \
            "$CU" "$CR" "$CAND" "$MB" "$BEHIND" "$ONLY"
    fi
done
printf ']}\n'
