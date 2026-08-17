"""Text normalization, in one place because normalization only works if it
happens at exactly one place.

Three primitives, each protecting a different invariant:

- :func:`slugify` — the human-readable half of an on-disk name. Never parsed;
  the id in front of it is what carries meaning.
- :func:`normalize_tag` — tags are free-form and need no declaration, which
  works only because ``dns``, ``DNS`` and ``  dns  `` are folded to one value
  on the single write path. A tag normalized on read instead would still split
  on disk.
- :func:`edit_distance` / :func:`nearest` — "did you mean WI-0042?" for
  mistyped references, and the near-duplicate tag warning. Transposition
  counts as one edit, because ``dns`` versus ``dsn`` is *the* typo this is
  meant to catch and plain Levenshtein scores it as two.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_REPEATED_HYPHEN = re.compile(r"-{2,}")

#: A slug is cosmetic, but an unbounded one produces paths the OS refuses.
SLUG_MAX_LENGTH = 60


def slugify(text: str, *, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Fold arbitrary text into the trailing half of an entity's filename."""
    folded = unicodedata.normalize("NFKD", text)
    folded = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", folded).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0] or slug[:max_length]
    return slug.strip("-")


def normalize_tag(tag: str) -> str:
    """Trim, lowercase, collapse internal whitespace to hyphens.

    Returns "" for anything that normalizes away entirely; the caller decides
    whether that is a refusal or something to skip.
    """
    folded = unicodedata.normalize("NFKC", tag).strip().lower()
    folded = _WHITESPACE.sub("-", folded)
    folded = folded.replace("_", "-")
    folded = _REPEATED_HYPHEN.sub("-", folded)
    return folded.strip("-")


def normalize_tags(tags: Iterable[str], *, normalize: bool = True) -> list[str]:
    """Normalize and de-duplicate, preserving first-appearance order.

    Order is preserved rather than sorted so a hand-authored frontmatter list
    round-trips unchanged when nothing about it actually changed.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = normalize_tag(tag) if normalize else str(tag).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def add_tags(existing: Iterable[str], additions: Iterable[str],
             *, normalize: bool = True) -> list[str]:
    """Existing tags plus new ones, normalized once over the whole result.

    Normalizing the existing list too is deliberate: a hand-edited ``DNS``
    already in the file gets folded the next time the entity is tagged, rather
    than surviving as a second spelling forever.
    """
    return normalize_tags([*existing, *additions], normalize=normalize)


def remove_tags(existing: Iterable[str], removals: Iterable[str],
                *, normalize: bool = True) -> list[str]:
    """Untagging matches on the normalized form, so ``untag WI-1 DNS`` removes
    ``dns``. Requiring the exact stored spelling would make the normalization
    that fixed the tag on write into the thing that hides it on removal."""
    drop = set(normalize_tags(removals, normalize=normalize))
    return [t for t in normalize_tags(existing, normalize=normalize) if t not in drop]


def edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insert, delete, substitute, or swap
    two adjacent characters, each costing one."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous_previous: list[int] = []
    previous = list(range(len(b) + 1))

    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                current[j - 1] + 1,       # insertion
                previous[j] + 1,          # deletion
                previous[j - 1] + cost,   # substitution
            )
            if (
                i > 1
                and j > 1
                and ca == b[j - 2]
                and a[i - 2] == cb
            ):
                current[j] = min(current[j], previous_previous[j - 2] + cost)
        previous_previous, previous = previous, current

    return previous[len(b)]


def nearest(word: str, candidates: Iterable[str], *, max_distance: int = 2) -> str | None:
    """The single best suggestion, or None when nothing is close enough.

    One suggestion only: a list of maybes costs an agent a decision, where a
    single name costs it one corrected call.
    """
    best: str | None = None
    best_distance = max_distance + 1
    for candidate in candidates:
        distance = edit_distance(word.lower(), candidate.lower())
        if distance < best_distance:
            best, best_distance = candidate, distance
    return best


def near_duplicates(values: Sequence[str], *, max_distance: int = 1) -> list[tuple[str, str]]:
    """Every unordered pair within `max_distance` — the tag-typo warning."""
    if max_distance <= 0:
        return []
    pairs: list[tuple[str, str]] = []
    ordered = sorted(set(values))
    for i, left in enumerate(ordered):
        for right in ordered[i + 1:]:
            if abs(len(left) - len(right)) > max_distance:
                continue
            if edit_distance(left, right) <= max_distance:
                pairs.append((left, right))
    return pairs
