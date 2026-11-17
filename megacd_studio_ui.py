"""Interfaz adaptable de Mega CD Estudio. Los flujos de trabajo viven en la aplicación."""
from math import sqrt
from pathlib import Path
import os
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk


BG = "#111119"
PANEL = "#1c1b29"
BORDER = "#323045"
FIELD = "#151420"
TEXT = "#f4f0ff"
MUTED = "#b4aec9"
ACCENT = "#bdaaff"
MINT = "#81dfc5"
FONT = "Segoe UI"


def rounded_image(root, radius, fill, outline=None, backdrop=None):
    """Baldosa con esquinas transparentes para los elementos redimensionables de ttk."""
    size = radius * 2 + 3
    image = tk.PhotoImage(master=root, width=size, height=size)
    if backdrop:
        image.put(backdrop, to=(0, 0, size, size))

    def inset(row, corner):
        distance = max(0, corner - row, row - (size - 1 - corner))
        return round(corner - sqrt(corner * corner - distance * distance))

    for row in range(size):
        left = inset(row, radius)
        image.put(outline or fill, to=(left, row, size - left, row + 1))
        if outline and 0 < row < size - 1:
            inner = 1 + inset(row, radius)
            image.put(fill, to=(inner, row, size - inner, row + 1))
    return image


