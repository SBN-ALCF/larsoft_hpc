#!/bin/bash

# create_layered_stack.sh
# Usage: ./create_layered_stack.sh [options] <os> <base-bundle> <base-qual> <target-bundle> <target-qual> <build> <mount-point>
# Example: ./create_layered_stack.sh slf7 larsoft-v10_06_00 s131-e26 sbnd-v10_06_03 e26 prof /products

set -e

# --- UI Helpers ---
if [ -t 1 ]; then
    # Terminal is interactive, use colors
    BOLD=$(tput bold)
    NORMAL=$(tput sgr0)
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6)
else
    BOLD=""
    NORMAL=""
    RED=""
    GREEN=""
    YELLOW=""
    CYAN=""
fi

log_phase() { echo -e "\n${BOLD}${CYAN}=== $1 ===${NORMAL}"; }
log_info()  { echo -e "${GREEN}[INFO]${NORMAL} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NORMAL} $1"; }
log_error() { echo -e "${RED}[ERROR]${NORMAL} $1"; }
log_dry()   { echo -e "${BOLD}[DRY-RUN]${NORMAL} $1"; }

usage() {
    cat <<EOF
Usage: $0 [options] <os> <base-bundle> <base-qual> <target-bundle> <target-qual> <build> <mount-point>

This script creates layered SquashFS images for LArSoft deployments. It can create 
a base image or a delta image that only contains the differences from a base.

Arguments:
  os             The target operating system (e.g., slf7).
  base-bundle    The bundle name for the foundation (e.g., larsoft-v10_06_00).
  base-qual      The qualifiers for the base (e.g., s131-e26).
  target-bundle  The bundle name for the target (e.g., sbnd-v10_06_03).
  target-qual    The qualifiers for the target (e.g., e26).
  build          The build type (e.g., prof or debug).
  mount-point    The absolute path where products will be mounted (e.g., /products).

Options:
  -j, --jobs <n> Number of parallel jobs for downloading packages (default: 1).
  --clean        Automatically delete the uncompressed work directory on exit.
  --dry-run      Perform a trial run without downloading products or creating images.
  --pause        Pause before pulling products to allow manual manifest editing.
  --force        Bypass the "Base Drift" confirmation prompt.
  -h, --help     Show this help message.

Example:
  $0 slf7 larsoft-v10_06_00 s131-e26 sbnd-v10_06_03 e26 prof /products
EOF
}

# --- Argument Parsing ---
DRY_RUN=0
FORCE=0
CLEAN=0
JOBS=1
PAUSE=0
while [[ "$#" -gt 0 && "$1" == -* ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --force)   FORCE=1;   shift ;;
        --clean)   CLEAN=1;   shift ;;
        --pause)   PAUSE=1;   shift ;;
        --jobs|-j) JOBS=$2;   shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) break ;;
    esac
done

if [ "$#" -ne 7 ]; then
    usage
    exit 1
fi

OS=$1
BASE_BUNDLE=$2
BASE_QUAL=$3
TARGET_BUNDLE=$4
TARGET_QUAL=$5
BUILD=$6
MOUNT_POINT=$7

# Ensure MOUNT_POINT is absolute and clean
MOUNT_POINT=$(realpath -m "$MOUNT_POINT")

if [ "$CLEAN" -eq 0 ]; then
    log_warn "Automatic cleanup is DISABLED. The work directory will persist after execution."
    if [ "$DRY_RUN" -eq 0 ]; then
        log_warn "This can consume 30GB+ of space. Use --clean to enable automatic deletion."
    fi
fi

if [ "$JOBS" -gt 1 ]; then
    log_info "Parallel download enabled: using ${JOBS} jobs."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PULL_PRODUCTS="${ROOT_DIR}/scisoft/pullProducts"
FILTER_SCRIPT="${SCRIPT_DIR}/filter_manifest.py"
IMAGE_DIR="${ROOT_DIR}/images"
MANIFEST_DIR="${ROOT_DIR}/manifest_cache"

mkdir -p "${IMAGE_DIR}" "${MANIFEST_DIR}"

BASE_IMAGE="${IMAGE_DIR}/${BASE_BUNDLE}.squashfs"
DELTA_IMAGE="${IMAGE_DIR}/${TARGET_BUNDLE}_delta.squashfs"

# Work in a temporary space
WORK_DIR="${ROOT_DIR}/work_$(date +%Y%m%d_%H%M%S)"
# We create a mirroring root for squashfs
SQUASH_ROOT="${WORK_DIR}/root"

cleanup() {
    if [ "$CLEAN" -eq 1 ] && [ -d "${WORK_DIR}" ]; then
        log_info "Cleaning up work directory: ${WORK_DIR}"
        rm -rf "${WORK_DIR}"
    elif [ -d "${WORK_DIR}" ]; then
        log_warn "Work directory preserved: ${WORK_DIR}"
        if [ "$DRY_RUN" -eq 0 ]; then
            log_warn "You must manually delete it to free up space."
        fi
    fi

    if [ $DRY_RUN -eq 1 ] && [ -f "${BASE_IMAGE}.dry" ]; then
        rm -f "${BASE_IMAGE}.dry"
    fi
}
trap cleanup EXIT

