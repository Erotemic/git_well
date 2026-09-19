"""Depth and submodule selection policy for source archives."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, cast

from ._common import (
    DepthArg,
    SubmoduleArchiveDecision,
    SubmoduleDepthSpecArg,
    SubmoduleStatus,
    _UNINITIALIZED_SUBMODULE_REASON,
)

_UNSET = object()

@dataclass(frozen=True)
class SubmoduleDepthPolicy:
    """
    Parsed ``--submodule-depth`` policy.

    The user-facing spec is intentionally small: a scalar depth applies to all
    submodules; a YAML mapping may contain exact submodule paths, fnmatch-style
    glob patterns, ``"*"`` as a catch-all glob, and ``__default__`` as a
    non-glob fallback.

    Example:
        >>> policy = _parse_submodule_depth_spec('0')
        >>> _depth_label(policy.resolve('any/submodule', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{"*": 0, special/submod: 100}')
        >>> _depth_label(policy.resolve('special/submod', inherited_depth=25))
        '100'
        >>> _depth_label(policy.resolve('other/submod', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{__default__: 0, special/*: full}')
        >>> _depth_label(policy.resolve('special/lib', inherited_depth=25))
        'full'
        >>> _depth_label(policy.resolve('plain/lib', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{special/*: 25, "*/submod": 100}')
        >>> policy.resolve('special/submod', inherited_depth=1)
        Traceback (most recent call last):
        ...
        ValueError: ambiguous submodule depth for special/submod: matched special/* -> 25, */submod -> 100; add an exact path entry to disambiguate
    """

    specified: bool
    raw: Any = None
    scalar_depth: Any = _UNSET
    exact_depths: dict[str, int | None] = field(default_factory=dict)
    glob_depths: dict[str, int | None] = field(default_factory=dict)
    star_depth: Any = _UNSET
    default_depth: Any = _UNSET

    def resolve(self, path: str, inherited_depth: int | None) -> int | None:
        """
        Resolve the depth for one submodule path.
        """
        if not self.specified:
            return inherited_depth
        if self.scalar_depth is not _UNSET:
            return cast(int | None, self.scalar_depth)
        if path in self.exact_depths:
            return self.exact_depths[path]

        matches = [
            (pattern, depth)
            for pattern, depth in self.glob_depths.items()
            if fnmatch.fnmatchcase(path, pattern)
        ]
        if matches:
            depths = {depth for _pattern, depth in matches}
            if len(depths) == 1:
                return matches[0][1]
            rendered = ', '.join(
                f'{pattern} -> {_depth_label(depth)}'
                for pattern, depth in matches
            )
            raise ValueError(
                f'ambiguous submodule depth for {path}: matched {rendered}; '
                'add an exact path entry to disambiguate'
            )

        if self.star_depth is not _UNSET:
            return cast(int | None, self.star_depth)
        if self.default_depth is not _UNSET:
            return cast(int | None, self.default_depth)
        return inherited_depth

    def summary_lines(self) -> list[str]:
        """Return human-readable manifest lines for this policy."""
        if not self.specified:
            return ['Submodule depth spec: (omitted; inherits superproject depth)']
        if self.scalar_depth is not _UNSET:
            return [
                f'Submodule depth spec: scalar {_depth_label(cast(int | None, self.scalar_depth))}'
            ]
        lines = ['Submodule depth spec: YAML mapping']
        if self.default_depth is not _UNSET:
            lines.append(
                f'  __default__: {_depth_label(cast(int | None, self.default_depth))}'
            )
        if self.star_depth is not _UNSET:
            lines.append(
                f'  "*": {_depth_label(cast(int | None, self.star_depth))}'
            )
        if self.exact_depths:
            lines.append('  exact paths:')
            for path, depth in sorted(self.exact_depths.items()):
                lines.append(f'    {path}: {_depth_label(depth)}')
        if self.glob_depths:
            lines.append('  glob patterns:')
            for pattern, depth in sorted(self.glob_depths.items()):
                lines.append(f'    {pattern}: {_depth_label(depth)}')
        return lines

def _normalize_depth(depth: DepthArg) -> int | None:
    import re

    if depth is None:
        return None
    if isinstance(depth, bool):
        raise ValueError(
            "depth must be a non-negative integer, None, or 'full'"
        )
    if isinstance(depth, int):
        value = depth
    else:
        text = str(depth)
        if text in {'', 'full'}:
            return None
        if text == '0':
            return 0
        if not re.match(r'^[1-9][0-9]*$', text):
            raise ValueError(
                "depth must be a non-negative integer, None, or 'full'"
            )
        value = int(text)
    if value < 0:
        raise ValueError(
            "depth must be a non-negative integer, None, or 'full'"
        )
    return value


def _depth_label(depth: int | None) -> str:
    """
    Render an internal normalized depth for humans.

    Example:
        >>> _depth_label(None)
        'full'
        >>> _depth_label(0)
        '0'
        >>> _depth_label(25)
        '25'
    """
    return 'full' if depth is None else str(depth)


def _clone_depth_from_normalized_depth(
    depth: int | None,
) -> int | None:
    """Return the Git clone ``--depth`` value for a normalized depth."""
    return None if depth in {0, None} else depth


def _parse_submodule_depth_spec(
    spec: SubmoduleDepthSpecArg,
) -> SubmoduleDepthPolicy:
    """
    Parse a ``--submodule-depth`` YAML spec.

    Example:
        >>> p = _parse_submodule_depth_spec(None)
        >>> p.resolve('lib', inherited_depth=7)
        7
        >>> p = _parse_submodule_depth_spec('full')
        >>> _depth_label(p.resolve('lib', inherited_depth=7))
        'full'
        >>> p = _parse_submodule_depth_spec('{__default__: 0, lib: 3}')
        >>> p.resolve('lib', inherited_depth=7)
        3
        >>> p.resolve('other', inherited_depth=7)
        0
        >>> p = _parse_submodule_depth_spec('{lib: 3}')
        >>> p.resolve('other', inherited_depth=7)
        7
    """
    if spec is None:
        return SubmoduleDepthPolicy(specified=False)

    parsed: Any
    if isinstance(spec, dict):
        parsed = spec
    elif isinstance(spec, int):
        parsed = spec
    else:
        text = str(spec)
        if text == '':
            return SubmoduleDepthPolicy(specified=False)
        import yaml

        parsed = yaml.safe_load(text)

    if isinstance(parsed, dict):
        exact_depths: dict[str, int | None] = {}
        glob_depths: dict[str, int | None] = {}
        star_depth: Any = _UNSET
        default_depth: Any = _UNSET
        for raw_key, raw_depth in parsed.items():
            if not isinstance(raw_key, str):
                raise ValueError(
                    'submodule depth mapping keys must be strings; got '
                    f'{raw_key!r}'
                )
            key = raw_key.strip()
            if not key:
                raise ValueError('submodule depth mapping keys cannot be empty')
            normalized = _normalize_depth(cast(DepthArg, raw_depth))
            if key == '__default__':
                default_depth = normalized
            elif key == '*':
                star_depth = normalized
            elif _looks_like_fnmatch_pattern(key):
                glob_depths[key] = normalized
            else:
                exact_depths[key] = normalized
        return SubmoduleDepthPolicy(
            specified=True,
            raw=parsed,
            exact_depths=exact_depths,
            glob_depths=glob_depths,
            star_depth=star_depth,
            default_depth=default_depth,
        )
    if isinstance(parsed, (list, tuple)):
        raise ValueError(
            'submodule depth spec must be a scalar depth or a YAML mapping, '
            f'not {type(parsed).__name__}'
        )
    return SubmoduleDepthPolicy(
        specified=True,
        raw=parsed,
        scalar_depth=_normalize_depth(cast(DepthArg, parsed)),
    )


def _looks_like_fnmatch_pattern(text: str) -> bool:
    """
    Return true for keys containing fnmatch glob metacharacters.

    Example:
        >>> _looks_like_fnmatch_pattern('external/lib')
        False
        >>> _looks_like_fnmatch_pattern('external/*')
        True
        >>> _looks_like_fnmatch_pattern('external/lib[12]')
        True
    """
    return any(ch in text for ch in '*?[')


def _normalize_submodule_path_list(
    value: str | list[str] | None
) -> list[str]:
    """
    Normalize CLI/API submodule path lists.

    Example:
        >>> _normalize_submodule_path_list(None)
        []
        >>> _normalize_submodule_path_list('extern/data')
        ['extern/data']
        >>> _normalize_submodule_path_list(['extern/data', ' other '])
        ['extern/data', 'other']
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    paths = []
    for item in items:
        path = str(item).strip()
        if path:
            paths.append(path)
    return paths


def _resolve_exclude_submodule_paths(
    submodule_status: list[SubmoduleStatus],
    exclude_submodule: list[str],
    *,
    no_submodules: bool,
) -> set[str]:
    """
    Resolve ``--exclude-submodule`` selectors to recursive submodule paths.

    Each selector may be either an exact recursive submodule path or an
    fnmatch-style pattern over recursive submodule paths. Shell-expanded globs
    arrive here as ordinary argv entries, so those entries still have to match
    known recursive submodule paths individually.

    Example:
        >>> infos = [
        ...     SubmoduleStatus(' ', 'a' * 40, 'lib/a', ''),
        ...     SubmoduleStatus(' ', 'b' * 40, 'lib/b', ''),
        ...     SubmoduleStatus(' ', 'c' * 40, 'third_party/c', ''),
        ... ]
        >>> sorted(_resolve_exclude_submodule_paths(infos, ['lib/*'], no_submodules=False))
        ['lib/a', 'lib/b']
        >>> sorted(_resolve_exclude_submodule_paths(infos, ['lib/a'], no_submodules=False))
        ['lib/a']
        >>> _resolve_exclude_submodule_paths(infos, ['missing/*'], no_submodules=False)
        Traceback (most recent call last):
        ...
        ValueError: --exclude-submodule selector does not match a recursive submodule: missing/*...
    """
    if not exclude_submodule:
        return set()

    known_paths = sorted({info.path for info in submodule_status})
    exclude_set: set[str] = set()
    unmatched: list[str] = []

    for selector in exclude_submodule:
        if selector in known_paths:
            exclude_set.add(selector)
            continue

        if _looks_like_fnmatch_pattern(selector):
            matches = [
                path
                for path in known_paths
                if fnmatch.fnmatchcase(path, selector)
            ]
            if matches:
                exclude_set.update(matches)
                continue

        unmatched.append(selector)

    if unmatched and not no_submodules:
        rendered = ', '.join(unmatched)
        message = (
            '--exclude-submodule selector does not match a recursive '
            f'submodule: {rendered}'
        )
        if any(_looks_like_fnmatch_pattern(item) for item in unmatched):
            message += (
                "; quote glob patterns such as 'external/*' so your "
                'shell does not expand them before git-well sees them'
            )
        raise ValueError(message)

    inherited_excludes = {
        path
        for path in known_paths
        if any(
            path == excluded or path.startswith(excluded.rstrip('/') + '/')
            for excluded in exclude_set
        )
    }
    exclude_set.update(inherited_excludes)

    return exclude_set


def _resolve_submodule_archive_decisions(
    submodule_status: list[SubmoduleStatus],
    *,
    policy: SubmoduleDepthPolicy,
    inherited_depth: int | None,
    exclude_submodule: list[str],
    no_submodules: bool,
) -> list[SubmoduleArchiveDecision]:
    """
    Resolve submodule archive decisions.

    Example:
        >>> infos = [SubmoduleStatus(' ', 'a' * 40, 'lib/a', ''), SubmoduleStatus(' ', 'b' * 40, 'lib/b', '')]
        >>> policy = _parse_submodule_depth_spec('{"*": 0, lib/a: 5}')
        >>> decisions = _resolve_submodule_archive_decisions(infos, policy=policy, inherited_depth=10, exclude_submodule=[], no_submodules=False)
        >>> [(d.info.path, d.mode, _depth_label(d.depth)) for d in decisions]
        [('lib/a', 'shallow-git-checkout', '5'), ('lib/b', 'source-only-git-archive', '0')]
        >>> decisions = _resolve_submodule_archive_decisions(infos, policy=policy, inherited_depth=10, exclude_submodule=['lib/b'], no_submodules=False)
        >>> [(d.info.path, d.omitted, d.reason) for d in decisions]
        [('lib/a', False, 'included'), ('lib/b', True, 'excluded by --exclude-submodule')]
    """
    exclude_set = _resolve_exclude_submodule_paths(
        submodule_status,
        exclude_submodule,
        no_submodules=no_submodules,
    )
    decisions: list[SubmoduleArchiveDecision] = []
    for info in submodule_status:
        if no_submodules:
            decisions.append(
                SubmoduleArchiveDecision(
                    info=info,
                    omitted=True,
                    depth=inherited_depth,
                    mode='omitted',
                    reason='omitted by --no-submodules',
                )
            )
            continue
        if info.path in exclude_set:
            decisions.append(
                SubmoduleArchiveDecision(
                    info=info,
                    omitted=True,
                    depth=inherited_depth,
                    mode='omitted',
                    reason='excluded by --exclude-submodule',
                )
            )
            continue
        if info.status == '-':
            decisions.append(
                SubmoduleArchiveDecision(
                    info=info,
                    omitted=True,
                    depth=inherited_depth,
                    mode='omitted',
                    reason=_UNINITIALIZED_SUBMODULE_REASON,
                )
            )
            continue

        depth = policy.resolve(info.path, inherited_depth)
        if depth == 0:
            mode = 'source-only-git-archive'
        elif depth is None:
            mode = 'full-git-checkout'
        else:
            mode = 'shallow-git-checkout'
        decisions.append(
            SubmoduleArchiveDecision(
                info=info,
                omitted=False,
                depth=depth,
                mode=mode,
                reason='included',
            )
        )
    return decisions
