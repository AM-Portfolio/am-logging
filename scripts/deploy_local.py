import argparse
import os
import subprocess
import sys
import tempfile
from typing import List


def run_command(command: str, cwd: str | None = None) -> None:
    """Run a shell command and exit on failure."""
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def discover_kind_nodes(cluster_name: str) -> List[str]:
    """Return KIND node container names for the given cluster."""
    if not cluster_name:
        return []

    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Docker CLI not found; skipping KIND image load.")
        return []

    if result.returncode != 0:
        print("Unable to list docker containers; skipping KIND image load.")
        return []

    prefix = f"{cluster_name}-"
    return [name.strip() for name in result.stdout.splitlines() if name.strip().startswith(prefix)]


def load_image_into_kind(image_tag: str, cluster_name: str) -> None:
    nodes = discover_kind_nodes(cluster_name)
    if not nodes:
        print(
            f"No KIND nodes detected for cluster '{cluster_name}'. "
            "Skipping automatic image load."
        )
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = os.path.join(tmpdir, "image.tar")
        print(f"Saving {image_tag} to temporary tarball ...")
        save_result = subprocess.run(["docker", "save", image_tag, "-o", tar_path])
        if save_result.returncode != 0:
            print("Failed to save Docker image; skipping KIND image load.")
            return

        for node in nodes:
            print(f"Loading {image_tag} into {node} ...")
            with open(tar_path, "rb") as tar_file:
                load_result = subprocess.run(
                    ["docker", "exec", "-i", node, "ctr", "-n", "k8s.io", "image", "import", "-"],
                    stdin=tar_file,
                )
            if load_result.returncode != 0:
                print(f"Warning: failed to load {image_tag} into {node} (exit code {load_result.returncode}).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and deploy the AM Logging service locally."
    )
    parser.add_argument(
        "--skip-build", "-k", action="store_true", help="Skip Docker build step"
    )
    parser.add_argument(
        "--build-only",
        "-b",
        action="store_true",
        help="Only build Docker image, do not run Helm deploy",
    )
    parser.add_argument(
        "--deploy-only",
        "-d",
        action="store_true",
        help="Only deploy via Helm, skip Docker build",
    )
    parser.add_argument(
        "--namespace-prefix",
        "-p",
        type=str,
        default="am",
        help="Prefix for namespaces (default: am)",
    )
    parser.add_argument(
        "--kind-cluster-name",
        type=str,
        default="am-preprod",
        help="Name of the KIND cluster whose nodes should receive the image (set empty to skip)",
    )
    parser.add_argument(
        "--skip-kind-load",
        action="store_true",
        help="Skip automatically loading the Docker image into KIND nodes",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    am_logging_root = os.path.dirname(script_dir)
    service_context = os.path.join(am_logging_root, "service")

    image_tag = "local/am-logging-svc:latest"

    if not (args.skip_build or args.deploy_only):
        print("\n--- Building am-logging-svc ---")
        run_command(f'docker build -t "{image_tag}" "{service_context}"')

    if not args.skip_kind_load:
        load_image_into_kind(image_tag, args.kind_cluster_name)

    if not args.build_only:
        print("\n--- Deploying am-logging ---")
        logging_helm = os.path.join(am_logging_root, "helm")
        run_command(
            f'helm upgrade --install am-logging "{logging_helm}" '
            f'-f "{os.path.join(logging_helm, "values.yaml")}" '
            f'-f "{os.path.join(logging_helm, "values-local.yaml")}" '
            f'--namespace {args.namespace_prefix}-logging-local --create-namespace'
        )

    print("\n✅ Logging deployment task completed.")


if __name__ == "__main__":
    main()
