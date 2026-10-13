"""
merge-markdown - Compile a Markdown file by resolving transclusion directives
and {{var:name}} variable substitutions.

Transclusion syntax (must be the only thing on the line):
    {{relative/path/to/file.md}}          Sibling mode (auto demotion)
    {{relative/path/to/file.md|child}}    Child mode
    {{relative/path/to/file.md|N}}        Explicit top-level heading number

Demotion rules:
    Sibling (no modifier): demotion = preceding_heading_level - 1
        After ###, included # becomes ###  (demotion of 2)
    Child (|child):        demotion = preceding_heading_level
        After ###, included # becomes #### (demotion of 3)
    Explicit (|N):         demotion = N - 1
        |5 means included # becomes #####  (demotion of 4)
    If no preceding heading exists, demotion defaults to 0 for both
    sibling and child modes.

Headings beyond level 6 are capped at level 6.
Only ATX-style headings (lines beginning with #) are adjusted.
Transclusions inside fenced code blocks are not processed.

Variable syntax (may appear anywhere in a line, including inline in prose):
    {{var:name}}

Variables are resolved in a final pass over the fully-merged output (after
every transclusion has been expanded), using a flat name -> value mapping
loaded from a YAML file passed via --vars. This pass always runs, even when
--vars is omitted (in which case the mapping is empty), so any {{var:...}}
reference left in the compiled output is a hard error rather than a token
that silently survives into the final document. Like transclusions,
{{var:...}} references inside fenced code blocks are left untouched.

Library use:
    merge_file(path, variables=None, strict=False) returns the compiled text.
    With strict=True, a transclusion that cannot be resolved (missing file,
    unreadable file, circular include) raises TransclusionError instead of
    printing a warning and dropping the line. An undefined {{var:...}}
    reference raises UndefinedVariableError. Both subclass MergeError.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml


# Matches a transclusion line (optional modifier after |).
# The entire line (ignoring trailing whitespace) must be the directive.
TRANSCLUSION_RE = re.compile(r'^\{\{([^|{}]+?)(?:\|([^}]*))?\}\}\s*$')

# Matches a {{var:name}} reference anywhere within a line.
VAR_RE = re.compile(r'\{\{var:([A-Za-z0-9_.-]+)\}\}')

# Matches an ATX heading: one to six # chars followed by whitespace or end of line.
HEADING_RE = re.compile(r'^(#{1,6})(?=\s|$)')

# Matches the opening of a fenced code block (``` or ~~~, with optional info string).
FENCE_OPEN_RE = re.compile(r'^(`{3,}|~{3,})')


class MergeError(Exception):
    """Base class for errors raised while merging."""


class TransclusionError(MergeError):
    """A transclusion could not be resolved (raised only in strict mode)."""


class UndefinedVariableError(MergeError):
    """One or more {{var:name}} references have no value."""

    def __init__(self, names):
        self.names = sorted(names)
        super().__init__(f"undefined variable(s) referenced: {', '.join(self.names)}")


def _problem(message: str, strict: bool) -> None:
    """Raise in strict mode; otherwise print a warning to stderr."""
    if strict:
        raise TransclusionError(message)
    print(f"Warning: {message}; skipping.", file=sys.stderr)


def get_heading_level(line: str) -> int:
    """Return the ATX heading level of a line, or 0 if it is not a heading."""
    m = HEADING_RE.match(line)
    return len(m.group(1)) if m else 0


def last_heading_level(lines: list[str]) -> int:
    """Return the level of the most recent heading in a list of lines, or 0."""
    for line in reversed(lines):
        level = get_heading_level(line)
        if level:
            return level
    return 0


def demote_headings(lines: list[str], demotion: int) -> list[str]:
    """
    Add `demotion` hashes to every ATX heading, capped at level 6.
    Lines that are not headings pass through unchanged.
    """
    if demotion == 0:
        return lines
    result = []
    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            old_level = len(m.group(1))
            new_level = min(old_level + demotion, 6)
            # Preserve everything after the opening hashes (including the space).
            line = '#' * new_level + line[old_level:]
        result.append(line)
    return result


def process_file(file_path: Path, visited: frozenset | None = None, strict: bool = False) -> list[str]:
    """
    Read `file_path`, resolve any transclusion directives, and return the
    resulting lines (no trailing newlines on individual lines).

    `visited` is a frozenset of resolved absolute paths already on the call
    stack, used to detect circular transclusions.

    With `strict`, a circular, missing or unreadable transclusion raises
    TransclusionError instead of being skipped with a warning.
    """
    if visited is None:
        visited = frozenset()

    abs_path = file_path.resolve()

    if abs_path in visited:
        _problem(f"Circular transclusion detected for '{file_path}'", strict)
        return []

    try:
        text = file_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _problem(f"Transcluded file not found: '{file_path}'", strict)
        return []
    except OSError as exc:
        _problem(f"Could not read '{file_path}': {exc}", strict)
        return []

    visited = visited | {abs_path}
    raw_lines = text.splitlines()
    result: list[str] = []

    # Track fenced code blocks so we do not process transclusions inside them.
    in_fence = False
    fence_marker = ''

    for line in raw_lines:
        # --- Fence tracking ---------------------------------------------------
        fence_match = FENCE_OPEN_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)  # normalise to same char
                result.append(line)
                continue
            elif line.strip() == marker[0] * len(line.strip()) and len(line.strip()) >= len(fence_marker) and marker[0] == fence_marker[0]:
                # Closing fence: same character, at least as long
                in_fence = False
                fence_marker = ''
                result.append(line)
                continue

        if in_fence:
            result.append(line)
            continue

        # --- Transclusion directive -------------------------------------------
        m = TRANSCLUSION_RE.match(line)
        if m:
            rel_path = m.group(1).strip()
            raw_modifier = m.group(2)
            modifier = raw_modifier.strip() if raw_modifier is not None else None

            included_path = (file_path.parent / rel_path).resolve()
            current_level = last_heading_level(result)

            # Determine demotion amount.
            if modifier is None:
                # Sibling mode: included top-level heading matches the preceding heading.
                demotion = max(current_level - 1, 0)

            elif modifier.lower() == 'child':
                # Child mode: included top-level heading is one level deeper.
                demotion = current_level  # works correctly when current_level == 0

            else:
                # Numeric mode: modifier is the desired level for the top-level heading.
                try:
                    target_level = int(modifier)
                    if not 1 <= target_level <= 6:
                        print(
                            f"Warning: Explicit level '{modifier}' is outside [1, 6]; "
                            f"clamping to {max(1, min(6, target_level))}.",
                            file=sys.stderr,
                        )
                        target_level = max(1, min(6, target_level))
                    demotion = target_level - 1
                except ValueError:
                    print(
                        f"Warning: Unknown modifier '{modifier}' in '{line.strip()}'; "
                        f"falling back to sibling mode.",
                        file=sys.stderr,
                    )
                    demotion = max(current_level - 1, 0)

            # Recursively process the included file.
            included_lines = process_file(Path(included_path), visited, strict)

            # Strip leading and trailing blank lines from included content.
            while included_lines and included_lines[0].strip() == '':
                included_lines.pop(0)
            while included_lines and included_lines[-1].strip() == '':
                included_lines.pop()

            # Apply heading demotion and splice in place of the transclusion line.
            included_lines = demote_headings(included_lines, demotion)
            result.extend(included_lines)

        else:
            result.append(line)

    return result


def substitute_variables(lines: list[str], variables: dict) -> list[str]:
    """
    Replace every {{var:name}} reference in `lines` with str(variables[name]).

    Runs as a single pass over already-merged output, so a variable may be
    referenced from any file in the transclusion tree. References inside
    fenced code blocks are left untouched, matching transclusion behavior.

    Any reference to a name not present in `variables` is collected and
    raised as UndefinedVariableError rather than left in the output, since
    a stray {{var:...}} token silently surviving into a compiled prompt or
    document is a worse failure than stopping the build.
    """
    missing: set[str] = set()
    result: list[str] = []

    in_fence = False
    fence_marker = ''

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            missing.add(name)
            return match.group(0)
        return str(variables[name])

    for line in lines:
        fence_match = FENCE_OPEN_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0] * len(marker)
                result.append(line)
                continue
            elif line.strip() == marker[0] * len(line.strip()) and len(line.strip()) >= len(fence_marker) and marker[0] == fence_marker[0]:
                in_fence = False
                fence_marker = ''
                result.append(line)
                continue

        if in_fence:
            result.append(line)
            continue

        result.append(VAR_RE.sub(replace, line))

    if missing:
        raise UndefinedVariableError(missing)

    return result


def merge_file(file_path, variables: dict | None = None, strict: bool = False) -> str:
    """
    Compile `file_path` (transclusions, then {{var:name}} substitution) and
    return the result as text ending in a newline. Raises MergeError
    subclasses on failure; see the module docstring.
    """
    path = Path(file_path)
    if not path.is_file():
        raise TransclusionError(f"File not found: '{path}'")
    lines = process_file(path, strict=strict)
    lines = substitute_variables(lines, variables or {})
    return '\n'.join(lines) + '\n'


def load_variables(vars_path: Path) -> dict:
    if not vars_path.exists():
        print(f"Error: Vars file '{vars_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    try:
        loaded = yaml.safe_load(vars_path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError as exc:
        print(f"Error: Could not parse vars file '{vars_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(loaded, dict):
        print(f"Error: Vars file '{vars_path}' must contain a mapping at the top level.", file=sys.stderr)
        sys.exit(1)

    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='mergeMarkdown',
        description=(
            'Compile a Markdown file by resolving {{transclusion}} directives '
            'and {{var:name}} variables. Included files have their headings '
            'demoted to fit the surrounding context.'
        ),
    )
    parser.add_argument('input', help='Source Markdown file to compile.')
    parser.add_argument('output', help='Destination file for the compiled output.')
    parser.add_argument(
        '-v', '--vars',
        help=(
            'Path to a YAML file of variables (flat name: value mapping) for '
            '{{var:name}} substitution. If omitted, no variables are defined, '
            'so any {{var:...}} reference in the compiled output is an error.'
        ),
    )
    parser.add_argument(
        '--strict',
        action='store_true',
        help='Fail on a missing, unreadable or circular transclusion instead of skipping it.',
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file '{input_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    if not input_path.is_file():
        print(f"Error: '{input_path}' is not a regular file.", file=sys.stderr)
        sys.exit(1)

    if input_path.resolve() == output_path.resolve():
        print("Error: Input and output files must be different paths.", file=sys.stderr)
        sys.exit(1)

    variables = load_variables(Path(args.vars)) if args.vars else {}

    try:
        result_lines = process_file(input_path, strict=args.strict)
        result_lines = substitute_variables(result_lines, variables)
    except MergeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        output_path.write_text('\n'.join(result_lines) + '\n', encoding='utf-8')
    except OSError as exc:
        print(f"Error: Could not write to '{output_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Compiled output written to '{output_path}'.")


if __name__ == '__main__':
    main()
