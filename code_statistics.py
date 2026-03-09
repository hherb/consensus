#!/usr/bin/env python3
"""Codebase statistics dashboard for the Consensus project.

Analyzes source files for lines of code, documentation, classes, functions,
and generates a terminal dashboard with per-language breakdowns and
refactoring indicators.

Usage:
    python code_statistics.py [ROOT_DIR] [--exclude DIR ...]
"""

import argparse
import ast
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ── Configuration ───────────────────────────────────────────────────────────

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".eggs", "*.egg-info",
    "consensus.egg-info", "build", "dist", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

# Relative paths (from project root) to exclude by default
DEFAULT_EXCLUDE_PATHS = {
    "docs/discussions",
}

LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".html": "HTML",
    ".css": "CSS",
    ".md": "Markdown",
    ".toml": "TOML",
    ".ini": "INI",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".sql": "SQL",
    ".sh": "Shell",
}

CODE_EXTENSIONS = {".py", ".js", ".html", ".css", ".sql", ".sh"}
DOC_EXTENSIONS = {".md"}


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class FileStats:
    path: str
    language: str
    total_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    docstring_lines: int = 0
    code_lines: int = 0          # total - blank - comment - docstring
    classes: int = 0
    functions: int = 0
    max_function_length: int = 0  # longest function body
    longest_function_name: str = ""


@dataclass
class LanguageStats:
    language: str
    files: int = 0
    total_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    docstring_lines: int = 0
    code_lines: int = 0
    classes: int = 0
    functions: int = 0
    file_stats: list = field(default_factory=list)


# ── Analysis Functions ──────────────────────────────────────────────────────

def should_exclude(path: Path) -> bool:
    for part in path.parts:
        if part in EXCLUDE_DIRS or part.endswith(".egg-info"):
            return True
    return False


def analyze_python_file(filepath: str, content: str) -> FileStats:
    """Deep analysis of a Python file using the AST."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language="Python", total_lines=len(lines))

    # Count blank lines
    stats.blank_lines = sum(1 for line in lines if not line.strip())

    # Use AST to find docstrings, classes, functions
    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError:
        # Fall back to simple counting
        return _simple_count(filepath, "Python", content, comment_char="#")

    # Collect docstring line ranges
    docstring_ranges = set()
    _collect_docstrings(tree, docstring_ranges)

    # Collect comment lines (lines where stripped content starts with #)
    comment_lines = set()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            comment_lines.add(i)

    stats.comment_lines = len(comment_lines)
    stats.docstring_lines = len(docstring_ranges - comment_lines)
    stats.code_lines = (
        stats.total_lines - stats.blank_lines
        - stats.comment_lines - stats.docstring_lines
    )

    # Count classes and functions, track function lengths
    max_func_len = 0
    longest_name = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            stats.classes += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stats.functions += 1
            func_len = _node_line_count(node, lines)
            if func_len > max_func_len:
                max_func_len = func_len
                longest_name = node.name
    stats.max_function_length = max_func_len
    stats.longest_function_name = longest_name

    return stats


def _collect_docstrings(tree, ranges: set):
    """Walk AST and collect line numbers that are part of docstrings."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                ds = node.body[0]
                if ds.end_lineno is not None:
                    for ln in range(ds.lineno, ds.end_lineno + 1):
                        ranges.add(ln)


