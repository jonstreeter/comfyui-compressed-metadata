# comfyui-compressed-metadata: Custom nodes for saving/loading compressed workflow metadata in JPEGs.
# Short entrypoint: Imports and registers nodes from nodes.py. Exports js/ for auto-loading JS.

print("[Compressed Metadata] Loading node...")  # Debug

from .nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
)

WEB_DIRECTORY = "./js"  # Serves and auto-loads all .js files from js/ under /extensions/<node>/js/
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

print("[Compressed Metadata] Node loaded successfully with mappings:", list(NODE_CLASS_MAPPINGS.keys()))  # Debug