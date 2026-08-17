TITLE_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 5000
TAGS_MAX_COMBINED_LENGTH = 500
EXPECTED_WIDTH = 1920
EXPECTED_HEIGHT = 1080

# Any license_type containing one of these markers is treated as unresolved —
# currently just the Phase 4 music placeholder, but written as a list so a
# future "unverified"/"pending-review" marker slots in the same way.
UNRESOLVED_LICENSE_MARKERS = ("placeholder",)


def audit_licenses(assets: list[dict]) -> list[str]:
    """The copyright-audit-trail check from ARCHITECTURE.md §11 — every asset's
    license_type gets inspected here, not just recorded and forgotten."""
    issues = []
    for asset in assets:
        license_type = asset.get("license_type")
        if not license_type or any(marker in license_type for marker in UNRESOLVED_LICENSE_MARKERS):
            issues.append(f"{asset.get('type', 'unknown')}: {license_type or 'missing'}")
    return issues


def evaluate_checklist(
    *,
    video_info: dict,
    title: str | None,
    description: str | None,
    tags: list[str] | None,
    assets: list[dict],
    content_flags: list[str],
) -> dict:
    tags = tags or []
    license_issues = audit_licenses(assets)

    checks = {
        "resolution_ok": (
            video_info.get("width") == EXPECTED_WIDTH
            and video_info.get("height") == EXPECTED_HEIGHT
        ),
        "has_video_stream": bool(video_info.get("has_video")),
        "has_audio_stream": bool(video_info.get("has_audio")),
        "title_present": bool(title),
        "title_length_ok": len(title or "") <= TITLE_MAX_LENGTH,
        "description_length_ok": len(description or "") <= DESCRIPTION_MAX_LENGTH,
        "tags_length_ok": sum(len(t) for t in tags) <= TAGS_MAX_COMBINED_LENGTH,
        "no_content_flags": len(content_flags) == 0,
        "license_audit_clean": len(license_issues) == 0,
    }

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "license_issues": license_issues,
        "content_flags": content_flags,
    }
