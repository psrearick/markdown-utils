import os
import re
import argparse
import shutil
import yaml
from typing import Any

def remove_leading_number(title):
    return re.sub(r'^\d+\s+', '', title)

def substitute_title(title, mod_config):
    substitutions = mod_config.get("substitutions")
    substitution = substitutions.get(title)

    return title if substitution is None else substitution

def read_file_safely(file_path):
    """
    Returns file content or empty string if file can't be read.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, PermissionError, IOError):
        return ""

def adjust_headings(content: str, base_level_offset: int = 0, keep_numbers: bool = False, mod_config: Any = None) -> str:
    """
    Adjusts all markdown heading levels in a string by a given offset.
    """
    if base_level_offset == 0:
        return content

    for i in range(8, 0, -1):
        def replace_func(match: re.Match) -> str:
            original_level = len(match.group(1))
            new_level = original_level + base_level_offset

            new_level = max(1, min(8, new_level))

            title = match.group(2)

            if not keep_numbers:
                title = remove_leading_number(title)

            title = substitute_title(title, mod_config) if mod_config else title

            return f'{"#" * new_level} {title}'

        search_pattern = re.compile(rf"^(#{'{'}{i}{'}'}) (.*)", re.MULTILINE)
        content = search_pattern.sub(replace_func, content)

    return content

def process_includes(content: str, base_dir: str) -> str:
    """
    Processes placeholders using this syntax: <!-- include file="path/to/file.md" heading-level="3" show-title="false" -->
    """
    pattern = re.compile(r'<!-- include\s+((?:[\w-]+="[^"]*"\s*)+)-->')
    attr_pattern = re.compile(r'([\w-]+)="([^"]*)"')

    def parse_attributes(attr_string: str) -> dict:
        return dict(attr_pattern.findall(attr_string))

    def replace_include(match: re.Match) -> str:
        include_config = parse_attributes(match.group(0))
        path = include_config['file']
        level = int(include_config.get('heading-level', 1))
        show_title = include_config.get('show-title', "true").lower() == "true"

        included_content = read_file_safely(path).strip()

        if not show_title:
            included_content = re.sub(r'^# .+$\n*', '', included_content, count=1, flags=re.MULTILINE)
            level -= 1

        level = max(0, level - 1)
        included_content = adjust_headings(included_content, level)

        return included_content

    return pattern.sub(replace_include, content)

def get_content_for_path(item_path, depth=1, custom_title=None, keep_numbers=False, mod_config=None, ignore_frontmatter=False) -> str:

    content = read_file_safely(item_path)

    if not content:
        return ""

    content = process_includes(content, os.path.dirname(item_path))

    frontmatter_content = ""

    frontmatter_pattern = r'^---\n(.*?)\n---\n'
    frontmatter_match = re.match(frontmatter_pattern, content, re.DOTALL)
    if frontmatter_match:
        frontmatter_content = "" if ignore_frontmatter else f"## Metadata\n\n{frontmatter_match.group(1).strip()}" + "\n\n"
        content = content[frontmatter_match.end():].strip()
    else:
        content = content.strip()

    existing_title_match = re.match(r'^# (.+)$', content, re.MULTILINE)

    new_title = os.path.splitext(os.path.basename(item_path))[0]
    if custom_title:
        new_title = custom_title
    elif existing_title_match:
        new_title = existing_title_match.group(1)

    if not keep_numbers:
        new_title = remove_leading_number(new_title)

    new_title = substitute_title(new_title, mod_config) if mod_config else new_title
    new_title_header = f"# {new_title}"

    if os.path.exists(os.path.join(os.path.dirname(item_path), ".no-headings")):
        new_title_header = "* * *"

    content = re.sub(r'^# .+\n', '', content, count=1, flags=re.MULTILINE)
    content = f"{new_title_header}\n\n{frontmatter_content}{content.strip()}\n"

    content = adjust_headings(content, depth, keep_numbers, mod_config)

    return f"\n{content}"

def process_folder(folder_path, depth=1, item_order=None, include_all=False, keep_numbers=False, mod_config=None, ignore_frontmatter=False):
    output = []
    processed_items = set()

    if item_order:
        for item in item_order:
            sub_item_order = None
            item_name = item
            custom_title = None

            if isinstance(item, dict):
                item_name, item_value = next(iter(item.items()))
                if isinstance(item_value, str):
                    custom_title = item_value
                elif isinstance(item_value, list):
                    order_list = list()
                    for sub_item in item_value:
                        if isinstance(sub_item, dict) and 'order' in sub_item:
                            sub_item_order = sub_item['order']
                        elif isinstance(sub_item, dict) and 'title' in sub_item:
                            custom_title = sub_item['title']
                        elif isinstance(sub_item, str):
                            order_list.append(sub_item)
                    if len(order_list) > 0 and sub_item_order == None:
                        sub_item_order = order_list

            item_path = os.path.join(folder_path, item_name)
            processed_items.add(item_name)

            if os.path.isdir(item_path):
                no_compile = os.path.join(item_path, ".no_compile")
                if os.path.exists(no_compile):
                    continue

                folder_title = custom_title or os.path.basename(item_path)
                if not keep_numbers:
                    folder_title = remove_leading_number(folder_title)
                folder_title = substitute_title(folder_title, mod_config) if mod_config else folder_title
                output.append(f"\n{'#' * (depth + 1)} {folder_title}\n")
                output.extend(process_folder(item_path, depth + 1, sub_item_order, include_all=(include_all or sub_item_order is None), keep_numbers=keep_numbers, mod_config=mod_config, ignore_frontmatter=ignore_frontmatter))
            elif item_name.endswith('.md') and os.path.isfile(item_path):
                output.append(get_content_for_path(item_path, depth, custom_title, keep_numbers=keep_numbers, mod_config=mod_config, ignore_frontmatter=ignore_frontmatter))

    if include_all:
        all_items = sorted(os.listdir(folder_path))
        for item in all_items:
            if item not in processed_items:
                item_path = os.path.join(folder_path, item)
                if os.path.isdir(item_path):
                    no_compile = os.path.join(item_path, ".no_compile")
                    if os.path.exists(no_compile):
                        continue
                    item_title = os.path.basename(item_path)
                    folder_title = item_title if keep_numbers else remove_leading_number(item_title)
                    folder_title = substitute_title(folder_title, mod_config) if mod_config else folder_title
                    output.append(f"\n{'#' * (depth + 1)} {folder_title}\n")
                    output.extend(process_folder(item_path, depth + 1, None, include_all=include_all, keep_numbers=keep_numbers, mod_config=mod_config, ignore_frontmatter=ignore_frontmatter))
                elif item.endswith('.md') and os.path.isfile(item_path):
                    output.append(get_content_for_path(item_path, depth, keep_numbers=keep_numbers, mod_config=mod_config, ignore_frontmatter=ignore_frontmatter))

    if len(output) > 0 and output[0].startswith("\n* * *\n\n"):
        output[0] = output[0][7:]

    return output

def compile_directory_to_file(root_folder, output, yaml_path=None, include_all=True, keep_numbers=False, output_name=None, mod_path=None, ignore_frontmatter=False):
    root_folder_name = os.path.basename(os.path.normpath(root_folder))
    root_title = root_folder_name if keep_numbers else remove_leading_number(root_folder_name)
    output_file = f"{root_title}.md" if output_name is None or not output_name else output_name

    if os.path.exists(output):
        output_file = os.path.join(output, output_file) if os.path.isdir(output) else output

    frontmatter = ''
    frontmatter_file_path = os.path.join(os.path.normpath(root_folder), 'frontmatter.yaml')
    if os.path.exists(frontmatter_file_path):
        with open(frontmatter_file_path, 'r') as f:
            frontmatter = f.read().strip()

    order_config = None
    item_order = None
    include = include_all

    if yaml_path and os.path.exists(yaml_path):
        with open(yaml_path, 'r') as f:
            order_config = yaml.safe_load(f)

    if order_config:
        item_order = order_config.get(root_folder_name)
        if item_order is None:
            item_order = order_config.get("root")
        if item_order is not None:
            for item in item_order:
                if "order" in item:
                    item_order = item["order"]
                if "title" in item:
                    root_title = item["title"]
    else:
        include = True

    mod_config = None

    if mod_path and os.path.exists(mod_path):
        with open(mod_path, 'r') as f:
            mod_config = yaml.safe_load(f)

    if mod_config:
        root_title = substitute_title(root_title, mod_config)

    with open(output_file, 'w') as f:
        if frontmatter:
            f.write('---\n')
            f.write(frontmatter)
            f.write('\n---\n\n')
        f.write(f"# {root_title}\n")
        f.writelines(process_folder(root_folder, item_order=item_order if order_config else None, include_all=include, keep_numbers=keep_numbers, mod_config=mod_config, ignore_frontmatter=ignore_frontmatter))

def delete_dirs(
    source,
    output,
    target="",
):
    if not os.path.exists(os.path.normpath(os.path.join(source, target))):
        return

    if not os.path.isdir(output) and os.path.exists(output):
        os.remove(os.path.normpath(output))
        return

    output_target_dir = os.path.abspath(os.path.join(output, target))
    top_level = target == "" or target == "."

    if not top_level:
        return

    if not os.path.exists(output_target_dir):
        return

    shutil.rmtree(output_target_dir)

def compile_all(
    source,
    output,
    recursive=False,
    yaml_path=None,
    include_all=True,
    keep_numbers=True,
    propagate=False,
    target="",
    mod_path=None,
    ignore_frontmatter=False
):
    source_target_dir = os.path.normpath(os.path.join(source, target))

    if not os.path.exists(source_target_dir):
        return

    output_name = None

    if not os.path.isdir(output):
        output_name = os.path.basename(output) if os.path.basename(output) else output_name
        output = os.path.dirname(output)

    output_target_dir = os.path.join(output, target)

    if not os.path.exists(output_target_dir) or not os.path.samefile(output, output_target_dir):
        output_target_dir = os.path.dirname(output_target_dir)

    source_target_dir = os.path.abspath(source_target_dir)
    output_target_dir = os.path.abspath(output_target_dir)
    source_path = os.path.abspath(source)
    output_path = os.path.abspath(output)

    no_compile = os.path.join(source_target_dir, ".no_compile")
    if os.path.exists(no_compile):
        return

    if source_path not in os.path.commonpath([source_path, source_target_dir]):
        source_target_dir = source_path

    if output_path not in os.path.commonpath([output_path, output_target_dir]):
        output_target_dir = output_path

    os.makedirs(output_target_dir, exist_ok=True)

    order_file = yaml_path
    if yaml_path is None:
        order_file = os.path.join(source_target_dir, "order.yaml")
        order_file = order_file if os.path.exists(order_file) else None

    mod_file = mod_path if mod_path and os.path.exists(mod_path) else None

    compile_directory_to_file(
        source_target_dir,
        output_target_dir,
        yaml_path=order_file,
        include_all=include_all,
        keep_numbers=keep_numbers,
        output_name=output_name,
        mod_path=mod_file,
        ignore_frontmatter=ignore_frontmatter
    )

    if propagate:
        if source_target_dir != source_path:
            new_target = os.path.dirname(source_target_dir)
            compile_all(
                source,
                output,
                recursive=False,
                yaml_path=yaml_path,
                include_all=include_all,
                keep_numbers=keep_numbers,
                propagate=propagate,
                target=new_target,
                mod_path=mod_file,
                ignore_frontmatter=ignore_frontmatter
            )

        return

    if recursive:
        for item in os.listdir(source_target_dir):
            item_path = os.path.join(source_target_dir, item)
            if os.path.isdir(item_path):
                no_compile = os.path.join(item_path, ".no_compile")
                if os.path.exists(no_compile):
                    continue

                end_compile = os.path.join(os.path.dirname(item_path), ".end_compile")
                should_continue = not os.path.exists(end_compile)

                item_rel_path = os.path.relpath(item_path, source_path)
                compile_all(
                    source,
                    output,
                    recursive=should_continue,
                    include_all=include_all,
                    keep_numbers=keep_numbers,
                    propagate=propagate,
                    target=item_rel_path,
                    mod_path=mod_file,
                    ignore_frontmatter=ignore_frontmatter
                )

def main():
    parser = argparse.ArgumentParser(description="Combine Markdown files from a folder hierarchy.")
    parser.add_argument("-a", "--all", action="store_true", default=None, help="Include all markdown files, even those not in the YAML file")
    parser.add_argument("-c", "--config", help="Path to the YAML config file (default: compile.yaml in source directory)")
    parser.add_argument("-d", "--delete", action="store_true", default=None, help="Delete existing output directory before compiling")
    parser.add_argument("-i", "--ignore-frontmatter", action="store_true", default=None, help="Do not add YAML frontmatter")
    parser.add_argument("-k", "--keep-numbers", action="store_true", default=None, help="Keep leading numbers in titles")
    parser.add_argument("-m", "--mod", help="Path to the YAML modification file")
    parser.add_argument("-o", "--output", help="Output directory name (default: ./compiled in source directory)")
    parser.add_argument("-p", "--propagate", action="store_true", default=None, help="Propagate up to parent directories")
    parser.add_argument("-r", "--recursive", action="store_true", default=None, help="Compile files recursively")
    parser.add_argument("-s", "--source", help="Path to the source directory (default: current working directory)")
    parser.add_argument("-t", "--target", help="Path to the target directory relative to source directory (default: './')")
    parser.add_argument("-y", "--yaml", help="Path to the YAML order file (default: order.yaml in directory to compile)")

    args = parser.parse_args()

    include_all = args.all
    keep_numbers = args.keep_numbers
    output = args.output
    recursive = args.recursive
    source = args.source
    yaml_path = args.yaml
    propagate = args.propagate
    target = args.target
    mod_path = args.mod
    delete = args.delete
    ignore_frontmatter = args.ignore_frontmatter

    config_path = args.config or "compile.yaml"
    config = None
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

    if config:
        if "include_all" in config:
            include_all = include_all if include_all else config.get("include_all")
        if "keep_numbers" in config:
            keep_numbers = keep_numbers if keep_numbers else config.get("keep_numbers")
        if "output" in config:
            output = output if output else config.get("output")
        if "recursive" in config:
            recursive = recursive if recursive else config.get("recursive")
        if "source" in config:
            source = source if source else config.get("source")
        if "yaml_path" in config:
            yaml_path = yaml_path if yaml_path else config.get("yaml_path")
        if "propagate" in config:
            propagate = propagate if propagate else config.get("propagate")
        if "target" in config:
            target = target if target else config.get("target")
        if "modification_path" in config:
            mod_path = mod_path if mod_path else config.get("modification_path")
        if "ignore_frontmatter" in config:
            ignore_frontmatter = ignore_frontmatter if ignore_frontmatter else config.get("ignore_frontmatter")
        if "delete" in config:
            delete = delete if delete else config.get("delete")

    include_all = True if include_all is None else include_all
    keep_numbers = False if keep_numbers is None else keep_numbers
    ignore_frontmatter = False if ignore_frontmatter is None else ignore_frontmatter
    recursive = False if recursive is None else recursive
    propagate = False if propagate is None else propagate
    delete = False if delete is None else delete
    source = os.getcwd() if source is None else source
    output = os.path.join(source, "compiled") if output is None else output
    target = "" if target is None else target
    target = os.path.normpath(target)

    if delete:
        delete_dirs(source, output, target)

    compile_all(
        source,
        output,
        recursive=recursive,
        yaml_path=yaml_path,
        include_all=include_all,
        keep_numbers=keep_numbers,
        propagate=propagate,
        target=target,
        mod_path=mod_path,
        ignore_frontmatter=ignore_frontmatter
    )

if __name__ == "__main__":
    main()
