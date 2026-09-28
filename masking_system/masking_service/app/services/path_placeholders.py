"""Reverse path tokens, which can be embedded in compound file names.

Content keeps its stricter token boundaries. Path masking can produce e.g.
mask_deneme_316Service.java, so paths need exact mapping matches without
word boundaries. Compile once per run and never rescan restored values.
"""

from pathlib import Path
import re

from app.services.rule_engine import JSON_NUMERIC_PLACEHOLDER_RE


class UnsafeUnmaskPathError(ValueError):
    """A restored component cannot safely be used as a file name."""


# _TEXT_TOKEN: broad candidate grammar used to look tokens up directly in
# the mapping - matches both the current mask_<prefix>_<N> scheme and the
# legacy <PREFIX>_<N> scheme (so paths masked before the mask_ switch stay
# reversible).
# _UNKNOWN_TOKEN: stricter grammar - only shapes that unambiguously carry
# one of our own markers (mask_ or the legacy _TEST_) are flagged as
# "recognizably ours but missing from the mapping"; an arbitrary uppercase
# identifier that merely looks like PREFIX_N is not (see rule_engine.
# PLACEHOLDER_RE for the same split).
_TEXT_TOKEN = re.compile(r"mask_[a-z][a-z0-9_]*_\d+|[A-Z][A-Z0-9_]*_\d+")
_UNKNOWN_TOKEN = re.compile(r"mask_[a-z][a-z0-9_]*_\d+|[a-z][a-z0-9_]*_test_\d+", re.IGNORECASE)


class PathPlaceholderResolver:
    def __init__(self, placeholder_map: dict[str, str]):
        self.mapping = placeholder_map
        tokens = sorted(
            (token for token in placeholder_map if _TEXT_TOKEN.fullmatch(token)),
            key=lambda token: (-len(token), token),
        )
        # Do not mistake token 31 for token 316 (including an unknown 316).
        textual = "(?:" + "|".join(map(re.escape, tokens)) + r")(?!\d)" if tokens else r"(?!)"
        self.pattern = re.compile(textual + "|" + JSON_NUMERIC_PLACEHOLDER_RE.pattern)
        self.cache: dict[str, tuple[str, int, tuple[str, ...]]] = {}

    def known_placeholder_spans(self, text: str) -> list[tuple[int, int]]:
        """Locate exact known tokens, including those inside compound names."""
        return [
            match.span() for match in self.pattern.finditer(text)
            if match.group() in self.mapping
        ]

    def reverse_component(self, component: str) -> tuple[str, int, tuple[str, ...]]:
        if component in self.cache:
            return self.cache[component]
        parts: list[str] = []
        unresolved: list[str] = []
        cursor = resolved = 0
        for match in self.pattern.finditer(component):
            gap = component[cursor:match.start()]
            parts.append(gap)
            unresolved.extend(_UNKNOWN_TOKEN.findall(gap))
            token = match.group()
            if token in self.mapping:
                parts.append(self.mapping[token])
                resolved += 1
            else:
                parts.append(token)
                unresolved.append(token)
            cursor = match.end()
        tail = component[cursor:]
        parts.append(tail)
        unresolved.extend(_UNKNOWN_TOKEN.findall(tail))
        result = "".join(parts)
        if any(char in result for char in ("/", "\\", "\0", ":")) or result in ("", ".", ".."):
            # Do not expose the decrypted original in an error or audit log.
            raise UnsafeUnmaskPathError("geri cozulmus yol bileseni guvensiz")
        outcome = (result, resolved, tuple(unresolved))
        self.cache[component] = outcome
        return outcome

    def reverse(self, path: Path) -> tuple[Path, int, list[str]]:
        parts: list[str] = []
        resolved = 0
        unresolved: list[str] = []
        for component in path.parts:
            value, count, missing = self.reverse_component(component)
            parts.append(value)
            resolved += count
            unresolved.extend(missing)
        return Path(*parts), resolved, unresolved
