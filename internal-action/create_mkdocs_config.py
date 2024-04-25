#!/usr/bin/env python3
"""
Create MKDocs config yaml.

If a README file is given, the content is split into individual markdown file
for MKDocs to render.
"""
import argparse
import collections
import contextlib
import itertools
import os
import re
import shutil
import tempfile

from urllib.parse import urlparse, urlunparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import magic
import mdformat
import yaml

from markdown_it import MarkdownIt
from markdown_it.token import Token


VALID_IMAGE_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/pjpeg'
    'image/gif',
    'image/tiff',
    'image/x-tiff',
    'image/svg+xml'
}


def repo_name_type(value: str) -> str:
    "An argparse type for GitHub repo names of the form `org/repo`."
    fragments = value.split("/")
    if len(fragments) != 2 \
            or not fragments[0].strip() \
            or not fragments[1].strip():
        raise ValueError(f"{value} doesn't match the form 'orgname/reponame'")

    return value


def parse_args():
    """ parse args """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--pipeline-dir',
        type=Path,
        required=True,
        help='Path to the pipeline directory. Should be set to '
        'GITHUB_WORKSPACE when called from github action.'
    )
    parser.add_argument(
        '--pipeline-repo',
        type=repo_name_type,
        required=True,
        help='Pipeline repo name. Should be set to GITHUB_REPOSITORY '
        ' when called from github action.'
    )
    parser.add_argument(
        '--mkdocs-config',
        type=Path,
        help='Additional MKDocs config file.',
        default=None
    )
    parser.add_argument(
        '--readme',
        type=Path,
        help='Relative path to the README.md file.',
        default=Path('README.md')
    )

    return parser.parse_args()


def strip_markdown(text):
    """
    Strip away all Markdown formatting in this inline text.
    """
    renderer = MarkdownIt("gfm-like")

    def render_code_inline(_renderer, tokens, i, _options, _env):
        "Render just the content of the code block."
        return tokens[i].content

    renderer.add_render_rule("code_inline", render_code_inline)

    def render_nothing(*_):
        "Render nothing for offending tags."
        return ""

    # Ignore the following format styles:
    #   **bold text** <strong>
    #   [linked text](example.com) <link>
    #   _italic text_ <em>
    #   ~~struck text~~ <s>
    for ignore_tag in ("strong", "link", "em", "s"):
        renderer.add_render_rule(f"{ignore_tag}_open", render_nothing)
        renderer.add_render_rule(f"{ignore_tag}_close", render_nothing)

    return renderer.renderInline(text)


def get_heading_anchor(text):
    """
    Return the anchor name GitHub would assign to this heading text.
    """
    # Based on https://gist.github.com/asabaylus/3071099, it seems like GitHub
    # replaces spaces with dashes and strips all special characters other than
    # - and _. I can't find an authoritative source confirming these rules.
    plain_text = strip_markdown(text.strip())
    no_spaces = re.sub(r"\s", "-", plain_text)
    no_specials = re.sub(r"[^\w_-]", "", no_spaces)
    return no_specials.casefold()


@dataclass
class Page:
    "A page to be rendered."
    title: str
    filename: str = ""
    tokens: list[Token] = field(default_factory=list)

    def get_filename(self):
        "Get the associated filename."
        if not self.filename:
            return f"{get_heading_anchor(self.title)}.md"
        return self.filename