mkdir -p "${SQUASH_ROOT}${MOUNT_POINT}"
cd "${WORK_DIR}"

log_phase "Phase 1: Checking Base Bundle ${BASE_BUNDLE}"
if [ -f "${BASE_IMAGE}" ] || [ -f "${BASE_IMAGE}.dry" ]; then
    log_info "Base image ${BOLD}${BASE_BUNDLE}${NORMAL} already exists. Skipping download/squash."
    log_info "Fetching base manifest for filtering..."
    ACTUAL_BASE_MANIFEST_NAME=$("${PULL_PRODUCTS}" -M . "${OS}" "${BASE_BUNDLE}" "${BASE_QUAL}" "${BUILD}" | grep "INFO: Manifest at" | awk '{print $NF}')
    BASE_MANIFEST="${MANIFEST_DIR}/${BASE_BUNDLE}_${BASE_QUAL}_MANIFEST.txt"
    mv "${ACTUAL_BASE_MANIFEST_NAME}" "${BASE_MANIFEST}"
else
    log_info "Base image not found. Pulling Base Bundle..."
    if [ $DRY_RUN -eq 1 ]; then
        log_dry "Would fetch manifest for ${BASE_BUNDLE}"
        ACTUAL_BASE_MANIFEST_NAME=$("${PULL_PRODUCTS}" -M . "${OS}" "${BASE_BUNDLE}" "${BASE_QUAL}" "${BUILD}" | grep "INFO: Manifest at" | awk '{print $NF}')
        BASE_MANIFEST="${MANIFEST_DIR}/${BASE_BUNDLE}_${BASE_QUAL}_MANIFEST.txt"
        mv "${ACTUAL_BASE_MANIFEST_NAME}" "${BASE_MANIFEST}"
        touch "${BASE_IMAGE}.dry"
    else
        # Install directly into the mirrored path
        if [ "$PAUSE" -eq 1 ]; then
            log_info "Pulling manifest first due to --pause..."
            ACTUAL_BASE_MANIFEST_NAME=$("${PULL_PRODUCTS}" -M . "${OS}" "${BASE_BUNDLE}" "${BASE_QUAL}" "${BUILD}" | grep "INFO: Manifest at" | awk '{print $NF}')
            log_warn "PAUSED: Base manifest downloaded to $(pwd)/${ACTUAL_BASE_MANIFEST_NAME}"
            read -p "Edit the manifest if needed, then press Enter to continue..."
            "${PULL_PRODUCTS}" -j "${JOBS}" -l -r "${SQUASH_ROOT}${MOUNT_POINT}" "${OS}" "${BASE_BUNDLE}" "${BASE_QUAL}" "${BUILD}"
        else
            "${PULL_PRODUCTS}" -j "${JOBS}" -r "${SQUASH_ROOT}${MOUNT_POINT}" "${OS}" "${BASE_BUNDLE}" "${BASE_QUAL}" "${BUILD}"
            ACTUAL_BASE_MANIFEST_NAME=$(ls *MANIFEST.txt)
        fi
        BASE_MANIFEST="${MANIFEST_DIR}/${BASE_BUNDLE}_${BASE_QUAL}_MANIFEST.txt"
        cp "${ACTUAL_BASE_MANIFEST_NAME}" "${BASE_MANIFEST}"
    fi
    
    if [ $DRY_RUN -eq 1 ]; then
        log_dry "Would create base squashfs (root: ${MOUNT_POINT}) with gzip compression: ${BASE_IMAGE}"
    else
        # Write metadata for the base
        METADATA_DIR="${SQUASH_ROOT}${MOUNT_POINT}/.metadata"
        mkdir -p "${METADATA_DIR}"
        echo "BUNDLE=${BASE_BUNDLE}" > "${METADATA_DIR}/${BASE_BUNDLE}.info"
        echo "QUAL=${BASE_QUAL}" >> "${METADATA_DIR}/${BASE_BUNDLE}.info"
        echo "TYPE=BASE" >> "${METADATA_DIR}/${BASE_BUNDLE}.info"

        if command -v mksquashfs &> /dev/null; then
            log_info "Creating base squashfs mirroring ${MOUNT_POINT}..."
            mksquashfs "${SQUASH_ROOT}" "${BASE_IMAGE}" -comp gzip -no-progress
        else
            log_warn "mksquashfs not found. Cannot create base image."
        fi
    fi
fi

# Clean up any local manifests in work dir
rm -f *MANIFEST.txt

# Reset SQUASH_ROOT for the delta
rm -rf "${SQUASH_ROOT}"
mkdir -p "${SQUASH_ROOT}${MOUNT_POINT}"

