from __future__ import annotations

from injector import inject
from tabulate import tabulate

from photoprismhelper.repository.media_repository import MediaRepository


class StorageAnalysisService:
    @inject
    def __init__(self, repository: MediaRepository) -> None:
        self._repository = repository

    @staticmethod
    def format_bytes(num_bytes: int | float) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(num_bytes)
        unit_idx = 0
        while size >= 1024.0 and unit_idx < len(units) - 1:
            size /= 1024.0
            unit_idx += 1
        return f"{size:.2f} {units[unit_idx]}"

    def get_summary_report(self) -> str:
        session = self._repository.get_session()
        try:
            summary = self._repository.get_storage_summary(session)

            summary_rows = [
                ["Total Files", f"{summary.total_files:,}", self.format_bytes(summary.total_size_bytes)],
                ["Images", f"{summary.image_count:,}", self.format_bytes(summary.image_size_bytes)],
                ["Videos", f"{summary.video_count:,}", self.format_bytes(summary.video_size_bytes)],
                ["Other / Documents", f"{summary.other_count:,}", self.format_bytes(summary.other_size_bytes)],
            ]
            summary_table = tabulate(
                summary_rows,
                headers=["Category", "Count", "Total Storage"],
                tablefmt="github",
            )

            # Extension breakdown
            ext_stats = self._repository.get_breakdown_by_extension(session, limit=10)
            ext_rows = [
                [item.category.upper() if item.category else "UNKNOWN", f"{item.count:,}", self.format_bytes(item.total_bytes)]
                for item in ext_stats
            ]
            ext_table = tabulate(
                ext_rows,
                headers=["Extension", "Count", "Storage"],
                tablefmt="github",
            )

            # Heaviest folders
            folder_stats = self._repository.get_breakdown_by_folder(session, limit=10)
            folder_rows = [
                [item.category[:50], f"{item.count:,}", self.format_bytes(item.total_bytes)]
                for item in folder_stats
            ]
            folder_table = tabulate(
                folder_rows,
                headers=["Folder Path", "Count", "Storage"],
                tablefmt="github",
            )

            return (
                f"### Storage Summary\n\n{summary_table}\n\n"
                f"### Storage by Extension (Top 10)\n\n{ext_table}\n\n"
                f"### Heaviest Folders (Top 10)\n\n{folder_table}"
            )
        finally:
            session.close()

    def get_largest_files_report(self, limit: int = 15, media_type: str | None = None) -> str:
        session = self._repository.get_session()
        try:
            items = self._repository.find_largest_files(session, limit=limit, media_type=media_type)
            rows = []
            for item in items:
                duration_str = f"{item.duration:.1f}s" if item.duration else "-"
                dims = f"{item.width}x{item.height}" if item.width and item.height else "-"
                rows.append([
                    item.uid[:12],
                    item.file_name[:40],
                    item.media_type,
                    item.extension.upper(),
                    self.format_bytes(item.file_size),
                    dims,
                    duration_str,
                    item.folder_path[:30],
                ])

            table = tabulate(
                rows,
                headers=["UID", "File Name", "Type", "Ext", "Size", "Dimensions", "Duration", "Folder"],
                tablefmt="github",
            )
            type_label = f" ({media_type.capitalize()})" if media_type else ""
            return f"### Top {limit} Largest Files{type_label}\n\n{table}"
        finally:
            session.close()
