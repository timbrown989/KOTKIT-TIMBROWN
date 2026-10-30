#!/usr/bin/env python3
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = "https://open.tiktokapis.com"
OAUTH_URL = f"{API_BASE}/v2/oauth/token/"
MAX_VIDEO_SIZE = 4 * 1024**3
CHUNK_SIZE = 10 * 1024**2
SUPPORTED_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class TikTokError(RuntimeError):
    pass


def request_json(url, *, method="POST", payload=None, headers=None, form=False):
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    if payload is None:
        body = None
    elif form:
        body = urllib.parse.urlencode(payload).encode()
    else:
        body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = response.read()
    except urllib.error.HTTPError as error:
        response_body = error.read()
        details = error_message(response_body)
        raise TikTokError(
            f"TikTok request failed (HTTP {error.code}): {details}"
        ) from error
    except urllib.error.URLError as error:
        raise TikTokError(f"Could not reach TikTok: {error.reason}") from error

    try:
        result = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TikTokError("TikTok returned an invalid JSON response.") from error
    if not isinstance(result, dict):
        raise TikTokError("TikTok returned an unexpected response.")
    api_error = result.get("error")
    if isinstance(api_error, dict) and api_error.get("code") not in (None, "ok"):
        raise TikTokError(f"TikTok API error: {error_message(response_body)}")
    return result


def error_message(response_body):
    try:
        result = json.loads(response_body)
        error = result.get("error", {})
        if isinstance(error, str):
            message = result.get("error_description")
            return f"{error}: {message}" if message else error
        if isinstance(error, dict):
            message = error.get("message") or error.get("error_description")
            code = error.get("code") or error.get("error")
            if message and code:
                return f"{code}: {message}"
            if message or code:
                return str(message or code)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    return "TikTok did not accept the request."


def added_videos():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise TikTokError("GITHUB_EVENT_PATH is not available.")
    event = json.loads(Path(event_path).read_text())
    before = event.get("before", "")
    after = event.get("after", "")
    if not after:
        raise TikTokError("The push event is missing its after commit.")

    if before and set(before) != {"0"}:
        command = [
            "git",
            "diff",
            "--no-renames",
            "--diff-filter=A",
            "--name-only",
            "-z",
            before,
            after,
        ]
    else:
        command = ["git", "ls-tree", "-r", "--name-only", "-z", after]
    result = subprocess.run(command, check=True, capture_output=True)
    paths = [
        Path(os.fsdecode(path))
        for path in result.stdout.split(b"\0")
        if path and Path(os.fsdecode(path)).suffix.lower() in SUPPORTED_TYPES
        and os.fsdecode(path).startswith("videos/")
    ]
    return paths


