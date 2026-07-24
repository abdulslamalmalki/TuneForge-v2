"""Strict vendor-domain and support-page URL validation."""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.normalization.vendors import canonical_vendor_name


@dataclass(frozen=True)
class VendorSourcePolicy:
    canonical_name: str
    domains: tuple[str, ...]
    support_page_shapes: tuple[tuple[str, ...], ...]


_POLICIES = {
    "asus": VendorSourcePolicy(
        "ASUS",
        ("asus.com",),
        (
            ("/supportonly/", "helpdesk_manual"),
            ("/motherboards-components/motherboards/", "helpdesk_manual"),
        ),
    ),
    "msi": VendorSourcePolicy(
        "MSI",
        ("msi.com",),
        (
            ("/motherboard/", "/support"),
            ("/support/download-manual/",),
        ),
    ),
    "gigabyte": VendorSourcePolicy(
        "GIGABYTE",
        ("gigabyte.com",),
        (("/motherboard/", "/support", "support-manual"),),
    ),
    "asrock": VendorSourcePolicy("ASRock", ("asrock.com",), (("/mb/", "index.asp", "#manual"),)),
}


def canonical_vendor(value: str | None) -> str | None:
    """Backward-compatible provenance wrapper around the normalization contract."""
    return canonical_vendor_name(value)


def vendor_policy(value: str | None) -> VendorSourcePolicy | None:
    canonical = canonical_vendor(value)
    if canonical is None:
        return None
    return next(policy for policy in _POLICIES.values() if policy.canonical_name == canonical)


def normalize_support_url(value: str) -> str:
    """Normalize scheme/host while preserving the vendor page path and fragment."""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").rstrip(".").casefold()
    netloc = host
    port = _safe_port(parsed)
    if port not in (None, 443):
        netloc = f"{host}:{port}"
    return urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path, parsed.query, parsed.fragment)
    )


def is_official_vendor_url(vendor: str | None, value: str) -> bool:
    policy = vendor_policy(vendor)
    if policy is None:
        return False
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").rstrip(".").casefold()
    except ValueError:
        return False
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        return False
    if _safe_port(parsed) not in (None, 443):
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in policy.domains)


def is_official_support_page(vendor: str | None, value: str) -> bool:
    """True only for an HTTPS page in the vendor's known support-page shape."""
    if not is_official_vendor_url(vendor, value):
        return False
    policy = vendor_policy(vendor)
    assert policy is not None
    parsed = urlsplit(value.strip())
    path_and_fragment = f"{parsed.path}#{parsed.fragment}".casefold()
    return any(
        all(part.casefold() in path_and_fragment for part in shape)
        for shape in policy.support_page_shapes
    )


def support_page_error(vendor: str | None, value: str) -> str | None:
    policy = vendor_policy(vendor)
    if policy is None:
        return "Choose one of the supported vendors: ASUS, MSI, GIGABYTE, or ASRock."
    if not is_official_vendor_url(vendor, value):
        domains = ", ".join(policy.domains)
        return f"The support URL must use HTTPS on the official {domains} domain."
    if not is_official_support_page(vendor, value):
        return (
            f"The URL is official, but it is not a recognized {policy.canonical_name} support page."
        )
    return None


def _safe_port(parsed) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return -1
