#!/usr/bin/env bash

# create_env_file.sh
# Usage: ./create_env_file.sh [options] <os> <target-pkg> <version> <qual> <mount-point> [base_image [delta_image ...]]
# Example: ./create_env_file.sh slf7 sbndcode v10_14_02_04 e26:prof /products images/larsoft.squashfs images/sbnd_delta.squashfs
#
# Generates a pre-computed environment file by sourcing LArSoft setup
# inside a container with the layered SquashFS images mounted.

set -euo pipefail

# --- UI Helpers ---
if [ -t 1 ]; then
    BOLD=$(tput bold)
    NORMAL=$(tput sgr0)
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    CYAN=$(tput setaf 6)
else
    BOLD=""
    NORMAL=""
    RED=""
    GREEN=""
    CYAN=""
fi

log_phase() { echo -e "\n${BOLD}${CYAN}=== $1 ===${NORMAL}"; }
log_info()  { echo -e "${GREEN}[INFO]${NORMAL} $1"; }
log_error() { echo -e "${RED}[ERROR]${NORMAL} $1"; }

usage() {
    cat <<EOF
Usage: $0 [options] <os> <target-pkg> <version> <qual> <mount-point> [base_image [delta_image ...]]

This script generates a pre-computed environment file by mounting layered SquashFS 
images inside a container, sourcing the LArSoft setup, and exporting the environment.
This file can then be sourced in batch jobs to avoid the overhead of the 'setup' command.

Arguments:
  os             The target operating system (e.g., slf7).
  target-pkg     The top-level package to set up (e.g., sbndcode).
  version        The version of the target package (e.g., v10_14_02_04).
  qual           The qualifiers for the package (e.g., e26:prof).
  mount-point    The directory inside the container where products are mounted (e.g., /products).
  base_image     The path to the base SquashFS image.
  delta_image    (Optional) One or more paths to delta SquashFS images to layer on top.

Options:
  --keep-build   Do not filter out build-specific variables (CMAKE_PREFIX_PATH, CPATH, etc.).
                 By default, these are removed to keep the environment file lean.
  -h, --help     Show this help message.

Example:
  $0 slf7 sbndcode v10_14_02_04 e26:prof /products images/larsoft.squashfs images/sbnd_delta.squashfs
EOF
}

# --- Argument Parsing ---
KEEP_BUILD=0
while [[ "$#" -gt 0 && "$1" == -* ]]; do
    case "$1" in
        --keep-build) KEEP_BUILD=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) break ;;
    esac
done

if [ "$#" -lt 5 ]; then
    usage
    exit 1
fi

OS=$1
TOPLEVEL_PKG=$2
TOPLEVEL_VER=$3
QUAL=$4
MOUNT_POINT=$5
shift 5

# The remaining arguments are SquashFS images
IMAGES=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENVS_DIR="${ROOT_DIR}/envs"
mkdir -p "${ENVS_DIR}"

ENV_FILE="${ENVS_DIR}/${TOPLEVEL_PKG}-${TOPLEVEL_VER}.env"
TMP_ENV="${ENV_FILE}.tmp"

cleanup() {
    rm -f "${TMP_ENV}"
}
trap cleanup EXIT

# Configuration - update these as needed for your HPC environment
SINGULARITY_IMAGE="/lus/flare/projects/neutrinoGPU/containers/slf7.sif"
# Ensure we use Apptainer if available, otherwise fallback to Singularity
RUNTIME=$(command -v apptainer || command -v singularity || echo "")

if [[ -z "${RUNTIME}" ]]; then
    log_error "Neither apptainer nor singularity found in PATH."
    exit 1
fi

log_phase "Preparing environment for ${TOPLEVEL_PKG} ${TOPLEVEL_VER}"

# Construct the overlay arguments for the images
OVERLAY_LIST=""
for img in "${IMAGES[@]}"; do
    if [[ ! -f "${img}" ]]; then
        log_error "Image not found: ${img}"
        exit 1
    fi
    # Apptainer layers overlays in the order they are specified.
    # We append :ro to ensure they are mounted read-only.
    # Because of Path Mirroring, we don't need -B; the internal paths 
    # will match the target paths.
    if [[ -z "${OVERLAY_LIST}" ]]; then
        OVERLAY_LIST="$(realpath "${img}"):ro"
    else
        OVERLAY_LIST="${OVERLAY_LIST},$(realpath "${img}"):ro"
    fi
done

log_info "Using runtime: ${RUNTIME}"
log_info "Mounting ${#IMAGES[@]} images as overlays"

