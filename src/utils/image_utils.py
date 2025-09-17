import requests
from PIL import Image, ImageEnhance, ImageOps
from io import BytesIO
import os
import logging
import hashlib
import tempfile
import subprocess

logger = logging.getLogger(__name__)

def get_image(image_url):
    response = requests.get(image_url)
    img = None
    if 200 <= response.status_code < 300 or response.status_code == 304:
        img = Image.open(BytesIO(response.content))
    else:
        logger.error(f"Received non-200 response from {image_url}: status_code: {response.status_code}")
    return img

def change_orientation(image, orientation, inverted=False):
    if orientation == 'horizontal':
        angle = 0
    elif orientation == 'vertical':
        angle = 90

    if inverted:
        angle = (angle + 180) % 360

    return image.rotate(angle, expand=1)

def resize_image(image, desired_size, image_settings=None, orientation="horizontal", background=(255, 255, 255)):
    """
    Resize/crop an image to desired_size with optional smart behavior.
    
    image_settings flags:
      - "keep-width": keep full width, crop height only
      - "smart-orientation": apply orientation-aware rules:
          orientation="horizontal":
            * portrait  -> letterbox (no crop)
            * landscape/square -> crop to fill
          orientation="vertical":
            * portrait  -> crop just enough to fill
            * landscape/square -> stretch (distort) to fill
    """
    if image_settings is None:
        image_settings = []

    desired_width, desired_height = map(int, desired_size)
    img_w, img_h = image.size
    is_landscape_or_square = img_w >= img_h
    is_portrait = not is_landscape_or_square

    # ---- 1) Legacy behavior: keep-width ----
    if "keep-width" in image_settings:
        # Keep full width; crop vertically as needed, then resize
        desired_ratio = desired_width / desired_height
        new_height = int(img_w / desired_ratio)
        # Centered vertical crop; clamp to image bounds
        y_offset = max(0, (img_h - new_height) // 2)
        y_end = min(img_h, y_offset + new_height)
        cropped = image.crop((0, y_offset, img_w, y_end))
        return cropped.resize((desired_width, desired_height), Image.LANCZOS)

    # ---- 2) Smart orientation-aware behavior ----
    if "smart-orientation" in image_settings:
        if orientation == "horizontal":
            if is_portrait:
                # Letterbox: preserve aspect, no crop
                fitted = ImageOps.contain(image, (desired_width, desired_height), method=Image.LANCZOS)
                canvas = Image.new("RGB", (desired_width, desired_height), background)
                x = (desired_width - fitted.width) // 2
                y = (desired_height - fitted.height) // 2
                canvas.paste(fitted, (x, y))
                return canvas
            else:
                # Crop to fill (centered)
                return ImageOps.fit(image, (desired_width, desired_height), method=Image.LANCZOS, centering=(0.5, 0.5))

        else:  # orientation == "vertical"
            if is_portrait:
                # Crop just enough to fill (centered); typically a slight crop
                return ImageOps.fit(image, (desired_width, desired_height), method=Image.LANCZOS, centering=(0.5, 0.5))
            else:
                # Stretch landscape to fill portrait (non-proportional)
                return image.resize((desired_width, desired_height), Image.LANCZOS)

    # ---- 3) Default behavior (simple, robust): crop to fill ----
    return ImageOps.fit(image, (desired_width, desired_height), method=Image.LANCZOS, centering=(0.5, 0.5))

def apply_image_enhancement(img, image_settings={}):

    # Apply Brightness
    img = ImageEnhance.Brightness(img).enhance(image_settings.get("brightness", 1.0))

    # Apply Contrast
    img = ImageEnhance.Contrast(img).enhance(image_settings.get("contrast", 1.0))

    # Apply Saturation (Color)
    img = ImageEnhance.Color(img).enhance(image_settings.get("saturation", 1.0))

    # Apply Sharpness
    img = ImageEnhance.Sharpness(img).enhance(image_settings.get("sharpness", 1.0))

    return img

def compute_image_hash(image):
    """Compute SHA-256 hash of an image."""
    image = image.convert("RGB")
    img_bytes = image.tobytes()
    return hashlib.sha256(img_bytes).hexdigest()

def take_screenshot_html(html_str, dimensions, timeout_ms=None):
    image = None
    try:
        # Create a temporary HTML file
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as html_file:
            html_file.write(html_str.encode("utf-8"))
            html_file_path = html_file.name

        image = take_screenshot(html_file_path, dimensions, timeout_ms)

        # Remove html file
        os.remove(html_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image

def take_screenshot(target, dimensions, timeout_ms=None):
    image = None
    try:
        # Create a temporary output file for the screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_file:
            img_file_path = img_file.name

        command = [
            "chromium-headless-shell",
            target,
            "--headless",
            f"--screenshot={img_file_path}",
            f"--window-size={dimensions[0]},{dimensions[1]}",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--use-gl=swiftshader",
            "--hide-scrollbars",
            "--in-process-gpu",
            "--js-flags=--jitless",
            "--disable-zero-copy",
            "--disable-gpu-memory-buffer-compositor-resources",
            "--disable-extensions",
            "--disable-plugins",
            "--mute-audio",
            "--no-sandbox"
        ]
        if timeout_ms:
            command.append(f"--timeout={timeout_ms}")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Check if the process failed or the output file is missing
        if result.returncode != 0 or not os.path.exists(img_file_path):
            logger.error("Failed to take screenshot:")
            logger.error(result.stderr.decode('utf-8'))
            return None

        # Load the image using PIL
        with Image.open(img_file_path) as img:
            image = img.copy()

        # Remove image files
        os.remove(img_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image
