"""A real git repository to work in, wherever git happens to run.

Comparison, patch-id and cherry-pick all need an actual repository with both
sides fetched into it - you cannot answer "which commits am I missing" from
metadata. The machine driving this tool may not be the machine with repository
access, so every command here goes through a transport and the working directory
lives on whichever host that is.

Workspaces are reused between calls and keyed by package and release, so the
second comparison of a package costs a fetch rather than a clone.
"""

from __future__ import annotations

import base64
import logging
import re
import shlex
from dataclasses import dataclass
from typing import Dict, List, Optional

from .transport import GitResult

log = logging.getLogger(__name__)

ARCOS_REMOTE = "arcos"
UPSTREAM_REMOTE = "upstream"

# A record separator that cannot occur in a commit message.
_FIELD = "\x1f"
_RECORD = "\x1e"
_LOG_FORMAT = _FIELD.join(["%H", "%an", "%ae", "%aI", "%s", "%b"]) + _RECORD


class GitWorkspaceError(RuntimeError):
    def __init__(self, message: str, stderr: str = "",
                 timed_out: bool = False) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.timed_out = timed_out


@dataclass
class LogEntry:
    sha: str
    author_name: str
    author_email: str
    authored_at: str
    subject: str
    body: str


class GitWorkspace:
    """One repository directory, with the two sides fetched as named remotes."""

    def __init__(self, transport, path: str, timeout: int = 60,
                 long_timeout: int = 900,
                 committer_name: str = "ARCoS Package Manager",
                 committer_email: str = "arcos@localhost") -> None:
        self.transport = transport
        self.path = path
        self.timeout = timeout
        self.long_timeout = long_timeout
        self.committer_name = committer_name
        self.committer_email = committer_email

    # -- plumbing ----------------------------------------------------------

    def _run(self, git_args: str, timeout: Optional[int] = None,
             check: bool = True, label: str = "") -> GitResult:
        command = f"cd {shlex.quote(self.path)} && git {git_args}"
        result = self.transport.shell(command, timeout=timeout or self.timeout)
        if check and not result.ok:
            raise GitWorkspaceError(
                f"git {label or git_args.split()[0]} failed", result.stderr.strip()
            )
        return result

    def shell(self, command: str, timeout: Optional[int] = None,
              check: bool = False) -> GitResult:
        full = f"cd {shlex.quote(self.path)} && {command}"
        result = self.transport.shell(full, timeout=timeout or self.timeout)
        if check and not result.ok:
            raise GitWorkspaceError("command failed", result.stderr.strip())
        return result

    def ensure(self) -> None:
        """Create the repository if it is not there yet. Safe to call repeatedly."""
        command = (
            f"mkdir -p {shlex.quote(self.path)} && cd {shlex.quote(self.path)} && "
            f"(test -d .git || git init -q .) && "
            f"git config user.name {shlex.quote(self.committer_name)} && "
            f"git config user.email {shlex.quote(self.committer_email)} && "
            f"git config advice.detachedHead false && "
            f"git config gc.auto 0"
        )
        result = self.transport.shell(command, timeout=self.timeout)
        if not result.ok:
            raise GitWorkspaceError(
                f"could not prepare a git workspace at {self.path}. With the ssh "
                f"backend this path is on the remote host and must be writable.",
                result.stderr.strip(),
            )

    def destroy(self) -> bool:
        result = self.transport.shell(
            f"rm -rf {shlex.quote(self.path)}", timeout=self.timeout
        )
        return result.ok

    # -- fetching ----------------------------------------------------------

    def set_remote(self, name: str, url: str) -> None:
        """Point `name` at `url`, without tearing the remote down.

        `remote remove` also deletes remote.<name>.promisor and
        .partialclonefilter, which is what lets git lazily fetch the blobs a
        blobless fetch skipped. Removing and re-adding the remote on every
        fetch left the workspace holding commits whose blobs were unreachable,
        so `git log -p` - and with it every patch-id - failed.
        """
        self._run(
            f"remote set-url {shlex.quote(name)} {shlex.quote(url)} "
            f">/dev/null 2>&1 || "
            f"git remote add {shlex.quote(name)} {shlex.quote(url)}",
            check=False,
        )

    def fetch(self, name: str, url: str, ref: str,
              blobless: bool = True, depth: Optional[int] = None) -> GitResult:
        """Fetch one ref into FETCH_HEAD and a local tracking ref.

        Blobless by default: the commit graph is what comparison needs, and it is
        a fraction of the download. Blobs are fetched on demand when a diff or a
        patch-id actually requires them.

        `depth` limits history as well, for callers that only build on the tip:
        committing one file on top of the linux fork needs its tip tree, not
        twenty years of commits.
        """
        self.set_remote(name, url)
        filters = "--filter=blob:none " if blobless else ""
        shallow = f"--depth {int(depth)} " if depth else ""
        local = f"refs/apm/{name}"
        spec = f"+{ref}:{local}" if not _looks_like_sha(ref) else ref
        result = self._run(
            f"fetch -q --no-tags {shallow}{filters}{shlex.quote(name)} "
            f"{shlex.quote(spec)}",
            timeout=self.long_timeout,
            check=False,
            label="fetch",
        )
        if not result.ok and blobless:
            # Not every server supports partial clone; retry complete.
            log.info("blobless fetch failed for %s; retrying in full", url)
            result = self._run(
                f"fetch -q --no-tags {shallow}{shlex.quote(name)} "
                f"{shlex.quote(spec)}",
                timeout=self.long_timeout,
                check=False,
                label="fetch",
            )
        return result

    def backfill_blobs(self, name: str, url: str, ref: str,
                       head: str) -> Optional[GitResult]:
        """Make sure the file contents for `head` are actually present locally.

        A second `git fetch` without --filter does NOT backfill the blobs an
        earlier blobless fetch skipped: the ref has not moved, so git reports
        everything up to date and downloads nothing. `--refetch` is what
        re-requests the objects the filter excluded - but only once
        remote.<name>.partialclonefilter is gone, because a stored filter is
        applied to the refetch too and it downloads nothing all over again.

        Without this, the blobs stayed missing and every diff was served one
        lazy promisor round trip at a time: 120 packs and a patch-id that ran
        out of time rather than an answer.

        That is expensive, so it is done once per head commit and remembered in
        the workspace's own config; a repeat comparison of an unchanged package
        skips it. Returns None when nothing needed doing.
        """
        key = f"apm.blobs.{name}"
        current = self._run(
            f"config --local --get {shlex.quote(key)}", check=False,
            label="config",
        ).text
        if current == head:
            return None
        self.set_remote(name, url)
        spec = f"+{ref}:refs/apm/{name}" if not _looks_like_sha(ref) else ref
        result = self.shell(
            f"git config --local --unset remote.{name}.partialclonefilter "
            f">/dev/null 2>&1; "
            f"git fetch -q --no-tags --refetch {shlex.quote(name)} "
            f"{shlex.quote(spec)}",
            timeout=self.long_timeout,
        )
        if result.ok:
            self._run(
                f"config --local {shlex.quote(key)} {shlex.quote(head)}",
                check=False, label="config",
            )
        return result

    def fetch_side(self, name: str, url: str, ref: str,
                   blobless: bool = True, depth: Optional[int] = None) -> str:
        """Fetch a side and return the commit it resolves to."""
        result = self.fetch(name, url, ref, blobless=blobless, depth=depth)
        if not result.ok:
            raise GitWorkspaceError(
                f"could not fetch {ref} from {url}", result.stderr.strip(),
                timed_out=_is_timeout(result.stderr),
            )
        return self.rev_parse("FETCH_HEAD")

    # -- reading -----------------------------------------------------------

    def rev_parse(self, rev: str) -> str:
        return self._run(f"rev-parse {shlex.quote(rev)}", label="rev-parse").text

    def merge_base(self, a: str, b: str) -> Optional[str]:
        result = self._run(
            f"merge-base {shlex.quote(a)} {shlex.quote(b)}",
            check=False, label="merge-base",
        )
        return result.text or None

    def count(self, rev_range: str, no_merges: bool = False) -> int:
        flags = "--no-merges " if no_merges else ""
        result = self._run(
            f"rev-list --count {flags}{rev_range}", check=False, label="rev-list"
        )
        try:
            return int(result.text)
        except ValueError:
            return 0

    def log(self, rev_range: str, limit: Optional[int] = None,
            no_merges: bool = True) -> List[LogEntry]:
        """Commits in `rev_range`, oldest first - cherry-pick order."""
        flags = "--no-merges " if no_merges else ""
        if limit:
            flags += f"-n {int(limit)} "
        result = self._run(
            f"log --reverse {flags}--format={shlex.quote(_LOG_FORMAT)} {rev_range}",
            timeout=self.long_timeout, check=False, label="log",
        )
        if not result.ok:
            raise GitWorkspaceError("git log failed", result.stderr.strip())
        return _parse_log(result.stdout)

    def patch_ids(self, rev_range: str, limit: Optional[int] = None,
                  timeout: Optional[int] = None) -> Dict[str, str]:
        """Map commit SHA -> patch-id for every commit in the range.

        `git patch-id` reads a diff stream and is stable across cherry-picks,
        rebases and whitespace-only differences, which is exactly what identifies
        a backport whose SHA necessarily changed.

        Bounded separately from the rest of the comparison: materialising diffs
        is the most expensive thing here, and a slow one must degrade backport
        detection rather than hold the whole result hostage.
        """
        cap = f"-n {int(limit)} " if limit else ""
        # Staged through a file rather than piped straight into patch-id. A
        # shell pipeline reports only the LAST command's exit status, so a
        # `git log -p` that died - on a missing blob, say - read as success with
        # empty output, and backport detection silently found nothing while
        # reporting that it had looked.
        result = self.shell(
            "TMP=$(mktemp .git/apm-patch-id.XXXXXX) || exit 97\n"
            f"git log {cap}--no-merges --format=%H -p {rev_range} > \"$TMP\"\n"
            "rc=$?\n"
            'if [ "$rc" -eq 0 ]; then git patch-id --stable < "$TMP"; rc=$?; fi\n'
            'rm -f "$TMP"\n'
            'exit "$rc"\n',
            timeout=timeout or self.long_timeout,
        )
        if not result.ok:
            log.warning("patch-id failed: %s", result.stderr.strip()[:200])
            raise GitWorkspaceError(
                "patch-id did not complete", result.stderr.strip(),
                timed_out=_is_timeout(result.stderr),
            )
        mapping = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                patch_id, sha = parts
                mapping[sha] = patch_id
        return mapping

    def show_stat(self, rev_range: str) -> List[dict]:
        result = self._run(
            f"diff --numstat {rev_range}", timeout=self.long_timeout,
            check=False, label="diff",
        )
        changes = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added, removed, path = parts
                changes.append({
                    "path": path,
                    "insertions": int(added) if added.isdigit() else 0,
                    "deletions": int(removed) if removed.isdigit() else 0,
                })
        return changes

    def list_branches(self, url: str) -> List[str]:
        result = self._run(
            f"ls-remote --heads {shlex.quote(url)}",
            timeout=self.long_timeout, check=False, label="ls-remote",
        )
        return [
            line.split("refs/heads/", 1)[1].strip()
            for line in result.stdout.splitlines()
            if "refs/heads/" in line
        ]

    # -- writing -----------------------------------------------------------

    def checkout_new_branch(self, name: str, start_point: str) -> None:
        self._run(
            f"checkout -q -B {shlex.quote(name)} {shlex.quote(start_point)}",
            timeout=self.long_timeout, label="checkout",
        )

    def cherry_pick(self, sha: str) -> GitResult:
        """Apply one commit. A conflict is a normal outcome, not an exception."""
        return self._run(
            f"cherry-pick -x {shlex.quote(sha)}",
            timeout=self.long_timeout, check=False, label="cherry-pick",
        )

    def cherry_pick_abort(self) -> None:
        self._run("cherry-pick --abort", check=False, label="cherry-pick --abort")

    def conflicted_paths(self) -> List[str]:
        result = self._run(
            "diff --name-only --diff-filter=U", check=False, label="diff"
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def write_file(self, relative_path: str, content: str) -> None:
        """Write exactly `content`. Sent base64-encoded: a heredoc added a
        newline of its own, and quoting would have to survive two shells."""
        directory = relative_path.rsplit("/", 1)[0] if "/" in relative_path else "."
        self.shell(
            f"mkdir -p {shlex.quote(directory)} && "
            f"printf %s {shlex.quote(_b64(content))} | base64 -d > "
            f"{shlex.quote(relative_path)}",
            check=True,
        )

    def read_file(self, relative_path: str, rev: str = "HEAD") -> Optional[str]:
        result = self._run(
            f"show {shlex.quote(rev)}:{shlex.quote(relative_path)}",
            check=False, label="show",
        )
        return result.stdout if result.ok else None

    def commit_all(self, message: str) -> Optional[str]:
        """Commit everything in the working tree. For cherry-picks only: it
        stages whatever is there, so a change that must be one file uses
        commit_file_on instead."""
        self._run("add -A", label="add")
        result = self._run(
            f"-c user.name={shlex.quote(self.committer_name)} "
            f"-c user.email={shlex.quote(self.committer_email)} "
            f"commit -q -m {shlex.quote(message)}",
            check=False, label="commit",
        )
        if not result.ok:
            if "nothing to commit" in (result.stdout + result.stderr):
                return None
            raise GitWorkspaceError("git commit failed", result.stderr.strip())
        return self.rev_parse("HEAD")

    def push(self, url: str, branch: str, force: bool = False) -> GitResult:
        flag = "--force-with-lease " if force else ""
        return self._run(
            f"push {flag}{shlex.quote(url)} "
            f"{shlex.quote('HEAD')}:{shlex.quote('refs/heads/' + branch)}",
            timeout=self.long_timeout, check=False, label="push",
        )


    # -- proposing one file ------------------------------------------------
    #
    # Everything below works on objects, never on a checkout. No branch is
    # created locally, no working tree is written, and the commit is built from
    # the base tree plus exactly one entry - so it cannot carry another file,
    # and nothing here can move the branch it was built on.

    def remote_ref(self, url: str, ref: str) -> Optional[str]:
        """The commit `ref` names on the remote, or None when it does not exist.

        Raises when the remote cannot be asked: "no such branch" and "could not
        connect" lead to opposite decisions, so they must not look alike.
        """
        result = self._run(
            f"ls-remote {shlex.quote(url)} {shlex.quote(ref)}",
            timeout=self.long_timeout, check=False, label="ls-remote",
        )
        if not result.ok:
            raise GitWorkspaceError(
                f"could not list {ref} on {url}", result.stderr.strip(),
                timed_out=_is_timeout(result.stderr),
            )
        for line in result.stdout.splitlines():
            sha, _, name = line.partition("\t")
            if name.strip() == ref:
                return sha.strip()
        return None

    def path_exists(self, rev: str, relative_path: str) -> bool:
        """Whether `rev` has a file at `relative_path`. Reads trees only."""
        result = self._run(
            f"ls-tree --name-only {shlex.quote(rev)} -- "
            f"{shlex.quote(relative_path)}",
            label="ls-tree",
        )
        return bool(result.text)

    def commit_file_on(self, base: str, relative_path: str, content: str,
                       message: str, author_name: str,
                       author_email: str) -> str:
        """A new commit whose parent is `base` and whose tree is `base`'s tree
        with `relative_path` set to `content`. Returns its SHA.

        Uses a private index file, so a stray file in the workspace - or an
        index left behind by anything else - cannot reach the commit. The tree
        is written with --missing-ok because a blobless fetch holds the base
        tree's entries by name only; the server has every one of them.
        """
        identity = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in (
                ("GIT_AUTHOR_NAME", author_name),
                ("GIT_AUTHOR_EMAIL", author_email),
                ("GIT_COMMITTER_NAME", author_name),
                ("GIT_COMMITTER_EMAIL", author_email),
            )
        )
        script = (
            # git refuses an empty index file, so only the name is reserved.
            "IDX=$(mktemp .git/apm-index.XXXXXX) || exit 97\n"
            'rm -f "$IDX"\n'
            "trap 'rm -f \"$IDX\"' EXIT\n"
            'export GIT_INDEX_FILE="$IDX"\n'
            f"git read-tree {shlex.quote(base)} || exit 1\n"
            f"BLOB=$(printf %s {shlex.quote(_b64(content))} | base64 -d | "
            f"git hash-object -w --stdin) || exit 1\n"
            f'git update-index --add --cacheinfo 100644,"$BLOB",'
            f"{shlex.quote(relative_path)} || exit 1\n"
            "TREE=$(git write-tree --missing-ok) || exit 1\n"
            f"printf %s {shlex.quote(_b64(message))} | base64 -d | "
            f'env {identity} git commit-tree "$TREE" -p {shlex.quote(base)}\n'
        )
        result = self.shell(script, timeout=self.long_timeout)
        sha = result.text
        if not result.ok or not _looks_like_sha(sha):
            raise GitWorkspaceError(
                f"could not build a commit for {relative_path}",
                result.stderr.strip(),
            )
        return sha

    def changed_paths(self, a: str, b: str) -> List[str]:
        """Every path that differs between two commits. Reads trees only."""
        result = self._run(
            f"diff-tree -r --no-commit-id --name-only {shlex.quote(a)} "
            f"{shlex.quote(b)}",
            label="diff-tree",
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def diff_check(self, a: str, b: str) -> str:
        """`git diff --check` between two commits: empty when whitespace-clean."""
        result = self._run(
            f"diff --check {shlex.quote(a)} {shlex.quote(b)}",
            check=False, label="diff --check",
        )
        return (result.stdout + result.stderr).strip()

    def push_commit(self, url: str, sha: str, branch: str) -> GitResult:
        """Create `branch` on the remote at `sha`. Never forced.

        The destination is spelled out in full, so this can only ever write
        refs/heads/<branch>. An existing branch that is not an ancestor of
        `sha` rejects the push rather than being replaced.
        """
        if not branch or not _looks_like_sha(sha):
            raise GitWorkspaceError(f"refusing to push {sha!r} to {branch!r}")
        return self._run(
            f"push -q {shlex.quote(url)} "
            f"{shlex.quote(sha + ':refs/heads/' + branch)}",
            timeout=self.long_timeout, check=False, label="push",
        )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _is_timeout(stderr: str) -> bool:
    return "timed out after" in (stderr or "")


def _looks_like_sha(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{7,40}", ref or ""))


def _parse_log(raw: str) -> List[LogEntry]:
    entries = []
    for chunk in raw.split(_RECORD):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        fields = chunk.split(_FIELD)
        if len(fields) < 5:
            continue
        sha, author, email, when, subject = fields[:5]
        body = fields[5] if len(fields) > 5 else ""
        entries.append(
            LogEntry(
                sha=sha.strip(), author_name=author.strip(),
                author_email=email.strip(), authored_at=when.strip(),
                subject=subject.strip(), body=body.strip(),
            )
        )
    return entries
