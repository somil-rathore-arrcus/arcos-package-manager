#!/bin/bash
# Measure how an ARCoS commit relates to candidate upstream refs.
#
# Emits exactly one JSON object on stdout. Run on a host with access to the
# private ARCoS forks.
#
# Usage: ancestry_probe.sh <workdir> <arcos_url> <arcos_ref> [<cand_url> <cand_ref>]...
#
# Every git failure is reported as an error with git's own message. Nothing here
# turns a failure into a number: a rev-list that failed is not "0 behind", and a
# merge-base that errored is not "no shared history".
#
# The workdir persists between runs, so a second probe of an unchanged package
# fetches almost nothing. Only the commit graph is fetched (tree:0, falling back
# to blob:none and then a full fetch for servers without partial clone).
set -u
WORK=$1; AU=$2; AR=$3; shift 3
ERR=$(mktemp "${TMPDIR:-/tmp}/apm-probe-err.XXXXXX") || { echo '{"error":"mktemp failed"}'; exit 0; }
trap 'rm -f "$ERR"' EXIT

jstr() {
    local s=${1-}
    s=${s//\\/\\\\}; s=${s//\"/\\\"}
    s=${s//$'\n'/ }; s=${s//$'\r'/ }; s=${s//$'\t'/ }
    printf '"%s"' "$s"
}
errtext() { tr '\n' ' ' < "$ERR" | sed 's/  */ /g' | cut -c1-400; }
errkind() {
    if grep -qiE "couldn't find remote ref|not our ref|no such ref|invalid refspec|unadvertised object|bad object|not a valid object" "$ERR"; then
        echo INVALID_REF
    elif grep -qiE "could not resolve host|timed out|connection refused|network is unreachable|could not read from remote|permission denied|repository not found|unable to access|connection reset|host key verification|early eof|hung up" "$ERR"; then
        echo NETWORK_ERROR
    else
        echo GIT_ERROR
    fi
}
fail() {  # fail <kind> <message>
    printf '{"arcos_ok":false,"error_kind":%s,"error":%s,"candidates":[],"contains":[]}\n' \
        "$(jstr "$1")" "$(jstr "$2")"
    exit 0
}
fetch_ref() {  # fetch_ref <url> <ref> ; FETCH_HEAD on success; 2 = no such ref
    local f
    for f in --filter=tree:0 --filter=blob:none ""; do
        if git fetch -q --no-tags $f "$1" "$2" 2>"$ERR"; then return 0; fi
        case $(errkind) in
            INVALID_REF) return 2 ;;
            NETWORK_ERROR) return 1 ;;   # another filter will not help
        esac
    done
    return 1
}

mkdir -p "$WORK" 2>"$ERR" && cd "$WORK" 2>"$ERR" || fail GIT_ERROR "cannot use workdir $WORK: $(errtext)"
if [ ! -d .git ]; then
    git init -q . 2>"$ERR" || fail GIT_ERROR "git init failed: $(errtext)"
fi
git config advice.detachedHead false
git config gc.auto 0
git config fetch.writeCommitGraph true

fetch_ref "$AU" "$AR"; rc=$?
if [ $rc -ne 0 ]; then
    fail "$( [ $rc -eq 2 ] && echo INVALID_REF || errkind )" "could not fetch $AR from $AU: $(errtext)"
fi
ARCOS=$(git rev-parse --verify -q FETCH_HEAD^{commit}) || fail GIT_ERROR "FETCH_HEAD is not a commit"
ARCOS_DATE=$(git log -1 --format=%cI "$ARCOS" 2>"$ERR") || fail GIT_ERROR "git log failed on $ARCOS: $(errtext)"

