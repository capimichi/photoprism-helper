from __future__ import annotations

import logging
from dataclasses import dataclass
from injector import inject

from photoprismhelper.client.photoprism_client import PhotoprismClient
from photoprismhelper.mapper.media_mapper import MediaMapper
from photoprismhelper.repository.media_repository import MediaRepository

logger = logging.getLogger(__name__)


@dataclass
class TagProposal:
    uid: str
    file_name: str
    folder_path: str
    existing_tags: str
    new_tags_to_add: list[str]


class TagService:
    @inject
    def __init__(self, client: PhotoprismClient, repository: MediaRepository) -> None:
        self._client = client
        self._repository = repository

    def find_tag_proposals(self, limit: int = 100) -> list[TagProposal]:
        """Find media items where suggested folder tags are not yet present in existing tags."""
        proposals: list[TagProposal] = []
        session = self._repository.get_session()
        try:
            items = self._repository.find_media_with_folders(session, limit=limit)
            for item in items:
                existing = [t.strip().lower() for t in (item.tags or "").split(",") if t.strip()]
                suggested = MediaMapper.extract_suggested_tags_from_folder(item.folder_path)

                missing = [s for s in suggested if s.lower() not in existing]
                if missing:
                    proposals.append(
                        TagProposal(
                            uid=item.uid,
                            file_name=item.file_name,
                            folder_path=item.folder_path,
                            existing_tags=item.tags or "",
                            new_tags_to_add=missing,
                        )
                    )
            return proposals
        finally:
            session.close()

    def apply_tags(self, proposals: list[TagProposal]) -> int:
        """Apply suggested folder tags to PhotoPrism and update local database."""
        success_count = 0
        with self._repository.transaction() as session:
            for proposal in proposals:
                media_item = self._repository.get_by_uid(session, proposal.uid)
                if not media_item:
                    continue

                applied_for_item = False
                for tag in proposal.new_tags_to_add:
                    ok = self._client.add_label_to_photo(proposal.uid, tag)
                    if ok:
                        applied_for_item = True
                        logger.info("Added tag '%s' to photo %s (%s)", tag, proposal.uid, proposal.file_name)

                if applied_for_item:
                    existing_list = [t.strip() for t in (media_item.tags or "").split(",") if t.strip()]
                    merged = list(dict.fromkeys(existing_list + proposal.new_tags_to_add))
                    media_item.tags = ", ".join(merged)
                    success_count += 1

        return success_count
