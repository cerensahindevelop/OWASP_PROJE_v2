"""Pure matching logic for the exclude-pattern engine.

Second line of defense alongside rule_engine: filter_rules matches
sensitive *content* wherever it appears, but a credential file (.env,
*.pem, id_rsa, .git/) can easily contain secrets in a shape no regex
happens to cover. Rather than trying to enumerate every possible secret
format, known-sensitive file/directory patterns are excluded outright -
never scanned, never copied to the export target at all.

No DB access here on purpose (same rationale as rule_engine.py): this
module only knows how to check a name against a list of glob patterns,
which keeps it trivially unit testable.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


# DB'deki bir exclude_patterns satirinin DB'den bagimsiz, saf (pure) temsili.
@dataclass(frozen=True)
class ExcludeSpec:
    id: int
    pattern_name: str
    glob_pattern: str
    applies_to: str  # 'file' | 'directory' | 'both'


# Verilen dosya/klasor adinin (tam yol degil, sadece son bilesen) herhangi
# bir haric tutma desenine uyup uymadigini kontrol eder.
def matches_exclude(name: str, specs: list[ExcludeSpec], kind: str) -> ExcludeSpec | None:
    """kind is 'file' or 'directory'. Returns the first matching ExcludeSpec,
    or None if nothing matches."""
    for spec in specs:
        if spec.applies_to not in (kind, "both"):
            continue
        if fnmatch.fnmatch(name, spec.glob_pattern):
            return spec
    return None
