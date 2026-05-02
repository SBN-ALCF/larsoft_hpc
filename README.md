# LArSoft HPC Deployment Tools

This repository contains tools to optimize LArSoft deployment on HPC systems
using  **layered SquashFS images**. This approach reduces metadata overhead
associated with loading LArSoft's immense shared library requirements on Lustre
filesystems that prevent scaling to large node counts.

## Key Features
- **Parallel Downloads**: A parallel version of [`pullProducts`](https://scisoft.fnal.gov/scisoft/bundles/tools/), configurable via `-j`.
- **Layered SquashFS**: Separate base images containing common dependencies (ROOT, Geant4, etc.) from experiment-specific bundles.
- **Automatic Validation**: Images carry metadata provenance; `create_env_file.sh` automatically detects and blocks version mismatches between layered images.
- **Drift Detection**: Warns you if an update is too large relative to its base, suggesting a refresh of the foundation.

## Prerequisites
- **Python 3**: For manifest filtering.
- **SquashFS Tools**: `mksquashfs` must be in your `PATH`.
- **Apptainer/Singularity**: For running the resulting images.

---

## Usage

### Creating a Base Image
A "Base" is typically a full LArSoft release. Use the same bundle for both base and target arguments.

```bash
./scripts/create_layered_stack.sh \
    slf7 \                    # OS
    larsoft-v10_14_00 \       # Base Bundle
    s131-e26 \                # Base Qualifiers
    larsoft-v10_14_00 \       # Target Bundle
    s131-e26 \                # Target Qualifiers
    prof \                    # Build Type
    /products                 # Internal Mount Point
```
**Output**: `images/larsoft-v10_14_00.squashfs` (>=10GB)

### Creating an Experiment Delta
Once a base exists, you can create a lightweight delta for a specific experiment or a newer patch.

```bash
./scripts/create_layered_stack.sh \
    slf7 \
    larsoft-v10_14_00 \       # Use existing base
    s131-e26 \
    sbnd-v10_14_02_04 \       # New Target
    e26 \
    prof \
    /products                 # Must match Base Mount Point
```
**Output**: `images/sbnd-v10_14_02_04_delta.squashfs` (Only unique packages)

### Dry Runs
Before committing to a multi-GB download, use the `--dry-run` flag to see the package delta and drift percentage:
```bash
./scripts/create_layered_stack.sh --dry-run slf7 larsoft-v10_06_00 s131-e26 sbnd-v10_14_02 e26 prof /products
```

---

## Deployment on Aurora

On worker nodes, use Apptainer to mount the images as overlays. Because of **Path Mirroring**, the products will appear exactly at the `/products` directory inside the container.

```bash
# Example: Overlaying SBND on top of LArSoft Base
apptainer exec \
  --overlay images/larsoft-v10_14_00.squashfs:ro,images/sbnd-v10_14_02_04_delta.squashfs:ro \
  larsoft_sl7_container.sif \
  bash -c "source /products/setup && setup sbndcode v10_14_02_04 -q e26:prof && ..."
```

*Note: Apptainer layers multiple images specified in the comma-separated list.*

---

## Options Summary

| Flag | Description |
|------|-------------|
| `-j <n>`, `--jobs <n>` | Number of parallel jobs for downloading and unzipping packages (default: 1). |
| `--clean` | Automatically delete the uncompressed work directory after image creation. |
| `--dry-run` | Fetch manifests and calculate delta without downloading products or creating images. |
| `--pause` | Pause execution before product downloads to allow manual manifest editing. |
| `--force` | Bypass the "Base Drift" confirmation prompt if the delta is large (>20%). |

### Persistence and Cleanup
By default, the script **preserves** the uncompressed source and binary files in a temporary `work_...` directory. This allows you to inspect the contents or re-run `mksquashfs` without re-downloading data if something goes wrong.

- **To keep files (default)**: Just run the script. It will warn you at the end to manually clean up.
- **To auto-delete**: Add the `--clean` flag to your command.

## Generating Environment Files

To avoid the overhead of `setup` in every batch job, you can pre-compute the environment variables using the `create_env_file.sh` script. This script runs the setup inside the container with your SquashFS layers and exports the resulting environment.

By default, it removes build-only variables like `CMAKE_PREFIX_PATH` and `CPATH` to keep the environment lean.

```bash
./scripts/create_env_file.sh \
    [--keep-build] \          # Optional: Keep build-specific variables
    slf7 \
    sbndcode \
    v10_14_02_04 \
    e26:prof \
    /products \
    images/larsoft-v10_14_00.squashfs \
    images/sbnd-v10_14_02_04_delta.squashfs
```

**Usage in Job Scripts:**
Instead of running `setup`, simply source the generated file:
```bash
source envs/sbndcode-v10_14_02_04.env
```

## Troubleshooting
- **Base Manifest Not Found**: Ensure you haven't deleted the `manifest_cache/` directory if you intend to reuse a base image.
- **Qualifier Mismatch**: Ensure the qualifiers match exactly what is available on [scisoft](https://scisoft.fnal.gov).
