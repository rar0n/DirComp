"""
Optional dependency shim: send2trash is used by delete_selected (see
transfer_ops.py) to send files to the desktop trash instead of permanently
deleting them. It's optional -- if it's not installed, the app still runs;
Delete just raises a clear error instead of silently doing nothing.
"""

try:
    from send2trash import send2trash
    SEND2TRASH_AVAILABLE = True
except ImportError:
    SEND2TRASH_AVAILABLE = False

    def send2trash(path):  # type: ignore[no-redef]
        raise RuntimeError("The 'send2trash' package is not installed.")
