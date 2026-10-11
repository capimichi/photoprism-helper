from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.stack_check_command import VideoStackCheckCommand
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.service.stack_analysis_service import (
    InvalidStackFile,
    InvalidVideoStack,
    StackAnalysisService,
)
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService


@pytest.fixture
def mock_media_repo():
    return MagicMock()


@pytest.fixture
def mock_file_repo():
    return MagicMock()


@pytest.fixture
def mock_photoprism_client():
    client = MagicMock(spec=PhotoprismClient)
    client.base_url = "https://photoprism.example.com"
    return client


def test_find_invalid_video_stacks_detects_mismatch(mock_media_repo, mock_file_repo):
    service = StackAnalysisService(
        media_repository=mock_media_repo,
        file_repository=mock_file_repo,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.find_stacked_video_media_uids.return_value = ["uid_123"]

    media_item = MediaItem(
        id=1,
        uid="uid_123",
        file_name="video_a.mov",
        file_path="2025/01/video_a.mov",
        taken_at=datetime(2025, 1, 15, 10, 0, 0),
    )
    mock_media_repo.get_by_uid.return_value = media_item

    f1 = MediaFile(
        id=10,
        media_id=1,
        media_uid="uid_123",
        file_uid="f_1",
        file_name="video_a.mov",
        file_path="2025/01/video_a.mov",
        file_size=100_000_000,
        duration=15.0,
        is_video=True,
        is_sidecar=False,
        is_missing=False,
    )
    f2 = MediaFile(
        id=11,
        media_id=1,
        media_uid="uid_123",
        file_uid="f_2",
        file_name="video_b.mov",
        file_path="2025/01/video_b.mov",
        file_size=500_000_000,
        duration=75.0,  # 60 seconds difference!
        is_video=True,
        is_sidecar=False,
        is_missing=False,
    )

    mock_file_repo.get_stack_files_for_media.return_value = [f1, f2]

    results = service.find_invalid_video_stacks(min_diff_sec=2.0)

    assert len(results) == 1
    stack = results[0]
    assert stack.media_uid == "uid_123"
    assert stack.primary_file.file_uid == "f_1"
    assert stack.primary_file.file_name == "video_a.mov"
    assert len(stack.invalid_files) == 1
    assert stack.invalid_files[0].file_uid == "f_2"
    assert stack.invalid_files[0].duration == 75.0
    assert stack.max_duration_diff == 60.0


def test_find_invalid_video_stacks_ignores_matching_durations(mock_media_repo, mock_file_repo):
    service = StackAnalysisService(
        media_repository=mock_media_repo,
        file_repository=mock_file_repo,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.find_stacked_video_media_uids.return_value = ["uid_valid"]

    media_item = MediaItem(
        id=2,
        uid="uid_valid",
        file_name="video.mp4",
        file_path="2025/01/video.mp4",
    )
    mock_media_repo.get_by_uid.return_value = media_item

    f1 = MediaFile(
        id=20,
        media_id=2,
        media_uid="uid_valid",
        file_uid="f_mp4",
        file_name="video.mp4",
        file_path="2025/01/video.mp4",
        file_size=50_000_000,
        duration=30.0,
        is_video=True,
        is_sidecar=False,
        is_missing=False,
    )
    f2 = MediaFile(
        id=21,
        media_id=2,
        media_uid="uid_valid",
        file_uid="f_mov",
        file_name="video.mov",
        file_path="2025/01/video.mov",
        file_size=200_000_000,
        duration=30.5,  # Only 0.5s difference -> normal transcode / container diff
        is_video=True,
        is_sidecar=False,
        is_missing=False,
    )

    mock_file_repo.get_stack_files_for_media.return_value = [f1, f2]

    results = service.find_invalid_video_stacks(min_diff_sec=2.0)
    assert len(results) == 0


def test_find_invalid_video_stacks_limit(mock_media_repo, mock_file_repo):
    service = StackAnalysisService(
        media_repository=mock_media_repo,
        file_repository=mock_file_repo,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.find_stacked_video_media_uids.return_value = ["uid_1", "uid_2", "uid_3"]

    def get_media(session, uid):
        return MediaItem(id=int(uid[-1]), uid=uid, file_name=f"{uid}.mov", file_path=f"{uid}.mov")

    mock_media_repo.get_by_uid.side_effect = get_media

    def get_files(session, uid, include_sidecars=False):
        return [
            MediaFile(id=1, media_id=1, media_uid=uid, file_uid=f"{uid}_1", file_name=f"{uid}.mov", file_path=f"{uid}.mov", duration=10.0, is_video=True, is_sidecar=False, is_missing=False),
            MediaFile(id=2, media_id=1, media_uid=uid, file_uid=f"{uid}_2", file_name=f"{uid}_other.mov", file_path=f"{uid}_other.mov", duration=50.0, is_video=True, is_sidecar=False, is_missing=False),
        ]

    mock_file_repo.get_stack_files_for_media.side_effect = get_files

    results = service.find_invalid_video_stacks(min_diff_sec=2.0, limit=2)
    assert len(results) == 2


def test_service_unstack_file_success(mock_media_repo, mock_file_repo, mock_photoprism_client):
    service = StackAnalysisService(
        media_repository=mock_media_repo,
        file_repository=mock_file_repo,
        photoprism_client=mock_photoprism_client,
    )
    mock_photoprism_client.unstack_file.return_value = True

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session

    res = service.unstack_file("photo_uid_1", "file_uid_1")
    assert res is True
    mock_photoprism_client.unstack_file.assert_called_once_with("photo_uid_1", "file_uid_1")
    mock_file_repo.delete_by_file_uid.assert_called_once_with(mock_session, "file_uid_1")
    mock_session.commit.assert_called_once()


def test_client_unstack_file_http(mock_photoprism_client):
    client = PhotoprismClient(base_url="https://photoprism.test", api_token="secret_tok")

    with patch.object(client._session, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        res = client.unstack_file("photo_1", "file_1")
        assert res is True
        mock_post.assert_called_once_with(
            "https://photoprism.test/api/v1/photos/photo_1/files/file_1/unstack",
            headers={"Accept": "application/json", "Content-Type": "application/json", "X-Auth-Token": "secret_tok", "X-Session-ID": "secret_tok"},
            verify=True,
            timeout=60.0,
        )


def test_cli_report_mode(mock_media_repo, mock_file_repo):
    storage_svc = StorageAnalysisService(mock_media_repo)
    stack_svc = MagicMock(spec=StackAnalysisService)

    sample_stack = InvalidVideoStack(
        media_id=1,
        media_uid="pttest123",
        photo_title="Vacation Clip",
        taken_at=datetime(2025, 6, 7, 12, 0, 0),
        primary_file=InvalidStackFile(
            file_uid="f_prim",
            file_name="clip1.mov",
            file_path="2025/06/clip1.mov",
            file_size=20_000_000,
            duration=10.0,
        ),
        invalid_files=[
            InvalidStackFile(
                file_uid="f_sec",
                file_name="clip2.mov",
                file_path="2025/06/clip2.mov",
                file_size=100_000_000,
                duration=60.0,
            )
        ],
        max_duration_diff=50.0,
    )
    stack_svc.find_invalid_video_stacks.return_value = [sample_stack]

    cmd = VideoStackCheckCommand(
        stack_service=stack_svc,
        storage_analysis_service=storage_svc,
    )

    runner = CliRunner()
    result = runner.invoke(cmd.to_click_command(), [])

    assert result.exit_code == 0
    assert "pttest123" in result.output
    assert "clip1.mov" in result.output
    assert "clip2.mov" in result.output
    assert "50.0s" in result.output


def test_cli_json_mode(mock_media_repo, mock_file_repo):
    storage_svc = StorageAnalysisService(mock_media_repo)
    stack_svc = MagicMock(spec=StackAnalysisService)

    sample_stack = InvalidVideoStack(
        media_id=1,
        media_uid="pttestjson",
        photo_title=None,
        taken_at=datetime(2025, 6, 7, 12, 0, 0),
        primary_file=InvalidStackFile(
            file_uid="f1",
            file_name="p.mov",
            file_path="2025/p.mov",
            file_size=10_000,
            duration=5.0,
        ),
        invalid_files=[
            InvalidStackFile(
                file_uid="f2",
                file_name="s.mov",
                file_path="2025/s.mov",
                file_size=50_000,
                duration=45.0,
            )
        ],
        max_duration_diff=40.0,
    )
    stack_svc.find_invalid_video_stacks.return_value = [sample_stack]

    cmd = VideoStackCheckCommand(
        stack_service=stack_svc,
        storage_analysis_service=storage_svc,
    )

    runner = CliRunner()
    result = runner.invoke(cmd.to_click_command(), ["--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert len(parsed) == 1
    assert parsed[0]["media_uid"] == "pttestjson"
    assert parsed[0]["max_duration_diff"] == 40.0


def test_cli_batch_unstack_with_yes(mock_media_repo, mock_file_repo):
    storage_svc = StorageAnalysisService(mock_media_repo)
    stack_svc = MagicMock(spec=StackAnalysisService)

    sample_stack = InvalidVideoStack(
        media_id=1,
        media_uid="pttestyes",
        photo_title=None,
        taken_at=None,
        primary_file=InvalidStackFile(
            file_uid="f1", file_name="p.mov", file_path="p.mov", file_size=100, duration=5.0
        ),
        invalid_files=[
            InvalidStackFile(
                file_uid="f2", file_name="s.mov", file_path="s.mov", file_size=500, duration=35.0
            )
        ],
        max_duration_diff=30.0,
    )
    stack_svc.find_invalid_video_stacks.return_value = [sample_stack]
    stack_svc.unstack_file.return_value = True

    cmd = VideoStackCheckCommand(
        stack_service=stack_svc,
        storage_analysis_service=storage_svc,
    )

    runner = CliRunner()
    result = runner.invoke(cmd.to_click_command(), ["--unstack", "-y"])

    assert result.exit_code == 0
    stack_svc.unstack_file.assert_called_once_with("pttestyes", "f2")
    assert "1 separati con successo" in result.output
