"""Bundled application identity and icons for source, wheels and frozen builds."""
import base64
from pkgutil import get_data
import tkinter as tk

WINDOW_CLASS = 'Sshmultitool'


def apply_window_icon(root):
    images = [tk.PhotoImage(master=root, data=base64.b64encode(get_data('ssh_multi_tool', f'assets/app-icon-{size}.png')))
              for size in (16, 32, 48, 64, 128, 256)]
    root.iconphoto(True, *images)
    # Release Tcl image handles on the UI thread, even when callers destroy the root directly.
    root.bind("<Destroy>", lambda event: images.clear() if event.widget is root else None, add="+")
    return images
