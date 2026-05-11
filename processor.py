import os
import io
import json
import paramiko
import warnings
import re
import google.auth

from pathlib import Path
from dotenv import load_dotenv
from PIL import Image
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv(Path(__file__).parent / ".env")

Image.MAX_IMAGE_PIXELS = None
warnings.filterwarnings('ignore', category=Image.DecompressionBombWarning)

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets'
]

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff'}
VIDEO_EXTS = {'mp4', 'mov', 'avi', 'mkv', 'flv', 'wmv', 'webm', 'm4v'}


def get_services():
    creds, _ = google.auth.default(scopes=SCOPES)
    drive = build('drive', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, sheets


def get_sftp():
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.exists(known_hosts):
        ssh.load_host_keys(known_hosts)
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(
        os.getenv("HOSTINGER_HOST"),
        port=int(os.getenv("HOSTINGER_PORT", 65002)),
        username=os.getenv("HOSTINGER_USER"),
        key_filename=os.path.expanduser(os.getenv("HOSTINGER_SSH_KEY", "~/.ssh/sofi_etl"))
    )
    sftp = ssh.open_sftp()
    return ssh, sftp


def compress_image(image_path: Path, max_size_kb: float = 200) -> Path:
    """Compress image to target size in KB. Returns final path."""
    try:
        target_bytes = max_size_kb * 1024
        original_size = os.path.getsize(image_path)

        img = Image.open(image_path)

        # Convert to RGB
        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Scale down large images first (max 1200px for ~200kb target)
        max_dim = 1200
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)

        output_path = image_path.with_suffix('.jpg')

        # Aggressive compression - try quality + scaling combos
        for quality in [75, 60, 45, 30, 20]:
            img.save(output_path, 'JPEG', quality=quality, optimize=True)
            if os.path.getsize(output_path) <= target_bytes:
                break

        # Still too big? Scale down more aggressively
        current_img = img
        for scale in [0.7, 0.5, 0.35, 0.25, 0.15]:
            if os.path.getsize(output_path) <= target_bytes:
                break
            scaled = current_img.resize(
                (int(current_img.width * scale), int(current_img.height * scale)),
                Image.Resampling.LANCZOS
            )
            scaled.save(output_path, 'JPEG', quality=50, optimize=True)

        final_size = os.path.getsize(output_path)
        print(f"Compressed: {original_size//1024}KB -> {final_size//1024}KB")

        # Delete original if different
        if image_path != output_path and image_path.exists():
            image_path.unlink()

        return output_path
    except Exception as e:
        print(f"Compression error: {e}")
        return image_path


def ensure_remote_dir(sftp, path):
    """Create remote directory recursively."""
    current = ""
    for part in path.strip('/').split('/'):
        current += "/" + part
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def upload_file(sftp, local_path: Path, remote_dir: str, base_url: str) -> dict:
    """Upload single file and return URL info."""
    remote_path = f"{remote_dir}/{local_path.name}"
    sftp.put(str(local_path), remote_path)
    url = f"{base_url}/{local_path.name}"
    return {'filename': local_path.name, 'url': url}


