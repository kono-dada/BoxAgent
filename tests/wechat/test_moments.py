from __future__ import annotations
from pathlib import Path
import pytest
from boxagent.infrastructure.wechat._private import private_dir, write_json
from boxagent.infrastructure.wechat.moments import Expressions, Visibility, previous_request, utf16_units


def test_moments_unicode_and_visibility_preparation_has_no_subscription() -> None:
    first = Expressions(0x100000000, 0x200000000, "中文🙂", Visibility.PRIVATE)
    second = Expressions(0x300000000, 0x400000000, "中文🙂", Visibility.PUBLIC)
    assert first.prepare() != second.prepare()
    assert first.address(0xEB9DF0) not in first.prepare()
    assert first.address(0xEB9DF0) in first.submit()
    assert len(utf16_units("中文🙂")) == 4


@pytest.mark.parametrize("body", ["", " \n", "x\0y", "\ud800"])
def test_invalid_text_is_rejected_before_native_calls(body: str) -> None:
    with pytest.raises(ValueError):
        utf16_units(body)


def test_started_publish_replays_without_another_submission(tmp_path: Path) -> None:
    directory = private_dir(tmp_path / "request")
    write_json(directory / "context.json", {"fingerprint": "same", "request_id": "one"})
    report = previous_request(directory, "same")
    assert report is not None and report["status"] == "outcome_unknown"
    assert report["executed_again"] is False
    with pytest.raises(ValueError):
        previous_request(directory, "changed")
