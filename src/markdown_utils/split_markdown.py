import os
import re
import argparse
from pathlib import Path

def sanitize_filename(name):
    """
    Sanitizes a string to be safe for filenames.
    Removes illegal characters and trims whitespace.
    """
    # Remove invalid characters (Windows/Unix safe)
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace whitespace with spaces (or underscores if preferred)
    name = name.strip()
    return name

def split_book_markdown(source_file, output_dir):
    source_path = Path(source_file)
    output_path = Path(output_dir)

    if not source_path.exists():
        print(f"Error: Source file '{source_file}' not found.")
        return

    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)

    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    chapter_counter = 0
    scene_counter = 0

    current_chapter_dir = None
    current_file = None

    # State flags
    in_preamble = True  # Text before the first chapter (Title page, etc.)
    pending_blanks = 0  # Blank lines held back until a content line follows
    file_has_content = False  # True once at least one content line has been written

    print(f"Processing '{source_file}'...")

    for line in lines:
        stripped_line = line.strip()

        # --- DETECT CHAPTER (##) ---
        if stripped_line.startswith("## ") and not stripped_line.startswith("###"):
            # Close existing scene file if open
            if current_file:
                current_file.close()
                current_file = None

            # Increment Chapter
            chapter_counter += 1
            scene_counter = 0 # Reset scene count for new chapter
            in_preamble = False

            # Extract and sanitize title
            raw_title = stripped_line[3:].strip()
            safe_title = sanitize_filename(raw_title)
            folder_name = f"{chapter_counter:02d} {safe_title}"

            # Create Directory
            current_chapter_dir = output_path / folder_name
            current_chapter_dir.mkdir(exist_ok=True)
            print(f"  Created Chapter: {folder_name}")

            # Prepare for potential Intro text (text before first ###)
            # We don't open a file yet; we wait to see if there is content.
            continue

        # --- DETECT SCENE (###) ---
        elif stripped_line.startswith("### "):
            if not current_chapter_dir:
                print(f"Warning: Scene found before first Chapter. Skipping: {stripped_line}")
                continue

            # Close existing scene file
            if current_file:
                current_file.close()

            # Increment Scene
            scene_counter += 1

            # Extract and sanitize title
            raw_title = stripped_line[4:].strip()
            safe_title = sanitize_filename(raw_title)
            if not safe_title:
                safe_title = "Scene"

            filename = f"{scene_counter:02d} {safe_title}.md"
            file_path = current_chapter_dir / filename

            current_file = open(file_path, 'w', encoding='utf-8')
            pending_blanks = 0
            file_has_content = False
            print(f"    Created Scene: {filename}")
            continue

        # --- HANDLE CONTENT ---

        # 1. Ignore Frontmatter/Preamble before first chapter
        if in_preamble:
            continue

        # 2. Handle Text inside a Chapter but before the first Scene (Intro)
        if current_chapter_dir and current_file is None:
            if stripped_line: # Only create intro file if there is actual text
                filename = "00 Intro.md"
                file_path = current_chapter_dir / filename
                current_file = open(file_path, 'w', encoding='utf-8')
                pending_blanks = 0
                file_has_content = True
                print(f"    Created Intro: {filename}")
                current_file.write(line)
            # If empty line, ignore until we hit text or a scene
            continue

        # 3. Write normal content to the active scene file
        if current_file:
            if not stripped_line:
                if file_has_content:
                    pending_blanks += 1
            else:
                if file_has_content and pending_blanks:
                    current_file.write('\n' * pending_blanks)
                current_file.write(line)
                file_has_content = True
                pending_blanks = 0

    # Cleanup: Close the last file
    if current_file:
        current_file.close()

    print("Done.")

def main():
    parser = argparse.ArgumentParser(description="Split a Markdown book into Chapters/Scenes.")
    parser.add_argument("source_file", help="Path to the source markdown file")
    parser.add_argument("output_dir", help="Directory to save the split files")

    args = parser.parse_args()

    split_book_markdown(args.source_file, args.output_dir)

if __name__ == "__main__":
    main()
