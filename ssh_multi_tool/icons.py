"""Bundled application identity and icons for source, wheels and frozen builds."""
import base64
from importlib.resources import files
import tkinter as tk

WINDOW_CLASS = 'Sshmultitool'


def apply_window_icon(root):
    assets = files('ssh_multi_tool').joinpath('assets')
    images = [tk.PhotoImage(master=root, data=base64.b64encode(assets.joinpath(f'app-icon-{size}.png').read_bytes()))
              for size in (16, 32, 48, 64, 128, 256)]
    root.iconphoto(True, *images)
    # Release Tcl image handles on the UI thread, even when callers destroy the root directly.
    root.bind("<Destroy>", lambda event: images.clear() if event.widget is root else None, add="+")
    return images
