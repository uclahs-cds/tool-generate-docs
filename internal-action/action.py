#!/usr/bin/env python3
"""
GitHub Action to build and deploy docs from the README.
"""
import argparse
import datetime
import functools
import json
import os
import re
import subprocess
import sys

from pathlib import Path

import create_mkdocs_config


TAG_REGEX = re.compile(r"""
    ^v                      # Leading `v` character
    (?P<major>\d+)          # Major version
    \.                      # Dot
    (?P<minor>\d+)          # Minor version
    \.                      # Dot
    (?P<patch>\d+)          # Patch version
    (?:-rc\.(?P<rc>\d+))?   # Optional release candidate version
    (?:                     # Optional `git describe` addition
        -(?P<depth>\d+)     #   Commits since last tag
        -g(?P<hash>\w+)     #   Commit hash
    )?$
    """, re.VERBOSE)


def sort_key(version_str: str, strings_high: bool):
    """
    Return a key suitable for sorting version strings.

    Release candidates and `git describe` tags are weird. Here is a correctly
    ordered list (highest to lowest):

    v1.2.4
    v1.2.4-rc.2-1-gXXXXX
    v1.2.4-rc.2
    v1.2.4-rc.1
    v1.2.3

    In order to handle the rule that an absent RC outranks all RCs, an
    absent RC is treated as sys.maxsize.

    In order to sort post-tag commits above the tags, an absent commits number
    is treated as 0.

    If `strings_high` is True, non-version strings (like "development") are
    ranked higher than all version strings.
    """
    match = TAG_REGEX.match(version_str)
    if match:
        numbers = match.groupdict()

        return (
            int(numbers['major']),
            int(numbers['minor']),
            int(numbers['patch']),
            int(numbers['rc']) if numbers['rc'] else sys.maxsize,
            int(numbers['depth']) if numbers['depth'] else 0
        )

    return (
        sys.maxsize if strings_high else -1,
        version_str
    )


strings_low_key = functools.partial(sort_key, strings_high=False)
strings_high_key = functools.partial(sort_key, strings_high=True)


def is_release_candidate(version_str: str):
    "Return True if the version string corresponds to a release candidate."
    match = TAG_REGEX.match(version_str)
    if match:
        return match.groupdict()['rc'] is not None

    return False


def setup_git(do_remote_actions: bool):
    """
    Do various required git actions to prepare for generating documentation.
    """
    # Only do these things if we're running in GitHub actions
    if os.environ.get("CI", None) and os.environ.get("GITHUB_ACTIONS", None):
        # see https://github.com/actions/checkout/issues/766
        subprocess.check_call([
            "git",
            "config",
            "--global",
            "--add", "safe.directory", os.environ["GITHUB_WORKSPACE"]
        ])

        subprocess.check_call([
            "git",
            "config",
            "--global",
            "user.name",
            os.environ["GITHUB_ACTOR"],
        ])

        subprocess.check_call([
            "git",
            "config",
            "--global",
            "user.email",
            f"{os.environ['GITHUB_ACTOR']}@users.noreply.github.com"
        ])

    if do_remote_actions:
        # https://github.com/jimporter/mike/tree/af47b9699aeeeea7f9ecea2631e1c9cfd92e06af#deploying-via-ci
        # This can fail if the branch doesn't exist yet, so tolerate problems
        subprocess.run(
            ["git", "fetch", "origin", "gh-pages", "--depth=1"],
            check=False
        )

        # Fetch all of the tags as well
        subprocess.check_call(["git", "fetch", "--tags"])


def current_is_development(mike_versions: dict, head_props: dict) -> bool:
    """
    Return True if the current commit should be versioned as "development".

    This commit will be marked "development" if either:
    * It includes the current development version as an ancestor
    * It is not an ancestor of the current development version _and_ it has a
    more recent commit date (this protects against weird branches cases)
    """
    dev_version = mike_versions.get("development", None)

    if not dev_version:
        # There is no development version, so this commit might as well be it!
        return True

    if "properties" not in dev_version:
        # There are no properties established, so this one is probably newer
        return True

    dev_hash = dev_version["properties"].get("commit", None)
    dev_date = dev_version["properties"].get("date", None)

    if not dev_hash or not dev_date:
        # There are no properties established, so this one is probably newer
        return True

    if subprocess.call(
            ["git", "merge-base", "--is-ancestor", dev_hash, "HEAD"]) == 0:
        # The current development commit is an ancestor
        return True

    if subprocess.call(
            ["git", "merge-base", "--is-ancestor", "HEAD", dev_hash]) == 0:
        # The current development commit is a descendant
        return False

    # Okay, the commits are unrelated. This generally shouldn't happen, but
    # just in case it does...
    return datetime.datetime.fromisoformat(head_props["date"]) > \
        datetime.datetime.fromisoformat(dev_date)


def get_mike_versions():
    "Return a dictionary of current documented versions."
    # Get all doc versions
    doc_versions = json.loads(
        subprocess.check_output(["mike", "list", "--json"])
    )

    # Reformat the result into a dictionary mapped by versions
    return {
        item["version"]: item for item in doc_versions
    }


