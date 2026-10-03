#!/usr/bin/env bash
# ==============================================================================
# move_backups.sh (MoveBackUps)
#
# Recursively finds all *.bak files in SOURCE_DIR, replicates the nested directory
# structure in DEST_DIR, copies each file, strictly verifies its integrity (size
# and optional SHA-256 checksum), and deletes the original only upon success.
# ==============================================================================

set -eo pipefail

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Default options
DRY_RUN=false
VERIFY_CHECKSUM=false
AUTO_CONFIRM=false
VERBOSE=false

show_help() {
    echo -e "${BOLD}Usage:${NC} $(basename "$0") [OPTIONS] <SOURCE_DIR> <DEST_DIR>

Recursively finds all *.bak files in SOURCE_DIR, mirrors the directory structure
into DEST_DIR, verifies file size (and optional checksum), and removes the original
source file only if the copy is 100% verified.

${BOLD}Arguments:${NC}
  <SOURCE_DIR>          Source root directory to scan (e.g. /mnt/nas/Immagini)
  <DEST_DIR>            Destination directory for backup storage

${BOLD}Options:${NC}
  -n, --dry-run         Simulate the scan and transfer without copying or deleting files
  -c, --checksum        Verify SHA-256 checksum in addition to exact byte size (extra safe)
  -y, --yes             Automatically proceed without asking for confirmation
  -v, --verbose         Print additional details for every processed file
  -h, --help            Show this help message and exit

${BOLD}Examples:${NC}
  # Test with dry-run
  $(basename "$0") --dry-run /mnt/nas/Immagini /mnt/external_backup/Immagini_bak

  # Move backups and verify byte sizes
  $(basename "$0") /mnt/nas/Immagini /mnt/external_backup/Immagini_bak

  # Move with full SHA-256 checksum verification and auto-confirm
  $(basename "$0") -c -y /mnt/nas/Immagini /mnt/external_backup/Immagini_bak
"
}

# Parse options
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -c|--checksum)
            VERIFY_CHECKSUM=true
            shift
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        -*)
            echo -e "${RED}Error: Unknown option $1${NC}" >&2
            show_help
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ${#POSITIONAL_ARGS[@]} -lt 2 ]]; then
    echo -e "${RED}Error: Missing required arguments: SOURCE_DIR and DEST_DIR.${NC}\n" >&2
    show_help
    exit 1
fi

SRC_INPUT="${POSITIONAL_ARGS[0]}"
DEST_INPUT="${POSITIONAL_ARGS[1]}"

# Validate SOURCE_DIR
if [[ ! -d "$SRC_INPUT" ]]; then
    echo -e "${RED}Error: Source directory does not exist: $SRC_INPUT${NC}" >&2
    exit 1
fi

SOURCE_DIR="$(cd "$SRC_INPUT" && pwd)"

# Prepare DEST_DIR
if [[ "$DRY_RUN" = false ]]; then
    mkdir -p "$DEST_INPUT"
    DEST_DIR="$(cd "$DEST_INPUT" && pwd)"
else
    # In dry-run, if dest doesn't exist yet, resolve canonical path safely
    DEST_DIR="$(mkdir -p "$DEST_INPUT" 2>/dev/null && cd "$DEST_INPUT" 2>/dev/null && pwd || echo "$DEST_INPUT")"
fi

if [[ "$SOURCE_DIR" == "$DEST_DIR" ]]; then
    echo -e "${RED}Error: SOURCE_DIR and DEST_DIR cannot be the same directory!${NC}" >&2
    exit 1
fi

# Format bytes to human readable
format_bytes() {
    local bytes="$1"
    if [[ $bytes -lt 1024 ]]; then
        echo "${bytes} B"
    elif [[ $bytes -lt 1048576 ]]; then
        echo "$(awk "BEGIN {printf \"%.2f KB\", $bytes/1024}")"
    elif [[ $bytes -lt 1073741824 ]]; then
        echo "$(awk "BEGIN {printf \"%.2f MB\", $bytes/1048576}")"
    else
        echo "$(awk "BEGIN {printf \"%.2f GB\", $bytes/1073741824}")"
    fi
}

# Portable file size in bytes
get_file_size() {
    wc -c < "$1" | tr -d ' '
}

# Portable sha256 calculation
get_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "none"
    fi
}

echo -e "${BOLD}${CYAN}=== PhotoPrism Helper - Move Backups ===${NC}"
echo -e "  • Source directory : ${BOLD}$SOURCE_DIR${NC}"
echo -e "  • Destination      : ${BOLD}$DEST_DIR${NC}"
if [[ "$DRY_RUN" = true ]]; then
    echo -e "  • Mode             : ${YELLOW}${BOLD}DRY-RUN (Simulation only, no files will be moved/deleted)${NC}"
else
    echo -e "  • Mode             : ${GREEN}Live Execution${NC}"
fi
echo -e "  • Checksum verify  : $([[ "$VERIFY_CHECKSUM" = true ]] && echo -e "${GREEN}Enabled (SHA-256)${NC}" || echo -e "Disabled (Byte-size verification)")"
echo ""

