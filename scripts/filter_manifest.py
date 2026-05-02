#!/usr/bin/env python3
"""
Filter LArSoft manifest files to find the difference between two versions.

This script compares a base manifest and a target manifest, identifying packages
that are present in the target but missing from the base. This is useful for
identifying what needs to be installed when layering software stacks.
"""
import sys
import os

def read_manifest(filepath):
    """
    Reads a manifest file and returns a dictionary of packages.

    Args:
        filepath (str): Path to the manifest file.

    Returns:
        dict: A mapping of (name, version, tarball) tuples to the full line from the file.
    """
    pkgs = {}
    with open(filepath, 'r') as f:
        for line in f:
            # Skip comments and empty lines
            if not line.strip() or line.strip().startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[0]
            version = parts[1]
            tarball = parts[2]
            # Store the full line to reconstruct the manifest later
            # Using tarball in key handles same name/version with different qualifiers
            pkgs[(name, version, tarball)] = line.strip()
    return pkgs

def main():
    """
    Main execution block to compare manifests and output the delta.

    Expects command line arguments: base_manifest, target_manifest, and optionally output_manifest.
    """
    if len(sys.argv) < 3:
        print("Usage: filter_manifest.py <base_manifest> <target_manifest> [output_manifest]")
        sys.exit(1)

    base_path = sys.argv[1]
    target_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else "delta_MANIFEST.txt"

    base_pkgs = read_manifest(base_path)
    target_pkgs = read_manifest(target_path)

    delta_pkgs = []
    for pkg_id, line in target_pkgs.items():
        if pkg_id not in base_pkgs:
            delta_pkgs.append(line)

    with open(output_path, 'w') as f:
        for line in delta_pkgs:
            f.write(line + '\n')

    print(f"Base: {len(base_pkgs)} packages")
    print(f"Target: {len(target_pkgs)} packages")
    print(f"Delta: {len(delta_pkgs)} packages written to {output_path}")

if __name__ == "__main__":
    main()
