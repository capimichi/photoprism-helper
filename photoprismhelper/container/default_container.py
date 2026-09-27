from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from injector import Injector

import photoprismhelper.entity.media_item  # noqa: F401
from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.command.db_init_command import DbInitCommand
from photoprismhelper.command.media_stats_command import MediaStatsCommand
from photoprismhelper.command.media_sync_command import MediaSyncCommand
from photoprismhelper.command.optimize_command import VideoOptimizeCommand
from photoprismhelper.command.tag_command import TagCommand
from photoprismhelper.config.app_config import AppConfig
from photoprismhelper.manager.db_manager import DbManager
from photoprismhelper.mapper.media_mapper import MediaMapper
from photoprismhelper.repository.media_conversion_repository import MediaConversionRepository
from photoprismhelper.repository.media_repository import MediaRepository
from photoprismhelper.service.media_sync_service import MediaSyncService
from photoprismhelper.service.storage_analysis_service import StorageAnalysisService
from photoprismhelper.service.tag_service import TagService
from photoprismhelper.service.video_optimizer_service import VideoOptimizerService


class DefaultContainer:
    _instance: "DefaultContainer | None" = None

    def __init__(self) -> None:
        self.injector = Injector()

        load_dotenv()

        self._init_environment_variables()
        self._init_directories()
        self._init_logging()
        self._init_bindings()

    @classmethod
    def getInstance(cls) -> "DefaultContainer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, cls: type[Any]) -> Any:
        return self.injector.get(cls)

    def get_var(self, key: str) -> Any:
        if key not in self.__dict__:
            raise KeyError(f"Unknown container var: {key}")
        return self.__dict__[key]

    def _init_environment_variables(self) -> None:
        self.app_name = os.environ.get("APP_NAME", "photoprism-helper")
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"
        self.database_url = os.environ.get(
            "DATABASE_URL",
            "mysql+pymysql://photoprismhelper:photoprismhelper@localhost:3306/photoprismhelper",
        )
        self.photoprism_base_url = os.environ.get("PHOTOPRISM_BASE_URL", "http://localhost:2342")
        self.photoprism_username = os.environ.get("PHOTOPRISM_USERNAME", "admin")
        self.photoprism_password = os.environ.get("PHOTOPRISM_PASSWORD", "")
        self.photoprism_api_token = os.environ.get("PHOTOPRISM_API_TOKEN", "")
        self.photoprism_verify_ssl = os.environ.get("PHOTOPRISM_VERIFY_SSL", "true").lower() == "true"
        self.photoprism_timeout_seconds = float(os.environ.get("PHOTOPRISM_TIMEOUT_SECONDS", "60"))
        self.photoprism_originals_path = os.environ.get("PHOTOPRISM_ORIGINALS_PATH", "/photoprism/originals")

    def _init_directories(self) -> None:
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.var_dir = os.path.join(self.root_dir, "var")
        os.makedirs(self.var_dir, exist_ok=True)
        self.log_dir = os.path.join(self.var_dir, "log")
        os.makedirs(self.log_dir, exist_ok=True)
        self.app_log_path = os.path.join(self.log_dir, "app.log")

    def _init_logging(self) -> None:
        level = logging.DEBUG if self.debug else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

    def _init_bindings(self) -> None:
        app_config = AppConfig(
            app_name=self.app_name,
            debug=self.debug,
            database_url=self.database_url,
            photoprism_base_url=self.photoprism_base_url,
            photoprism_username=self.photoprism_username,
            photoprism_password=self.photoprism_password,
            photoprism_api_token=self.photoprism_api_token,
            photoprism_verify_ssl=self.photoprism_verify_ssl,
            photoprism_timeout_seconds=self.photoprism_timeout_seconds,
            photoprism_originals_path=self.photoprism_originals_path,
        )
        self.injector.binder.bind(AppConfig, to=app_config)

        db_manager = DbManager(self.database_url, debug=self.debug)
        self.injector.binder.bind(DbManager, to=db_manager)

        photoprism_client = PhotoprismClient(
            base_url=self.photoprism_base_url,
            username=self.photoprism_username,
            password=self.photoprism_password,
            api_token=self.photoprism_api_token,
            verify_ssl=self.photoprism_verify_ssl,
            timeout_seconds=self.photoprism_timeout_seconds,
        )
        self.injector.binder.bind(PhotoprismClient, to=photoprism_client)

        media_mapper = MediaMapper()
        self.injector.binder.bind(MediaMapper, to=media_mapper)

        media_repository = MediaRepository(db_manager)
        self.injector.binder.bind(MediaRepository, to=media_repository)

        media_conversion_repository = MediaConversionRepository(db_manager)
        self.injector.binder.bind(MediaConversionRepository, to=media_conversion_repository)

        storage_analysis_service = StorageAnalysisService(media_repository)
        self.injector.binder.bind(StorageAnalysisService, to=storage_analysis_service)

        media_sync_service = MediaSyncService(photoprism_client, media_repository, media_mapper)
        self.injector.binder.bind(MediaSyncService, to=media_sync_service)

        tag_service = TagService(photoprism_client, media_repository)
        self.injector.binder.bind(TagService, to=tag_service)

        video_optimizer_service = VideoOptimizerService(
            media_repository,
            media_conversion_repository,
            storage_analysis_service,
            photoprism_client=photoprism_client,
            originals_path=self.photoprism_originals_path,
        )
        self.injector.binder.bind(VideoOptimizerService, to=video_optimizer_service)

        # Commands
        self.injector.binder.bind(DbInitCommand, to=DbInitCommand(db_manager, app_config))
        self.injector.binder.bind(
            MediaSyncCommand,
            to=MediaSyncCommand(db_manager, photoprism_client, media_sync_service, storage_analysis_service),
        )
        self.injector.binder.bind(MediaStatsCommand, to=MediaStatsCommand(storage_analysis_service))
        self.injector.binder.bind(TagCommand, to=TagCommand(tag_service))
        self.injector.binder.bind(
            VideoOptimizeCommand,
            to=VideoOptimizeCommand(video_optimizer_service, storage_analysis_service),
        )
