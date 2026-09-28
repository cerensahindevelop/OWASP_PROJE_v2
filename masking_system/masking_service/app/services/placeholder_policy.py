"""Public placeholder names must not expose corporate dictionary titles."""

CORPORATE_PLACEHOLDER_PREFIX = "mask_kurumsal_ifade"
CORPORATE_RULE_PREFIXES = ("kurumsal_terim_", "kurumsal_alias_")


def is_corporate_rule(rule_name: str) -> bool:
    return rule_name.startswith(CORPORATE_RULE_PREFIXES)


def public_placeholder_prefix(rule_name: str, configured_prefix: str) -> str:
    # Applies to previously saved rules as well as new uploads.
    return CORPORATE_PLACEHOLDER_PREFIX if is_corporate_rule(rule_name) else configured_prefix
