from src.workers.stages._final_qa_checks import audit_licenses, evaluate_checklist

GOOD_VIDEO_INFO = {"width": 1920, "height": 1080, "has_video": True, "has_audio": True}


def test_audit_licenses_flags_missing_license():
    issues = audit_licenses([{"type": "visual", "license_type": None}])
    assert len(issues) == 1
    assert "visual" in issues[0]


def test_audit_licenses_flags_placeholder():
    issues = audit_licenses(
        [{"type": "video_final", "license_type": "platform-placeholder-not-for-production"}]
    )
    assert len(issues) == 1


def test_audit_licenses_accepts_real_license():
    issues = audit_licenses([{"type": "visual", "license_type": "pexels-free-commercial-use"}])
    assert issues == []


def test_evaluate_checklist_passes_when_everything_is_clean():
    result = evaluate_checklist(
        video_info=GOOD_VIDEO_INFO,
        title="A Good Title",
        description="A reasonable description.",
        tags=["tag1", "tag2"],
        assets=[{"type": "visual", "license_type": "pexels-free-commercial-use"}],
        content_flags=[],
    )
    assert result["passed"] is True
    assert result["license_issues"] == []


def test_evaluate_checklist_fails_on_unresolved_license():
    result = evaluate_checklist(
        video_info=GOOD_VIDEO_INFO,
        title="Title",
        description="Desc",
        tags=[],
        assets=[{"type": "video_final", "license_type": "platform-placeholder-not-for-production"}],
        content_flags=[],
    )
    assert result["passed"] is False
    assert result["checks"]["license_audit_clean"] is False
    assert len(result["license_issues"]) == 1


def test_evaluate_checklist_fails_on_wrong_resolution():
    result = evaluate_checklist(
        video_info={"width": 1280, "height": 720, "has_video": True, "has_audio": True},
        title="Title",
        description="Desc",
        tags=[],
        assets=[],
        content_flags=[],
    )
    assert result["passed"] is False
    assert result["checks"]["resolution_ok"] is False


def test_evaluate_checklist_fails_on_missing_audio_stream():
    result = evaluate_checklist(
        video_info={"width": 1920, "height": 1080, "has_video": True, "has_audio": False},
        title="Title",
        description="Desc",
        tags=[],
        assets=[],
        content_flags=[],
    )
    assert result["passed"] is False
    assert result["checks"]["has_audio_stream"] is False


def test_evaluate_checklist_fails_on_missing_title():
    result = evaluate_checklist(
        video_info=GOOD_VIDEO_INFO,
        title=None,
        description="Desc",
        tags=[],
        assets=[],
        content_flags=[],
    )
    assert result["passed"] is False
    assert result["checks"]["title_present"] is False


def test_evaluate_checklist_fails_on_title_too_long():
    result = evaluate_checklist(
        video_info=GOOD_VIDEO_INFO,
        title="x" * 101,
        description="Desc",
        tags=[],
        assets=[],
        content_flags=[],
    )
    assert result["passed"] is False
    assert result["checks"]["title_length_ok"] is False


def test_evaluate_checklist_fails_on_content_flags():
    result = evaluate_checklist(
        video_info=GOOD_VIDEO_INFO,
        title="Title",
        description="Desc",
        tags=[],
        assets=[],
        content_flags=["potentially misleading claim"],
    )
    assert result["passed"] is False
    assert result["checks"]["no_content_flags"] is False
