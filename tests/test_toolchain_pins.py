"""The Node version that builds the console is one exact literal, in two files.

The image (``ARG NODE_VERSION`` in the Dockerfile) and CI (``setup-node`` in the
``console`` job) cannot share a file, so this keeps them equal and exact: a floating
``22`` would let the image and the job drift apart, and a version below the floor
cannot run the console's tests.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# jsdom 30 (the console's component tests) requires Node ^22.22.2.
NODE_FLOOR = (22, 22, 2)
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text()


def _dockerfile_node_version() -> str:
    found = re.findall(r"^ARG NODE_VERSION=(\S+)$", _dockerfile(), re.MULTILINE)
    assert len(found) == 1, "expected one ARG NODE_VERSION=<exact version>"
    return found[0]


def _ci_node_versions() -> list[str]:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    return re.findall(
        r"^\s*node-version:\s*['\"]?([^\s'\"]+)['\"]?\s*$", ci, re.MULTILINE
    )


def test_the_image_builds_the_console_with_the_pinned_node():
    assert "FROM node:${NODE_VERSION}-alpine AS console" in _dockerfile()


def test_the_node_version_is_exact_and_not_below_the_floor():
    version = _dockerfile_node_version()
    assert EXACT_VERSION.fullmatch(version), f"{version!r} is not major.minor.patch"
    assert tuple(int(part) for part in version.split(".")) >= NODE_FLOOR


def test_ci_uses_the_same_node_version_as_the_image():
    assert _ci_node_versions() == [_dockerfile_node_version()]
