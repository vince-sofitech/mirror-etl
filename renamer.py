import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import google.auth
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account


SCOPES = ["https://www.googleapis.com/auth/drive"]
SERVICE_ACCOUNT_PATH = "/Users/vinceaustria/Downloads/splendid-sonar-496007-a1-4e9da10b9703.json"


@dataclass
class FileEntry:
	file_id: str
	name: str
	parent_id: str
	parent_path: str


@dataclass
class RenamePlan:
	entry: FileEntry
	new_name: Optional[str]
	status: str
	message: str


def resolve_credentials():
	load_dotenv()

	sa_path = SERVICE_ACCOUNT_PATH or os.getenv("SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
	if not sa_path:
		sa_path = input("Service account JSON path (leave blank to use ADC): ").strip()

	if sa_path:
		sa_path = os.path.expanduser(sa_path)
		if not os.path.isfile(sa_path):
			raise FileNotFoundError(f"Service account file not found: {sa_path}")
		return service_account.Credentials.from_service_account_file(
			sa_path, scopes=SCOPES
		)

	creds, _ = google.auth.default(scopes=SCOPES)
	return creds


def build_drive_service():
	creds = resolve_credentials()
	return build("drive", "v3", credentials=creds)


def prompt_choice(prompt: str, choices: List[str]) -> str:
	while True:
		value = input(prompt).strip().lower()
		if value in choices:
			return value
		print(f"Please choose one of: {', '.join(choices)}")


def get_folder_metadata(drive, folder_id: str) -> Tuple[str, str]:
	data = (
		drive.files()
		.get(fileId=folder_id, fields="id,name,mimeType")
		.execute()
	)
	if data.get("mimeType") != "application/vnd.google-apps.folder":
		raise ValueError("The provided ID is not a folder.")
	return data["id"], data["name"]


def get_folder_metadata_any_drive(drive, folder_id: str) -> Tuple[str, str]:
	data = (
		drive.files()
		.get(fileId=folder_id, fields="id,name,mimeType", supportsAllDrives=True)
		.execute()
	)
	if data.get("mimeType") != "application/vnd.google-apps.folder":
		raise ValueError("The provided ID is not a folder.")
	return data["id"], data["name"]


def search_folders(drive, query: str) -> List[Tuple[str, str]]:
	results: List[Tuple[str, str]] = []
	page_token = None
	q = (
		"mimeType='application/vnd.google-apps.folder' and "
		f"name contains '{query.replace("'", "\\'")}' and trashed=false"
	)
	while True:
		resp = (
			drive.files()
			.list(
				q=q,
				corpora="allDrives",
				fields="nextPageToken, files(id, name)",
				includeItemsFromAllDrives=True,
				pageSize=50,
				supportsAllDrives=True,
				pageToken=page_token,
			)
			.execute()
		)
		for f in resp.get("files", []):
			results.append((f["id"], f["name"]))
		page_token = resp.get("nextPageToken")
		if not page_token:
			break
	return results


def select_folder(drive) -> Tuple[str, str]:
	while True:
		print("Select a folder:")
		print("1) Enter folder ID")
		print("2) Search by folder name")
		choice = prompt_choice("Choose 1 or 2: ", ["1", "2"])

		if choice == "1":
			folder_id = input("Folder ID: ").strip()
			if not folder_id:
				print("Folder ID is required.")
				continue
			try:
				return get_folder_metadata_any_drive(drive, folder_id)
			except Exception as exc:
				print(f"Could not access folder: {exc}")
				continue

		query = input("Folder name (partial ok): ").strip()
		if not query:
			print("Folder name is required.")
			continue
		results = search_folders(drive, query)
		if not results:
			print("No folders found. Try again.")
			continue

		for idx, (_, name) in enumerate(results, start=1):
			print(f"{idx}) {name}")

		while True:
			selection = input("Select a folder number or 'r' to retry: ").strip().lower()
			if selection == "r":
				break
			if selection.isdigit():
				index = int(selection)
				if 1 <= index <= len(results):
					folder_id, folder_name = results[index - 1]
					return folder_id, folder_name
			print("Invalid selection.")


def list_folder_contents(drive, folder_id: str) -> List[dict]:
	items: List[dict] = []
	page_token = None
	while True:
		resp = (
			drive.files()
			.list(
				q=f"'{folder_id}' in parents and trashed=false",
				corpora="allDrives",
				fields="nextPageToken, files(id, name, mimeType)",
				includeItemsFromAllDrives=True,
				pageSize=1000,
				supportsAllDrives=True,
				pageToken=page_token,
			)
			.execute()
		)
		items.extend(resp.get("files", []))
		page_token = resp.get("nextPageToken")
		if not page_token:
			break
	return items


def gather_files(
	drive,
	root_id: str,
	root_name: str,
	include_subfolders: bool,
) -> List[FileEntry]:
	files: List[FileEntry] = []
	queue: List[Tuple[str, str]] = [(root_id, root_name)]

	while queue:
		folder_id, folder_path = queue.pop(0)
		items = list_folder_contents(drive, folder_id)
		for item in items:
			mime_type = item.get("mimeType", "")
			if mime_type == "application/vnd.google-apps.folder":
				if include_subfolders:
					queue.append((item["id"], f"{folder_path}/{item['name']}"))
				continue
			files.append(
				FileEntry(
					file_id=item["id"],
					name=item["name"],
					parent_id=folder_id,
					parent_path=folder_path,
				)
			)
	return files


def split_name(name: str) -> Tuple[str, str]:
	if name.startswith(".") and name.count(".") == 1:
		return name, ""
	if "." in name:
		base, ext = name.rsplit(".", 1)
		return base, ext
	return name, ""


def build_new_name(name: str) -> Tuple[Optional[str], str]:
	base, ext = split_name(name)
	if "%" not in base and "%" not in ext:
		return None, "skipped"
	if "%" in ext:
		return None, "percent_in_extension"
	if "%" not in base:
		return None, "skipped"
	new_base = base.replace("%", "P")
	if new_base == base:
		return None, "skipped"
	if ext:
		return f"{new_base}.{ext}", "rename"
	return new_base, "rename"


def plan_renames(entries: List[FileEntry]) -> List[RenamePlan]:
	existing_by_folder: Dict[str, set] = {}
	for entry in entries:
		existing_by_folder.setdefault(entry.parent_id, set()).add(entry.name)

	planned_targets_by_folder: Dict[str, set] = {}
	plans: List[RenamePlan] = []

	for entry in entries:
		new_name, status = build_new_name(entry.name)

		if status == "rename" and new_name:
			existing = existing_by_folder.get(entry.parent_id, set())
			planned_targets = planned_targets_by_folder.setdefault(entry.parent_id, set())

			if new_name in existing and new_name != entry.name:
				plans.append(
					RenamePlan(
						entry,
						new_name,
						"skipped",
						"conflict: name already exists",
					)
				)
				continue

			if new_name in planned_targets:
				plans.append(
					RenamePlan(
						entry,
						new_name,
						"skipped",
						"conflict: duplicate target name",
					)
				)
				continue

			planned_targets.add(new_name)
			plans.append(RenamePlan(entry, new_name, "pending", "ready"))
			continue

		if status == "percent_in_extension":
			plans.append(
				RenamePlan(
					entry,
					None,
					"skipped",
					"percent in extension; not modified",
				)
			)
		else:
			plans.append(RenamePlan(entry, None, "skipped", "no percent in name"))

	return plans


def print_preview(plans: List[RenamePlan]):
	print("Preview:")
	for plan in plans:
		original = plan.entry.name
		new_name = plan.new_name or "-"
		status = plan.status if plan.status != "pending" else "will rename"
		print(f"[{status}] {original} -> {new_name} ({plan.message})")


def confirm(prompt: str) -> bool:
	value = input(f"{prompt} [y/N]: ").strip().lower()
	return value in ("y", "yes")


def execute_renames(drive, plans: List[RenamePlan]) -> List[RenamePlan]:
	results: List[RenamePlan] = []
	for plan in plans:
		if plan.status != "pending" or not plan.new_name:
			results.append(plan)
			continue
		try:
			(
				drive.files()
				.update(
					fileId=plan.entry.file_id,
					body={"name": plan.new_name},
					supportsAllDrives=True,
				)
				.execute()
			)
			results.append(
				RenamePlan(
					plan.entry,
					plan.new_name,
					"success",
					"renamed",
				)
			)
		except HttpError as exc:
			results.append(
				RenamePlan(
					plan.entry,
					plan.new_name,
					"failure",
					f"api error: {exc}",
				)
			)
		except Exception as exc:
			results.append(
				RenamePlan(
					plan.entry,
					plan.new_name,
					"failure",
					f"error: {exc}",
				)
			)
	return results


def print_results(plans: List[RenamePlan]):
	print("Results:")
	for plan in plans:
		original = plan.entry.name
		new_name = plan.new_name or "-"
		print(f"[{plan.status}] {original} -> {new_name} ({plan.message})")


def main():
	try:
		drive = build_drive_service()
	except Exception as exc:
		print(f"Failed to initialize Drive API: {exc}")
		sys.exit(1)

	try:
		folder_id, folder_name = select_folder(drive)
	except Exception as exc:
		print(f"Could not select folder: {exc}")
		sys.exit(1)

	include_subfolders = confirm("Include subfolders")

	try:
		entries = gather_files(drive, folder_id, folder_name, include_subfolders)
	except HttpError as exc:
		print(f"Drive API error while listing files: {exc}")
		sys.exit(1)
	except Exception as exc:
		print(f"Error while listing files: {exc}")
		sys.exit(1)

	if not entries:
		print("No files found in the selected folder.")
		return

	plans = plan_renames(entries)
	print_preview(plans)

	rename_count = sum(1 for plan in plans if plan.status == "pending")
	if rename_count == 0:
		print("No files need renaming.")
		return

	if not confirm(f"Proceed with renaming {rename_count} file(s)"):
		print("Cancelled.")
		return

	results = execute_renames(drive, plans)
	print_results(results)


if __name__ == "__main__":
	main()