def get_versions_and_aliases():
    """
    Return multiple tuples of (version, aliases, props) for the current commit.

    Versions:
    A commit gets a stable version for each tag referencing it.

    This commit will also get the version "development" if either:
    * It includes the current development version as an ancestor
    * It is not an ancestor of the current development version _and_ it has a
    more recent commit date (this protects against weird branches cases)

    Aliases:
    This commit will get the alias "latest" if it has the highest-ordered
    non-release-candidate tag.

    This commit will get the alias "release-candidate" if it has the
    highest-ordered tag of the documented versions (regardless of whether or
    not that tag is actually a release candidate). This is to ensure that
    "release-candidate" doesn't lag behind "latest".
    """
    # Get all tags pointing to the current commit
    head_tags = [
        tag.strip() for tag in
        subprocess.check_output(
            ["git", "tag", "--points-at", "HEAD"]
        ).decode("utf-8").strip().splitlines()
        if TAG_REGEX.match(tag.strip())
    ]

    aliases = set()
    props = {}

    # All of the versions should share these properties
    props["commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"]
    ).decode("utf-8").strip()
    props["date"] = subprocess.check_output(
        ["git", "show", "HEAD", "--format=%cI", "--no-patch"]
    ).decode("utf-8").strip()

    mike_versions = get_mike_versions()

    # This should only happen on the very first documentation build
    if not mike_versions:
        aliases.add("latest")

    result = []

    if current_is_development(mike_versions, props):
        result.append(("development", aliases, props))

    # Return a version for each tag
    head_tags.sort(key=strings_low_key)

    highest_mike_version = max(
        mike_versions.keys(),
        key=strings_low_key,
        default="v0.0.0"
    )

    highest_nonrc_mike_version = max(
        (key for key in mike_versions.keys() if not is_release_candidate(key)),
        key=strings_low_key,
        default="v0.0.0"
    )

    for tag in head_tags:
        mike_commit = mike_versions.get(tag, {})\
            .get("properties", {})\
            .get("commit", None)

        if mike_commit == props["commit"]:
            # We've already documented this tag
            continue

        # This is a new tag. Figure out what aliases it needs.
        tag_aliases = set()

        if strings_low_key(tag) > strings_low_key(highest_mike_version):
            # This tag ranks higher than any current tag, so mark it as the
            # release candidate.
            tag_aliases.add("release-candidate")

        if not is_release_candidate(tag) and \
                strings_low_key(tag) > \
                strings_low_key(highest_nonrc_mike_version):
            # This tag ranks higher than any current non-RC tag, so mark it as
            # the release.
            tag_aliases.add("latest")

        result.append((
            tag,
            aliases | tag_aliases,
            props
        ))

    return result


def run_action(mkdocs_config, readme):
    "Build and deploy the documentation."
    # When backfilling tags, we want to avoid doing any explicit git
    # operations. Use this environment variable to disable those.
    do_remote_actions = not os.environ.get("BACKFILL_TAGS", False)

    setup_git(do_remote_actions)

    # Build the mkdocs configuration
    config_file = create_mkdocs_config.build_mkdocs_config(
        pipeline_dir=Path(os.environ["GITHUB_WORKSPACE"]),
        pipeline_repo=os.environ["GITHUB_REPOSITORY"],
        readme=Path(readme),
        mkdocs_config=Path(mkdocs_config)
    )

    for (version, aliases, props) in get_versions_and_aliases():
        # For any tag-like version, we want the edit_uri template of the
        # rendered site to go to that tag, not just the commit. Use a
        # hierarchical config file to make that happen.
        overrides = {}
        if TAG_REGEX.match(version):
            base_url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}"
            overrides["repo_url"] = f'{base_url}/tree/{version}'
            overrides["edit_uri_template"] = \
                f'{base_url}/blob/{version}/README.md'

        with create_mkdocs_config.inherited_config(
                config_file, overrides) as version_config:
            mike_args = [
                "mike",
                "deploy",
                "--config-file",
                version_config,
                "--prop-set-all",
                json.dumps(props)
            ]

            if aliases:
                mike_args.extend(["--update-aliases", version])
                mike_args.extend(list(aliases))
            else:
                mike_args.append(version)

            # Build the docs as a commit on the gh-pages branch
            subprocess.check_call(mike_args)

    # Redirect from the base site to the latest version. This will be a no-op
    # after the very first deployment, but it will not cause problems
    subprocess.check_call(
        ["mike", "set-default", "--config-file", config_file, "latest"]
    )

    # Push up the changes to the docs
    if do_remote_actions:
        subprocess.check_call(["git", "push", "origin", "gh-pages"])


if __name__ == "__main__":
    PARSER = argparse.ArgumentParser()
    PARSER.add_argument("mkdocs_config")
    PARSER.add_argument("readme")

    ARGS = PARSER.parse_args()

    run_action(ARGS.mkdocs_config, ARGS.readme)
