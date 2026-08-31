"""cloud-sync CLI.

    cloud-sync run --job photos-local
    cloud-sync run --job gdrive-backup
    cloud-sync status
    cloud-sync verify --job photos-local
"""
from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from .config import cloud_sync_home, get_job, load_jobs
from .engine.manifest import Manifest
from .engine.uploader import UploadStats, run_job, verify_job

console = Console()


@click.group()
def main():
    """cloud-sync: sync files from any configured source into Backblaze B2."""


@main.command()
@click.option("--job", "job_name", required=True, help="Job name from config/jobs.yaml")
@click.option("--config", "config_path", default="config/jobs.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="List what would be uploaded without transferring anything")
def run(job_name: str, config_path: str, dry_run: bool):
    """Run a sync job. Safe to interrupt (Ctrl-C) and re-run — already
    uploaded, unchanged files are skipped via the manifest.
    """
    try:
        job = get_job(job_name, config_path)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[bold]Running job[/bold] '{job.name}' "
                  f"({job.source.type} -> {job.destination.type}:{job.destination.options.get('bucket')})"
                  + (" [yellow](dry run)[/yellow]" if dry_run else ""))

    with console.status("Uploading...", spinner="dots") as status:
        def on_progress(stats: UploadStats):
            status.update(
                f"[cyan]{stats.files_uploaded}[/cyan] uploaded, "
                f"[yellow]{stats.files_skipped}[/yellow] skipped, "
                f"[red]{stats.files_failed}[/red] failed "
                f"(of {stats.files_total} seen so far)"
            )

        try:
            stats = run_job(job, progress_cb=on_progress, dry_run=dry_run)
        except Exception as exc:
            console.print(f"[red]Run failed:[/red] {exc}")
            sys.exit(1)

    console.print(
        f"[bold green]Done.[/bold green] {stats.files_uploaded} uploaded, "
        f"{stats.files_skipped} skipped, {stats.files_failed} failed, "
        f"{stats.bytes_uploaded / (1024**3):.2f} GB transferred."
    )
    if stats.files_failed:
        console.print(f"[yellow]{stats.files_failed} files failed — re-run the same command to retry them.[/yellow]")
        sys.exit(2)


@main.command()
@click.option("--job", "job_name", required=True)
@click.option("--config", "config_path", default="config/jobs.yaml", show_default=True)
@click.option("--sample", type=int, default=None, help="Only re-verify N files instead of all of them")
def verify(job_name: str, config_path: str, sample: int | None):
    """Re-hash uploaded files and flag anything that doesn't match the manifest.
    Run this after every big transfer before deleting anything locally/in Drive.
    """
    try:
        job = get_job(job_name, config_path)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[bold]Verifying job[/bold] '{job.name}'" + (f" (sample={sample})" if sample else ""))
    with console.status("Verifying...", spinner="dots"):
        result = verify_job(job, sample=sample)

    if result["mismatches"]:
        console.print(f"[red]{len(result['mismatches'])} mismatches found[/red] "
                       f"(checked {result['checked']}):")
        for path, reason in result["mismatches"]:
            console.print(f"  [red]x[/red] {path} — {reason}")
        console.print("[yellow]Do not delete local/source data until this is clean.[/yellow]")
        sys.exit(1)
    else:
        console.print(f"[bold green]Clean.[/bold green] {result['checked']} files verified, no mismatches.")


@main.command()
@click.option("--config", "config_path", default="config/jobs.yaml", show_default=True)
def status(config_path: str):
    """Show configured jobs and their most recent run."""
    try:
        jobs = load_jobs(config_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    manifest = Manifest(cloud_sync_home() / "manifest.db")
    table = Table(title="cloud-sync jobs")
    table.add_column("Job")
    table.add_column("Source")
    table.add_column("Destination")
    table.add_column("Last run")
    table.add_column("Status")
    table.add_column("Files")
    table.add_column("Bytes")

    for job in jobs:
        runs = manifest.recent_runs(job.name, limit=1)
        last = runs[0] if runs else None
        table.add_row(
            job.name,
            f"{job.source.type}",
            f"{job.destination.type}:{job.destination.options.get('bucket', '')}",
            last.started_at if last else "never",
            last.status if last else "-",
            f"{last.files_uploaded}/{last.files_total}" if last else "-",
            f"{last.bytes_uploaded / (1024**3):.2f} GB" if last else "-",
        )
    console.print(table)
    manifest.close()


if __name__ == "__main__":
    main()
