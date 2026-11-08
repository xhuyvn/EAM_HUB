#!/usr/bin/env python3
"""
tools/check_translation.py — Translation comparison, identifier consistency, and SC-08 staleness checker.

Modes:
  1. Default mode: SC-08 staleness report.
     Scans all files under docs/vi/**, reads the header:
       Translated from <path> @ <sha>
     Checks whether <path> was modified in git since <sha>.
  2. --compare mode:
     Compares identifier sets (ARCH-, PPM-, C-, WP IDs, ADR-, DG-, D-, ISS-, codes, backtick paths, numbers)
     between each vi/en pair. Reports 0 differences on valid translation.
  3. --fixture mode:
     Tests the fixture detection logic against a sample pair with a removed ID and changed number.
  4. --links mode:
     Verifies all local markdown relative links in English docs resolve to existing files.
  5. --tokens mode:
     Measures token counts before (vi) and after (en) for 3 representative docs to resolve ISS-007.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Identifier extraction regexes
ID_PATTERNS = [
    r"\bARCH-\d+\b",
    r"\bPPM-[A-Za-z0-9_-]+\b",
    r"\bC\d{2}[a-z]?\b",
    r"\b(?:P\d+[a-z]?-WP\d+|PPM-M\d+-WP\d+|H\d+-WP\d+)\b",
    r"\bADR-\d+\b",
    r"\bDG-\d+\b",
    r"\bISS-\d+\b",
    r"\bD-\d+\b",
    r"\bSC-\d+\b",
    r"\bAC-\d+\b",
    r"\b[A-Z][A-Z0-9_]{3,}[A-Z0-9]\b",  # uppercase reason/error codes
]

HEADER_REGEX = re.compile(
    r"(?:<!--\s*)?(?:>\s*)?Translated from\s+([^\s@]+)\s+@\s+([0-9a-fA-F]+)(?:\s*-->)?"
)


def normalize_text_numbers(text: str) -> str:
    """Normalize regional number formatting (decimal commas, thousands separators)."""
    # 86.400 or 86,400 -> 86400
    text = re.sub(r"(\d+)[.,](\d{3})\b", r"\1\2", text)
    # 17,3 -> 17.3
    text = re.sub(r"(\d+),(\d+)\b", r"\1.\2", text)
    return text


def extract_identifiers(text: str) -> set[str]:
    """Extract set of identifiers, backtick references, and numbers from text."""
    ids: set[str] = set()
    # Remove YAML/header or translation comments if at very start
    lines = text.splitlines()
    body_lines = []
    for line in lines:
        if HEADER_REGEX.search(line):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines)

    for pat in ID_PATTERNS:
        for m in re.finditer(pat, body):
            ids.add(m.group(0))

    # Backtick tokens (paths, symbols, flags, config keys)
    for m in re.finditer(r"`([^`\n]+)`", body):
        val = m.group(1).strip()
        if re.match(r"^[A-Za-z0-9_\-\./\*#]+$", val):
            ids.add(val)

    # Standalone or unit-attached numbers (e.g. 17.3, 86400, 60s, 2s, 1600x1000)
    norm_body = normalize_text_numbers(body)
    for m in re.finditer(r"(?<![a-zA-Z0-9_])(\d+(?:\.\d+)?)(?![0-9])", norm_body):
        ids.add(m.group(1))

    return ids


def parse_header(content: str) -> tuple[str | None, str | None]:
    """Extract (path, sha) from the first 5 lines of a translated file."""
    for line in content.splitlines()[:5]:
        m = HEADER_REGEX.search(line)
        if m:
            return m.group(1).replace("\\", "/"), m.group(2)
    return None, None


def find_vi_files(repo_root: Path) -> list[Path]:
    """Find all markdown files under docs/vi/."""
    vi_root = repo_root / "docs" / "vi"
    if not vi_root.exists():
        return []
    return sorted(vi_root.rglob("*.md"))


def has_git_history(repo_root: Path) -> bool:
    """Check whether git history is available at repo_root."""
    git_entry = repo_root / ".git"
    if not git_entry.exists():
        return False
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=False,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0 or res.stdout.strip() != "true":
            return False
        res_head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            check=False,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        return res_head.returncode == 0
    except (FileNotFoundError, PermissionError):
        return False


def run_compare(repo_root: Path) -> int:
    """Compare identifiers between all docs/vi/** and their canonical en counterparts."""
    vi_files = find_vi_files(repo_root)
    if not vi_files:
        print("ERROR: No translated files found under docs/vi/")
        return 1

    total_pairs = 0
    total_diffs = 0

    print(f"Comparing identifier sets for {len(vi_files)} vi/en file pairs...")

    for vi_path in vi_files:
        vi_content = vi_path.read_text(encoding="utf-8")
        rel_target, _sha = parse_header(vi_content)
        if not rel_target:
            # Fallback to computing relative path from docs/vi
            rel_from_vi = vi_path.relative_to(repo_root / "docs" / "vi").as_posix()
            rel_target = rel_from_vi

        en_path = repo_root / rel_target
        if not en_path.exists():
            print(f"ERROR: Canonical English file missing: {rel_target} (referenced in {vi_path})")
            total_diffs += 1
            continue

        en_content = en_path.read_text(encoding="utf-8")
        vi_ids = extract_identifiers(vi_content)
        en_ids = extract_identifiers(en_content)

        missing_in_en = vi_ids - en_ids
        extra_in_en = en_ids - vi_ids

        total_pairs += 1

        if missing_in_en or extra_in_en:
            print(f"\n[FAIL] {rel_target}:")
            if missing_in_en:
                print(f"  Missing in English ({len(missing_in_en)}): {sorted(missing_in_en)[:20]}")
            if extra_in_en:
                print(f"  Extra in English ({len(extra_in_en)}): {sorted(extra_in_en)[:20]}")
            total_diffs += len(missing_in_en) + len(extra_in_en)
        else:
            print(f"  OK: {rel_target} ({len(vi_ids)} identifiers matched)")

    print("\n------------------------------------------------------------")
    if total_diffs == 0:
        print(f"PASS: 0 identifier differences across {total_pairs} file pairs.")
        return 0
    else:
        print(f"FAIL: {total_diffs} identifier differences detected across {total_pairs} pairs.")
        return 1


def run_staleness_report(repo_root: Path, strict: bool = False) -> int:
    """SC-08: report any stale translations where the English file changed after sha."""
    vi_files = find_vi_files(repo_root)
    if not vi_files:
        print("No translated files found in docs/vi/.")
        return 0

    if not has_git_history(repo_root):
        print(f"SC-08 Staleness Report: checking {len(vi_files)} translations against git history...")
        print("  WARNING: No git history available (not a git repository or archive export).")
        print("\n------------------------------------------------------------")
        print("SC-08 Report Summary: not_run: no git history.")
        return 1 if strict else 0

    print(f"SC-08 Staleness Report: checking {len(vi_files)} translations against git history...")
    stale_count = 0
    ok_count = 0
    no_header_count = 0

    for vi_path in vi_files:
        vi_content = vi_path.read_text(encoding="utf-8")
        rel_target, sha = parse_header(vi_content)
        if not rel_target or not sha:
            print(f"  WARNING: Missing or invalid translation header in {vi_path}")
            no_header_count += 1
            continue

        en_path = repo_root / rel_target
        if not en_path.exists():
            print(f"  WARNING: English original missing for {rel_target}")
            stale_count += 1
            continue

        # Check git log between sha and HEAD for rel_target
        cmd = ["git", "log", f"{sha}..HEAD", "--oneline", "--", rel_target]
        res = subprocess.run(cmd, check=False, cwd=repo_root, capture_output=True, text=True)
        if res.returncode == 0:
            if res.stdout.strip():
                commits = res.stdout.strip().splitlines()
                print(f"  STALE: {rel_target} (translated @ {sha}, {len(commits)} commits ahead)")
                stale_count += 1
            else:
                print(f"  OK: {rel_target} @ {sha}")
                ok_count += 1
        else:
            print(f"  ERROR: Unable to check git log for {rel_target} @ {sha}: {res.stderr.strip()}")
            stale_count += 1

    print("\n------------------------------------------------------------")
    print(f"SC-08 Report Summary: {ok_count} up-to-date, {stale_count} stale, {no_header_count} missing headers.")
    if strict and (stale_count > 0 or no_header_count > 0):
        return 1
    # Stale translations are warnings and do not block merge per plan §5
    return 0


def run_fixture_test(repo_root: Path) -> int:
    """Test fixture detection: catch removed ID and changed number."""
    fixture_dir = repo_root / "tests" / "fixtures" / "translation"
    vi_sample = fixture_dir / "sample_vi.md"
    en_broken = fixture_dir / "sample_en_broken.md"

    if not vi_sample.exists() or not en_broken.exists():
        print(f"ERROR: Fixture files missing in {fixture_dir}")
        return 1

    vi_ids = extract_identifiers(vi_sample.read_text(encoding="utf-8"))
    en_ids = extract_identifiers(en_broken.read_text(encoding="utf-8"))

    missing = vi_ids - en_ids
    extra = en_ids - vi_ids

    # Fixture deliberately removes ARCH-01 and changes 18.6 to 18.7
    id_caught = "ARCH-01" in missing
    number_caught = "18.6" in missing and "18.7" in extra

    if id_caught and number_caught:
        print("PASS: Fixture detection verified:")
        print("  - Detected removed identifier: 'ARCH-01'")
        print("  - Detected changed number: '18.6' -> '18.7'")
        return 0
    else:
        print(f"FAIL: Fixture detection failed. missing={missing}, extra={extra}")
        return 1


def run_link_check(repo_root: Path) -> int:
    """Verify all internal relative markdown links in English docs exist."""
    print("Checking internal markdown links across canonical English documentation...")
    broken_links = 0
    checked_links = 0

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^:\)]+?)(?:#[^\)]*)?\)")

    for md_path in repo_root.rglob("*.md"):
        # Skip docs/vi/, .venv, node_modules, .git
        posix = md_path.as_posix()
        if any(skip in posix for skip in ["/docs/vi/", "/.venv/", "/node_modules/", "/.git/"]):
            continue

        content = md_path.read_text(encoding="utf-8")
        for m in link_pattern.finditer(content):
            target = m.group(2).strip()
            # Skip empty, mailto, anchor-only
            if not target or target.startswith(("#", "mailto:")):
                continue

            checked_links += 1
            # Resolve relative to the file's parent
            target_path = (md_path.parent / target).resolve()
            if not target_path.exists():
                print(f"  BROKEN LINK in {md_path.relative_to(repo_root)}: '{target}' (target not found: {target_path})")
                broken_links += 1

    print(f"Link check: {checked_links} links checked, {broken_links} broken.")
    return 0 if broken_links == 0 else 1


def run_token_report(repo_root: Path) -> int:
    """Report token counts before and after translation for 3 representative files (ISS-007)."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        print("tiktoken not installed; token report requires tiktoken.")
        return 1

    targets = [
        "docs/ARCHITECTURE_REVIEW.md",
        "docs/IMPLEMENTATION_PLAN.md",
        "docs/04_PPM_INTEGRATION.md",
    ]

    print("\nISS-007 Token Count Report (cl100k_base):")
    print(f"{'File':<40} {'Vietnamese':<12} {'English':<12} {'Tokens Saved':<15} {'% Reduction'}")
    print("-" * 90)

    total_vi = 0
    total_en = 0

    for rel in targets:
        vi_file = repo_root / "docs" / "vi" / rel
        en_file = repo_root / rel

        if not vi_file.exists() or not en_file.exists():
            print(f"  Missing file pair for {rel}")
            continue

        vi_toks = len(enc.encode(vi_file.read_text(encoding="utf-8")))
        en_toks = len(enc.encode(en_file.read_text(encoding="utf-8")))

        saved = vi_toks - en_toks
        pct = (saved / vi_toks) * 100 if vi_toks > 0 else 0

        total_vi += vi_toks
        total_en += en_toks

        print(f"{rel:<40} {vi_toks:<12} {en_toks:<12} {saved:<15} {pct:.1f}%")

    print("-" * 90)
    total_saved = total_vi - total_en
    total_pct = (total_saved / total_vi) * 100 if total_vi > 0 else 0
    print(f"{'TOTAL':<40} {total_vi:<12} {total_en:<12} {total_saved:<15} {total_pct:.1f}%\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check translation consistency and staleness.")
    parser.add_argument("--compare", action="store_true", help="Compare identifiers between vi and en pairs")
    parser.add_argument("--fixture", action="store_true", help="Run test fixture verification")
    parser.add_argument("--links", action="store_true", help="Check for broken internal markdown links")
    parser.add_argument("--tokens", action="store_true", help="Measure token count difference for ISS-007")
    parser.add_argument("--strict", action="store_true", help="Fail with non-zero exit code on stale translations or missing git history")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    if args.fixture:
        return run_fixture_test(repo_root)
    if args.compare:
        return run_compare(repo_root)
    if args.links:
        return run_link_check(repo_root)
    if args.tokens:
        return run_token_report(repo_root)

    # Default mode: SC-08 staleness report
    return run_staleness_report(repo_root, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