def refresh_tokens():
    client_key = os.environ["TIKTOK_CLIENT_KEY"]
    client_secret = os.environ["TIKTOK_CLIENT_SECRET"]
    refresh_token = os.environ["TIKTOK_REFRESH_TOKEN"]
    response = request_json(
        OAUTH_URL,
        payload={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        form=True,
    )
    data = response.get("data", response)
    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    if not access_token or not new_refresh_token:
        raise TikTokError("TikTok's refresh response did not include both tokens.")

    updater_token = os.environ.get("TIKTOK_SECRETS_UPDATE_TOKEN")
    if updater_token:
        update_github_secrets(updater_token, access_token, new_refresh_token)
    elif new_refresh_token != refresh_token:
        raise TikTokError(
            "TikTok rotated the refresh token, but TIKTOK_SECRETS_UPDATE_TOKEN is "
            "not set. No video was posted. Complete OAuth again and replace "
            "TIKTOK_ACCESS_TOKEN and TIKTOK_REFRESH_TOKEN, or add a fine-grained "
            "token with repository Secrets: write permission as "
            "TIKTOK_SECRETS_UPDATE_TOKEN."
        )
    print("TikTok access token refreshed.")
    return access_token


def update_github_secrets(token, access_token, refresh_token):
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise TikTokError("GITHUB_REPOSITORY is not available to update secrets.")
    command_env = os.environ.copy()
    command_env["GH_TOKEN"] = token
    for name, value in (
        ("TIKTOK_ACCESS_TOKEN", access_token),
        ("TIKTOK_REFRESH_TOKEN", refresh_token),
    ):
        try:
            result = subprocess.run(
                ["gh", "secret", "set", name, "--repo", repository],
                input=value,
                text=True,
                capture_output=True,
                env=command_env,
                check=False,
            )
        except FileNotFoundError as error:
            raise TikTokError("GitHub CLI is unavailable; could not update secrets.") from error
        if result.returncode:
            raise TikTokError(
                f"Could not update {name} in repository secrets. Check that "
                "TIKTOK_SECRETS_UPDATE_TOKEN has repository Secrets: write permission."
            )
    print("Updated TikTok token secrets in GitHub.")


def api_post(path, payload, access_token):
    response = request_json(
        f"{API_BASE}{path}",
        payload=payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise TikTokError("TikTok returned an unexpected API response.")
    return data


def upload_video(path, access_token):
    size = path.stat().st_size
    if size == 0:
        raise TikTokError(f"{path} is empty.")
    if size > MAX_VIDEO_SIZE:
        raise TikTokError(f"{path} exceeds TikTok's 4 GB video size limit.")
    chunk_size = min(CHUNK_SIZE, size)
    chunk_count = math.ceil(size / chunk_size)
    title = path.stem[:1500]
    init = api_post(
        "/v2/post/publish/video/init/",
        {
            "post_info": {
                "title": title,
                "privacy_level": "SELF_ONLY",
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        },
        access_token,
    )
    publish_id = init.get("publish_id")
    upload_url = init.get("upload_url")
    if not publish_id or not upload_url:
        raise TikTokError("TikTok did not return a publish ID and upload URL.")

    content_type = SUPPORTED_TYPES[path.suffix.lower()]
    with path.open("rb") as video:
        for index in range(chunk_count):
            start = index * chunk_size
            chunk = video.read(min(chunk_size, size - start))
            end = start + len(chunk) - 1
            request = urllib.request.Request(
                upload_url,
                data=chunk,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Type": content_type,
                },
                method="PUT",
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    if response.status not in (200, 201, 206):
                        raise TikTokError(
                            f"TikTok rejected upload chunk {index + 1} "
                            f"(HTTP {response.status})."
                        )
            except urllib.error.HTTPError as error:
                raise TikTokError(
                    f"TikTok rejected upload chunk {index + 1} (HTTP {error.code})."
                ) from error
            except urllib.error.URLError as error:
                raise TikTokError(
                    f"Could not upload chunk {index + 1}: {error.reason}"
                ) from error

    return publish_id


def publish_status(publish_id, access_token):
    data = api_post(
        "/v2/post/publish/status/fetch/",
        {"publish_id": publish_id},
        access_token,
    )
    return data.get("status", "UNKNOWN")


def report(publish_id, status):
    print(f"publish_id={publish_id} status={status}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(f"- TikTok publish ID: `{publish_id}` -- status: `{status}`\n")


def main():
    required = (
        "TIKTOK_CLIENT_KEY",
        "TIKTOK_CLIENT_SECRET",
        "TIKTOK_ACCESS_TOKEN",
        "TIKTOK_REFRESH_TOKEN",
    )
    if any(not os.environ.get(name) for name in required):
        raise TikTokError("One or more required TikTok repository secrets are missing.")

    videos = added_videos()
    if not videos:
        print("No newly added supported videos found; nothing to post.")
        return
    access_token = refresh_tokens()

    for path in videos:
        print(f"Uploading {path} (SELF_ONLY).")
        publish_id = upload_video(path, access_token)
        deadline = time.monotonic() + 600
        status = "UNKNOWN"
        while time.monotonic() < deadline:
            status = publish_status(publish_id, access_token)
            report(publish_id, status)
            if status in ("PUBLISH_COMPLETE", "FAILED"):
                break
            time.sleep(10)
        if status == "FAILED":
            raise TikTokError(f"TikTok reported FAILED for publish ID {publish_id}.")
        if status != "PUBLISH_COMPLETE":
            raise TikTokError(
                f"Timed out waiting for publish ID {publish_id}; latest status: {status}."
            )


if __name__ == "__main__":
    try:
        main()
    except (
        KeyError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        TikTokError,
    ) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
