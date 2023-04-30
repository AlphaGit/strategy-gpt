import logging

# ANSI escape codes for colors
class ColorCodes:
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"

class CustomFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)

    def format(self, record):
        # Apply colors based on the log level
        if record.levelno == logging.DEBUG:
            color = ColorCodes.GREEN
        elif record.levelno == logging.WARNING:
            color = ColorCodes.YELLOW
        elif record.levelno >= logging.ERROR:
            color = ColorCodes.RED
        else:
            color = ColorCodes.RESET

        # Add color to the level name
        record.levelname = f"{color}{record.levelname}{ColorCodes.RESET}"

        # Call the parent class's format method
        formatted_msg = super().format(record)

        # Indent every line of the message portion message
        message_parts = formatted_msg.split(" - ")
        lines_in_message = message_parts[1].splitlines()
        formatted_msg = message_parts[0] + "\n" + "\n    ".join(lines_in_message)

        return formatted_msg

logger = logging.getLogger("strategy-gpt")
logger.setLevel(logging.DEBUG)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(CustomFormatter("%(levelname)s %(asctime)s - %(message)s"))
logger.addHandler(stream_handler)

logger.debug("Logger initialized")