import fnmatch
import logging

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from display.mock_display import MockDisplay
from PIL import ImageColor

logger = logging.getLogger(__name__)

# Try to import hardware displays, but don't fail if they're not available
try:
    from display.inky_display import InkyDisplay
except ImportError:
    logger.info("Inky display not available, hardware support disabled")

try:
    from display.waveshare_display import WaveshareDisplay
except ImportError:
    logger.info("Waveshare display not available, hardware support disabled")

class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config):

        """
        Initializes the display manager and selects the correct display type 
        based on the configuration.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """
        
        self.device_config = device_config
     
        display_type = device_config.get_config("display_type", default="inky")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "inky":
            self.display = InkyDisplay(device_config)
        elif fnmatch.fnmatch(display_type, "epd*in*"):  
            # derived from waveshare epd - we assume here that will be consistent
            # otherwise we will have to enshring the manufacturer in the 
            # display_type and then have a display_model parameter.  Will leave
            # that for future use if the need arises.
            #
            # see https://github.com/waveshareteam/e-Paper
            self.display = WaveshareDisplay(device_config)
        else:
            raise ValueError(f"Unsupported display type: {display_type}")

    def display_image(self, image, photo_fit=None, backgroundColor=None):

        """
        Resize, enhance, and delegate rendering of an image to the active display.

        Args:
            image (PIL.Image): The image to be displayed.
            photo_fit (dict, optional): Fit configuration describing how the image
                should be resized (keys like "strategy" or "preserve").
            backgroundColor (str, optional): Hex color for any padding applied
                during contain/letterbox style fits.

        Raises:
            ValueError: If no valid display instance is found.
        """

        if not hasattr(self, "display"):
            raise ValueError("No valid display instance initialized.")

        bg_hex = backgroundColor or "#FFFFFF"
        try:
            background = ImageColor.getrgb(bg_hex)
        except ValueError:
            logger.warning(f"Invalid backgroundColor '{bg_hex}', defaulting to white")
            background = (255, 255, 255)

        orientation = self.device_config.get_config("orientation", default="horizontal")
        logger.debug("display_image applying orientation=%s", orientation)
        image = change_orientation(image, orientation)
        logger.debug("display_image post-orientation size=%s", image.size)

        fit_config = {}
        if isinstance(photo_fit, dict):
            candidate = photo_fit.get("fit") if isinstance(photo_fit.get("fit"), dict) else photo_fit
            for key in ("strategy", "preserve"):
                if key in candidate:
                    fit_config[key] = str(candidate[key]).lower()
        elif photo_fit:
            logger.warning("Unsupported photo_fit format %r; expected dict", photo_fit)

        logger.debug("display_image normalized fit_config=%s", fit_config)
        image = resize_image(
            image,
            self.device_config.get_resolution(),
            fit=fit_config,
            orientation=orientation,
            background=background,
        )
        logger.debug("display_image post-resize size=%s", image.size)

        if self.device_config.get_config("inverted_image"):
            image = image.rotate(180)
            logger.debug("display_image applied inversion rotation")

        image = apply_image_enhancement(
            image,
            self.device_config.get_config("image_settings", {}),
        )
        logger.debug("display_image post-enhancement size=%s", image.size)

        logger.info(f"Saving image to {self.device_config.current_image_file}")
        logger.debug(
            "display_image start | resolution=%s orientation=%s photo_fit=%s backgroundColor=%s",
            self.device_config.get_resolution(),
            self.device_config.get_config("orientation", default="horizontal"),
            photo_fit,
            backgroundColor,
        )
        image.save(self.device_config.current_image_file)

        self.display.display_image(image)