# Scan for *.bak files
echo -n "Scanning for .bak files in source directory... "
FILES=()
TOTAL_BYTES=0

while IFS= read -r -d '' file; do
    FILES+=("$file")
    size=$(get_file_size "$file")
    TOTAL_BYTES=$((TOTAL_BYTES + size))
done < <(find "$SOURCE_DIR" -type f -name "*.bak" -print0)

TOTAL_COUNT=${#FILES[@]}
TOTAL_FMT=$(format_bytes "$TOTAL_BYTES")

echo -e "${GREEN}Done!${NC}"
echo -e "Found ${BOLD}$TOTAL_COUNT${NC} backup file(s) occupying ${BOLD}$TOTAL_FMT${NC}.\n"

if [[ $TOTAL_COUNT -eq 0 ]]; then
    echo -e "${GREEN}No .bak files found in source directory. Nothing to do.${NC}"
    exit 0
fi

# Confirmation prompt
if [[ "$AUTO_CONFIRM" = false && "$DRY_RUN" = false ]]; then
    read -r -p "Do you want to proceed with moving $TOTAL_COUNT file(s) ($TOTAL_FMT)? [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY])
            echo ""
            ;;
        *)
            echo -e "${YELLOW}Operation cancelled by user.${NC}"
            exit 0
            ;;
    esac
fi

# Execution loop
SUCCESS_COUNT=0
ERROR_COUNT=0
MOVED_BYTES=0
INDEX=0

for src_file in "${FILES[@]}"; do
    INDEX=$((INDEX + 1))
    rel_path="${src_file#"$SOURCE_DIR/"}"
    dest_file="$DEST_DIR/$rel_path"
    dest_dir="$(dirname "$dest_file")"
    src_size=$(get_file_size "$src_file")
    src_size_fmt=$(format_bytes "$src_size")

    echo -ne "[${INDEX}/${TOTAL_COUNT}] Moving: ${BOLD}${rel_path}${NC} (${src_size_fmt})... "

    if [[ "$DRY_RUN" = true ]]; then
        echo -e "${YELLOW}[DRY-RUN - WOULD COPY & REMOVE]${NC}"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        MOVED_BYTES=$((MOVED_BYTES + src_size))
        continue
    fi

    # 1. Create target directory
    mkdir -p "$dest_dir"

    # 2. Copy preserving attributes (cp -p)
    if ! cp -p "$src_file" "$dest_file"; then
        echo -e "${RED}[FAILED COPY]${NC}"
        echo -e "  ${RED}✗ cp failed for: $src_file${NC}" >&2
        ERROR_COUNT=$((ERROR_COUNT + 1))
        continue
    fi

    # 3. Verify destination file existence
    if [[ ! -f "$dest_file" ]]; then
        echo -e "${RED}[FAILED VERIFY: File not found]${NC}" >&2
        ERROR_COUNT=$((ERROR_COUNT + 1))
        continue
    fi

    # 4. Verify exact byte size
    dest_size=$(get_file_size "$dest_file")
    if [[ "$src_size" -ne "$dest_size" ]]; then
        echo -e "${RED}[FAILED VERIFY: Size mismatch]${NC}"
        echo -e "  ${RED}✗ Size mismatch: source ($src_size bytes) != dest ($dest_size bytes)${NC}" >&2
        rm -f "$dest_file"
        ERROR_COUNT=$((ERROR_COUNT + 1))
        continue
    fi

    # 5. Optional SHA-256 verification
    if [[ "$VERIFY_CHECKSUM" = true ]]; then
        src_hash=$(get_sha256 "$src_file")
        dest_hash=$(get_sha256 "$dest_file")
        if [[ "$src_hash" != "$dest_hash" || "$src_hash" == "none" ]]; then
            echo -e "${RED}[FAILED VERIFY: Checksum mismatch]${NC}"
            echo -e "  ${RED}✗ Checksum mismatch: source ($src_hash) != dest ($dest_hash)${NC}" >&2
            rm -f "$dest_file"
            ERROR_COUNT=$((ERROR_COUNT + 1))
            continue
        fi
    fi

    # 6. Verification passed! Safely delete source file
    if rm "$src_file"; then
        echo -e "${GREEN}✓ OK${NC}"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        MOVED_BYTES=$((MOVED_BYTES + src_size))
    else
        echo -e "${YELLOW}✓ Copied but could not delete source file${NC}"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    fi
done

echo ""
echo -e "${BOLD}${CYAN}=== Summary ===${NC}"
echo -e "  • Total files found    : ${BOLD}$TOTAL_COUNT${NC}"
echo -e "  • Successfully moved   : ${GREEN}${BOLD}$SUCCESS_COUNT${NC}"
if [[ $ERROR_COUNT -gt 0 ]]; then
    echo -e "  • Errors / Aborted     : ${RED}${BOLD}$ERROR_COUNT${NC}"
fi
echo -e "  • Total space freed    : ${GREEN}${BOLD}$(format_bytes "$MOVED_BYTES")${NC}"

if [[ $ERROR_COUNT -gt 0 ]]; then
    exit 1
fi
exit 0
