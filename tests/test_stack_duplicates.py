from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch
import click
import pytest
from click.testing import CliRunner

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.stack_duplicates_command import VideoDuplicatesCommand
from photoprismhelper.entity.media_file import MediaFile
from photoprismhelper.entity.media_item import MediaItem
from photoprismhelper.model.stack_duplicate import StackDuplicateCandidate
from photoprismhelper.service.preview_server import VideoPreviewServer
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


@pytest.fixture
def mock_media_repo():
    return MagicMock()


@pytest.fixture
def mock_file_repo():
    return MagicMock()


@pytest.fixture
def mock_conv_repo():
    return MagicMock()


@pytest.fixture
def mock_storage_service(mock_media_repo):
    return StorageAnalysisService(repository=mock_media_repo)


@pytest.fixture
def mock_photoprism_client():
    client = MagicMock(spec=PhotoprismClient)
    client.base_url = "https://photoprism.example.com"
    return client


def test_find_stack_duplicates_master_proxy_resolution(mock_media_repo, mock_file_repo, mock_conv_repo, mock_storage_service):
    """Test finding a master (4K/1080p) vs proxy (720p) pair within a stack."""
    service = VideoOptimizerService(
        media_repository=mock_media_repo,
        conversion_repository=mock_conv_repo,
        storage_analysis_service=mock_storage_service,
        file_repository=mock_file_repo,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.find_stacked_video_media_uids.return_value = ["stack_uid_1"]

    media_item = MediaItem(
        id=1,
        uid="stack_uid_1",
        file_name="20240716_194020_E838E1F4.mov",
        file_path="2024/07/20240716_194020_E838E1F4.mov",
        photo_title="Summer Vacation",
        taken_at=datetime(2024, 7, 16, 19, 40, 20),
    )
    mock_media_repo.get_by_uid.return_value = media_item

    # Master: 4K (3840x2160), 334 MB, secondary
    f_master = MediaFile(
        id=10,
        media_id=1,
        media_uid="stack_uid_1",
        file_uid="file_master_uid",
        file_name="20240716_194020_DB687C2A.mov",
        file_path="2024/07/20240716_194020_DB687C2A.mov",
        file_size=350_000_000,
        width=3840,
        height=2160,
        duration=15.0,
        is_primary=False,
        is_video=True,
        is_missing=False,
        is_sidecar=False,
    )
    # Proxy: 720p (1280x720), 5 MB, primary in PhotoPrism
    f_proxy = MediaFile(
        id=11,
        media_id=1,
        media_uid="stack_uid_1",
        file_uid="file_proxy_uid",
        file_name="20240716_194020_E838E1F4.mov",
        file_path="2024/07/20240716_194020_E838E1F4.mov",
        file_size=5_000_000,
        width=1280,
        height=720,
        duration=15.0,
        is_primary=True,
        is_video=True,
        is_missing=False,
        is_sidecar=False,
    )

    mock_file_repo.get_stack_files_for_media.return_value = [f_proxy, f_master]

    candidates = service.find_stack_duplicates()
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.media_uid == "stack_uid_1"
    assert cand.duplicate_type == "master_proxy"
    assert cand.master_file.file_uid == "file_master_uid"
    assert cand.proxy_file.file_uid == "file_proxy_uid"
    assert cand.is_proxy_primary is True
    assert cand.is_master_primary is False
    assert cand.duration_diff == 0.0


def test_find_stack_duplicates_ignores_invalid_stacks_with_duration_diff(
    mock_media_repo, mock_file_repo, mock_conv_repo, mock_storage_service
):
    """Stacks with duration diff > 1.5s should be ignored (handled by stack-check)."""
    service = VideoOptimizerService(
        media_repository=mock_media_repo,
        conversion_repository=mock_conv_repo,
        storage_analysis_service=mock_storage_service,
        file_repository=mock_file_repo,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.find_stacked_video_media_uids.return_value = ["diff_dur_uid"]

    media_item = MediaItem(
        id=1,
        uid="diff_dur_uid",
        file_name="video1.mov",
        file_path="2024/07/video1.mov",
    )
    mock_media_repo.get_by_uid.return_value = media_item

    f1 = MediaFile(
        id=1,
        media_id=1,
        media_uid="diff_dur_uid",
        file_uid="f1",
        file_name="video1.mov",
        file_path="2024/07/video1.mov",
        file_size=100_000_000,
        duration=10.0,
        is_primary=True,
        is_video=True,
        is_missing=False,
        is_sidecar=False,
    )
    f2 = MediaFile(
        id=2,
        media_id=1,
        media_uid="diff_dur_uid",
        file_uid="f2",
        file_name="video2.mov",
        file_path="2024/07/video2.mov",
        file_size=50_000_000,
        duration=35.0,  # diff is 25s!
        is_primary=False,
        is_video=True,
        is_missing=False,
        is_sidecar=False,
    )
    mock_file_repo.get_stack_files_for_media.return_value = [f1, f2]

    candidates = service.find_stack_duplicates()
    assert len(candidates) == 0


def test_resolve_stack_duplicate_keep_master(mock_media_repo, mock_file_repo, mock_conv_repo, mock_storage_service, mock_photoprism_client, tmp_path):
    """Test keeping master promotes master to primary and cleans proxy from disk and DB."""
    service = VideoOptimizerService(
        media_repository=mock_media_repo,
        conversion_repository=mock_conv_repo,
        storage_analysis_service=mock_storage_service,
        file_repository=mock_file_repo,
        photoprism_client=mock_photoprism_client,
        originals_path=str(tmp_path),
    )

    master_path = tmp_path / "master.mov"
    proxy_path = tmp_path / "proxy.mov"
    master_path.write_bytes(b"master_video_data")
    proxy_path.write_bytes(b"proxy_video_data")

    f_master = MediaFile(
        id=1,
        media_id=1,
        media_uid="uid1",
        file_uid="m_uid",
        file_name="master.mov",
        file_path="master.mov",
        file_size=1000,
        width=3840,
        height=2160,
        duration=10.0,
        is_primary=False,
    )
    f_proxy = MediaFile(
        id=2,
        media_id=1,
        media_uid="uid1",
        file_uid="p_uid",
        file_name="proxy.mov",
        file_path="proxy.mov",
        file_size=200,
        width=1280,
        height=720,
        duration=10.0,
        is_primary=True,
    )

    candidate = StackDuplicateCandidate(
        media_uid="uid1",
        media_id=1,
        photo_title="Test Stack",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=5.0,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.get_by_file_uid.return_value = f_master
    media_item = MediaItem(id=1, uid="uid1", file_name="proxy.mov", file_path="proxy.mov")
    mock_media_repo.get_by_uid.return_value = media_item

    ok, msg = service.resolve_stack_duplicate(candidate, keep="master", keep_backup=False, notify_photoprism=True)
    assert ok is True
    assert not os.path.exists(proxy_path)
    assert os.path.exists(master_path)
    assert f_master.is_primary is True
    assert media_item.file_name == "master.mov"
    mock_file_repo.delete_by_file_uid.assert_called_once_with(mock_session, "p_uid")
    mock_photoprism_client.trigger_index.assert_called_once()


def test_resolve_stack_duplicate_numbered_copy_clean_rename(mock_media_repo, mock_file_repo, mock_conv_repo, mock_storage_service, tmp_path):
    """Test keeping a .00001.mp4 file cleanly renames it to .mp4."""
    service = VideoOptimizerService(
        media_repository=mock_media_repo,
        conversion_repository=mock_conv_repo,
        storage_analysis_service=mock_storage_service,
        file_repository=mock_file_repo,
        originals_path=str(tmp_path),
    )

    orig_path = tmp_path / "video.mov"
    numbered_path = tmp_path / "video.00001.mp4"
    orig_path.write_bytes(b"original_mov")
    numbered_path.write_bytes(b"optimized_mp4")

    f_orig = MediaFile(
        id=1,
        media_id=1,
        media_uid="uid1",
        file_uid="orig_uid",
        file_name="video.mov",
        file_path="video.mov",
        file_size=5000,
        is_primary=False,
    )
    f_num = MediaFile(
        id=2,
        media_id=1,
        media_uid="uid1",
        file_uid="num_uid",
        file_name="video.00001.mp4",
        file_path="video.00001.mp4",
        file_size=1000,
        is_primary=True,
    )

    candidate = StackDuplicateCandidate(
        media_uid="uid1",
        media_id=1,
        photo_title="Numbered Test",
        taken_at=datetime.now(),
        file1=f_orig,
        file2=f_num,
        master_file=f_orig,
        proxy_file=f_num,
        duplicate_type="numbered_copy",
        duration_diff=0.0,
        size_ratio=5.0,
    )

    mock_session = MagicMock()
    mock_file_repo.get_session.return_value = mock_session
    mock_file_repo.get_by_file_uid.return_value = f_num
    media_item = MediaItem(id=1, uid="uid1", file_name="video.00001.mp4", file_path="video.00001.mp4")
    mock_media_repo.get_by_uid.return_value = media_item

    # We keep candidate.proxy_file (the .00001.mp4)
    ok, msg = service.resolve_stack_duplicate(candidate, keep="proxy", keep_backup=False, notify_photoprism=False)
    assert ok is True
    assert not os.path.exists(orig_path)
    # The file should now be cleanly renamed to video.mp4
    clean_target = tmp_path / "video.mp4"
    assert clean_target.exists()
    assert media_item.file_name == "video.mp4"


def test_command_batch_mode_auto_prefer_master(mock_media_repo, mock_file_repo, mock_storage_service):
    """Test CLI batch mode with --auto-prefer master and --yes skips interactive prompt."""
    runner = CliRunner()
    mock_optimizer = MagicMock(spec=VideoOptimizerService)

    f_master = MediaFile(id=1, file_uid="m", file_name="master.mov", file_size=100_000_000, width=1920, height=1080, is_primary=True)
    f_proxy = MediaFile(id=2, file_uid="p", file_name="proxy.mov", file_size=10_000_000, width=1280, height=720, is_primary=False)
    cand = StackDuplicateCandidate(
        media_uid="cand_uid",
        media_id=1,
        photo_title="Batch Test",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )
    mock_optimizer.find_stack_duplicates.return_value = [cand]
    mock_optimizer.resolve_stack_duplicate.return_value = (True, "Mantenuto Master, rimosso Proxy")

    cmd = VideoDuplicatesCommand(
        file_repository=mock_file_repo,
        media_repository=mock_media_repo,
        optimizer_service=mock_optimizer,
        storage_analysis_service=mock_storage_service,
    )

    cli = click.Command("video:duplicates", callback=cmd.run)
    cmd.register_options(cli)

    result = runner.invoke(cli, ["--mode", "stack", "--auto-prefer", "master", "--yes"])
    assert result.exit_code == 0
    assert "Pulizia batch in corso (--auto-prefer master)..." in result.output
    assert "Mantenuto Master, rimosso Proxy" in result.output
    mock_optimizer.resolve_stack_duplicate.assert_called_once_with(
        cand, keep="master", keep_backup=False, notify_photoprism=True
    )


def test_command_interactive_mode_choice_1(mock_media_repo, mock_file_repo, mock_storage_service):
    """Test CLI interactive mode choosing option 1 (Keep Master)."""
    runner = CliRunner()
    mock_optimizer = MagicMock(spec=VideoOptimizerService)
    mock_optimizer.resolve_disk_path.side_effect = lambda p: f"/mock/{p}"

    f_master = MediaFile(id=1, file_uid="m", file_name="master.mov", file_size=100_000_000, width=3840, height=2160, is_primary=False, duration=10.0)
    f_proxy = MediaFile(id=2, file_uid="p", file_name="proxy.mov", file_size=10_000_000, width=1280, height=720, is_primary=True, duration=10.0)
    cand = StackDuplicateCandidate(
        media_uid="interactive_uid",
        media_id=1,
        photo_title="Interactive Test",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )
    mock_optimizer.find_stack_duplicates.return_value = [cand]
    mock_optimizer.resolve_stack_duplicate.return_value = (True, "Risolto con successo")

    cmd = VideoDuplicatesCommand(
        file_repository=mock_file_repo,
        media_repository=mock_media_repo,
        optimizer_service=mock_optimizer,
        storage_analysis_service=mock_storage_service,
    )

    cli = click.Command("video:duplicates", callback=cmd.run)
    cmd.register_options(cli)

    # Input '1' chooses keeping master
    result = runner.invoke(cli, ["--mode", "stack", "--no-preview"], input="1\n")
    assert result.exit_code == 0
    assert "Stack UID: interactive_uid" in result.output
    assert "Risolto con successo" in result.output
    mock_optimizer.resolve_stack_duplicate.assert_called_once_with(
        cand, keep="master", keep_backup=False, notify_photoprism=True
    )


def test_command_interactive_mode_choice_2(mock_media_repo, mock_file_repo, mock_storage_service):
    """Test CLI interactive mode choosing option 2 (Keep Proxy)."""
    runner = CliRunner()
    mock_optimizer = MagicMock(spec=VideoOptimizerService)
    mock_optimizer.resolve_disk_path.side_effect = lambda p: f"/mock/{p}"

    f_master = MediaFile(id=1, file_uid="m", file_name="master.mov", file_size=100_000_000, width=3840, height=2160, is_primary=True, duration=10.0)
    f_proxy = MediaFile(id=2, file_uid="p", file_name="proxy.mov", file_size=10_000_000, width=1280, height=720, is_primary=False, duration=10.0)
    cand = StackDuplicateCandidate(
        media_uid="interactive_uid_2",
        media_id=2,
        photo_title="Interactive Test 2",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )
    mock_optimizer.find_stack_duplicates.return_value = [cand]
    mock_optimizer.resolve_stack_duplicate.return_value = (True, "Mantenuto Proxy")

    cmd = VideoDuplicatesCommand(
        file_repository=mock_file_repo,
        media_repository=mock_media_repo,
        optimizer_service=mock_optimizer,
        storage_analysis_service=mock_storage_service,
    )

    cli = click.Command("video:duplicates", callback=cmd.run)
    cmd.register_options(cli)

    result = runner.invoke(cli, ["--mode", "stack", "--no-preview"], input="2\n")
    assert result.exit_code == 0
    assert "Mantenuto Proxy" in result.output
    mock_optimizer.resolve_stack_duplicate.assert_called_once_with(
        cand, keep="proxy", keep_backup=False, notify_photoprism=True
    )


def test_command_interactive_mode_choice_unstack(mock_media_repo, mock_file_repo, mock_storage_service):
    """Test CLI interactive mode choosing option 'u' (Unstack)."""
    runner = CliRunner()
    mock_optimizer = MagicMock(spec=VideoOptimizerService)
    mock_optimizer.resolve_disk_path.side_effect = lambda p: f"/mock/{p}"

    f_master = MediaFile(id=1, file_uid="m", file_name="master.mov", file_size=100_000_000, is_primary=True)
    f_proxy = MediaFile(id=2, file_uid="p", file_name="proxy.mov", file_size=10_000_000, is_primary=False)
    cand = StackDuplicateCandidate(
        media_uid="unstack_uid",
        media_id=3,
        photo_title="Unstack Test",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )
    mock_optimizer.find_stack_duplicates.return_value = [cand]
    mock_optimizer.unstack_duplicate.return_value = (True, "Separato con successo")

    cmd = VideoDuplicatesCommand(
        file_repository=mock_file_repo,
        media_repository=mock_media_repo,
        optimizer_service=mock_optimizer,
        storage_analysis_service=mock_storage_service,
    )

    cli = click.Command("video:duplicates", callback=cmd.run)
    cmd.register_options(cli)

    result = runner.invoke(cli, ["--mode", "stack", "--no-preview"], input="u\n")
    assert result.exit_code == 0
    assert "Separato con successo" in result.output
    mock_optimizer.unstack_duplicate.assert_called_once_with(cand, notify_photoprism=True)


def test_command_batch_mode_confirmation_rejection(mock_media_repo, mock_file_repo, mock_storage_service):
    """Test CLI batch mode prompts confirmation and aborts when answered 'n'."""
    runner = CliRunner()
    mock_optimizer = MagicMock(spec=VideoOptimizerService)

    f_master = MediaFile(id=1, file_uid="m", file_name="master.mov", file_size=100_000_000, is_primary=True)
    f_proxy = MediaFile(id=2, file_uid="p", file_name="proxy.mov", file_size=10_000_000, is_primary=False)
    cand = StackDuplicateCandidate(
        media_uid="cand_uid",
        media_id=1,
        photo_title="Batch Test",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )
    mock_optimizer.find_stack_duplicates.return_value = [cand]

    cmd = VideoDuplicatesCommand(
        file_repository=mock_file_repo,
        media_repository=mock_media_repo,
        optimizer_service=mock_optimizer,
        storage_analysis_service=mock_storage_service,
    )

    cli = click.Command("video:duplicates", callback=cmd.run)
    cmd.register_options(cli)

    result = runner.invoke(cli, ["--mode", "stack", "--auto-prefer", "master"], input="n\n")
    assert result.exit_code == 0
    assert "Operazione annullata." in result.output
    mock_optimizer.resolve_stack_duplicate.assert_not_called()


def test_unstack_duplicate_service(mock_media_repo, mock_file_repo, mock_conv_repo, mock_storage_service, mock_photoprism_client):
    """Test unstack_duplicate calls PhotoprismClient."""
    service = VideoOptimizerService(
        media_repository=mock_media_repo,
        conversion_repository=mock_conv_repo,
        storage_analysis_service=mock_storage_service,
        file_repository=mock_file_repo,
        photoprism_client=mock_photoprism_client,
    )

    mock_photoprism_client.unstack_file.return_value = True

    f_master = MediaFile(id=1, file_uid="m_uid", file_name="master.mov", file_path="2024/07/master.mov", is_primary=True)
    f_proxy = MediaFile(id=2, file_uid="p_uid", file_name="proxy.mov", file_path="2024/07/proxy.mov", is_primary=False)
    cand = StackDuplicateCandidate(
        media_uid="photo_uid",
        media_id=1,
        photo_title="Test",
        taken_at=datetime.now(),
        file1=f_master,
        file2=f_proxy,
        master_file=f_master,
        proxy_file=f_proxy,
        duplicate_type="master_proxy",
        duration_diff=0.0,
        size_ratio=10.0,
    )

    ok, msg = service.unstack_duplicate(cand, notify_photoprism=True)
    assert ok is True
    mock_photoprism_client.unstack_file.assert_called_once_with("photo_uid", "p_uid")
    mock_photoprism_client.trigger_index.assert_called_once()
