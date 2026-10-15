"""Shared desktop styles using standard Tk/ttk, including Python 3.8."""
from tkinter import font, ttk

COLORS = dict(canvas='#f3f6fa', surface='#ffffff', ink='#182b43', muted='#65768c',
              border='#dce4ee', primary='#2463eb', hover='#1d4ed8', danger='#be3045',
              success='#13795b', selection='#e7efff')


def apply_theme(root):
    style = ttk.Style(root)
    if 'clam' in style.theme_names(): style.theme_use('clam')
    families = font.families(root)
    family = 'Noto Sans CJK SC' if 'Noto Sans CJK SC' in families else font.nametofont('TkDefaultFont').actual('family')
    font.nametofont('TkDefaultFont').configure(family=family, size=10, weight='normal')
    c = COLORS
    root.configure(background=c['canvas'])
    style.configure('.', font=(family, 10), background=c['surface'], foreground=c['ink'])
    style.configure('TFrame', background=c['surface'])
    style.configure('App.TFrame', background=c['canvas'])
    style.configure('Card.TFrame', background=c['surface'], bordercolor=c['border'], relief='solid', borderwidth=1)
    style.configure('TLabel', background=c['surface'], foreground=c['ink'])
    style.configure('Title.TLabel', font=(family, 20, 'bold'), background=c['canvas'])
    style.configure('Subtitle.TLabel', foreground=c['muted'], background=c['canvas'], font=(family, 9))
    style.configure('Section.TLabel', font=(family, 12, 'bold'))
    style.configure('Muted.TLabel', foreground=c['muted'], font=(family, 9))
    style.configure('Badge.TLabel', foreground=c['primary'], background=c['selection'], padding=(10, 5), font=(family, 9))
    style.configure('TButton', padding=(8, 4), background=c['surface'], bordercolor=c['border'], borderwidth=1, relief='solid')
    style.map('TButton', background=[('disabled', '#f3f5f8'), ('pressed', c['selection']), ('active', '#edf3ff')],
              foreground=[('disabled', '#9aa8b8')], bordercolor=[('active', '#a5bdf5')])
    for name in ('Primary.TButton', 'Launch.TButton'):
        style.configure(name, foreground='white', background=c['primary'], bordercolor=c['primary'], padding=(14, 7))
        style.map(name, background=[('disabled', '#d8e0ed'), ('active', c['hover'])],
                  foreground=[('disabled', '#8998ab'), ('!disabled', 'white')])
    style.configure('Compact.TButton', padding=(6, 2), font=(family, 9))
    style.configure('TMenubutton', padding=(8, 4), background=c['surface'], bordercolor=c['border'], relief='flat')
    style.configure('Danger.TButton', foreground=c['danger'])
    style.map('Danger.TButton', foreground=[('disabled', '#9aa8b8'), ('!disabled', c['danger'])], background=[('active', '#fff0f2')])
    style.configure('TEntry', padding=5, fieldbackground=c['surface'], bordercolor=c['border'])
    style.configure('TCombobox', padding=5, fieldbackground=c['surface'], bordercolor=c['border'])
    style.map('TCombobox', fieldbackground=[('readonly', c['surface'])], foreground=[('readonly', c['ink'])])
    style.configure('TCheckbutton', background=c['surface'], padding=2)
    style.map('TCheckbutton', background=[('active', c['surface'])])
    style.configure('TLabelframe', background=c['surface'], bordercolor=c['border'])
    style.configure('TLabelframe.Label', background=c['surface'], foreground=c['ink'], font=(family, 10, 'bold'))
    style.configure('TNotebook', background=c['surface'], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', padding=(14, 5), background=c['canvas'])
    style.map('TNotebook.Tab', background=[('selected', c['surface'])], foreground=[('selected', c['primary'])])
    style.configure('Treeview', background=c['surface'], fieldbackground=c['surface'], foreground=c['ink'], rowheight=30, borderwidth=0)
    style.configure('Treeview.Heading', background='#eef3f9', foreground=c['muted'], padding=(8, 8), relief='flat', font=(family, 9, 'bold'))
    style.map('Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['primary'])])
    return c


def style_text(widget, *, code=False):
    widget.configure(background=COLORS['surface'], foreground=COLORS['ink'],
                     selectbackground=COLORS['selection'], selectforeground=COLORS['primary'],
                     highlightbackground=COLORS['border'], highlightcolor=COLORS['primary'], highlightthickness=1,
                     borderwidth=0, relief='flat')
    if code:
        widget.configure(insertbackground=COLORS['ink'], padx=10, pady=8,
                         font=(font.nametofont('TkFixedFont').actual('family'), 10))


def style_table(table):
    table.tag_configure('alternate', background='#f6f9fd')
    table.tag_configure('active', foreground=COLORS['success'])
    table.tag_configure('error', foreground=COLORS['danger'])
    table.tag_configure('muted', foreground=COLORS['muted'])


def row_tags(state, index):
    tags = ['alternate'] if index % 2 else []
    if state in {'running', 'interactive', 'succeeded'}: tags.append('active')
    elif state in {'failed', 'unknown'}: tags.append('error')
    else: tags.append('muted')
    return tuple(tags)
