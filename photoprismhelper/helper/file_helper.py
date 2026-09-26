from __future__ import annotations


class FileHelper:
    @staticmethod
    def format_bytes(num_bytes: int | float) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(num_bytes)
        unit_idx = 0
        while size >= 1024.0 and unit_idx < len(units) - 1:
            size /= 1024.0
            unit_idx += 1
        return f"{size:.2f} {units[unit_idx]}"
