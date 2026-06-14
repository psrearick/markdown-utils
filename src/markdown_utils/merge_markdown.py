"""
merge-markdown - Compile a Markdown file by resolving transclusion directives.

Transclusion syntax:
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
"""

import argparse
import re
import sys
from pathlib import Path


# Matches a transclusion line (optional modifier after |).
# The entire line (ignoring trailing whitespace) must be the directive.
TRANSCLUSION_RE = re.compile(r'^\{\{([^|{}]+?)(?:\|([^}]*))?\}\}\s*$')

# Matches an ATX heading: one to six # chars followed by whitespace or end of line.
HEADING_RE = re.compile(r'^(#{1,6})(?=\s|$)')

# Matches the opening of a fenced code block (``` or ~~~, with optional info string).
FENCE_OPEN_RE = re.compile(r'^(`{3,}|~{3,})')


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


def process_file(file_path: Path, visited: frozenset | None = None) -> list[str]:
    """
    Read `file_path`, resolve any transclusion directives, and return the
    resulting lines (no trailing newlines on individual lines).

    `visited` is a frozenset of resolved absolute paths already on the call
    stack, used to detect circular transclusions.
    """
    if visited is None:
        visited = frozenset()

    abs_path = file_path.resolve()

    if abs_path in visited:
        print(
            f"Warning: Circular transclusion detected for '{file_path}'; skipping.",
            file=sys.stderr,
        )
        return []

    try:
        text = file_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        print(f"Warning: Transcluded file not found: '{file_path}'; skipping.", file=sys.stderr)
        return []
    except OSError as exc:
        print(f"Warning: Could not read '{file_path}': {exc}; skipping.", file=sys.stderr)
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
            included_lines = process_file(Path(included_path), visited)

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='mergeMarkdown',
        description=(
            'Compile a Markdown file by resolving {{transclusion}} directives. '
            'Included files have their headings demoted to fit the surrounding context.'
        ),
    )
    parser.add_argument('input', help='Source Markdown file to compile.')
    parser.add_argument('output', help='Destination file for the compiled output.')
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

    result_lines = process_file(input_path)

    try:
        output_path.write_text('\n'.join(result_lines) + '\n', encoding='utf-8')
    except OSError as exc:
        print(f"Error: Could not write to '{output_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Compiled output written to '{output_path}'.")


if __name__ == '__main__':
    main()
