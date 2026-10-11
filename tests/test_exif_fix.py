from __future__ import annotations

import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from photoprismhelper.command.exif_fix_command import ExifFixCommand
from photoprismhelper.config.app_config import AppConfig
from photoprismhelper.service.exif_fix_service import (
    ExifFixService,
    FileExifState,
)


def test_parse_filename_datetime_valid():
    dt = ExifFixService.parse_filename_datetime("20260107_074747021_iOS.heic")
    assert dt == datetime(2026, 1, 7, 7, 47, 47)

    dt2 = ExifFixService.parse_filename_datetime("20251223_080906000_iOS.jpg")
    assert dt2 == datetime(2025, 12, 23, 8, 9, 6)

    dt3 = ExifFixService.parse_filename_datetime("20260426_123814933_iOS_1.heic")
    assert dt3 == datetime(2026, 4, 26, 12, 38, 14)


def test_parse_filename_datetime_invalid():
    assert ExifFixService.parse_filename_datetime("IMG_4432.jpg") is None
    assert ExifFixService.parse_filename_datetime("screenshot.png") is None
    assert ExifFixService.parse_filename_datetime("invalid_date.mov") is None


def test_inspect_file_skips_unsupported_extension():
    service = ExifFixService(exiftool_bin="/usr/bin/mock-exiftool")
    with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
        res = service.inspect_file(tmp.name)
        assert res.state == FileExifState.SKIPPED_UNSUPPORTED_EXT


def test_inspect_file_skips_no_date():
    service = ExifFixService(exiftool_bin="/usr/bin/mock-exiftool")
    with tempfile.NamedTemporaryFile(suffix=".jpg", prefix="IMG_1234_") as tmp:
        res = service.inspect_file(tmp.name)
        assert res.state == FileExifState.SKIPPED_NO_DATE_IN_FILENAME


@patch.object(ExifFixService, "get_exif_tags")
def test_inspect_file_needs_update(mock_get_tags):
    service = ExifFixService(exiftool_bin="/usr/bin/mock-exiftool")
    # Missing CreateDate and DateTimeOriginal is invalid
    mock_get_tags.return_value = {
        "DateTimeOriginal": "0000:00:00 00:00:00",
        "ModifyDate": "2026:01:07 07:47:47",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "20260107_074747021_iOS.heic")
        with open(test_file, "wb") as f:
            f.write(b"mock bytes")

        res = service.inspect_file(test_file)
        assert res.state == FileExifState.NEEDS_UPDATE
        assert "CreateDate" in res.missing_tags
        assert "DateTimeOriginal" in res.missing_tags
        assert "ModifyDate" not in res.missing_tags


@patch.object(ExifFixService, "get_exif_tags")
def test_inspect_file_already_valid(mock_get_tags):
    service = ExifFixService(exiftool_bin="/usr/bin/mock-exiftool")
    mock_get_tags.return_value = {
        "CreateDate": "2026:01:07 07:47:47",
        "DateTimeOriginal": "2026:01:07 07:47:47",
        "ModifyDate": "2026:01:07 07:47:47",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "20260107_074747021_iOS.heic")
        with open(test_file, "wb") as f:
            f.write(b"mock bytes")

        res = service.inspect_file(test_file)
        assert res.state == FileExifState.ALREADY_VALID
        assert not res.missing_tags


@patch("subprocess.run")
def test_apply_tags(mock_subproc):
    service = ExifFixService(exiftool_bin="/usr/bin/mock-exiftool")
    mock_subproc.return_value = MagicMock(returncode=0)

    dt = datetime(2026, 1, 7, 7, 47, 47)
    ok = service.apply_tags("/tmp/test.heic", ["CreateDate", "DateTimeOriginal"], dt)
    assert ok is True
    assert mock_subproc.called
    cmd_called = mock_subproc.call_args[0][0]
    assert "/usr/bin/mock-exiftool" in cmd_called
    assert "-CreateDate=2026:01:07 07:47:47" in cmd_called
    assert "-DateTimeOriginal=2026:01:07 07:47:47" in cmd_called


def test_command_dry_run_flow():
    mock_service = MagicMock(spec=ExifFixService)
    mock_service.exiftool_bin = "/usr/bin/exiftool"
    mock_config = MagicMock(spec=AppConfig)
    mock_config.photoprism_originals_path = "/tmp"

    cmd = ExifFixCommand(mock_service, mock_config)
    click_cmd = cmd.to_click_command()

    runner = CliRunner()
    result = runner.invoke(click_cmd, ["/tmp", "--dry-run"])
    assert result.exit_code == 0
    assert mock_service.process_path.called
