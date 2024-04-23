#!/usr/bin/env python3
"Backfill documentation for existing tags."
import argparse
import contextlib
import getpass
import re
import subprocess
import tempfile
import time
from pathlib import Path

from action import TAG_REGEX, strings_low_key


def checkrun(*args, context, **kwargs):
    "A wrapper around subprocess.run to handle common errors."
    kwargs.pop("check", None)
    kwargs.pop("capture_output", None)

    try:
        return subprocess.run(*args, **kwargs, check=True, capture_output=True)
    except subprocess.CalledProcessError as err:
        print(f"Error while {context}")
        print("cmd:", err.cmd)
        print("stdout:", err.stdout.decode("utf-8"))
        print("stderr:", err.stderr.decode("utf-8"))
        raise


class Repository:
    "A GitHub repository."
    def __init__(self, url: str):
        self.url = url
        self.path = None
        self._tempdir = None

        self.org_repo = re.match(
            r"^git@github\.com:(.*)\.git$",
            self.url
        ).group(1)

    def __enter__(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name)
        print(f"Cloning repository into {self.path} ...")
        checkrun(
            ["git", "clone", "--recurse-submodules", self.url, self.path],
            context=f"cloning repository {self.url}"
        )

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._tempdir.cleanup()
        self._tempdir = None

    def get_ordered_tags(self):
        "Get a list of the ordered tags in this repository."
        all_tags = subprocess.check_output(
            ["git", "tag"],
            cwd=self.path
        ).strip().decode("utf-8").splitlines()

        valid_tags = list(tag for tag in all_tags if TAG_REGEX.match(tag))
        valid_tags.sort(key=strings_low_key)
        return valid_tags

    def generate_docs(self, commit: str, image: str):
        "Generate the documentation for this commit."
        with contextlib.chdir(self.path):
            print(f"Generating docs for tag `{commit}`")
            checkrun(
                ["git", "checkout", commit],
                context=f"checking out {commit}"
            )
            checkrun(
                ["git", "clean", "-d", "-x", "--force"],
                context="cleaning repository"
            )
            checkrun(
                ["git", "submodule", "update", "--init", "--recursive"],
                context="updating submodules"
            )

        # Let the filesystem settle... I was running into strange
        # FileNotFoundErrors, and adding this seems to prevent them.
        time.sleep(1)

        checkrun(
            [
                "docker",
                "run",
                "-v", f"{self.path}:{self.path}",
                "-e", f"GITHUB_REPOSITORY={self.org_repo}",
                "-e", f"GITHUB_WORKSPACE={self.path}",
                "-e", f"GITHUB_SHA={commit}",
                "-e", "BACKFILL_TAGS=1",
                "-e", "CI=1",
                "-e", "GITHUB_ACTIONS=1",
                "-e", f"GITHUB_ACTOR={getpass.getuser()}",
                "-w", self.path,
                "--rm",
                image,
                "None",
                "README.md"
            ],
            context="generating documentation commit"
        )


# https://gist.github.com/gurunars/4470c97c916e7b3c4731469c69671d06
def confirm(message):
    """
    Ask user to enter Y or N (case-insensitive).
    :return: True if the answer is Y.
    :rtype: bool
    """
    answer = ""
    # Requiring a full "yes" or "no" is heavy-handed, but as it's the only
    # layer of confirmation I'm okay with that
    while answer not in ["yes", "no"]:
        try:
            answer = input(f"{message} [yes/no]? ").lower()
        except KeyboardInterrupt:
            print("Treating Ctrl-C as a no...")
            answer = "no"

    return answer == "yes"


def backfill_tag_docs(pipeline_url: str):
    "Backfill all of the tag documentation for a repository."
    image = "ghaction"

    checkrun(
        ["docker", "build", ".", "-t", image],
        context="building Docker image"
    )

    with Repository(pipeline_url) as repo:
        for tag in repo.get_ordered_tags():
            repo.generate_docs(tag, image)

        with subprocess.Popen(
                    ["mike", "serve"],
                    cwd=repo.path,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL) as server:
            try:
                print("Updated documentation at http://localhost:8000/")
                if confirm("Push these docs live"):
                    subprocess.check_call(
                        ["git", "push", "origin", "gh-pages"],
                        cwd=repo.path
                    )
                else:
                    print("Not pushing docs")
            finally:
                print("Stopping server...")
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    print("Timeout expired, killing server...")
                    server.kill()


def main():
    "The main entrypoint."
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_url")
    args = parser.parse_args()

    backfill_tag_docs(args.repo_url)


if __name__ == "__main__":
    main()
