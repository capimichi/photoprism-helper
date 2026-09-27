# Video Conversion Tracking & Interactive Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a dedicated `media_conversion` database table to track all video conversions (hashes, metadata diffs, sizes, timestamps), along with a safe, interactive CLI command to optimize videos ordered by size, with options for verification and rollback.

**Architecture:** A separate `media_conversion` table maintains 1:N history of optimizations while keeping the main `media` table clean. A `VideoConversionService` coordinates ffmpeg transcoding, `exiftool` metadata cloning, integrity verification, and atomic file replacement or backup. The CLI command `video:optimize` exposes sorting, limits, interactive confirmation, verification, and rollback options.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, MariaDB, Click, ffmpeg (hevc_videotoolbox / libx265), exiftool.

## Global Constraints
- Preserve `media` table schema integrity (no cluttering of optimization columns; all conversion lifecycle data lives in `media_conversion`).
- Maintain exact metadata fidelity: cloned EXIF/QuickTime atoms (creation date with timezone, GPS, hardware tags) and upright display matrix.
- Non-destructive execution: support dry-run, verification before replacement, and rollback capability.
- Compatible with containerized deployment on both local macOS (VideoToolbox) and Linux server (CPU/VAAPI).

---

### Task 1: Database Migration for `media_conversion` Table

**Files:**
- Create: `alembic/versions/0002_create_media_conversion_table.py`
- Create: `photoprismhelper/entity/media_conversion.py`
- Modify: `photoprismhelper/entity/__init__.py`

**Interfaces:**
- Produces: `MediaConversion` entity with fields:
  - `id`: Integer PK
  - `media_id`: Integer FK to `media.id` (nullable=False, index=True)
  - `media_uid`: String(64) index=True
  - `status`: String(32) ('pending', 'completed', 'failed', 'reverted')
  - `original_file_path`: String(1024)
  - `optimized_file_path`: String(1024)
  - `original_hash`: String(64)
  - `optimized_hash`: String(64)
  - `original_size`: BigInteger
  - `optimized_size`: BigInteger
  - `original_extension`: String(16)
  - `optimized_extension`: String(16)
  - `original_metadata`: JSON
  - `optimized_metadata`: JSON
  - `duration_seconds`: Float (encoding duration)
  - `error_message`: Text (nullable=True)
  - `created_at`: DateTime
  - `completed_at`: DateTime (nullable=True)

- [ ] **Step 1: Create `photoprismhelper/entity/media_conversion.py`**
- [ ] **Step 2: Generate and write Alembic migration `0002_create_media_conversion_table.py`**
- [ ] **Step 3: Run `alembic upgrade head` in docker container to verify schema creation**

---

### Task 2: Repository & Data Access Layer for Conversions

**Files:**
- Create: `photoprismhelper/repository/media_conversion_repository.py`
- Modify: `photoprismhelper/repository/media_repository.py`
- Modify: `photoprismhelper/container/default_container.py`

**Interfaces:**
- `MediaConversionRepository.create(session, conversion: MediaConversion) -> MediaConversion`
- `MediaConversionRepository.update_status(session, conversion_id: int, status: str, ...)`
- `MediaConversionRepository.get_by_id(session, conversion_id: int) -> MediaConversion | None`
- `MediaConversionRepository.find_completed_media_ids(session) -> set[int]`
- `MediaRepository.find_unoptimized_videos(session, limit: int | None = None) -> list[MediaItem]`
  (Queries `media` WHERE `media_type = 'video'` AND `id NOT IN (SELECT media_id FROM media_conversion WHERE status = 'completed')` ORDER BY `file_size DESC`)

- [ ] **Step 1: Write `MediaConversionRepository` with CRUD and query methods**
- [ ] **Step 2: Add `find_unoptimized_videos` to `MediaRepository`**
- [ ] **Step 3: Register `MediaConversionRepository` in `DefaultContainer`**

---

### Task 3: Video Conversion & Verification Engine

**Files:**
- Create: `photoprismhelper/service/video_converter.py`
- Create: `photoprismhelper/service/metadata_extractor.py`
- Modify: `photoprismhelper/service/video_optimizer_service.py`

**Interfaces:**
- `MetadataExtractor.extract(file_path: str) -> dict[str, Any]` (uses exiftool / ffprobe)
- `VideoConverter.convert(input_path: str, output_path: str, resolution: int = 1080) -> ConversionResult`
  - Encodes video to 1080p HEVC 60fps with native color transfer (no color distortion)
  - Clones metadata from original with `exiftool`
  - Sets filesystem timestamps with `touch -r`
- `VideoConverter.verify(input_path: str, output_path: str) -> tuple[bool, str]`
  - Checks output file exists and is non-empty
  - Compares duration within tolerance (+/- 0.5s)
  - Verifies audio stream presence
- `VideoOptimizerService.optimize_media(media_item: MediaItem, replace: bool = False, keep_backup: bool = True) -> MediaConversion`

- [ ] **Step 1: Implement `MetadataExtractor` to extract JSON snapshot of metadata**
- [ ] **Step 2: Implement `VideoConverter` with ffmpeg encoding and exiftool cloning**
- [ ] **Step 3: Implement verification logic comparing stream counts and duration**
- [ ] **Step 4: Implement atomic swap / backup handling**

---

### Task 4: Interactive CLI Command (`video:optimize`)

**Files:**
- Modify: `photoprismhelper/command/optimize_command.py`
- Modify: `photoprismhelper/container/default_container.py`

**Options to expose:**
- `--limit / -l`: Maximum number of files to process (default: 1)
- `--interactive / -i`: Ask confirmation before processing and before replacing (default: True)
- `--dry-run`: Display candidates with current size and estimated saving without running conversion
- `--keep-backup / --no-backup`: Keep `.bak` of original file (default: True)
- `--revert <conversion_id>`: Restore original file from backup using history record

**Execution flow in interactive mode:**
1. List candidate video: UID, file name, size, duration, folder path.
2. Show prompt: `Optimize this video from 4.22 GB to ~315 MB? [y/N/q]`
3. Run conversion and show progress.
4. Verify converted file (integrity, duration, metadata).
5. Prompt: `Conversion verified successfully. Apply changes? [y/N]`
6. Record entry in `media_conversion` with `status = 'completed'` and metadata diff.

- [ ] **Step 1: Implement CLI options and arguments in `VideoOptimizeCommand`**
- [ ] **Step 2: Implement dry-run and summary display table**
- [ ] **Step 3: Implement interactive confirmation prompts**
- [ ] **Step 4: Implement rollback / revert option**
- [ ] **Step 5: Test on local container with candidate test files**

---

### Task 5: End-to-End Verification & Deployment

- [ ] **Step 1: Run alembic upgrade on local MariaDB**
- [ ] **Step 2: Test `photoprismhelper video:optimize --dry-run`**
- [ ] **Step 3: Test single file conversion with verification and rollback**
- [ ] **Step 4: Deploy migration and code to prod server (`home`)**