log_phase "Phase 2: Pulling Target Manifest ${TARGET_BUNDLE}"
# Capture the canonical name pullProducts created
ACTUAL_TARGET_MANIFEST_NAME=$("${PULL_PRODUCTS}" -M . "${OS}" "${TARGET_BUNDLE}" "${TARGET_QUAL}" "${BUILD}" | grep "INFO: Manifest at" | awk '{print $NF}')
TARGET_MANIFEST="${MANIFEST_DIR}/${TARGET_BUNDLE}_${TARGET_QUAL}_MANIFEST.txt"
mv "${ACTUAL_TARGET_MANIFEST_NAME}" "${TARGET_MANIFEST}"

log_phase "Phase 3: Filtering Manifest"
"${FILTER_SCRIPT}" "${BASE_MANIFEST}" "${TARGET_MANIFEST}" delta_MANIFEST.txt

# Check for "Base Drift"
BASE_COUNT=$(grep -v "^#" "${BASE_MANIFEST}" | grep -c "[a-z]" || true)
DELTA_COUNT=$(grep -v "^#" "delta_MANIFEST.txt" | grep -c "[a-z]" || true)
if [ "$BASE_COUNT" -gt 0 ]; then
    DRIFT_PERCENT=$(( 100 * DELTA_COUNT / BASE_COUNT ))
    if [ "$DRIFT_PERCENT" -gt 20 ]; then
        echo -e "\n${BOLD}${YELLOW}!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo -e "WARNING: Significant Base Drift Detected!"
        echo -e "Delta contains ${DELTA_COUNT} packages (${DRIFT_PERCENT}% of base size)."
        echo -e "This will result in a large, inefficient layer."
        echo -e "Consider refreshing your base image: ${TARGET_BUNDLE}"
        echo -e "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!${NORMAL}\n"
        
        if [ $FORCE -eq 0 ]; then
            read -p "Do you want to continue? [y/N] " response
            if [[ ! "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
                log_error "Aborted by user."
                exit 1
            fi
        fi
    fi
fi

log_phase "Phase 4: Pulling Delta Products"
if [ -s delta_MANIFEST.txt ]; then
    if [ $DRY_RUN -eq 1 ]; then
        log_dry "Would pull delta products (target: ${MOUNT_POINT}) for ${TARGET_BUNDLE} using filtered manifest"
    else
        # Copy the filtered delta manifest into the working directory 
        # using the exact name pullProducts -l expects.
        cp delta_MANIFEST.txt "${ACTUAL_TARGET_MANIFEST_NAME}"
        
        if [ "$PAUSE" -eq 1 ]; then
            log_warn "PAUSED: Delta manifest prepared at $(pwd)/${ACTUAL_TARGET_MANIFEST_NAME}"
            read -p "Edit the manifest if needed, then press Enter to continue..."
        fi

        "${PULL_PRODUCTS}" -j "${JOBS}" -l -r "${SQUASH_ROOT}${MOUNT_POINT}" "${OS}" "${TARGET_BUNDLE}" "${TARGET_QUAL}" "${BUILD}"
    fi
    
    log_phase "Phase 5: Creating Delta SquashFS Image"
    if [ $DRY_RUN -eq 1 ]; then
        log_dry "Would create delta squashfs mirroring ${MOUNT_POINT} with gzip compression: ${DELTA_IMAGE}"
    else
        # Write metadata for the delta
        METADATA_DIR="${SQUASH_ROOT}${MOUNT_POINT}/.metadata"
        mkdir -p "${METADATA_DIR}"
        # Own info
        echo "BUNDLE=${TARGET_BUNDLE}" > "${METADATA_DIR}/${TARGET_BUNDLE}.info"
        echo "QUAL=${TARGET_QUAL}" >> "${METADATA_DIR}/${TARGET_BUNDLE}.info"
        echo "TYPE=DELTA" >> "${METADATA_DIR}/${TARGET_BUNDLE}.info"
        # Dependency info
        echo "REQUIRES_BUNDLE=${BASE_BUNDLE}" > "${METADATA_DIR}/${TARGET_BUNDLE}.depends_on"
        echo "REQUIRES_QUAL=${BASE_QUAL}" >> "${METADATA_DIR}/${TARGET_BUNDLE}.depends_on"

        if command -v mksquashfs &> /dev/null; then
            log_info "Creating delta squashfs mirroring ${MOUNT_POINT}..."
            mksquashfs "${SQUASH_ROOT}" "${DELTA_IMAGE}" -comp gzip -no-progress
            log_info "${BOLD}Delta image created:${NORMAL} ${DELTA_IMAGE}"
        else
            log_warn "mksquashfs not found. Delta products are in ${SQUASH_ROOT}${MOUNT_POINT}"
        fi
    fi

else
    log_info "No delta packages found. Target bundle is identical to or subset of base."
fi

echo -e "\n${BOLD}${GREEN}Done.${NORMAL}"
