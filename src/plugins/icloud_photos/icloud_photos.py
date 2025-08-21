import re
import json
import random
import io
import requests
import logging
from PIL import Image, ImageOps, ImageColor, UnidentifiedImageError
from plugins.base_plugin.base_plugin import BasePlugin

USER_AGENT = "InkyPi/iCloudPhotos/0.1"
DEFAULT_HEADERS = {"Content-Type": "text/plain", "User-Agent": USER_AGENT}
TIMEOUT = 30

SESSION = requests.Session()
SESSION.headers.update(DEFAULT_HEADERS)

logger = logging.getLogger(__name__)

# ---------------------------
# Module-level helper functions
# ---------------------------

def base62_decode(s):
    """
    Decode a base62-encoded string into an integer.
    Characters: 0-9, A-Z, a-z
    """
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    char_map = {c: i for i, c in enumerate(chars)}
    
    value = 0
    for char in s:
        if char not in char_map:
            raise ValueError(f"Invalid base62 character: {char}")
        value = value * 62 + char_map[char]
    return value

def get_stream_id(url):
    """Extract the stream ID from an iCloud shared album URL."""
    expected_prefix = "https://www.icloud.com/sharedalbum/#"
    if not url or not url.startswith(expected_prefix):
        raise RuntimeError("Please provide a full iCloud Shared Album URL, e.g. https://www.icloud.com/sharedalbum/#B2D...")

    stream_id = url.split("#", 1)[-1].strip()
    if not stream_id or not re.match(r"^[A-Za-z0-9]+$", stream_id):
        raise RuntimeError("The iCloud stream ID appears invalid. Double-check the URL.")
    logger.debug("Extracted stream_id=%s", stream_id)
    return stream_id


def get_partition(stream_id):
    """Compute the iCloud partition from the stream ID using base62."""
    enc = stream_id[1] if stream_id.startswith("A") else stream_id[1:3]
    part = base62_decode(enc)
    if part is None:
        raise RuntimeError("Could not compute iCloud partition from the Shared Album ID.")
    logger.debug("Computed partition=%s from enc=%s", part, enc)
    return part


def get_stream_contents(stream_id):
    """
    Fetch shared stream metadata and return:
      { photoGuid: checksum_of_largest_derivative }
    """
    url = f"https://p{get_partition(stream_id)}-sharedstreams.icloud.com/{stream_id}/sharedstreams/webstream"
    logger.debug("Fetching stream contents from %s", url)
    r = SESSION.post(url, data=json.dumps({"streamCtag": None}), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    photos = data.get("photos") or []
    logger.debug("Stream returned %d photo entries", len(photos))
    if not photos:
        raise RuntimeError("No photos found in the iCloud shared album.")

    guids = {
        item["photoGuid"]: max(item["derivatives"].values(), key=lambda d: int(d["width"]))["checksum"]
        for item in photos
        if item.get("derivatives")
    }
    if not guids:
        raise RuntimeError("No derivatives found for any photo in the stream.")
    return guids


def get_photo_url(stream_id, guid, checksum):
    """Resolve a downloadable URL for a photo (no expiry returned)."""
    url = f"https://p{get_partition(stream_id)}-sharedstreams.icloud.com/{stream_id}/sharedstreams/webasseturls"
    payload = {"photoGuids": [guid]}
    logger.debug("Resolving photo URL for guid=%s via %s", guid, url)

    r = SESSION.post(url, data=json.dumps(payload), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    item = (data.get("items") or {}).get(checksum)
    if not item:
        raise RuntimeError("Could not find matching checksum for asset in iCloud response.")

    url_location = item["url_location"]     # e.g. "cvws.icloud-content.com"
    url_path = item["url_path"]             # e.g. "/S/.../IMG_1234.JPG?...signed..."
    loc = (data.get("locations") or {}).get(url_location, {})
    scheme = loc.get("scheme", "https")
    hosts = loc.get("hosts") or [url_location]
    host = random.choice(hosts)

    download_url = f"{scheme}://{host}{url_path}"
    logger.debug("Resolved download URL host=%s location=%s", host, url_location)
    return download_url


# ---------------------------
# Plugin
# ---------------------------

class IcloudPhotos(BasePlugin):
    """
    InkyPi plugin that:
      1) Fetches the latest GUID->checksum map from iCloud
      2) Merges any new photos into a saved map in settings
      3) Picks a random photo not yet viewed; if all viewed, resets and picks again
      4) Downloads, fits to display, and returns the image
    """

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        return template_params

    def generate_image(self, settings, device_config):
        """
        Required by BasePlugin. Returns a PIL.Image to display.
        """
        album_url = (settings.get("album_url") or "").strip()
        if not album_url:
            raise RuntimeError("Missing album URL. Please set the iCloud Shared Album URL in plugin settings.")

        # 1) Fetch latest guid->checksum map
        stream_id = get_stream_id(album_url)
        latest_map = get_stream_contents(stream_id)

        # 2) Sync (preserve "viewed"; drop stale; add new)
        old = settings.get("photos") or {}
        saved = {
            guid: {
                "checksum": checksum,
                "viewed": (old.get(guid) or {}).get("viewed", False),
            }
            for guid, checksum in latest_map.items()
        }
        dirty = (saved != old)  # only persist if something actually changed
        if dirty:
            logger.debug("Detected %d new or updated photos", len(saved) - len(old))

        # 3) Choose an unviewed; if none, reset flags in one go
        unseen = [g for g, meta in saved.items() if not meta.get("viewed")]
        if not unseen:
            logger.info("All photos viewed; resetting flags.")
            saved = {g: {"checksum": meta["checksum"], "viewed": False} for g, meta in saved.items()}
            unseen = list(saved.keys())
            dirty = True

        if not unseen:
            raise RuntimeError("No photos available after refresh. Please check the album or network.")

        # 4) Pick random, mark viewed
        guid = random.choice(unseen)
        checksum = saved[guid]["checksum"]
        logger.info("Selected guid=%s (unseen remaining: %d, total online: %d)", guid, len(unseen), len(latest_map))

        photo_url = get_photo_url(stream_id, guid, checksum)
        if not saved[guid].get("viewed"):
            saved[guid]["viewed"] = True
            dirty = True

        # 5) Persist once at the end (only if changed)
        if dirty:
            settings["photos"] = saved
            logger.debug("Persisted state with %d photos", len(saved))

        # 6) Render
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        bg_hex = settings.get("backgroundColor", "#FFFFFF")
        bg_rgb = ImageColor.getrgb(bg_hex)

        img = self._download_and_fit(photo_url, dimensions, background=bg_rgb)
        return img

    def _download_and_fit(self, url, target_size, background=(255, 255, 255)):
        """
        Download image bytes and fit into target_size while preserving aspect ratio.
        Uses white letterboxing (common for e-ink).
        """
        logger.debug("Downloading image: %s", url)
        try:
            resp = SESSION.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download image: {e}") from e
        
        try:
            with Image.open(io.BytesIO(resp.content)) as im:
                im = im.convert("RGB")  # e-ink friendly
                canvas = Image.new("RGB", target_size, background)
                fitted = ImageOps.contain(im, target_size, method=Image.LANCZOS) 

                # center paste
                x = (canvas.width - fitted.width) // 2
                y = (canvas.height - fitted.height) // 2
                canvas.paste(fitted, (x, y))
                logger.debug("Pasted fitted image at (%d, %d) onto canvas %s", x, y, target_size)
                return canvas
        except UnidentifiedImageError as e:
            raise RuntimeError("Downloaded content is not a valid image format.") from e