def _node_line_count(node, lines):
    """Count non-blank, non-comment lines in a function's own body.

    Excludes lines that belong to nested function or class definitions,
    so a wrapper function only reports its own direct code.
    """
    if not hasattr(node, "end_lineno") or node.end_lineno is None:
        return 0

    # Collect line ranges of nested functions/classes to exclude
    nested_ranges = set()
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            if hasattr(child, "end_lineno") and child.end_lineno is not None:
                for ln in range(child.lineno, child.end_lineno + 1):
                    nested_ranges.add(ln)

    count = 0
    for i in range(node.lineno - 1, node.end_lineno):
        line_num = i + 1
        if line_num in nested_ranges:
            continue
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def analyze_js_file(filepath: str, content: str) -> FileStats:
    """Analyze a JavaScript file."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language="JavaScript",
                      total_lines=len(lines))

    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            stats.blank_lines += 1
            continue

        if in_block_comment:
            stats.comment_lines += 1
            if "*/" in stripped:
                in_block_comment = False
            continue

        if stripped.startswith("/*"):
            stats.comment_lines += 1
            if "*/" not in stripped or stripped.endswith("/*"):
                in_block_comment = True
            continue

        if stripped.startswith("//"):
            stats.comment_lines += 1
            continue

    stats.code_lines = (
        stats.total_lines - stats.blank_lines - stats.comment_lines
    )

    # Count functions and classes via regex
    stats.functions = len(re.findall(
        r'(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)|\b(?:async\s+)?\w+\s*\([^)]*\)\s*\{)',
        content
    ))
    stats.classes = len(re.findall(r'\bclass\s+\w+', content))

    # Track longest function (heuristic: brace-counting from function keyword)
    _track_js_functions(stats, content)

    return stats


def _track_js_functions(stats: FileStats, content: str):
    """Heuristic to find longest JS function."""
    lines = content.splitlines()
    func_pattern = re.compile(
        r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\())'
    )
    max_len = 0
    for i, line in enumerate(lines):
        m = func_pattern.search(line)
        if m:
            name = m.group(1) or m.group(2) or "anonymous"
            depth = 0
            started = False
            end = i
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    end = j
                    break
            length = end - i + 1
            if length > max_len:
                max_len = length
                stats.max_function_length = length
                stats.longest_function_name = name


def _simple_count(filepath: str, language: str, content: str,
                  comment_char: str = "#") -> FileStats:
    """Simple line counting for files without AST support."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language=language,
                      total_lines=len(lines))
    for line in lines:
        stripped = line.strip()
        if not stripped:
            stats.blank_lines += 1
        elif stripped.startswith(comment_char):
            stats.comment_lines += 1
    stats.code_lines = (
        stats.total_lines - stats.blank_lines - stats.comment_lines
    )
    return stats


def analyze_css_file(filepath: str, content: str) -> FileStats:
    """Analyze a CSS file."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language="CSS", total_lines=len(lines))

    in_comment = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            stats.blank_lines += 1
            continue
        if in_comment:
            stats.comment_lines += 1
            if "*/" in stripped:
                in_comment = False
            continue
        if stripped.startswith("/*"):
            stats.comment_lines += 1
            if "*/" not in stripped:
                in_comment = True
            continue
    stats.code_lines = (
        stats.total_lines - stats.blank_lines - stats.comment_lines
    )
    return stats


def analyze_html_file(filepath: str, content: str) -> FileStats:
    """Analyze an HTML file."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language="HTML", total_lines=len(lines))

    in_comment = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            stats.blank_lines += 1
            continue
        if in_comment:
            stats.comment_lines += 1
            if "-->" in stripped:
                in_comment = False
            continue
        if "<!--" in stripped:
            stats.comment_lines += 1
            if "-->" not in stripped:
                in_comment = True
            continue
    stats.code_lines = (
        stats.total_lines - stats.blank_lines - stats.comment_lines
    )
    return stats


def analyze_doc_file(filepath: str, content: str, language: str) -> FileStats:
    """Analyze a documentation file (Markdown, etc.)."""
    lines = content.splitlines()
    stats = FileStats(path=filepath, language=language,
                      total_lines=len(lines))
    stats.blank_lines = sum(1 for l in lines if not l.strip())
    stats.docstring_lines = stats.total_lines - stats.blank_lines
    return stats


def analyze_file(filepath: str) -> FileStats | None:
    ext = Path(filepath).suffix.lower()
    language = LANGUAGE_MAP.get(ext)
    if not language:
        return None

    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    if ext == ".py":
        return analyze_python_file(filepath, content)
    elif ext == ".js":
        return analyze_js_file(filepath, content)
    elif ext == ".css":
        return analyze_css_file(filepath, content)
    elif ext == ".html":
        return analyze_html_file(filepath, content)
    elif ext in DOC_EXTENSIONS:
        return analyze_doc_file(filepath, content, language)
    elif ext == ".sh":
        return _simple_count(filepath, language, content, "#")
    elif ext == ".sql":
        return _simple_count(filepath, language, content, "--")
    else:
        # Config files: count lines, no comment parsing
        lines = content.splitlines()
        stats = FileStats(path=filepath, language=language,
                          total_lines=len(lines))
        stats.blank_lines = sum(1 for l in lines if not l.strip())
        stats.code_lines = stats.total_lines - stats.blank_lines
        return stats