class StudioTheme:
    def __init__(self, root, px):
        self.root = root
        self.px = px
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.images = []
        s = self.style
        s.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
        s.configure("TFrame", background=BG, borderwidth=0)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("DockContent.TFrame", background="#1a1925")
        for style, background, color, font in (
            ("TLabel", PANEL, TEXT, (FONT, 10)),
            ("Title.TLabel", BG, TEXT, (FONT, 22, "bold")),
            ("BrandNote.TLabel", BG, MUTED, (FONT, 10)),
            ("CardTitle.TLabel", PANEL, TEXT, (FONT, 14, "bold")),
            ("Body.TLabel", PANEL, MUTED, (FONT, 10)),
            ("FieldLabel.TLabel", PANEL, TEXT, (FONT, 10, "bold")),
            ("Hint.TLabel", PANEL, "#efca89", (FONT, 9)),
            ("Count.TLabel", PANEL, MINT, (FONT, 10, "bold")),
            ("EmptyTitle.TLabel", FIELD, TEXT, (FONT, 13, "bold")),
            ("EmptyText.TLabel", FIELD, MUTED, (FONT, 10)),
            ("DockValue.TLabel", "#1a1925", MINT, (FONT, 10, "bold")),
        ):
            s.configure(style, background=background, foreground=color, font=font)

        for name, fill, border, hover, press, color, padding in (
            ("TButton", "#29273b", "#3c3855", "#37334d", "#302a47", TEXT, (13, 8)),
            ("Accent.TButton", ACCENT, ACCENT, "#d4c6ff", "#ab94f6", "#201536", (18, 10)),
            ("Dock.TButton", "#29273b", "#3c3855", "#37334d", "#302a47", TEXT, (13, 8)),
            ("Dock.Accent.TButton", ACCENT, ACCENT, "#d4c6ff", "#ab94f6", "#201536", (18, 10)),
            ("Tonal.TButton", "#3c305a", "#5a4785", "#4d3c72", "#473665", "#f0e7ff", (14, 8)),
            ("Quiet.TButton", PANEL, PANEL, "#302b44", "#2a263a", MUTED, (9, 7)),
            ("Nav.TButton", "#1a1925", "#1a1925", "#2b263e", "#322946", MUTED, (8, 8)),
            ("Active.Nav.TButton", "#3d315a", "#68568e", "#4b3a6d", "#423361", "#f5edff", (8, 8)),
        ):
            backdrop = "#1a1925" if "Nav" in name or "Dock" in name else PANEL
            self.button(name, fill, border, hover, press, color, padding, backdrop)

        for name in ("TEntry", "TCombobox"):
            s.configure(name, padding=(px(10), px(7)), foreground=TEXT,
                        fieldbackground=FIELD, background=FIELD, insertcolor=TEXT,
                        bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD,
                        selectbackground="#4b3c6c", selectforeground=TEXT,
                        arrowcolor=ACCENT, arrowsize=px(13))
            s.map(name, foreground=[("disabled", MUTED), ("readonly", TEXT)],
                  fieldbackground=[("readonly", FIELD), ("!disabled", FIELD)],
                  bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", FIELD)], darkcolor=[("focus", FIELD)],
                  background=[("active", "#292338"), ("readonly", FIELD)])

        self.checkbox()
        s.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                    borderwidth=0, relief="flat", rowheight=px(34), bordercolor=FIELD,
                    lightcolor=FIELD, darkcolor=FIELD)
        s.configure("Treeview.Heading", background="#252337", foreground=MUTED,
                    bordercolor="#252337", lightcolor="#252337", darkcolor="#252337",
                    relief="flat", padding=(px(9), px(7)), font=(FONT, 9, "bold"))
        s.map("Treeview", background=[("selected", "#4b3b6d")], foreground=[("selected", TEXT)])
        s.map("Treeview.Heading", background=[("active", "#252337")])
        for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            s.configure(name, arrowsize=px(11), borderwidth=0, gripcount=0,
                        troughcolor=BG, background="#49435e", bordercolor=BG,
                        arrowcolor=MUTED, lightcolor="#49435e", darkcolor="#49435e")
            s.map(name, background=[("active", "#685d85")])

        for name, element, color, backdrop in (
            ("Horizontal.TProgressbar", "StudioProgress", MINT, "#1a1925"),
            ("Estimate.Horizontal.TProgressbar", "StudioEstimate", ACCENT, PANEL),
        ):
            self.surface(element + "Track", 3, "#353146", backdrop=backdrop)
            image = rounded_image(root, px(3), color)
            self.images.append(image)
            s.element_create(element + ".pbar", "image", image,
                             border=px(3), padding=0, sticky="nswe")
            s.layout(name, [(element + "Track.surface", {"sticky": "nswe", "children": [
                (element + ".pbar", {"side": "left", "sticky": "ns"})]})])

        for key, value in (
            ("Background", FIELD), ("Foreground", TEXT), ("selectBackground", "#4b3c6c"),
            ("selectForeground", TEXT), ("Font", (FONT, 10)),
        ):
            root.option_add("*TCombobox*Listbox*" + key, value)

    def surface(self, name, radius, fill, border=None, states=(), backdrop=BG):
        radius = self.px(radius)
        images = [rounded_image(self.root, radius, fill, border, backdrop)]
        state_images = []
        for state, color, outline in states:
            img = rounded_image(self.root, radius, color, outline, backdrop)
            images.append(img)
            state_images.append((*state.split(), img))
        self.images.extend(images)
        self.style.element_create(name + ".surface", "image", images[0], *state_images,
                                  border=radius, padding=0, sticky="nswe")

    def button(self, name, fill, border, hover, press, color, padding, backdrop):
        element = "Studio" + name.replace(".", "")
        self.surface(element, 9, fill, border, [
            ("disabled", "#242234", "#302d41"),
            ("pressed focus", press, MINT), ("active focus", hover, MINT),
            ("focus", fill, MINT), ("pressed", press, border), ("active", hover, border),
        ], backdrop=backdrop)
        self.style.layout(name, [(element + ".surface", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"})]})]})])
        self.style.configure(name, font=(FONT, 10, "bold"), width=0, foreground=color,
                             padding=tuple(map(self.px, padding)), anchor="center")
        self.style.map(name, foreground=[("disabled", "#89849e")])

    def checkbox(self):
        px = self.px
        off = rounded_image(self.root, px(6), FIELD, "#696280", PANEL)
        on = rounded_image(self.root, px(6), ACCENT, backdrop=PANEL)
        size = on.width()
        for index in range(px(8)):
            x = index + px(3)
            y = size // 2 + min(index, px(2)) - max(0, index - px(2))
            on.put("#221735", to=(x, y, x + 1, min(size, y + px(2))))
        indicators = []
        for icon in (off, on):
            padded = tk.PhotoImage(master=self.root, width=size + px(8), height=size)
            padded.put(PANEL, to=(0, 0, padded.width(), size))
            padded.tk.call(padded, "copy", icon)
            indicators.append(padded)
        self.images.extend(indicators)
        self.style.element_create("StudioCheck.indicator", "image", indicators[0],
                                  ("selected", indicators[1]))
        self.style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("StudioCheck.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})])
        self.style.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                             focuscolor=MINT, padding=(0, px(4)))
        self.style.map("TCheckbutton", background=[("active", PANEL)],
                       foreground=[("active", ACCENT), ("disabled", MUTED)])


class Surface(ttk.Frame):
    """Tarjeta redondeada cuyo fondo no interviene en el cálculo de tamaños."""
    def __init__(self, parent, px, padding, fill=PANEL, radius=15):
        super().__init__(parent, padding=padding)
        radius = px(radius)
        canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0, takefocus=False)
        canvas.place(x=0, y=0, relwidth=1, relheight=1, bordermode="ignore")
        shape = canvas.create_polygon(0, 0, 0, 0, fill=fill, outline=BORDER,
                                      smooth="raw", splinesteps=16)

        def draw(event):
            w, h = event.width - 1, event.height - 1
            r = min(radius, w / 2, h / 2)
            k = r * 0.55228
            canvas.coords(shape,
                          r, 0, r, 0, w-r, 0, w-r, 0,
                          w-r+k, 0, w, r-k, w, r,
                          w, r, w, h-r, w, h-r,
                          w, h-r+k, w-r+k, h, w-r, h,
                          w-r, h, r, h, r, h,
                          r-k, h, 0, h-r+k, 0, h-r,
                          0, h-r, 0, r, 0, r,
                          0, r-k, r-k, 0, r, 0)

        canvas.bind("<Configure>", draw)


class ScrollPage(ttk.Frame):
    def __init__(self, parent, stretch=False):
        super().__init__(parent)
        self.stretch = stretch
        self.grid_propagate(False)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=BG, width=1, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = ttk.Frame(self.canvas)
        # Fijar el ancho también mientras la pestaña está oculta.
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw", width=1)
        self.inner.bind("<Configure>", self._update_region)
        self.bind("<Configure>", self._resize)
        self.bind("<Map>", self._resize)

    def _resize(self, _event=None):
        width = max(1, self.winfo_width() - self.bar.winfo_reqwidth())
        self.canvas.itemconfigure(self._window, width=width)
        if self.stretch:
            self.inner.rowconfigure(0, minsize=self.winfo_height())

    def _update_region(self, event):
        self.canvas.configure(scrollregion=(0, 0, event.width, event.height))

    def show_widget(self, widget):
        top = self.canvas.winfo_rooty()
        y = widget.winfo_rooty() - top
        height = self.canvas.winfo_height()
        if y < 0 or y + widget.winfo_height() > height:
            shift = y if y < 0 else min(y, y + widget.winfo_height() - height)
            self.canvas.yview_moveto(
                self.canvas.yview()[0] + shift / max(1, self.inner.winfo_height())
            )


class Flow(ttk.Frame):
    """Una fila de acciones que se reparte en varias filas si el espacio es reducido."""
    def __init__(self, parent, gap, style="Panel.TFrame"):
        super().__init__(parent, style=style)
        self.gap = gap
        self.items = []
        self.layout_key = None
        self.bind("<Configure>", self.reflow)

    def add(self, widget):
        self.items.append(widget)
        widget.bind("<Configure>", self.reflow, add="+")
        self.reflow()
        return widget

    def reflow(self, _event=None):
        width = self.winfo_width() if self.winfo_width() > 1 else 10000
        sizes = [(item.winfo_reqwidth(), item.winfo_reqheight()) for item in self.items]
        key = (width, sizes)
        if self.layout_key == key:
            return
        self.layout_key = key
        x = y = row_height = 0
        for widget, (w, h) in zip(self.items, sizes):
            if x and x + w > width:
                x = 0
                y += row_height + self.gap
                row_height = 0
            widget.place(x=x, y=y, width=width if w > width else "")
            x += w + self.gap
            row_height = max(row_height, h)
        self.configure(height=max(1, y + row_height))


class AdaptiveGrid(ttk.Frame):
    def __init__(self, parent, threshold, gap):
        super().__init__(parent)
        self.threshold = threshold
        self.gap = gap
        self.cards = []
        self.hidden = set()
        self.stacks = (ttk.Frame(self), ttk.Frame(self))
        for stack in self.stacks:
            stack.columnconfigure(0, weight=1)
        self._columns = 0
        self.bind("<Configure>", self._resize)

    def add(self, card):
        self.cards.append(card)
        self._columns = 0
        self._resize()

    def show_cards(self, *cards):
        hidden = set(self.cards).difference(cards)
        if hidden == self.hidden:
            return
        for card in hidden:
            card.grid_remove()
        self.hidden = hidden
        self._columns = 0
        self._resize()

    def _resize(self, _event=None):
        columns = 2 if self.winfo_width() >= self.threshold else 1
        if columns == self._columns:
            return
        self._columns = columns
        self.columnconfigure(0, weight=1, uniform="cards")
        self.columnconfigure(1, weight=1 if columns == 2 else 0,
                             uniform="cards" if columns == 2 else "")
        self.stacks[0].grid(row=0, column=0, sticky="new",
                            padx=(0, self.gap if columns == 2 else 0))
        if columns == 2:
            self.stacks[1].grid(row=0, column=1, sticky="new")
        else:
            self.stacks[1].grid_remove()
        for index, card in enumerate(card for card in self.cards if card not in self.hidden):
            row, col = divmod(index, columns)
            card.grid(in_=self.stacks[col], row=row, column=0, sticky="ew", pady=(0, self.gap))



def disc_art(parent, width, height, background, small=False):
    """Motivo del disco óptico, dibujado a la resolución de la ventana."""
    canvas = tk.Canvas(parent, width=width, height=height, bg=background,
                       highlightthickness=0, bd=0, takefocus=False)
    cx, cy = width / 2, height / 2
    r = height * (0.34 if small else 0.37)
    if small:
        canvas.create_oval(cx - r * 1.32, cy - r * 1.32, cx + r * 1.32, cy + r * 1.32,
                           fill="#33284d", outline="")
    else:
        for scale, color in ((1.28, "#262238"), (1.13, "#302641")):
            a = r * scale
            canvas.create_oval(cx - a, cy - a, cx + a, cy + a, outline=color)
        for x, y, size, color in ((0.15, 0.26, 0.015, "#776592"), (0.81, 0.19, 0.024, MINT),
                                   (0.88, 0.67, 0.016, ACCENT), (0.20, 0.77, 0.016, "#776592")):
            d = height * size
            canvas.create_oval(width*x-d, height*y-d, width*x+d, height*y+d, fill=color, outline="")
    bounds = (cx - r, cy - r, cx + r, cy + r)
    canvas.create_oval(*bounds, fill="#7c65bd", outline=ACCENT, width=1)
    for start, extent, color in ((22, 57, "#a18ade"), (79, 36, "#c8b7f6"),
                                 (203, 45, "#59478e"), (248, 48, "#8e77d0"),
                                 (296, 24, "#a89be1")):
        canvas.create_arc(*bounds, start=start, extent=extent, fill=color, outline="")
    for scale in (0.88, 0.74, 0.53):
        a = r * scale
        canvas.create_oval(cx-a, cy-a, cx+a, cy+a, outline="#b09be4")
    hub = r * 0.26
    canvas.create_oval(cx-hub, cy-hub, cx+hub, cy+hub, fill=background, outline="#e2d7ff")
    return canvas


class StudioLayout:
    def _init_window_geometry(self):
        root = self.root
        self.ui_scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(self.px(1200), int(sw * 0.94))
        height = min(self.px(850), int(sh * 0.86))
        root.geometry(f"{width}x{height}+{(sw-width)//2}+{max(0, (sh-height)//2-18)}")
        root.minsize(min(self.px(420), sw), min(self.px(360), sh))
        if sys.platform == "win32":
            assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            icon = assets / "astra.ico"
            if icon.is_file():
                root.iconbitmap(default=str(icon))

    def px(self, value):
        return max(1, round(value * self.ui_scale))

    def _setup_styles(self):
        self.theme = StudioTheme(self.root, self.px)
        self.body_font = tkfont.Font(root=self.root, family=FONT, size=10)
        self.heading_font = tkfont.Font(root=self.root, family=FONT, size=9, weight="bold")

    def _build_ui(self):
        root = self.root
        px = self.px
        self.header = ttk.Frame(root)
        self.header.pack(side="top", fill="x", pady=(px(16), px(12)))
        disc_art(self.header, px(49), px(49), BG, small=True).pack(side="left", padx=(0, px(10)))
        brand = ttk.Frame(self.header)
        brand.pack(side="left", fill="x", expand=True)
        ttk.Label(brand, text="Mega CD Estudio", style="Title.TLabel").pack(anchor="w")
        self.tagline = ttk.Label(brand, text="Vídeo para Sega Mega CD", style="BrandNote.TLabel")
        self.tagline.pack(anchor="w")
        badge = tk.Canvas(self.header, width=px(126), height=px(30), bg=BG,
                          highlightthickness=0, bd=0)
        badge.pack(side="right")
        w, h, r = px(126), px(30), px(12)
        badge.create_oval(0, 0, 2*r, h-1, fill="#352b4f", outline="")
        badge.create_rectangle(r, 0, w-r, h-1, fill="#352b4f", outline="")
        badge.create_oval(w-2*r, 0, w-1, h-1, fill="#352b4f", outline="")
        badge.create_text(w/2, h/2, text="VERSIÓN 14.1", fill="#e2d6ff", font=(FONT, 10, "bold"))

        self.nav = Surface(root, px, padding=px(4), fill="#1a1925", radius=12)
        self.nav.pack(side="top", fill="x", pady=(0, px(13)))
        self.stage = ttk.Frame(root)
        self.stage.pack_propagate(False)
        self.pages = {}
        self.nav_buttons = {}
        for col, (key, title) in enumerate((("library", "Biblioteca"), ("settings", "Ajustes"),
                                             ("disc", "Disco y grabación"))):
            self.nav.columnconfigure(col, weight=1, uniform="tabs")
            button = ttk.Button(self.nav, text=title, style="Nav.TButton",
                                command=lambda page=key: self._select_page(page))
            button.grid(row=0, column=col, sticky="ew", padx=px(2))
            self.nav_buttons[key] = button
            self.pages[key] = ScrollPage(self.stage, stretch=(key == "library"))
        library_inner = self.pages["library"].inner
        library_inner.columnconfigure(0, weight=1)
        library_inner.rowconfigure(0, weight=1)
        library = ttk.Frame(library_inner)
        library.grid(row=0, column=0, sticky="nsew")
        self._build_library(library)
        self._build_settings(self.pages["settings"].inner)
        self._build_disc(self.pages["disc"].inner)
        self._build_dock()
        self._settings_refresh_id = None
        for var in self._watched_settings:
            var.trace_add("write", self._queue_settings_refresh)
        self._reload_settings()
        self.stage.pack(side="top", fill="both", expand=True)
        self._select_page("library")
        self._window_layout()
        root.bind("<Configure>", self._window_layout, add="+")
        root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        root.bind_all("<Button-4>", self._on_mousewheel, add="+")
        root.bind_all("<Button-5>", self._on_mousewheel, add="+")
        root.bind_all("<FocusIn>", self._on_focus, add="+")

    def _select_page(self, key):
        for name, page in self.pages.items():
            self.nav_buttons[name].configure(style="Active.Nav.TButton" if name == key else "Nav.TButton")
            if name == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        self.scroll = self.pages[key]

    def _window_layout(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        width = self.root.winfo_width()
        gutter = self.px(20 if width >= self.px(650) else 10)
        gutter = max(gutter, (width - self.px(1500)) // 2)
        if getattr(self, "_gutter", None) != gutter:
            self._gutter = gutter
            for widget in (self.header, self.nav, self.stage, self.dock):
                widget.pack_configure(padx=gutter)
        short_nav = width < self.px(540)
        if getattr(self, "_short_nav", None) != short_nav:
            self._short_nav = short_nav
            self.nav_buttons["disc"].configure(text="Disco" if short_nav else "Disco y grabación")
        compact = self.root.winfo_height() < self.px(535)
        if getattr(self, "_compact", None) != compact:
            self._compact = compact
            self.header.pack_configure(pady=(self.px(5), self.px(6)) if compact else (self.px(16), self.px(12)))
            self.nav.pack_configure(pady=(0, self.px(6 if compact else 13)))
            self.dock.pack_configure(pady=(self.px(5), self.px(6)) if compact else (self.px(12), self.px(13)))
            self.dock.configure(padding=(self.px(9), self.px(6)) if compact else (self.px(13), self.px(10)))
            self.status_display.configure(height=1 if compact else 2)

    def _on_focus(self, event):
        widget = event.widget
        if isinstance(widget, tk.Widget) and str(widget).startswith(str(self.scroll.inner) + "."):
            self.root.after_idle(self._show_focus, widget)

    def _show_focus(self, widget):
        if widget.winfo_exists() and widget.winfo_ismapped() and self.root.focus_get() == widget:
            self.scroll.show_widget(widget)

    def _on_mousewheel(self, event):
        widget = event.widget
        if not isinstance(widget, tk.Widget) or not str(widget).startswith(str(self.scroll) + "."):
            return None
        number = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        if number not in (4, 5) and not delta:
            return None
        step = -1 if number == 4 or (number != 5 and delta > 0) else 1
        units = 3 * max(1, abs(delta) // 120) * step
        if widget is getattr(self, "tree", None):
            start, end = widget.yview()
            if (step < 0 and start > 0) or (step > 0 and end < 1):
                widget.yview_scroll(units, "units")
                return "break"
        self.scroll.canvas.yview_scroll(units, "units")
        return "break"

    def _card(self, parent, title):
        card = Surface(parent, self.px, padding=self.px(16))
        card.title_label = ttk.Label(card, text=title, style="CardTitle.TLabel")
        card.title_label.pack(anchor="w", pady=(0, self.px(12)))
        return card

    def _wrap(self, parent, **options):
        label = ttk.Label(parent, justify="left", wraplength=self.px(280), **options)
        def resize(event):
            width = max(1, event.width - 2)
            if int(label.cget("wraplength") or 0) != width:
                label.configure(wraplength=width)

        label.bind("<Configure>", resize)
        return label

    def _field(self, parent, text, variable=None, values=None, on_select=None):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, self.px(10)))
        ttk.Label(row, text=text, style="FieldLabel.TLabel").pack(anchor="w", pady=(0, self.px(4)))
        if values is not None:
            control = self._combo(row, variable, values, on_select)
        else:
            control = ttk.Entry(row, textvariable=variable, width=1)
        control.pack(fill="x")
        return row, control

    def _combo(self, parent, variable, values, on_select=None):
        combo = ttk.Combobox(parent, textvariable=variable, state="readonly", values=values, width=1)
        # El desplegable puede ser más ancho que su campo para mostrar la opción completa.
        style = f"AstraOption{len(self._combo_styles)}.TCombobox"
        self._combo_styles.append(style)
        combo.configure(style=style)

        def prepare():
            width = combo.winfo_width()
            needed = max((self.body_font.measure(text) for text in combo["values"]), default=0)
            extra = max(0, needed + self.px(28) - width)
            # Ampliar hacia la izquierda si está junto al borde derecho de la ventana.
            room = max(0, self.root.winfo_rootx() + self.root.winfo_width()
                       - combo.winfo_rootx() - width)
            left = min(max(0, combo.winfo_rootx() - self.root.winfo_rootx()), max(0, extra - room))
            self.theme.style.configure(style, postoffset=(-left, 0, min(extra, room + left), 0))

        def selected(event):
            event.widget.selection_clear()
            if on_select is not None:
                on_select()

        combo.configure(postcommand=prepare)
        combo.bind("<<ComboboxSelected>>", selected)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            combo.bind(event, self._on_mousewheel)
        return combo

    def _check(self, parent, text, variable, command=None):
        widget = ttk.Checkbutton(parent, text=text, variable=variable, command=command)
        widget.pack(anchor="w", pady=(0, self.px(2)))
        return widget

    def _action(self, row, text, command, style="TButton", **options):
        return row.add(ttk.Button(row, text=text, command=command, style=style, **options))

    def _build_library(self, body):
        px = self.px
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        library = self._card(body, "Lista de reproducción")
        plan = self._card(body, "Plan del disco")

        add_row = Flow(library, px(7))
        add_row.pack(fill="x", pady=(0, px(12)))
        self._action(add_row, "+ Añadir vídeos", self.add_videos, "Tonal.TButton")
        self._action(add_row, "Añadir carpeta", self.add_folder)

        table = ttk.Frame(library, style="Panel.TFrame")
        table.pack(fill="both", expand=True)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("idx", "name", "res", "dur", "chapters", "est_mb", "path")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=4,
                                 padding=0, selectmode="extended")
        headings = ("#", "Vídeo", "Resolución", "Duración", "Capítulos", "Tamaño est.", "Ruta completa")
        self._column_widths = {}
        for key, title in zip(columns, headings):
            size = self.heading_font.measure(title) + px(21)
            self._column_widths[key] = max(size, px(130 if key == "name" else 40))
            self.tree.heading(key, text=title, anchor="w" if key in ("name", "path") else "center")
            self.tree.column(key, width=self._column_widths[key], minwidth=0, stretch=False,
                             anchor="w" if key in ("name", "path") else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vertical.set)
        self._tree_cols_state = None
        self.tree.bind("<Configure>", self._on_tree_configure)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tree.bind(event, self._on_mousewheel)
        self.tree.bind("<<TreeviewSelect>>", self._update_selection_details)

        self.empty = tk.Frame(table, bg=FIELD)
        disc_art(self.empty, px(48), px(48), FIELD, small=True).pack(pady=(0, px(4)))
        ttk.Label(self.empty, text="Añade tu primer vídeo", style="EmptyTitle.TLabel").pack()
        ttk.Label(self.empty, text="MP4, MKV, AVI, MOV y más", style="EmptyText.TLabel").pack(pady=(px(3), 0))
        self.empty.place(relx=0.5, rely=0.56, anchor="center")

        self.selection_box = ttk.Frame(library, style="Panel.TFrame")
        self.selected_name = tk.StringVar()
        self.selected_meta = tk.StringVar()
        self.selected_path = tk.StringVar()
        self._wrap(self.selection_box, textvariable=self.selected_name,
                   style="FieldLabel.TLabel").pack(fill="x", pady=(px(9), px(2)))
        self._wrap(self.selection_box, textvariable=self.selected_meta,
                   style="Body.TLabel").pack(fill="x", pady=(0, px(5)))
        self.path_entry = ttk.Entry(self.selection_box, textvariable=self.selected_path,
                                    width=1, state="readonly")
        self.path_entry.pack(fill="x")

        self.edit_row = edit_row = Flow(library, px(5))
        edit_row.pack(fill="x", pady=(px(8), 0))
        self._action(edit_row, "↑ Subir", self.move_up, "Quiet.TButton")
        self._action(edit_row, "↓ Bajar", self.move_down, "Quiet.TButton")
        self._action(edit_row, "Quitar selección", self.remove_selected, "Quiet.TButton")
        self._action(edit_row, "Vaciar lista", self.clear_videos, "Quiet.TButton")

        disc_art(plan, px(180), px(126), PANEL).pack(pady=(0, px(8)))
        self.library_summary = tk.StringVar(value="0 vídeos")
        ttk.Label(plan, textvariable=self.library_summary, style="Count.TLabel").pack(anchor="w")
        self.cap_label = self._wrap(plan, text="0,0 / 700 MiB estimados", style="Body.TLabel")
        self.cap_label.pack(fill="x", pady=(px(5), px(9)))
        self.picture_summary = tk.StringVar()
        self._wrap(plan, textvariable=self.picture_summary, style="Body.TLabel").pack(
            fill="x", pady=(0, px(5)))
        self.estimate_var = tk.DoubleVar()
        ttk.Progressbar(plan, variable=self.estimate_var, maximum=100, length=px(110),
                        style="Estimate.Horizontal.TProgressbar").pack(fill="x")
        self.region_summary = tk.StringVar(value="PAL Europa")
        self.disc_format_var = tk.StringVar(value="ISO / CUE · Cinepak")
        ttk.Label(plan, textvariable=self.disc_format_var, style="FieldLabel.TLabel").pack(anchor="w", pady=(px(16), px(3)))
        ttk.Label(plan, textvariable=self.region_summary, style="Body.TLabel").pack(anchor="w")

        def arrange(_event=None):
            wide = body.winfo_width() >= px(830)
            if getattr(self, "_library_wide", None) == wide:
                return
            self._library_wide = wide
            body.rowconfigure(0, weight=1 if wide else 0)
            body.rowconfigure(1, weight=0)
            body.columnconfigure(1, minsize=px(264) if wide else 0)
            library.grid(row=0, column=0, sticky="nsew", padx=(0, px(12) if wide else 0),
                         pady=(0, 0 if wide else px(12)))
            plan.grid(row=0 if wide else 1, column=1 if wide else 0, sticky="new")

        body.bind("<Configure>", arrange)
        arrange()

    def _on_tree_configure(self, event):
        width = event.width - self.px(3)
        widths = self._column_widths
        cols = ["idx", "name", "dur"]
        for key in ("est_mb", "res", "chapters", "path"):
            required = sum(widths[col] for col in cols) + widths[key] + self.px(30)
            if width >= required:
                cols.append(key)
        ordered = tuple(key for key in self.tree["columns"] if key in cols)
        if ordered != self._tree_cols_state:
            self.tree.configure(displaycolumns=ordered)
            self._tree_cols_state = ordered
        remaining = max(0, width - sum(widths[key] for key in ordered))
        for key in ordered:
            extra = remaining if key == "name" else 0
            if "path" in ordered:
                extra = remaining // 2 if key in ("name", "path") else 0
            self.tree.column(key, width=widths[key] + extra)

    def _update_selection_details(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            self.selection_box.pack_forget()
            return
        values = self.tree.item(selection[0], "values")
        if len(values) != 7:
            return
        _, name, resolution, duration, chapters, size, path = values
        self.selected_name.set(name)
        self.selected_meta.set(f"{resolution} · {duration} · {chapters} · {size} est.")
        self.selected_path.set(path)
        if not self.selection_box.winfo_manager():
            self.selection_box.pack(fill="x", before=self.edit_row)

    def _update_library_summary(self, estimated_percent):
        count = len(self.videos)
        self.library_summary.set(f"{count} vídeo" if count == 1 else f"{count} vídeos")
        self.estimate_var.set(min(100, estimated_percent))
        if count:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.56, anchor="center")
        self._update_selection_details()

    def _build_settings(self, body):
        self._combo_styles = []
        grid = AdaptiveGrid(body, self.px(825), self.px(12))
        grid.pack(fill="x")
        self.settings_grid = grid
        self._watched_settings = []

        def field(card, label, attr, values, command=None, default=0):
            var = tk.StringVar(value=values[default])
            setattr(self, attr, var)
            self._watched_settings.append(var)
            if command == self._refresh_tree:
                command = None
            return self._field(card, label, var, values, command)

        disc = self._card(grid, "Formato y capacidad")
        grid.add(disc)
        self.format_card = disc
        field(disc, "Región de la consola", "region_var", [
            "E - Europa / España (Mega CD PAL 50Hz)",
            "U - Estados Unidos (Sega CD NTSC 60Hz)",
            "J - Japón (Mega CD NTSC-J 60Hz)",
        ])
        self.region_var.trace_add("write", lambda *_: self.region_summary.set(
            {"E": "PAL Europa", "U": "NTSC-U Estados Unidos", "J": "NTSC-J Japón"}.get(
                self.region_var.get()[:1], "")
        ))
        self.title_var = tk.StringVar(value="VIDEO FMV MEGA CD PAL")
        self._build_cavc_settings(grid, disc)
        self.cinepak_title, _ = self._field(disc, "Título interno del CD", self.title_var)
        self.cinepak_priority, _ = field(disc, "Prioridad al ajustar a CD", "quality_mode_var", [
            "Nitidez 7.0: FPS automáticos", "15 FPS nítidos: área máxima en 1 CD",
            "24 FPS nítidos: adapta área / varios CD", "FPS fijos: compresión temporal",
        ], self._quality_mode_selected)
        self.fit_cd_var = tk.BooleanVar(value=True)
        self.cinepak_fit = self._check(disc, "Ajustar a un CD (700 MiB)", self.fit_cd_var)
        self._watched_settings.append(self.fit_cd_var)

        picture = self._card(grid, "Imagen")
        grid.add(picture)
        self.cinepak_picture = picture
        field(picture, "Fotogramas por segundo", "fps_var", [
            "8 FPS (Máxima nitidez lossless ~2.5 horas en CD)",
            "10 FPS (Estándar clásico Tomcat Alley - Alta nitidez)",
            "12 FPS (Fluido estándar - Alta nitidez)",
            "15 FPS (Alta fluidez - Nitidez True-RGB sin artefactos)",
            "20 FPS (Ultra fluido - Con sincronía A/V activa)",
            "24 FPS (Cine 24p - Con sincronía A/V activa)",
        ], self._refresh_tree, default=3)
        field(picture, "Ajuste de pantalla", "aspect_var", [
            "Automático (Mantener proporción original con bandas tile-skip)",
            "Pantalla Completa 256x192 (Estirar a 4:3)",
            "Recortar al Centro 256x192 (Zoom / Rellenar 4:3)",
        ], self._refresh_tree)
        field(picture, "Calidad de color VDP", "pal_var", [
            "4 Paletas VDP por fotograma (Hasta 60 colores - Alta Calidad)",
            "2 Paletas VDP por fotograma (Hasta 30 colores - Equilibrado)",
            "1 Paleta VDP por fotograma (15 colores - Más rápido)",
        ], self._refresh_tree)
        field(picture, "Perfil de brillo para TV o cable", "video_eq_var", [
            "S-Video / Conversor HDMI (Realce de escenas oscuras - Recomendado)",
            "Estándar Mega CD (Gamma 1.12 equilibrado)",
            "Alto Contraste / TV CRT (Colores intensos)",
        ])
        field(picture, "Corrección de tinte rosa o magenta", "tint_var", [
            "Anti-rosa: Suave (recomendado)", "Anti-rosa: Fuerte (si aún se ve rosado)",
            "Anti-rosa: Desactivado",
        ])

        playback = self._card(grid, "Audio y reproducción")
        grid.add(playback)
        self.cinepak_playback = playback
        field(playback, "Potencia de audio PCM", "audio_mode_var", [
            "Alto Cine (+8 dB + Normalizador de Diálogos - Recomendado)",
            "Máximo (+12 dB Boost Extremo para películas muy bajas)",
            "Estándar (+4 dB Equilibrado sin ruido)",
            "Original (0 dB Directo con volumen chip al 100%)",
        ])
        self.vocal_clarity_var = tk.BooleanVar(value=True)
        self._check(playback, "Claridad vocal Mega CD (2,6 kHz)", self.vocal_clarity_var)
        self._wrap(playback, text="Incluye puerta de silencio.", style="Body.TLabel").pack(
            fill="x", pady=(0, self.px(11)))
        chapter_row, _ = field(playback, "Duración por capítulo", "chapter_dur_var", [
            "1 min por capítulo (Saltos rápidos estilo DVD)",
            "2 min por capítulo (Saltos ágiles)",
            "3 min por capítulo (Estándar - Saltos precisos en menú START)",
            "5 min por capítulo (Capítulos largos)",
            "10 min por capítulo (Capítulos largos)",
        ], self._refresh_tree, default=2)
        self.chap_hint_var = tk.StringVar()
        hint = self._wrap(chapter_row, textvariable=self.chap_hint_var, style="Hint.TLabel")

        def show_hint(*_):
            if self.chap_hint_var.get():
                hint.pack(fill="x", pady=(self.px(5), 0))
            else:
                hint.pack_forget()

        self.chap_hint_var.trace_add("write", show_hint)
        self.loop_var = tk.BooleanVar(value=True)
        self.skip_var = tk.BooleanVar(value=True)
        self.osd_menu_var = tk.BooleanVar(value=True)
        self._check(playback, "Repetir en bucle al terminar", self.loop_var)
        self._check(playback, "Saltar capítulo con A/B/C", self.skip_var)
        self._check(playback, "Menú multimedia con START", self.osd_menu_var)

        performance = self._card(grid, "Rendimiento")
        grid.add(performance)
        self.performance_card = performance
        field(performance, "Aceleración", "acceleration_var", [
            "Automático: GPU + CPU", "CPU optimizada (sin GPU)",
        ], self._refresh_tree)
        self.cinepak_workers, _ = field(performance, "Capítulos en paralelo", "workers_var", [
            "Automático", "1", "2", "4", "6", "8",
        ], self._refresh_tree)

    def _queue_settings_refresh(self, *_):
        if self._settings_refresh_id is None:
            self._settings_refresh_id = self.root.after_idle(self._reload_settings)

    def _reload_settings(self):
        self._settings_refresh_id = None
        cavc = self._uses_cavc()
        if cavc != getattr(self, "_settings_show_cavc", None):
            self._settings_show_cavc = cavc
            if cavc:
                self.settings_grid.show_cards(self.format_card, self.cavc_card,
                                              self.performance_card)
                for widget in (self.cinepak_title, self.cinepak_priority,
                               self.cinepak_fit, self.cinepak_workers):
                    widget.pack_forget()
            else:
                self.settings_grid.show_cards(self.format_card, self.cinepak_picture,
                                              self.cinepak_playback, self.performance_card)
                self.cinepak_title.pack(fill="x", pady=(0, self.px(10)))
                self.cinepak_priority.pack(fill="x", pady=(0, self.px(10)))
                self.cinepak_fit.pack(anchor="w", pady=(0, self.px(2)))
                self.cinepak_workers.pack(fill="x", pady=(0, self.px(10)))
        self._sync_cavc_region()
        self._sync_preview_label()
        selection = self.tree.selection()
        focus = self.tree.focus()
        scroll = self.tree.yview()[0]
        self._refresh_tree()
        if not cavc:
            self.tree.heading("chapters", text="Capítulos")
            framing = {"keep": "Conservar proporción", "stretch": "Pantalla completa",
                       "crop": "Recortar para llenar"}[self._get_selected_aspect()]
            brightness = {"svideo_hdmi": "S-Video / HDMI", "standard": "Brillo estándar",
                          "crt": "Alto contraste"}[self._get_selected_video_eq()]
            tint = {"auto": "suave", "strong": "fuerte", "off": "desactivado"}[
                self._get_selected_tint_fix()]
            self.picture_summary.set(
                f"{self._get_selected_fps()} FPS · {self._get_selected_palettes()} paletas · {framing}\n"
                f"{brightness} · anti-rosa {tint}")
        self.tree.selection_set(*(item for item in selection if self.tree.exists(item)))
        if self.tree.exists(focus):
            self.tree.focus(focus)
        self.tree.yview_moveto(scroll)
        self._update_selection_details()

    def _build_disc(self, body):
        px = self.px
        grid = AdaptiveGrid(body, px(825), px(12))
        grid.pack(fill="x")
        directory = self._card(grid, "Carpeta de salida")
        grid.add(directory)
        program_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
        self.out_dir_var = tk.StringVar(value=os.path.join(program_dir, "Discos_convertidos"))
        self._field(directory, "Destino de las imágenes ISO y CUE", self.out_dir_var)
        dir_actions = Flow(directory, px(7))
        dir_actions.pack(fill="x")
        self._action(dir_actions, "Examinar…", self.browse_out_dir)
        self._action(dir_actions, "Abrir carpeta", self.open_out_dir)

        image = self._card(grid, "Imagen de disco")
        grid.add(image)
        self.disc_name_var = tk.StringVar()
        self._wrap(image, textvariable=self.disc_name_var, style="Body.TLabel").pack(fill="x", pady=(0, px(12)))
        image_actions = Flow(image, px(7))
        image_actions.pack(fill="x")
        self._action(image_actions, "Elegir disco…", self.choose_generated_disc, "Tonal.TButton")
        self.fusion_btn = self._action(image_actions, "Probar disco en Fusion", self.launch_in_fusion, state="disabled")
        self._action(image_actions, "Probar en ares", self._launch_disc_in_ares)
        self._action(image_actions, "Vista MP4 de CAVC / LORE", self._open_cavc_preview)

        burner = self._card(grid, "Grabadora de CD")
        grid.add(burner)
        self.burner_var = tk.StringVar(value="Detección automática (Grabadora ASUS / CD-ROM)")
        _, self.burner_cb = self._field(burner, "Unidad de grabación", self.burner_var, [])
        self.burn_speed_var = tk.StringVar(value="4x (600 KB/s - Recomendado Mega CD)")
        self._field(burner, "Velocidad", self.burn_speed_var, [
            "4x (600 KB/s - Recomendado Mega CD)", "8x (1200 KB/s - Seguro)",
            "16x (2400 KB/s - Rápido)",
        ])
        self.auto_burn_var = tk.BooleanVar(value=False)
        self._check(burner, "Grabar al terminar la conversión", self.auto_burn_var)
        burn_actions = Flow(burner, px(7))
        burn_actions.pack(fill="x", pady=(px(9), 0))
        self.burn_btn = self._action(burn_actions, "Grabar con ImgBurn", self.start_burn_cd, "Accent.TButton")
        self._action(burn_actions, "Detectar unidades", self.refresh_burners)

        self.validation_card = self._card(grid, "Comprobación en Kega")
        grid.add(self.validation_card)
        self._wrap(self.validation_card, text="Reproduce la película completa en Kega Fusion para comprobar el disco.",
                   style="Body.TLabel").pack(fill="x", pady=(0, px(8)))

    def _update_selected_disc(self, *_):
        path = self.last_cue_path or self.last_iso_path
        label = path if path else "Elige un archivo ISO o CUE, o crea un disco desde la biblioteca."
        if self.disc_name_var.get() != label:
            self.disc_name_var.set(label)

    def _build_dock(self):
        px = self.px
        self.dock = Surface(self.root, px, padding=(px(13), px(10)), fill="#1a1925", radius=14)
        self.dock.pack(side="bottom", fill="x", pady=(px(12), px(13)))
        self.dock.columnconfigure(0, weight=1)
        info = ttk.Frame(self.dock, style="DockContent.TFrame")
        info.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Listo para convertir")
        self.status_display = status = tk.Text(info, height=2, width=1, wrap="word", bg="#1a1925", fg=MUTED,
                         font=(FONT, 10), bd=0, highlightthickness=0, padx=0, pady=0,
                         selectbackground="#4b3c6c", selectforeground=TEXT, takefocus=False)
        status.grid(row=0, column=0, sticky="ew", columnspan=2)
        self.progress_var = tk.DoubleVar()
        self.pbar = ttk.Progressbar(info, variable=self.progress_var, maximum=100, length=px(80))
        self.pbar.grid(row=1, column=0, sticky="ew", padx=(0, px(9)), pady=(px(3), 0))
        percent = ttk.Label(info, text="0 %", style="DockValue.TLabel", width=6, anchor="e")
        percent.grid(row=1, column=1, sticky="e")

        actions = Flow(self.dock, px(7), style="DockContent.TFrame")
        self.convert_btn = self._action(actions, "Crear disco", self.start_conversion, "Dock.Accent.TButton")
        self.preview_btn = self._action(actions, "Prueba de 15 s", self.start_quick_preview, "Dock.TButton")
        self.cancel_btn = self._action(actions, "Cancelar", self.cancel_conversion,
                                       "Dock.TButton", state="disabled")

        def update_status(*_):
            status.configure(state="normal")
            status.delete("1.0", "end")
            status.insert("1.0", self.status_var.get())
            status.configure(state="disabled")
            self._update_selected_disc()

        def update_progress(*_):
            percent.configure(text=f"{min(100, max(0, self.progress_var.get())):.0f} %")

        def arrange(_event=None):
            wide = self.dock.winfo_width() >= px(830)
            if getattr(self, "_dock_wide", None) == wide:
                return
            self._dock_wide = wide
            action_width = sum(item.winfo_reqwidth() for item in actions.items) + px(48)
            self.dock.columnconfigure(1, minsize=action_width if wide else 0)
            info.grid(row=0, column=0, sticky="ew", padx=(0, px(18) if wide else 0),
                      pady=(0, 0 if wide else px(7)))
            actions.grid(row=0 if wide else 1, column=1 if wide else 0, sticky="ew")

        self.status_var.trace_add("write", update_status)
        self.progress_var.trace_add("write", update_progress)
        self.dock.bind("<Configure>", arrange)
        update_status()
        arrange()
