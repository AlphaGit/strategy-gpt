import logging

# ANSI escape codes for colors
class ColorCodes:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    RESET = "\033[0m"

class CustomFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)

    def format(self, record):
        # Apply colors based on the log level
        if record.levelno == logging.DEBUG:
            color = ColorCodes.GREEN
        elif record.levelno == logging.INFO:
            color = ColorCodes.BLUE
        elif record.levelno == logging.WARNING:
            color = ColorCodes.YELLOW
        elif record.levelno >= logging.ERROR:
            color = ColorCodes.RED
        else:
            color = ColorCodes.RESET

        record.levelname = f"[{color}{record.levelname}{ColorCodes.RESET}]"

        formatted_msg = super().format(record)

        message_parts = formatted_msg.split(" - ", 1)
        lines_in_message = message_parts[1].splitlines()
        formatted_msg = message_parts[0] + "\n    " + "\n    ".join(lines_in_message)

        return formatted_msg

logger = logging.getLogger("strategy-gpt")

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(CustomFormatter("%(levelname)s %(asctime)s %(filename)s/%(funcName)s() - %(message)s"))
logger.addHandler(stream_handler)

logger.info("Logging initialized")