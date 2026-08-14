"""Git facts collected by running the ``git`` CLI on each host.

Every fact runs a direct ``git`` command through the host's pyinfra connector
— ``@local``, SSH, or any other — and parses what it prints. Targets need
only the ``git`` binary; no Python and no package install.

The two facts here demonstrate the two canonical shapes a pyinfra fact takes:
:class:`GitVersion` is argument-less with a trivial ``process()``, while
:class:`GitConfig` is parameterized and must carry its arguments from
``command()`` to ``process()``. Replace both when molding this template into
a real package; keep the structure.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from pyinfra.api import FactBase, StringCommand
from pyinfra.api.exceptions import FactProcessError

from pyinfra_vyos._cli import git_command
from pyinfra_vyos._gitconfig import parse_config_list

__all__ = ["GitConfig", "GitVersion"]

_P = ParamSpec("_P")
_T = TypeVar("_T")

DEFAULT_PATH = "."
"""Repository directory used when a fact or operation is not given one."""


def _fact_process(process: Callable[_P, _T]) -> Callable[_P, _T]:
    """Surface processing failures through pyinfra's per-host fact-failure path.

    pyinfra contains only :class:`FactProcessError` around ``fact.process()``;
    any other exception escaping ``process()`` aborts the entire multi-host
    run. Malformed CLI output (an unrecognised version banner, a valueless
    config entry) must instead fail only the affected host — logged as a fact
    failure, honoring ``_ignore_errors`` and ``default()``.
    """

    @wraps(process)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return process(*args, **kwargs)
        except FactProcessError:
            raise
        except Exception as error:
            raise FactProcessError(f"invalid git fact output: {error}") from error

    return wrapper


class GitVersion(FactBase[str]):
    """Return the target's ``git`` version as a bare version string.

    Runs ``git --version`` and strips the ``git version`` banner, so
    ``git version 2.39.5 (Apple Git-154)`` yields ``2.39.5``. Hosts without
    ``git`` yield :meth:`default` instead of failing, because
    :meth:`requires_command` gates the fact on the binary's presence.
    """

    @staticmethod
    def default() -> str:
        return ""

    def requires_command(self, *args: object, **kwargs: object) -> str:
        return "git"

    def command(self) -> StringCommand:
        return git_command("--version")

    @_fact_process
    def process(self, output: list[str]) -> str:
        banner = "\n".join(output).strip()
        prefix = "git version "
        if not banner.startswith(prefix):
            raise ValueError(f"unexpected `git --version` output: {banner!r}")
        return banner[len(prefix) :].split()[0]


class GitConfig(FactBase[dict]):
    """Return one repository's local git configuration keyed by config key.

    Runs ``git -C <path> config --local --list --null``, which reports only
    the repository's own ``.git/config`` — never the user's global or the
    machine's system configuration.

    ``--null`` is required for correctness: configuration values may contain
    newlines, so NUL is the only reliable record separator. pyinfra hands
    ``process()`` the output split on newlines, so it is rejoined before
    parsing. ``path`` is stored on the instance because pyinfra calls
    ``command()`` and ``process()`` on the same object and the repository is
    needed for the parse-failure message.
    """

    _path: str = DEFAULT_PATH

    @staticmethod
    def default() -> dict:
        return {}

    def requires_command(self, *args: object, **kwargs: object) -> str:
        return "git"

    def command(self, path: str = DEFAULT_PATH) -> StringCommand:
        self._path = path
        return git_command("config", "--local", "--list", "--null", path=path)

    @_fact_process
    def process(self, output: list[str]) -> dict:
        return parse_config_list("\n".join(output), source=self._path)