def process_and_upload(drive, sftp, folder_id: str, filter_type: str,
                       image_max_mb: float, max_files: int, root_name: str,
                       one_pic_per_folder: bool = False) -> list:
    """Download, compress, upload each file immediately."""

    remote_dir = os.getenv("HOSTINGER_REMOTE_DIR")
    base_url = os.getenv("HOSTINGER_BASE_URL")
    if base_url:
        base_url = base_url.rstrip("/")
    temp_dir = Path("storage/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    ensure_remote_dir(sftp, remote_dir)

    uploaded = []
    counters = {'image': 0, 'video': 0}

    def process_folder(fid, folder_name):
        nonlocal uploaded, counters

        if max_files > 0 and len(uploaded) >= max_files:
            return

        items = drive.files().list(
            q=f"'{fid}' in parents and trashed = false",
            fields="files(id, name, mimeType)"
        ).execute().get('files', [])

        folder_image_uploaded = False

        for item in items:
            if max_files > 0 and len(uploaded) >= max_files:
                return

            item_id = item['id']
            item_name = item['name']
            mime_type = item.get('mimeType', '')

            # Recurse into subfolders
            if mime_type == 'application/vnd.google-apps.folder':
                process_folder(item_id, item_name.replace(' ', '-').lower())
                continue

            ext = item_name.lower().rsplit('.', 1)[-1] if '.' in item_name else ''

            # Check if file type matches filter
            is_image = ext in IMAGE_EXTS and filter_type in ['image', 'both']
            is_video = ext in VIDEO_EXTS and filter_type in ['video', 'both']

            if not is_image and not is_video:
                continue

            # Skip additional images in this folder if one_pic_per_folder is set
            if one_pic_per_folder and is_image and folder_image_uploaded:
                continue

            # Download to temp
            temp_file = temp_dir / item_name
            try:
                request = drive.files().get_media(fileId=item_id)
                with io.FileIO(temp_file, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
            except Exception as e:
                print(f"Download error: {e}")
                continue

            # Process and rename
            clean_name = folder_name.replace(' ', '-').lower()

            if is_image:
                counters['image'] += 1
                final_name = f"{clean_name}_{counters['image']}.jpg"
                final_path = temp_dir / final_name
                temp_file.rename(final_path)
                final_path = compress_image(final_path, image_max_mb * 1024)  # Convert MB to KB
            else:
                counters['video'] += 1
                final_name = f"{clean_name}_{counters['video']}.mp4"
                final_path = temp_dir / final_name
                temp_file.rename(final_path)

            # Upload immediately
            try:
                info = upload_file(sftp, final_path, remote_dir, base_url)
                uploaded.append(info)
                print(f"Uploaded: {info['filename']}")
                if is_image:
                    folder_image_uploaded = True
            except Exception as e:
                print(f"Upload error: {e}")
            finally:
                # Clean up local file
                if final_path.exists():
                    final_path.unlink()

    process_folder(folder_id, root_name)
    return uploaded


def write_to_sheets(sheets, sheet_id: str, sheet_name: str, anchor: str,
                    is_bulk: bool, uploaded_files: list, folder_name: str) -> bool:
    """Write results to Google Sheets. Returns True on success."""
    if not uploaded_files:
        return True

    # Parse anchor (e.g., "A" or "A3")
    anchor = anchor.upper()
    col = ''.join(c for c in anchor if c.isalpha())
    row = ''.join(c for c in anchor if c.isdigit())

    # Format data
    if is_bulk:
        images_dict = {"count": str(len(uploaded_files))}
        for i, f in enumerate(uploaded_files, 1):
            images_dict[str(i)] = f['url']
        rows = [[folder_name, json.dumps(images_dict, indent=2)]]
    else:
        rows = [[folder_name, f['url']] for f in uploaded_files]

    try:
        if is_bulk and row:
            # Bulk with specific cell - write directly to that cell
            sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"{sheet_name}!{col}{row}",
                valueInputOption="RAW",
                body={"values": rows}
            ).execute()
        else:
            # Find next empty row
            col_values = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"{sheet_name}!{col}:{col}"
            ).execute().get('values', [])

            sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"{sheet_name}!{col}{len(col_values) + 1}",
                valueInputOption="RAW",
                body={"values": rows}
            ).execute()
        return True
    except Exception as e:
        print(f"Sheets error: {e}")
        return False


def run_etl(folder_id: str, filter_type: str, sheet_id: str, sheet_name: str,
            anchor_column: str, is_bulk: bool, image_max_size_mb: float = 1.0,
            video_max_size_mb: float = 5.0, max_files: int = 0,
            one_pic_per_folder: bool = False):
    """Main ETL function - stream processing."""

    ssh = None
    sftp = None
    temp_dir = Path("storage/temp")
    try:
        # Validate
        if not folder_id or not re.match(r'^[a-zA-Z0-9_-]+$', folder_id):
            raise ValueError("Invalid folder ID")
        if filter_type not in ['image', 'video', 'both']:
            raise ValueError("Invalid filter type")

        # Get services
        drive, sheets = get_services()
        ssh, sftp = get_sftp()

        # Get folder name
        folder_name = drive.files().get(
            fileId=folder_id, fields="name"
        ).execute().get('name', 'untitled')

        print(f"Processing folder: {folder_name}")

        # Process and upload (streaming)
        uploaded = process_and_upload(
            drive, sftp, folder_id, filter_type,
            image_max_size_mb, max_files, folder_name, one_pic_per_folder
        )

        print(f"Uploaded {len(uploaded)} files")

        # Write to sheets
        sheets_ok = write_to_sheets(sheets, sheet_id, sheet_name, anchor_column,
                                    is_bulk, uploaded, folder_name)
        if not sheets_ok:
            print("Warning: Sheet write failed")

        print("Done!")

    except Exception as e:
        print(f"ETL Error: {e}")
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()
        if temp_dir.exists():
            for f in temp_dir.iterdir():
                f.unlink()
            temp_dir.rmdir()