# Command to execute inside the container
# 1. Validate Metadata (Provenance check)
# 2. Source setup from the mount point
# 3. Setup the top-level package
# 4. Export environment
INNER_CMD="
    # --- Metadata Validation ---
    if [[ -d \"${MOUNT_POINT}/.metadata\" ]]; then
        # Check all delta images for dependency satisfaction
        for dep in \"${MOUNT_POINT}/.metadata/\"*.depends_on; do
            [[ -e \"\$dep\" ]] || continue
            delta_name=\$(basename \"\$dep\" .depends_on)
            source \"\$dep\" # Sets REQUIRES_BUNDLE and REQUIRES_QUAL
            
            # Verify the required base info is present in the merged overlay
            base_info=\"${MOUNT_POINT}/.metadata/\${REQUIRES_BUNDLE}.info\"
            if [[ ! -f \"\$base_info\" ]]; then
                echo \"ERROR: Version Mismatch! Delta '\$delta_name' requires base '\${REQUIRES_BUNDLE}', but that base is not mounted.\" >&2
                exit 1
            fi
            
            source \"\$base_info\" # Sets BUNDLE and QUAL of the actual base
            if [[ \"\$BUNDLE\" != \"\$REQUIRES_BUNDLE\" || \"\$QUAL\" != \"\$REQUIRES_QUAL\" ]]; then
                echo \"ERROR: Version Mismatch! Delta '\$delta_name' requires '\${REQUIRES_BUNDLE} (\${REQUIRES_QUAL})', but found '\${BUNDLE} (\${QUAL})' instead.\" >&2
                exit 1
            fi
        done
    fi

    if [[ ! -f \"${MOUNT_POINT}/setup\" ]]; then
        echo \"Setup script not found at ${MOUNT_POINT}/setup\" >&2
        exit 1
    fi
    source \"${MOUNT_POINT}/setup\"
    
    echo \"INFO: Setting up ${TOPLEVEL_PKG} ${TOPLEVEL_VER}...\" >&2
    if ! setup \"${TOPLEVEL_PKG}\" \"${TOPLEVEL_VER}\" -q \"${QUAL}\"; then
        echo \"ERROR: setup command failed for ${TOPLEVEL_PKG}\" >&2
        exit 1
    fi

    # Robustness check: Ensure the product was actually set up by checking its DIR variable
    # Convert package name to uppercase for UPS convention (e.g. sbndcode -> SBNDCODE_DIR)
    DIR_VAR=\"\$(echo \"${TOPLEVEL_PKG}\" | tr '[:lower:]' '[:upper:]')_DIR\"
    if [[ -z \"\${!DIR_VAR:-}\" ]]; then
        echo \"ERROR: ${TOPLEVEL_PKG} setup failed: \${DIR_VAR} is not set.\" >&2
        exit 1
    fi

    echo \"INFO: Setup successful. Exporting environment...\" >&2
    export -p
"

log_phase "Generating environment inside container"

# Run container and capture environment. 
# We temporarily disable set -e to handle the error gracefully.
set +e
${RUNTIME} exec \
    --overlay "${OVERLAY_LIST}" \
    "${SINGULARITY_IMAGE}" \
    /bin/bash -c "${INNER_CMD}" > "${ENV_FILE}"
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -ne 0 ]; then
    log_error "Environment generation failed (Exit Code: ${EXIT_CODE}). See logs above."
    exit $EXIT_CODE
fi

# --- Post-processing ---
log_phase "Post-processing ${ENV_FILE}"

# Temporary file for cleaning
TMP_ENV="${ENV_FILE}.tmp"

# 1. Filter out host-leaked or unnecessary variables
# - BASH_FUNC_*: Shell functions
# - APPTAINER_*, SINGULARITY_*: Container runtime variables
# - SSH_*, DISPLAY, TERM: Session-specific
# - PWD, OLDPWD, SHLVL, MAIL, USER, HOSTNAME: System-specific
# - module%%, ml%%: Lmod functions
# - LMOD, _ModuleTable, _LMFILES: Module system state
# - CONDA: Conda environment state
# - LS_COLORS, HIST, LESS: UI and shell settings
log_info "Trimming host-specific and redundant variables..."
FILTER_REGEX="BASH_FUNC_|module%%|ml%%|APPTAINER_|SINGULARITY_|_LMOD_|__LMOD_|LMOD_|_ModuleTable|_LMFILES_|MODULEPATH|LOADEDMODULES|MODULESHOME|CONDA_|_CONDA_|SSH_|XDG_|DISPLAY|TERM|PWD|OLDPWD|SHLVL|MAIL|USER|HOSTNAME|PROMPT_COMMAND|PS1|BASH_ENV|CLASSPATH|MANPATH|LS_COLORS|HIST|LESS"

if [ $KEEP_BUILD -eq 0 ]; then
    log_info "Removing build-specific variables (CMAKE_PREFIX_PATH, CPATH, etc.)..."
    FILTER_REGEX="${FILTER_REGEX}|CMAKE_PREFIX_PATH|CPATH|C_INCLUDE_PATH|CPLUS_INCLUDE_PATH|LIBRARY_PATH|PKG_CONFIG_PATH|LD_RUN_PATH"
fi

grep -vE "^declare -x (${FILTER_REGEX})" "${ENV_FILE}" > "${TMP_ENV}"

# 2. Specific HPC redirects (e.g. CVMFS stash)
if grep -q "stash" "${TMP_ENV}"; then
    log_info "Applying HPC-specific path redirects..."
    sed -i 's|/cvmfs/sbnd.osgstorage.org/pnfs/fnal.gov/usr/sbnd/persistent/stash|/lus/flare/projects/neutrinoGPU/simulation_inputs_striped|g' "${TMP_ENV}"
fi

# 3. Filter out any remaining lines that don't look like environment exports
grep "^declare -x " "${TMP_ENV}" > "${ENV_FILE}"
rm "${TMP_ENV}"

log_info "${BOLD}Environment file cleaned and created:${NORMAL} ${ENV_FILE}"
log_info "You can now source this file in your batch jobs to skip 'setup' overhead."