def split_readme(readme_file: Path,
                 docs_dir: Path,
                 pipeline_repo: str) -> List[Dict[str, Path]]:
    """
    Split the README file into individual markdown files.

    Return a list of {<title>: <filename>} dictionaries suitable for a MkDocs
    nav element.
    """
    # pylint: disable=too-many-locals,too-many-statements
    img_dir = docs_dir / 'img'

    docs_dir.mkdir(exist_ok=True)
    img_dir.mkdir(exist_ok=True)

    # Parse the original markdown file into a stream of tokens
    with readme_file.open(encoding="utf-8") as infile:
        tokens = MarkdownIt("gfm-like").parse(infile.read())

    # Break the monolithic page into multiple pages on H2s. Name the pages by
    # the content of their headings.
    pages = [Page("Home", filename="index.md"), ]

    # Simultaneously build up a corrected set of anchor links. As we're
    # splitting one file into multiple, an anchor link like `#authors` will
    # need to include the filename, like `repo-details.md#authors`.
    anchor_pages = {}

    # MarkdownIt treats headers as three sequential tokens: heading_open,
    # inline (which will have child tokens), and heading_close. Iterate through
    # with itertools.pairwise so that we'll have access to heading_open and
    # inline simultaneously.
    for token, next_token in itertools.pairwise(
            itertools.chain(tokens, [None, ])):
        if token.type == "heading_open":
            heading_content = next_token.content

            if token.tag == "h2":
                # We've moved on to a new page
                pages.append(Page(heading_content))

            # Associate this anchor with the current page
            anchor = get_heading_anchor(heading_content)

            # Okay, repeated anchors get numbers appended
            anchor_index = 0
            constructed_anchor = anchor
            while constructed_anchor in anchor_pages:
                anchor_index += 1
                constructed_anchor = f"{anchor}-{anchor_index}"

            anchor_pages[constructed_anchor] = pages[-1].get_filename()

        pages[-1].tokens.append(token)

    def sanitize_link(url):
        """
        Sanitize a link within the README.

        There are five cases to be handled:
        1. Links outside of the repository - leave unchanged
        2. File links within the docs/ folder - rewrite
        3. Images - copy into docs/ and rewrite
        4. File links within the repository - redirect to the code browser
        5. Anchor links - rewrite to reference the newly split pages
        """
        link = urlparse(url)

        if link.scheme or link.netloc:
            # This is a "real" link (https://, ftp://, etc.) - don't touch it
            pass

        elif link.path:
            # This is a link to a file on disk
            resolved_path = Path(readme_file.parent, link.path).resolve()

            # Only mess with paths within the repository
            if resolved_path.is_relative_to(readme_file.parent):
                # If the path is already under the docs/ directory, correct it
                # (the linking document will now be under docs/ as well)
                if resolved_path.is_relative_to(docs_dir):
                    link = link._replace(
                        path=str(resolved_path.relative_to(docs_dir))
                    )

                # If the link is to an image, copy that image to the docs
                elif resolved_path.is_file() and \
                        magic.from_file(resolved_path, mime=True) \
                        in VALID_IMAGE_MIME_TYPES:
                    output_path = Path(img_dir, resolved_path.name)
                    shutil.copy2(resolved_path, output_path)

                    link = link._replace(
                        path=str(output_path.relative_to(docs_dir))
                    )

                else:
                    # For everything else, link to the file on GitHub
                    link = link._replace(
                        scheme="https",
                        netloc="github.com",
                        path=str(Path(
                            pipeline_repo,
                            "blob",
                            os.environ.get("GITHUB_SHA", "main"),
                            resolved_path.relative_to(readme_file.parent)
                        ))
                    )

        elif link.fragment:
            # This is an anchor link. As we've split the monolithic README into
            # multiple files, we need to prepend those filepaths.
            try:
                link = link._replace(
                    path=anchor_pages[get_heading_anchor(link.fragment)],
                )
            except KeyError:
                # Well, the link seems broken, so just leave it broken, but add
                # a GitHub warning annotation
                print("::warning title=Broken Link::", end="")
                print(f"Broken anchor link {link.fragment}")

        return urlunparse(link)

    # Iterate through all tokens (including child tokens) to rewrite links and
    # copy images into the docs/ folder
    tokens_to_examine = collections.deque(tokens)
    while tokens_to_examine:
        token = tokens_to_examine.popleft()
        if token.children:
            tokens_to_examine.extend(token.children)

        if token.type == "link_open" and "href" in token.attrs:
            token.attrs["href"] = sanitize_link(token.attrs["href"])

        elif token.type == "image" and "src" in token.attrs:
            token.attrs["src"] = sanitize_link(token.attrs["src"])

    # Write out each page to a separate file
    renderer = mdformat.renderer.MDRenderer()
    options = {
        'parser_extension': [
            mdformat.plugins.PARSER_EXTENSIONS['gfm'],
            mdformat.plugins.PARSER_EXTENSIONS['tables'],
        ]
    }

    table_of_contents = []

    for page in pages:
        Path(docs_dir, page.get_filename()).write_text(
            renderer.render(page.tokens, options, {}),
            encoding="utf-8"
        )

        table_of_contents.append(
            {strip_markdown(page.title): page.get_filename()}
        )

    return table_of_contents