N=0
SHAS=(); URLS=(); SHARED=()
OUT=""
while [ $# -ge 2 ]; do
    CU=$1; CR=$2; shift 2
    [ -n "$OUT" ] && OUT="$OUT,"
    fetch_ref "$CU" "$CR"; rc=$?
    if [ $rc -ne 0 ]; then
        kind=$( [ $rc -eq 2 ] && echo INVALID_REF || errkind )
        OUT="$OUT{\"url\":$(jstr "$CU"),\"ref\":$(jstr "$CR"),\"reachable\":$( [ "$kind" = INVALID_REF ] && echo true || echo false ),\"shared\":false,\"error_kind\":$(jstr "$kind"),\"error\":$(jstr "$(errtext)")}"
        SHAS+=(""); URLS+=("$CU"); SHARED+=(0); N=$((N+1)); continue
    fi
    CAND=$(git rev-parse --verify -q FETCH_HEAD^{commit})
    if [ -z "$CAND" ]; then
        OUT="$OUT{\"url\":$(jstr "$CU"),\"ref\":$(jstr "$CR"),\"reachable\":true,\"shared\":false,\"error_kind\":\"INVALID_REF\",\"error\":\"not a commit\"}"
        SHAS+=(""); URLS+=("$CU"); SHARED+=(0); N=$((N+1)); continue
    fi
    CDATE=$(git log -1 --format=%cI "$CAND" 2>/dev/null)
    base="\"url\":$(jstr "$CU"),\"ref\":$(jstr "$CR"),\"reachable\":true,\"sha\":$(jstr "$CAND"),\"date\":$(jstr "$CDATE")"

    MBS=$(git merge-base --all "$ARCOS" "$CAND" 2>"$ERR"); rc=$?
    if [ $rc -eq 1 ] && [ ! -s "$ERR" ] && [ -z "$MBS" ]; then
        OUT="$OUT{$base,\"shared\":false}"
        SHAS+=("$CAND"); URLS+=("$CU"); SHARED+=(0); N=$((N+1)); continue
    elif [ $rc -ne 0 ]; then
        OUT="$OUT{$base,\"shared\":false,\"error_kind\":\"GIT_ERROR\",\"error\":$(jstr "merge-base failed: $(errtext)")}"
        SHAS+=("$CAND"); URLS+=("$CU"); SHARED+=(0); N=$((N+1)); continue
    fi

    problem=""
    BEHIND=$(git rev-list --count --no-merges "$CAND" "^$ARCOS" 2>"$ERR") || problem="rev-list ARCOS..CANDIDATE failed: $(errtext)"
    ONLY=""
    if [ -z "$problem" ]; then
        ONLY=$(git rev-list --count --no-merges "$ARCOS" "^$CAND" 2>"$ERR") || problem="rev-list CANDIDATE..ARCOS failed: $(errtext)"
    fi
    INA=null
    if [ -z "$problem" ]; then
        git merge-base --is-ancestor "$CAND" "$ARCOS" 2>"$ERR"; rc=$?
        case $rc in 0) INA=true ;; 1) INA=false ;; *) problem="is-ancestor failed: $(errtext)" ;; esac
    fi
    if [ -n "$problem" ]; then
        OUT="$OUT{$base,\"shared\":true,\"error_kind\":\"GIT_ERROR\",\"error\":$(jstr "$problem")}"
        SHAS+=("$CAND"); URLS+=("$CU"); SHARED+=(0); N=$((N+1)); continue
    fi
    MBLIST=""
    for mb in $MBS; do MBLIST="$MBLIST${MBLIST:+,}$(jstr "$mb")"; done
    MBDATE=$(git log --no-walk --format=%cI $MBS 2>/dev/null | sort -r | head -1)
    OUT="$OUT{$base,\"shared\":true,\"merge_bases\":[$MBLIST],\"merge_base_date\":$(jstr "$MBDATE"),\"behind\":$BEHIND,\"arcos_only\":$ONLY,\"in_arcos\":$INA}"
    SHAS+=("$CAND"); URLS+=("$CU"); SHARED+=(1); N=$((N+1))
done

# Which shared candidates of one repository contain which: [i, j] means i is an
# ancestor of j. That is how a release tag is matched to the maintenance branch
# it was cut from.
CONTAINS=""
i=0
while [ $i -lt $N ]; do
    j=0
    while [ $j -lt $N ]; do
        if [ $i -ne $j ] && [ "${SHARED[$i]}" = 1 ] && [ "${SHARED[$j]}" = 1 ] \
           && [ "${URLS[$i]}" = "${URLS[$j]}" ] && [ "${SHAS[$i]}" != "${SHAS[$j]}" ]; then
            if git merge-base --is-ancestor "${SHAS[$i]}" "${SHAS[$j]}" 2>/dev/null; then
                CONTAINS="$CONTAINS${CONTAINS:+,}[$i,$j]"
            fi
        fi
        j=$((j+1))
    done
    i=$((i+1))
done

printf '{"arcos_ok":true,"arcos_sha":%s,"arcos_date":%s,"candidates":[%s],"contains":[%s]}\n' \
    "$(jstr "$ARCOS")" "$(jstr "$ARCOS_DATE")" "$OUT" "$CONTAINS"
