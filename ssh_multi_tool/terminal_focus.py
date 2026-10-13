"""Focus an existing GNOME Terminal tab through desktop accessibility."""
import ctypes


def focus_tree(api, pid, title):
    remaining = [512]
    def visit(node, frame=None, depth=0):
        if depth > 40 or remaining[0] <= 0: return False
        remaining[0] -= 1
        role = api.role(node)
        if role == 23: frame = node  # AT-SPI frame
        if role == 23 and api.name(node) == title: return bool(api.focus(node))
        if role in (37, 60) and api.name(node) == title:  # page tab / terminal
            selected = api.activate(node) or api.focus(node)
            raised = api.focus(frame) if frame is not None else selected
            return bool(selected and raised)
        if role in (37, 60): return False
        children = api.children(node)
        try:
            return any(visit(child, frame, depth + 1) for child in children)
        finally:
            for child in children: api.release(child)
    desktop = api.desktop()
    apps = api.children(desktop)
    try:
        matching = [app for app in apps if api.pid(app) == pid]
        def attempt(app):
            remaining[0] = 512
            return visit(app)
        if any(attempt(app) for app in matching): return True
        # The session and accessibility buses can live in different PID namespaces.
        return any(attempt(app) for app in apps if app not in matching and 'gnome-terminal' in api.name(app).lower())
    finally:
        for app in apps: api.release(app)
        api.release(desktop)


class NativeAccessibility:
    def __init__(self):
        self.lib = ctypes.CDLL('libatspi.so.0')
        self.gobject = ctypes.CDLL('libgobject-2.0.so.0'); self.glib = ctypes.CDLL('libglib-2.0.so.0')
        pointer = ctypes.c_void_p; integer = ctypes.c_int
        signatures = {
            'atspi_init': (integer, []), 'atspi_get_desktop': (pointer, [integer]),
            'atspi_accessible_get_child_count': (integer, [pointer, pointer]),
            'atspi_accessible_get_child_at_index': (pointer, [pointer, integer, pointer]),
            'atspi_accessible_get_name': (pointer, [pointer, pointer]),
            'atspi_accessible_get_role': (integer, [pointer, pointer]),
            'atspi_accessible_get_process_id': (ctypes.c_uint, [pointer, pointer]),
            'atspi_accessible_get_action_iface': (pointer, [pointer]),
            'atspi_accessible_get_parent': (pointer, [pointer, pointer]),
            'atspi_accessible_get_index_in_parent': (integer, [pointer, pointer]),
            'atspi_accessible_get_selection_iface': (pointer, [pointer]),
            'atspi_selection_select_child': (integer, [pointer, integer, pointer]),
            'atspi_accessible_get_component_iface': (pointer, [pointer]),
            'atspi_action_get_n_actions': (integer, [pointer, pointer]),
            'atspi_action_do_action': (integer, [pointer, integer, pointer]),
            'atspi_component_grab_focus': (integer, [pointer, pointer]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.lib, name); function.restype = result; function.argtypes = arguments
        self.gobject.g_object_unref.argtypes = [pointer]; self.glib.g_free.argtypes = [pointer]
        if self.lib.atspi_init() not in (0, 1): raise RuntimeError('桌面辅助功能不可用')
        if hasattr(self.lib, 'atspi_set_timeout'):
            self.lib.atspi_set_timeout.argtypes = [integer, integer]; self.lib.atspi_set_timeout(500, 1000)

    def desktop(self): return self.lib.atspi_get_desktop(0)
    def children(self, node):
        count = min(128, max(0, self.lib.atspi_accessible_get_child_count(node, None)))
        return [child for index in range(count) if (child := self.lib.atspi_accessible_get_child_at_index(node, index, None))]
    def pid(self, node): return self.lib.atspi_accessible_get_process_id(node, None)
    def role(self, node): return self.lib.atspi_accessible_get_role(node, None)
    def name(self, node):
        value = self.lib.atspi_accessible_get_name(node, None)
        if not value: return ''
        try: return ctypes.string_at(value).decode('utf-8', errors='replace')
        finally: self.glib.g_free(value)
    def activate(self, node):
        action = self.lib.atspi_accessible_get_action_iface(node)
        if action:
            try:
                if self.lib.atspi_action_get_n_actions(action, None) > 0 and self.lib.atspi_action_do_action(action, 0, None):
                    return True
            finally: self.release(action)
        parent = self.lib.atspi_accessible_get_parent(node, None)
        if not parent: return False
        selection = self.lib.atspi_accessible_get_selection_iface(parent)
        try:
            index = self.lib.atspi_accessible_get_index_in_parent(node, None)
            return bool(selection and index >= 0 and self.lib.atspi_selection_select_child(selection, index, None))
        finally:
            self.release(selection); self.release(parent)
    def focus(self, node):
        component = self.lib.atspi_accessible_get_component_iface(node)
        if not component: return False
        try: return bool(self.lib.atspi_component_grab_focus(component, None))
        finally: self.release(component)
    def release(self, node):
        if node: self.gobject.g_object_unref(node)


def focus_terminal(pid, title):
    try: return focus_tree(NativeAccessibility(), pid, title)
    except (OSError, RuntimeError, AttributeError): return False
