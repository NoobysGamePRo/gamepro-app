"""
Readme viewer - shows a script's instructions over the video area.

A script's readme is a Markdown file next to it with the same name (FRLG_Breeding.py ->
FRLG_Breeding.md). Images are referenced relative to that file, e.g. ![Party](images/party.png).

Supported Markdown (kept small on purpose):
    # Heading / ## Heading / ### Heading
    paragraphs (consecutive lines are joined; a blank line ends a paragraph)
    - bullet / * bullet        1. numbered
    > tip or warning box       ---  horizontal rule
    **bold**   `code`   [link text](https://...)
    ![caption](images/file.png)   on its own line - scaled to fit, caption underneath
"""

import os
import re
import webbrowser
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

BG, BG2, BG3 = '#1a2d6b', '#0f1d4a', '#243580'
FG, FG2, ACCENT = '#ffffff', '#b8c4ff', '#cc0000'
TIP_BG = '#24396f'

_INLINE = re.compile(r'(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))')


def readme_path(script_path):
    """The readme for a script file, or None if it has none."""
    if not script_path:
        return None
    p = os.path.splitext(script_path)[0] + '.md'
    return p if os.path.isfile(p) else None


class ReadmeViewer(tk.Frame):
    """Scrollable Markdown view. Place it over the video panel; call show(path, title)."""

    def __init__(self, parent, width, height, on_close=None):
        super().__init__(parent, bg=BG2, width=width, height=height,
                         highlightthickness=2, highlightbackground=BG3)
        self.pack_propagate(False)
        self._on_close = on_close
        self._images = []                 # keep PhotoImage references alive
        self._img_w = width - 60

        bar = tk.Frame(self, bg=BG3)
        bar.pack(fill='x')
        self._title = tk.Label(bar, text='', bg=BG3, fg=FG, font=('Arial', 11, 'bold'),
                               anchor='w', padx=10, pady=5)
        self._title.pack(side='left', fill='x', expand=True)
        tk.Button(bar, text='✕  Close', command=self.close, bg=ACCENT, fg=FG,
                  activebackground='#e01b1b', activeforeground=FG, relief='flat',
                  font=('Arial', 9, 'bold'), padx=10, cursor='hand2').pack(side='right',
                                                                         padx=4, pady=3)

        body = tk.Frame(self, bg=BG2)
        body.pack(fill='both', expand=True)
        self._text = tk.Text(body, bg=BG2, fg=FG, wrap='word', relief='flat', borderwidth=0,
                             padx=18, pady=12, font=('Segoe UI', 10), cursor='arrow',
                             spacing1=2, spacing3=2)
        sb = ttk.Scrollbar(body, orient='vertical', command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._text.pack(side='left', fill='both', expand=True)
        self._text.bind('<MouseWheel>', lambda e: self._text.yview_scroll(int(-e.delta / 120), 'units'))
        self._styles()

    def _styles(self):
        t = self._text
        t.tag_configure('h1', font=('Segoe UI', 17, 'bold'), foreground=FG, spacing1=6, spacing3=6)
        t.tag_configure('h2', font=('Segoe UI', 13, 'bold'), foreground='#ff6b6b', spacing1=12,
                        spacing3=4)
        t.tag_configure('h3', font=('Segoe UI', 11, 'bold'), foreground=FG2, spacing1=8, spacing3=2)
        t.tag_configure('p', spacing3=6)
        t.tag_configure('li', lmargin1=14, lmargin2=32, spacing3=3)
        t.tag_configure('tip', background=TIP_BG, lmargin1=10, lmargin2=10, rmargin=10,
                        spacing1=6, spacing3=6, foreground='#ffe28a')
        t.tag_configure('bold', font=('Segoe UI', 10, 'bold'))
        t.tag_configure('code', font=('Consolas', 10), foreground='#9fe0ff', background='#16285e')
        t.tag_configure('link', foreground='#7fb2ff', underline=True)
        t.tag_configure('caption', font=('Segoe UI', 9, 'italic'), foreground=FG2,
                        justify='center', spacing3=10)
        t.tag_configure('img', justify='center', spacing1=6)
        t.tag_configure('rule', font=('Segoe UI', 4), foreground=BG3, justify='center')
        t.tag_bind('link', '<Enter>', lambda e: t.config(cursor='hand2'))
        t.tag_bind('link', '<Leave>', lambda e: t.config(cursor='arrow'))

    # ── Public ────────────────────────────────────────────────────────────────
    def show(self, path, title=''):
        self._title.config(text='📖  ' + title)
        try:
            with open(path, encoding='utf-8') as f:
                md = f.read()
        except OSError as e:
            md = f'Could not open the readme:\n\n`{e}`'
        self._render(md, os.path.dirname(path))
        self.lift()

    def close(self):
        self.place_forget()
        if self._on_close:
            self._on_close()

    def is_open(self):
        return bool(self.winfo_ismapped())

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render(self, md, base_dir):
        t = self._text
        t.config(state='normal')
        t.delete('1.0', 'end')
        self._images.clear()
        self._links = 0
        para = []

        def flush():
            if para:
                self._inline(' '.join(para), ('p',))
                t.insert('end', '\n')
                para.clear()

        for raw in md.splitlines():
            line = raw.rstrip()
            s = line.strip()
            m_img = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', s)
            if not s:
                flush()
            elif m_img:
                flush()
                self._image(os.path.join(base_dir, m_img.group(2)), m_img.group(1))
            elif s.startswith('#'):
                flush()
                level = min(len(s) - len(s.lstrip('#')), 3)
                self._inline(s.lstrip('#').strip(), ('h%d' % level,))
                t.insert('end', '\n')
            elif s in ('---', '***', '___'):
                flush()
                t.insert('end', '─' * 60 + '\n', ('rule',))
            elif s.startswith('>'):
                flush()
                self._inline(s.lstrip('>').strip(), ('tip',))
                t.insert('end', '\n', ('tip',))
            elif re.match(r'^[-*] ', s):
                flush()
                indent = '    ' * (len(line) - len(line.lstrip()) >= 2)
                self._inline(indent + '•  ' + s[2:], ('li',))
                t.insert('end', '\n')
            elif re.match(r'^\d+[.)] ', s):
                flush()
                n, rest = s.split(' ', 1)
                self._inline(n + '  ' + rest, ('li',))
                t.insert('end', '\n')
            else:
                para.append(s)
        flush()
        t.config(state='disabled')
        t.yview_moveto(0)

    def _inline(self, text, base):
        t = self._text
        for part in _INLINE.split(text):
            if not part:
                continue
            if part.startswith('**') and part.endswith('**'):
                t.insert('end', part[2:-2], base + ('bold',))
            elif part.startswith('`') and part.endswith('`'):
                t.insert('end', part[1:-1], base + ('code',))
            elif part.startswith('['):
                label, url = re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)', part).groups()
                self._links += 1
                tag = 'link%d' % self._links
                t.insert('end', label, base + ('link', tag))
                t.tag_bind(tag, '<Button-1>', lambda e, u=url: webbrowser.open(u))
            else:
                t.insert('end', part, base)

    def _image(self, path, caption):
        t = self._text
        try:
            im = Image.open(path)
            im.thumbnail((self._img_w, 700))
            photo = ImageTk.PhotoImage(im)
        except Exception:
            t.insert('end', f'[image missing: {os.path.basename(path)}]\n', ('caption',))
            return
        self._images.append(photo)
        t.insert('end', '\n', ('img',))
        idx = t.index('end-1c')
        t.image_create(idx, image=photo, padx=4, pady=2)
        t.tag_add('img', idx, idx + '+1c')                 # centres the image line
        t.insert('end', '\n', ('img',))
        if caption:
            t.insert('end', caption + '\n', ('caption',))