def get_mkdocs_config_data(path: Path, repo: str):
    """ Read the given MKDocs config file or create it from default.

    Args:
        - `path`: Path to the MKDocs config file. When set to None, default
          config is used.
        - `repo`: The github repo name.
    """
    pipeline_name = repo.rsplit("/", maxsplit=1)[-1]

    # Make pages link back to the commit that generated these docs (falling
    # back to `main` if that can't be found)
    commit_id = os.environ.get("GITHUB_SHA", "main")

    config = {
        'site_name': pipeline_name,
        'docs_dir': 'docs/',
        'repo_url': 'https://github.com/' + repo,
        'theme': 'readthedocs',
        'edit_uri_template': f'blob/{commit_id}/README.md',
        'nav': [],
    }

    if path:
        # Update the defaults with the local configuration
        config.update(yaml.safe_load(path.read_bytes()))

    # Add in the plugins and extensions that we require
    required_plugins = {"mike"}
    plugins = config.setdefault("plugins", [])
    plugins.extend(sorted(required_plugins - set(plugins)))

    required_extensions = {"tables", "admonition"}
    extensions = config.setdefault("markdown_extensions", [])
    extensions.extend(sorted(required_extensions - set(extensions)))

    return config


def build_mkdocs_config(pipeline_dir: Path,
                        pipeline_repo: str,
                        readme: Path,
                        mkdocs_config: Optional[Path]) -> Path:
    """ Build the mkdocs config file. """
    # Validate the arguments
    # Handle GitHub Actions passing the literal default value "None"
    if mkdocs_config is not None and mkdocs_config.name == 'None':
        mkdocs_config = None

    # Make sure the referenced files exist and are within this repository
    if mkdocs_config is not None:
        mkdocs_config = (pipeline_dir / mkdocs_config).resolve()

        if not mkdocs_config.is_relative_to(pipeline_dir):
            raise ValueError(
                f"Config file {mkdocs_config} outside of repository!"
            )

        if not mkdocs_config.exists():
            raise ValueError(f"Config file {mkdocs_config} not found!")

    readme = (pipeline_dir / readme).resolve()
    if not readme.exists():
        raise ValueError(f"README {readme} not found!")

    if not readme.is_relative_to(pipeline_dir):
        raise ValueError(f"README {readme} outside of repository!")

    config_data = get_mkdocs_config_data(mkdocs_config, pipeline_repo)

    # Sanity-check that we're not trying to reach outside the repository
    if Path(config_data["docs_dir"]).is_absolute():
        raise ValueError(
            f"MkDocs docs_dir={config_data['docs_dir']} cannot be absolute!"
        )

    readme_nav = split_readme(
        readme_file=readme,
        docs_dir=pipeline_dir/config_data['docs_dir'],
        pipeline_repo=pipeline_repo
    )

    config_data['nav'] = readme_nav + config_data['nav']

    output_config = Path(pipeline_dir, "mkdocs.yml")

    with output_config.open("w", encoding="utf-8") as outfile:
        yaml.safe_dump(
            config_data,
            outfile,
            explicit_start=True,
            sort_keys=False)

    return output_config


@contextlib.contextmanager
def inherited_config(config_file: Path, overrides: dict) -> Path:
    """
    Create an inherited MkDocs configuration file to override specific values.
    """
    with tempfile.NamedTemporaryFile(mode="w",
                                     dir=config_file.parent) as temp_config:
        yaml.safe_dump(
            {"INHERIT": config_file.name, **overrides},
            temp_config,
            explicit_start=True,
            sort_keys=False)

        yield Path(temp_config.name)


if __name__ == '__main__':
    ARGS = parse_args()
    build_mkdocs_config(
        pipeline_dir=ARGS.pipeline_dir,
        pipeline_repo=ARGS.pipeline_repo,
        readme=ARGS.readme,
        mkdocs_config=ARGS.mkdocs_config
    )
