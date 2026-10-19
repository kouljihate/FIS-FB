#!/usr/bin/env python3
"""Release the current working-tree changes.

Run it after every change prompt, from the repository root::

    python release.py --summary "Valves table and map filters" \
                      --note "Added: map geometry filter widget (Sectors/Zones/Rows/Trees/Pipes)" \
                      --note "Changed: Valves page now uses the Rows-style table" --yes

What it does, in order:

1. Reads the current version from every place the project keeps one
   (``irrigation/__init__.py``, ``CHANGELOG.md``, ``README.md``, ``OPENCODE.md``)
   and takes the highest, so a stale copy can never move the version backwards.
2. Bumps the patch number (``--minor`` / ``--major`` / ``--set X.Y.Z`` override).
3. Writes the new version everywhere and prepends a dated ``CHANGELOG.md`` entry.
4. Shows every changed / untracked file and asks for confirmation (``--yes`` skips).
5. Stages exactly those files plus the version files (never ``git add -A``),
   compiles the package, and commits as ``vX.Y.Z: <summary>``.
6. Pushes the branch to the FIS-FB remote's ``main`` with a force push, using
   ``--force-with-lease`` when the previous remote tip is known, and records that
   previous tip in ``.git/fis-fb-previous-main.txt`` before overwriting it.

Nothing is written until the plan is confirmed; ``--dry-run`` stops after step 4.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

VERSION_FILE = ROOT / "irrigation" / "__init__.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"
README_FILE = ROOT / "README.md"
OPENCODE_FILE = ROOT / "OPENCODE.md"

VERSION_RE = re.compile(r"(__version__\s*=\s*['\"])([^'\"]+)(['\"])")
CHANGELOG_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)", re.MULTILINE)
README_RE = re.compile(r"(Version:\s*\*\*)(\d+\.\d+\.\d+)(\*\*)")
OPENCODE_VERSION_RE = re.compile(r"(- \*\*Version\*\*:\s*)(\d+\.\d+\.\d+)")
OPENCODE_VC_VERSION_RE = re.compile(r"(- Version:\s*)(\d+\.\d+\.\d+)")
OPENCODE_DATE_RE = re.compile(r"(- \*\*Last Updated\*\*:\s*)(\d{4}-\d{2}-\d{2})")

DEFAULT_REMOTE = "fb"
DEFAULT_REMOTE_URL = "git@github.com:kouljihate/FIS-FB.git"
DEFAULT_BRANCH = "main"

ATTRIBUTION = (
    "🤖 Generated with Codebuff\n"
    "Co-Authored-By: Codebuff <noreply@codebuff.com>"
)

NOTE_SECTIONS = ("Added", "Changed", "Fixed")


class Failure(Exception):
    """A step the release cannot continue past."""


# --------------------------------------------------------------------------- #
# shell helpers
# --------------------------------------------------------------------------- #
def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise Failure("command failed: %s\n%s" % (" ".join(args), detail))
    return proc


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], check=check)


# --------------------------------------------------------------------------- #
# version bookkeeping
# --------------------------------------------------------------------------- #
def read_text(path: Path) -> str:
    # newline="" keeps the CRLF endings these files already use.
    return path.read_text(encoding="utf-8", newline="")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def collect_versions() -> dict[str, str]:
    """Every version string the project records, keyed by where it lives."""
    found: dict[str, str] = {}
    code = VERSION_RE.search(read_text(VERSION_FILE))
    if not code:
        raise Failure("no __version__ found in %s" % VERSION_FILE.name)
    found["irrigation/__init__.py"] = code.group(2)

    changelog = CHANGELOG_RE.search(read_text(CHANGELOG_FILE))
    if changelog:
        found["CHANGELOG.md"] = changelog.group(1)

    readme = README_RE.search(read_text(README_FILE))
    if readme:
        found["README.md"] = readme.group(2)

    opencode = read_text(OPENCODE_FILE)
    for label, pattern in (("OPENCODE.md (Current Version)", OPENCODE_VERSION_RE),
                           ("OPENCODE.md (Version Control)", OPENCODE_VC_VERSION_RE)):
        match = pattern.search(opencode)
        if match:
            found[label] = match.group(2)

    # Commit subjects ('vX.Y.Z: ...') also claim a version; honour the newest so a
    # changelog that fell behind the commits can never reissue a version number.
    subjects = git("log", "--pretty=%s", "-100", check=False).stdout
    committed = re.findall(r"^v(\d+\.\d+\.\d+)", subjects, re.MULTILINE)
    if committed:
        found["git log (last 100 commits)"] = max(committed, key=parse_version)
    return found


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        raise Failure("'%s' is not a MAJOR.MINOR.PATCH version" % version)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def highest_version(found: dict[str, str]) -> str:
    return max(found.values(), key=parse_version)


def next_version(current: str, args: argparse.Namespace) -> str:
    if args.set:
        return args.set.strip()
    major, minor, patch = parse_version(current)
    if args.major:
        return "%d.0.0" % (major + 1)
    if args.minor:
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def sync_versions(new_version: str, today: str) -> list[Path]:
    """Rewrite the version everywhere; returns the files that actually changed."""
    updated: list[Path] = []

    def substitute(path: Path, pattern: re.Pattern, replacement: str) -> None:
        text = read_text(path)
        new_text, count = pattern.subn(replacement, text, count=1)
        if count and new_text != text:
            write_text(path, new_text)
            updated.append(path)

    substitute(VERSION_FILE, VERSION_RE, r"\g<1>%s\g<3>" % new_version)
    substitute(README_FILE, README_RE, r"\g<1>%s\g<3>" % new_version)
    substitute(OPENCODE_FILE, OPENCODE_VERSION_RE, r"\g<1>%s" % new_version)
    substitute(OPENCODE_FILE, OPENCODE_VC_VERSION_RE, r"\g<1>%s" % new_version)
    substitute(OPENCODE_FILE, OPENCODE_DATE_RE, r"\g<1>%s" % today)
    return updated


# --------------------------------------------------------------------------- #
# changelog
# --------------------------------------------------------------------------- #
def build_notes(args: argparse.Namespace, changed: list[tuple[str, str]]) -> dict[str, list[str]]:
    notes = {section: [] for section in NOTE_SECTIONS}
    for note in args.note or []:
        text = note.strip()
        if not text:
            continue
        section = "Changed"
        head, sep, rest = text.partition(":")
        if sep and head.strip() in NOTE_SECTIONS and rest.strip():
            section = head.strip()
            text = rest.strip()
        notes[section].append(text)

    if not any(notes.values()):
        for status, path in changed:
            notes["Changed"].append("`%s` (%s)" % (path, status.strip() or "?"))
    return notes


def prepend_changelog(new_version: str, today: str, notes: dict[str, list[str]]) -> Path:
    text = read_text(CHANGELOG_FILE)
    newline = "\r\n" if "\r\n" in text else "\n"

    lines = ["## %s - %s" % (new_version, today), ""]
    for section in NOTE_SECTIONS:
        if not notes[section]:
            continue
        lines.append("### %s" % section)
        lines.extend("- %s" % item for item in notes[section])
        lines.append("")
    block = newline.join(lines) + newline + newline

    anchor = "# Changelog" + newline
    if anchor not in text:
        raise Failure("CHANGELOG.md does not start with '# Changelog'")
    if "## %s - " % new_version in text:
        raise Failure("CHANGELOG.md already has an entry for %s" % new_version)

    write_text(CHANGELOG_FILE, text.replace(anchor, anchor + newline + block, 1))
    return CHANGELOG_FILE


# --------------------------------------------------------------------------- #
# working tree
# --------------------------------------------------------------------------- #
def changed_files() -> list[tuple[str, str]]:
    out = git("status", "--porcelain").stdout
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, rest = line[:2], line[3:]
        if " -> " in rest:  # rename: keep the destination path
            rest = rest.split(" -> ", 1)[1]
        path = rest.strip().strip('"')
        if path:
            entries.append((status, path))
    return entries


def compile_check() -> None:
    proc = run([sys.executable, "-m", "compileall", "-q", "irrigation"], check=False)
    if proc.returncode != 0:
        raise Failure("compileall failed:\n%s" % (proc.stderr or proc.stdout).strip())


# --------------------------------------------------------------------------- #
# git steps
# --------------------------------------------------------------------------- #
def commit_message(new_version: str, summary: str, notes: dict[str, list[str]],
                   attribution: bool) -> str:
    body: list[str] = []
    for section in NOTE_SECTIONS:
        body.extend("- %s" % item for item in notes[section])
    parts = ["v%s: %s" % (new_version, summary)]
    if body:
        parts.append("\n".join(body))
    if attribution:
        parts.append(ATTRIBUTION)
    return "\n\n".join(parts)


def ensure_remote(name: str, url: str) -> None:
    current = git("remote", "get-url", name, check=False)
    if current.returncode == 0:
        existing = current.stdout.strip()
        if existing != url:
            print("! remote '%s' already points at %s (expected %s) — using it as-is"
                  % (name, existing, url))
        return
    git("remote", "add", name, url)
    print("+ added remote %s -> %s" % (name, url))


def remote_tip(name: str, branch: str) -> str | None:
    if git("fetch", name, branch, check=False).returncode != 0:
        return None
    rev = git("rev-parse", "--verify", "refs/remotes/%s/%s" % (name, branch), check=False)
    if rev.returncode != 0:
        return None
    return rev.stdout.strip()


def push(name: str, branch: str, previous_tip: str | None) -> None:
    args = ["push", name, "HEAD:%s" % branch]
    if previous_tip:
        args.append("--force-with-lease=%s:%s" % (branch, previous_tip))
    else:
        args.append("--force")

    proc = git(*args, check=False)
    if proc.returncode != 0:
        raise Failure("push failed:\n%s\n(stderr)\n%s"
                      % (proc.stdout.strip(), proc.stderr.strip()))
    print(proc.stderr.strip() or proc.stdout.strip())


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump the version, update the markdown files, commit and push to FIS-FB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python release.py --list\n"
               "  python release.py --summary \"Map filters\" --note \"Added: funnel widget\" --yes\n"
               "  python release.py --minor --no-push --summary \"Components tab\"\n",
    )
    bump = parser.add_mutually_exclusive_group()
    bump.add_argument("--minor", action="store_true", help="bump MINOR instead of patch")
    bump.add_argument("--major", action="store_true", help="bump MAJOR")
    bump.add_argument("--set", metavar="X.Y.Z", help="use this exact version")
    parser.add_argument("--summary", help="commit subject after 'vX.Y.Z: '")
    parser.add_argument("--note", action="append", metavar="[SECTION:] TEXT",
                        help="changelog bullet; SECTION is Added, Changed or Fixed")
    parser.add_argument("--list", action="store_true", help="print versions and changes, then stop")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--no-push", action="store_true", help="commit locally, do not push")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="remote name (default: %(default)s)")
    parser.add_argument("--remote-url", default=DEFAULT_REMOTE_URL, help="remote URL to add")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="branch on the remote (default: %(default)s)")
    parser.add_argument("--no-attribution", action="store_true",
                        help="omit the Codebuff footer from the commit message")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if not (ROOT / ".git").exists():
        raise Failure("release.py must live at the repository root")
    if git("rev-parse", "--git-dir", check=False).returncode != 0:
        raise Failure("not inside a git repository")
    for state in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"):
        if git("rev-parse", "--verify", "--quiet", state, check=False).returncode == 0:
            raise Failure("a %s is in progress; finish it before releasing" % state)

    found = collect_versions()
    current = highest_version(found)
    changed = changed_files()

    print("versions on disk:")
    for where, value in sorted(found.items()):
        print("  %-32s %s%s" % (where, value, "   <- newest" if value == current else ""))
    print("\nchanged files (%d):" % len(changed))
    for status, path in changed:
        print("  %-3s %s" % (status.strip(), path))

    if args.list:
        return 0

    if not changed:
        print("\nnothing to release: working tree is clean")
        return 0

    version = next_version(current, args)
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    today = datetime.date.today().isoformat()
    summary = (args.summary or "").strip()
    notes = build_notes(args, changed)
    if not summary:
        first = next((item for section in NOTE_SECTIONS for item in notes[section]), "")
        summary = first or "Update %d file(s)" % len(changed)

    print("\nrelease plan:")
    print("  version      %s -> %s" % (current, version))
    print("  changelog    %s  (%s)" % (CHANGELOG_FILE.name, today))
    print("  commit       v%s: %s" % (version, summary))
    print("  push         %s %s -> %s:%s%s"
          % (args.remote, branch, args.remote, args.branch,
             "" if args.no_push else "  (force)"))
    print("  staged       %d changed file(s) + version files" % len(changed))

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    if not args.yes:
        answer = input("\nproceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    originals = {path: read_text(path) for path in
                 (VERSION_FILE, CHANGELOG_FILE, README_FILE, OPENCODE_FILE)}
    touched = sync_versions(version, today)
    touched.append(prepend_changelog(version, today, notes))
    touched = sorted({path for path in touched})

    try:
        compile_check()
    except Failure as exc:
        for path, text in originals.items():
            write_text(path, text)
        raise Failure("%s\n(version files restored)" % exc)

    stage = [str(path.relative_to(ROOT)) for path in touched]
    stage.extend(path for _, path in changed if path not in stage)
    git("add", "--", *stage)
    print("\nstaged:")
    print(git("diff", "--cached", "--stat").stdout.strip())

    message = commit_message(version, summary, notes, not args.no_attribution)
    git("commit", "-m", message)
    print(git("log", "--oneline", "-1").stdout.strip())

    if args.no_push:
        print("\npushed: no (--no-push). Push later with: git push %s %s:%s --force"
              % (args.remote, branch, args.branch))
        return 0

    ensure_remote(args.remote, args.remote_url)
    previous_tip = remote_tip(args.remote, args.branch)
    if previous_tip:
        backup = ROOT / ".git" / "fis-fb-previous-main.txt"
        write_text(backup, "%s %s %s/%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"),
                                              previous_tip, args.remote, args.branch))
        print("previous %s/%s was %s (saved in %s)"
              % (args.remote, args.branch, previous_tip[:12], backup.name))
    push(args.remote, args.branch, previous_tip)
    print("\nreleased v%s" % version)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Failure as error:
        print("\nrelease failed: %s" % error, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
