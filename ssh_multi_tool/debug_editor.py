"""Edit four local debugging shortcuts shared by all targets."""
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .settings import debug_preferences, debug_slots
from .ui import apply_theme, style_text


class DebugSlotsEditor(tk.Toplevel):
    def __init__(self, parent, slots, on_save):
        super().__init__(parent)
        self.title('Debug Window Slots'); self.geometry('780x510'); self.minsize(650, 460)
        self.transient(parent); self.grab_set(); self.on_save = on_save; apply_theme(self)
        slots = debug_slots(debug_preferences(slots))
        self.names = []; self.commands = []
        body = ttk.Frame(self, padding=16); body.pack(fill='both', expand=True)
        for column in range(2): body.columnconfigure(column, weight=1)
        for row in (1, 2): body.rowconfigure(row, weight=1)
        ttk.Label(body, text='四个槽位供所有目标共用。启动时先执行当前配置的公共初始化，再运行下方命令。\n命令留空表示不使用该槽位；保存不会修改启动配置文件。',
                  wraplength=720, style='Muted.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        for index, slot in enumerate(slots):
            frame = ttk.LabelFrame(body, text=f'槽位 {index + 1}', padding=10)
            frame.grid(row=1 + index // 2, column=index % 2, sticky='nsew', padx=4, pady=4)
            frame.columnconfigure(1, weight=1); frame.rowconfigure(2, weight=1)
            name = tk.StringVar(value=slot['name']); self.names.append(name)
            ttk.Label(frame, text='名称').grid(row=0, column=0, padx=(0, 8))
            ttk.Entry(frame, textvariable=name).grid(row=0, column=1, sticky='ew')
            ttk.Label(frame, text='调试命令 · Bash 片段').grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 4))
            command = ScrolledText(frame, height=4, width=30, wrap='none', undo=True, font=('TkFixedFont', 11))
            style_text(command, code=True)
            command.grid(row=2, column=0, columnspan=2, sticky='nsew')
            command.insert('1.0', slot['commands']); self.commands.append(command)
        footer = ttk.Frame(body); footer.grid(row=3, column=0, columnspan=2, sticky='e', pady=(12, 0))
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=4)
        ttk.Button(footer, text='保存', command=self._save, style='Primary.TButton').pack(side='right', padx=4)
        self.bind('<Control-s>', lambda event: self._save())

    def _save(self):
        slots = [{'name': name.get().strip(), 'commands': command.get('1.0', 'end-1c')}
                 for name, command in zip(self.names, self.commands)]
        try:
            debug_preferences(slots)
            self.on_save(slots)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Error - SSH Multi Terminal', str(exc), parent=self); return False
        self.destroy(); return True