# ── Dashboard Rendering ─────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
RESET = "\033[0m"
UNDERLINE = "\033[4m"

BAR_FULL = "█"
BAR_PARTIAL = "▓░"


def color_bar(value: float, max_value: float, width: int = 30,
              color: str = GREEN) -> str:
    if max_value == 0:
        return " " * width
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    return f"{color}{BAR_FULL * filled}{DIM}{'░' * (width - filled)}{RESET}"


def severity_color(value: float, low: float, high: float) -> str:
    """Return GREEN/YELLOW/RED based on thresholds."""
    if value <= low:
        return GREEN
    elif value <= high:
        return YELLOW
    return RED


def render_dashboard(root: str, lang_stats: dict[str, LanguageStats],
                     all_files: list[FileStats]):
    w = 90  # dashboard width

    # ── Header ──
    print()
    print(f"{BOLD}{CYAN}{'═' * w}{RESET}")
    print(f"{BOLD}{CYAN}  CODEBASE STATISTICS DASHBOARD{RESET}")
    print(f"{DIM}  {root}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * w}{RESET}")

    # ── Grand Totals ──
    total_files = sum(ls.files for ls in lang_stats.values())
    total_lines = sum(ls.total_lines for ls in lang_stats.values())
    total_code = sum(ls.code_lines for ls in lang_stats.values())
    total_comments = sum(ls.comment_lines for ls in lang_stats.values())
    total_docstrings = sum(ls.docstring_lines for ls in lang_stats.values())
    total_blank = sum(ls.blank_lines for ls in lang_stats.values())
    total_classes = sum(ls.classes for ls in lang_stats.values())
    total_functions = sum(ls.functions for ls in lang_stats.values())
    total_docs = total_comments + total_docstrings

    print()
    print(f"  {BOLD}OVERVIEW{RESET}")
    print(f"  {'─' * (w - 4)}")
    col1 = 22
    print(f"  {'Files:':<{col1}} {BOLD}{total_files:>6}{RESET}"
          f"    {'Total lines:':<{col1}} {total_lines:>6}")
    print(f"  {'Classes:':<{col1}} {BOLD}{total_classes:>6}{RESET}"
          f"    {'Code lines:':<{col1}} {GREEN}{total_code:>6}{RESET}")
    print(f"  {'Functions:':<{col1}} {BOLD}{total_functions:>6}{RESET}"
          f"    {'Comment lines:':<{col1}} {BLUE}{total_comments:>6}{RESET}")
    print(f"  {'Languages:':<{col1}} {BOLD}{len(lang_stats):>6}{RESET}"
          f"    {'Docstring lines:':<{col1}} {BLUE}{total_docstrings:>6}{RESET}")
    print(f"  {'':<{col1}} {'':>6}"
          f"    {'Blank lines:':<{col1}} {DIM}{total_blank:>6}{RESET}")

    if total_code > 0:
        doc_ratio = total_docs / total_code * 100
        doc_color = severity_color(doc_ratio, 5, 10)  # <5% low, >10% ok
        # flip: low doc ratio is concerning
        doc_color = RED if doc_ratio < 5 else (YELLOW if doc_ratio < 15 else GREEN)
        print()
        print(f"  Documentation ratio: {doc_color}{BOLD}"
              f"{doc_ratio:.1f}%{RESET} "
              f"(comments + docstrings / code lines)")

    # ── Per-Language Breakdown ──
    print()
    print(f"  {BOLD}BY LANGUAGE{RESET}")
    print(f"  {'─' * (w - 4)}")
    hdr = (f"  {BOLD}{'Language':<12} {'Files':>5} {'Total':>7} "
           f"{'Code':>7} {'Comment':>7} {'DocStr':>7} {'Blank':>7} "
           f"{'Classes':>7} {'Funcs':>7}{RESET}")
    print(hdr)

    for lang in sorted(lang_stats, key=lambda l: lang_stats[l].code_lines,
                       reverse=True):
        ls = lang_stats[lang]
        print(f"  {lang:<12} {ls.files:>5} {ls.total_lines:>7} "
              f"{GREEN}{ls.code_lines:>7}{RESET} "
              f"{BLUE}{ls.comment_lines:>7}{RESET} "
              f"{BLUE}{ls.docstring_lines:>7}{RESET} "
              f"{DIM}{ls.blank_lines:>7}{RESET} "
              f"{ls.classes:>7} {ls.functions:>7}")

    # ── Code Distribution Bar Chart ──
    print()
    print(f"  {BOLD}CODE DISTRIBUTION{RESET}")
    print(f"  {'─' * (w - 4)}")
    max_code = max((ls.code_lines for ls in lang_stats.values()), default=1)
    for lang in sorted(lang_stats, key=lambda l: lang_stats[l].code_lines,
                       reverse=True):
        ls = lang_stats[lang]
        if ls.code_lines == 0:
            continue
        bar = color_bar(ls.code_lines, max_code, width=40,
                        color=GREEN if ls.language in ("Python", "JavaScript")
                        else CYAN)
        print(f"  {lang:<12} {bar} {ls.code_lines:>6} lines")

    # ── Largest Files (potential refactoring targets) ──
    print()
    print(f"  {BOLD}LARGEST FILES{RESET} {DIM}(potential refactoring targets){RESET}")
    print(f"  {'─' * (w - 4)}")

    code_files = [f for f in all_files if f.code_lines > 0]
    largest = sorted(code_files, key=lambda f: f.code_lines, reverse=True)[:15]
    max_lines = largest[0].code_lines if largest else 1

    for fs in largest:
        relpath = os.path.relpath(fs.path, root)
        clr = severity_color(fs.code_lines, 200, 400)
        bar = color_bar(fs.code_lines, max_lines, width=25, color=clr)
        flag = f" {RED}◄ consider splitting{RESET}" if fs.code_lines > 400 else ""
        print(f"  {relpath:<45} {bar} {clr}{fs.code_lines:>5}{RESET}"
              f" lines{flag}")

    # ── Longest Functions (complexity hotspots) ──
    print()
    print(f"  {BOLD}LONGEST FUNCTIONS{RESET} "
          f"{DIM}(complexity hotspots){RESET}")
    print(f"  {'─' * (w - 4)}")

    funcs_with_length = [
        f for f in all_files if f.max_function_length > 0
    ]
    longest = sorted(funcs_with_length, key=lambda f: f.max_function_length,
                     reverse=True)[:15]
    max_flen = longest[0].max_function_length if longest else 1

    for fs in longest:
        relpath = os.path.relpath(fs.path, root)
        clr = severity_color(fs.max_function_length, 50, 100)
        bar = color_bar(fs.max_function_length, max_flen, width=20, color=clr)
        flag = (f" {RED}◄ refactor{RESET}"
                if fs.max_function_length > 100 else "")
        print(f"  {relpath:<40} {fs.longest_function_name:<25} "
              f"{bar} {clr}{fs.max_function_length:>4}{RESET} lines{flag}")

    # ── Documentation Coverage by Directory ──
    print()
    print(f"  {BOLD}DOCUMENTATION COVERAGE BY DIRECTORY{RESET}")
    print(f"  {'─' * (w - 4)}")

    dir_stats = defaultdict(lambda: {"code": 0, "docs": 0, "files": 0})
    for fs in all_files:
        reldir = os.path.relpath(os.path.dirname(fs.path), root)
        if reldir == ".":
            reldir = "(root)"
        dir_stats[reldir]["code"] += fs.code_lines
        dir_stats[reldir]["docs"] += fs.comment_lines + fs.docstring_lines
        dir_stats[reldir]["files"] += 1

    for dirname in sorted(dir_stats, key=lambda d: dir_stats[d]["code"],
                          reverse=True):
        ds = dir_stats[dirname]
        if ds["code"] == 0:
            continue
        ratio = ds["docs"] / ds["code"] * 100
        clr = RED if ratio < 5 else (YELLOW if ratio < 15 else GREEN)
        bar_w = 15
        bar = color_bar(ratio, 50, width=bar_w, color=clr)
        print(f"  {dirname:<40} {bar} {clr}{ratio:>5.1f}%{RESET}"
              f"  ({ds['files']} files, {ds['code']} code lines)")

    # ── Test Coverage Indicator ──
    test_files = [f for f in all_files if "/tests/" in f.path
                  or f.path.endswith("_test.py")
                  or os.path.basename(f.path).startswith("test_")]
    src_files = [f for f in all_files
                 if f.language == "Python"
                 and "/tests/" not in f.path
                 and not os.path.basename(f.path).startswith("test_")]
    if src_files:
        test_code = sum(f.code_lines for f in test_files)
        src_code = sum(f.code_lines for f in src_files)
        if src_code > 0:
            ratio = test_code / src_code * 100
            clr = RED if ratio < 30 else (YELLOW if ratio < 70 else GREEN)
            print()
            print(f"  {BOLD}TEST / SOURCE RATIO{RESET}")
            print(f"  {'─' * (w - 4)}")
            print(f"  Test code: {test_code} lines  |  "
                  f"Source code: {src_code} lines  |  "
                  f"Ratio: {clr}{BOLD}{ratio:.1f}%{RESET}")

    # ── Footer ──
    print()
    print(f"{BOLD}{CYAN}{'═' * w}{RESET}")
    print(f"{DIM}  Thresholds: file >400 lines = split candidate, "
          f"function >100 lines = refactor candidate{RESET}")
    print(f"{DIM}  Doc ratio: <5% = low, 5-15% = moderate, >15% = good{RESET}")
    print()


