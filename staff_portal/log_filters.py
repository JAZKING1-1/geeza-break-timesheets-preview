import logging
import re


class RedactAccountLinks(logging.Filter):
    """Keep one-use account tokens out of HTTP access/error logs."""
    def filter(self, record):
        record.msg = re.sub(r"(/staff/set-password/)[A-Za-z0-9_-]+/", r"\1[redacted]/", record.getMessage())
        record.args = ()
        return True
