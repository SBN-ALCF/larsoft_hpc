import os
import sys
import shutil
import subprocess
import argparse

def main():
    parser = argparse.ArgumentParser(description="ifbeam SquashFS Image Creator")
    parser.add_argument("--db", default="ifbeam.db", help="Path to input SQLite database file (default: ifbeam.db)")
    parser.add_argument("--out", default="ifbeam.squashfs", help="Path to output SquashFS image file (default: ifbeam.squashfs)")
    parser.add_argument("--comp", default="gzip", help="Compression algorithm: gzip, lz4, lzo, xz, zstd (default: gzip)")
    parser.add_argument("--mount-path", default="", help="Mount path inside the SquashFS image (e.g. /var/lib/ifbeam). If empty, defaults to the root of the filesystem.")
    
    args = parser.parse_args()
    
    # 1. Print configuration immediately at the start of the script
    print("=" * 60)
    print("SquashFS Image Generator Configuration:")
    print(f"  Compression Algorithm: {args.comp.upper()}")
    print(f"  Source Database:       {args.db}")
    print(f"  Output SquashFS Image: {args.out}")
    print(f"  Internal Mount Path:   {args.mount_path if args.mount_path else '/'}")
    print("=" * 60)
    
    if not os.path.exists(args.db):
        print(f"Error: Source database file '{args.db}' was not found. Please scrape data first.")
        sys.exit(1)
        
    # Check if mksquashfs is installed on the current machine
    mksquashfs_path = shutil.which("mksquashfs")
    
    # Setup temporary build directory
    build_dir = "tmp_squashfs_build"
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir, exist_ok=True)
    
    # Resolve the destination directory structure inside the temporary build directory
    internal_dest_dir = build_dir
    mount_path_clean = args.mount_path.strip("/")
    if mount_path_clean:
        internal_dest_dir = os.path.join(build_dir, mount_path_clean)
        
    os.makedirs(internal_dest_dir, exist_ok=True)
    
    # Copy the database file to its target path structure
    db_filename = os.path.basename(args.db)
    dest_db_path = os.path.join(internal_dest_dir, db_filename)
    print(f"Structuring filesystem layout: copying {args.db} -> {dest_db_path}")
    shutil.copy2(args.db, dest_db_path)
    
    # Construct mksquashfs command
    cmd = [
        "mksquashfs",
        build_dir,
        args.out,
        "-comp",
        args.comp,
        "-noappend"
    ]
    
    cmd_str = " ".join(cmd)
    
    if not mksquashfs_path:
        print("\n" + "!" * 60)
        print("WARNING: 'mksquashfs' utility is not installed on this machine.")
        print("This is normal on local workstations (e.g. macOS).")
        print("This script is designed to run on your target HPC system.")
        print("!" * 60)
        print("\nTo generate the SquashFS image manually on the HPC system:")
        print(f"  1. Run this script directly on the HPC machine: python3 make_squashfs.py --db {args.db} --out {args.out} --comp {args.comp} --mount-path \"{args.mount_path}\"")
        print("  OR")
        print("  2. Execute the following shell commands on the HPC:")
        print(f"     mkdir -p {mount_path_clean if mount_path_clean else '.'}")
        if mount_path_clean:
            print(f"     cp {args.db} {mount_path_clean}/{db_filename}")
            print(f"     mksquashfs {mount_path_clean.split('/')[0]} {args.out} -comp {args.comp} -noappend")
        else:
            print(f"     mksquashfs {args.db} {args.out} -comp {args.comp} -noappend")
        
        # Clean up build directory
        shutil.rmtree(build_dir)
        sys.exit(0)
        
    print(f"\nRunning command: {cmd_str}")
    try:
        result = subprocess.run(cmd, check=True)
        print("=" * 60)
        print("SquashFS Generation: SUCCESS")
        print(f"  Created image: {args.out}")
        print("=" * 60)
    except subprocess.CalledProcessError as e:
        print(f"\nError executing mksquashfs: {e}")
        sys.exit(1)
    finally:
        # Clean up temporary build directory
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)

if __name__ == '__main__':
    main()