# ── Main ────────────────────────────────────────────────────────────────────

def collect_files(root: str, exclude_paths: set[str] | None = None) -> list[str]:
    excluded = set()
    for ep in (exclude_paths or DEFAULT_EXCLUDE_PATHS):
        excluded.add(os.path.normpath(os.path.join(root, ep)))

    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS
            and not d.endswith(".egg-info")
            and os.path.normpath(os.path.join(dirpath, d)) not in excluded
        ]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext in LANGUAGE_MAP:
                files.append(os.path.join(dirpath, fname))
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Codebase statistics dashboard")
    parser.add_argument("root", nargs="?", default=os.getcwd(),
                        help="Root directory to analyze (default: cwd)")
    parser.add_argument("--exclude", nargs="+", metavar="DIR",
                        help="Additional directories to exclude (relative to root)")
    parser.add_argument("--no-default-excludes", action="store_true",
                        help="Don't apply default path exclusions (docs/discussions)")
    args = parser.parse_args()

    root = os.path.abspath(args.root)

    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    exclude_paths = set() if args.no_default_excludes else set(DEFAULT_EXCLUDE_PATHS)
    if args.exclude:
        exclude_paths.update(args.exclude)

    filepaths = collect_files(root, exclude_paths)
    if not filepaths:
        print(f"No source files found in {root}", file=sys.stderr)
        sys.exit(1)

    all_files: list[FileStats] = []
    lang_stats: dict[str, LanguageStats] = {}

    for fp in filepaths:
        fs = analyze_file(fp)
        if fs is None:
            continue
        all_files.append(fs)

        if fs.language not in lang_stats:
            lang_stats[fs.language] = LanguageStats(language=fs.language)
        ls = lang_stats[fs.language]
        ls.files += 1
        ls.total_lines += fs.total_lines
        ls.blank_lines += fs.blank_lines
        ls.comment_lines += fs.comment_lines
        ls.docstring_lines += fs.docstring_lines
        ls.code_lines += fs.code_lines
        ls.classes += fs.classes
        ls.functions += fs.functions
        ls.file_stats.append(fs)

    render_dashboard(root, lang_stats, all_files)


if __name__ == "__main__":
    main()
