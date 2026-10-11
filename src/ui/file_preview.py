# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





























from __future__ import annotations

import codecs
import html
import os
import re
import zipfile

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFontDatabase, QImage, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_qss_font_px, widget_pixel_ratio
from .icons import pixmap_for
from .image_preview import open_image_preview
from .style import FONT_BASE, FONT_HINT, INK, INK_2, LINE, SURFACE
from .widgets import ElidedLabel, IconButton






FAMILY_COLORS = {
    "pdf": "#fa423e",
    "doc": "#0285ff",
    "sheet": "#10a37f",
    "slides": "#ff8a1f",
    "code": "#8e5cf7",
    "other": "#8f8f8f",
}
_FAMILY_GLYPHS = {
    "pdf": "file_pdf", "doc": "file", "sheet": "file_table",
    "slides": "file", "code": "file_html", "other": "file",
}
_FAMILIES = {
    "pdf": "pdf",
    "docx": "doc", "doc": "doc", "odt": "doc", "rtf": "doc", "txt": "doc",
    "md": "doc", "rst": "doc", "pages": "doc",
    "xlsx": "sheet", "xls": "sheet", "ods": "sheet", "csv": "sheet", "tsv": "sheet",
    "numbers": "sheet",
    "pptx": "slides", "ppt": "slides", "odp": "slides", "key": "slides",
    "html": "code", "htm": "code", "xml": "code", "json": "code", "yaml": "code",
    "yml": "code", "py": "code", "js": "code", "ts": "code", "sql": "code",
    "log": "code", "ini": "code", "cfg": "code", "toml": "code", "sh": "code",
    "r": "code", "ipynb": "code", "css": "code",
}


PREVIEW_TEXT_BYTES = 512 * 1024
_ZIP_PART_MAX_BYTES = 32 * 1024 * 1024
_ZIP_PARTS_MAX = 400

PDF_MAX_PAGES = 300

_FILL_W = 0.72
_FILL_H = 0.88
_PANEL_MAX_W = 760
_PAGE_GAP = 12


def _suffix(name: str) -> str:
    return os.path.splitext(str(name or ""))[1].lower().lstrip(".")


def family_of(name: str) -> str:

    return _FAMILIES.get(_suffix(name), "other")


def tile_art(widget, name: str, size: int) -> tuple[QPixmap, str]:

    family = family_of(name)
    return (pixmap_for(widget, _FAMILY_GLYPHS[family], size, QColor(255, 255, 255)),
            FAMILY_COLORS[family])


def item_path(item: dict) -> str:
    return str(item.get("path") or "") if isinstance(item, dict) else ""


def can_open(item: dict) -> bool:

    path = item_path(item)
    try:
        return bool(path) and os.path.isfile(path)
    except (OSError, ValueError):
        return False




def _read_text(path: str) -> tuple[str, bool] | None:

    try:
        with open(path, "rb") as handle:
            data = handle.read(PREVIEW_TEXT_BYTES + 1)
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    cut = len(data) > PREVIEW_TEXT_BYTES
    data = data[:PREVIEW_TEXT_BYTES]



    try:
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(data, final=not cut)
    except UnicodeDecodeError:


        from ..tools.csv_loader import _ansi_encoding

        text = data.decode(_ansi_encoding(), errors="replace")
    return text, cut


_W_PARAGRAPH = re.compile(r"</w:p>")
_W_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)
_A_PARAGRAPH = re.compile(r"</a:p>")
_A_TEXT = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.DOTALL)
_SLIDE = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def _zip_part(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > _ZIP_PART_MAX_BYTES:
        return ""
    return archive.read(info).decode("utf-8", errors="replace")


def _paragraphs(xml: str, paragraph: re.Pattern, run: re.Pattern) -> list[str]:
    lines = []
    for chunk in paragraph.split(xml):
        line = "".join(html.unescape(t) for t in run.findall(chunk)).strip()
        if line:
            lines.append(line)
    return lines


def _office_text(path: str) -> tuple[str, bool] | None:

    suffix = _suffix(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()[:_ZIP_PARTS_MAX]
            if suffix == "docx":
                part = next((i for i in infos if i.filename == "word/document.xml"), None)
                if part is None:
                    return None
                text = "\n\n".join(_paragraphs(_zip_part(archive, part), _W_PARAGRAPH, _W_TEXT))
            else:
                slides = []
                for info in infos:
                    match = _SLIDE.match(info.filename)
                    if match:
                        slides.append((int(match.group(1)), info))
                slides.sort(key=lambda pair: pair[0])
                blocks = []
                for number, part in slides:
                    lines = _paragraphs(_zip_part(archive, part), _A_PARAGRAPH, _A_TEXT)
                    blocks.append(f"[{number}]\n" + "\n".join(lines))
                text = "\n\n".join(blocks)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError):
        return None
    cut = len(text) > PREVIEW_TEXT_BYTES
    return text[:PREVIEW_TEXT_BYTES], cut


def size_words(size_bytes) -> str:





    try:
        value = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024 or unit == "GB":

            number = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return f"{number} {unit}"
    return ""


class _Pdf:


    def __init__(self, path: str):
        from osgeo import gdal

        self._gdal = gdal
        self.path = path
        gdal.PushErrorHandler("CPLQuietErrorHandler")
        try:
            first = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY,
                                allowed_drivers=["PDF"], open_options=["DPI=72"])
        finally:
            gdal.PopErrorHandler()
        if first is None:
            raise ValueError("not a PDF GDAL can draw")
        subs = first.GetSubDatasets() or []
        self.pages = max(1, len(subs))


        self.aspect = first.RasterXSize / max(1, first.RasterYSize)

    def render(self, index: int, width_px: int) -> QImage | None:
        gdal = self._gdal
        name = f"PDF:{index + 1}:{self.path}" if self.pages > 1 else self.path
        gdal.PushErrorHandler("CPLQuietErrorHandler")
        try:
            probe = gdal.OpenEx(name, gdal.OF_RASTER | gdal.OF_READONLY,
                                allowed_drivers=["PDF"], open_options=["DPI=72"])
            if probe is None:
                return None
            dpi = max(24, min(300, int(72 * width_px / max(1, probe.RasterXSize))))
            probe = None
            page = gdal.OpenEx(name, gdal.OF_RASTER | gdal.OF_READONLY,
                               allowed_drivers=["PDF"], open_options=[f"DPI={dpi}"])
            if page is None or page.RasterCount < 3:
                return None
            w, h = page.RasterXSize, page.RasterYSize
            data = page.ReadRaster(0, 0, w, h, band_list=[1, 2, 3], buf_pixel_space=3,
                                   buf_line_space=3 * w, buf_band_space=1)
        except (RuntimeError, ValueError, TypeError):
            return None
        finally:
            gdal.PopErrorHandler()
        if not data:
            return None
        image = QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888)
        return image.copy()


def _open_pdf(path: str) -> _Pdf | None:
    try:
        return _Pdf(path)
    except (ImportError, ValueError, RuntimeError, TypeError, AttributeError):
        return None




class _Page(QWidget):


    def __init__(self, pdf: _Pdf, index: int, parent=None):
        super().__init__(parent)
        self._pdf = pdf
        self._index = index
        self._pixmap: QPixmap | None = None
        self._drawn_at = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def fit(self, width: int) -> None:
        self.setFixedSize(width, int(round(width / max(0.05, self._pdf.aspect))))

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(255, 255, 255))
            ratio = widget_pixel_ratio(self)
            want = int(self.width() * ratio)
            if self._pixmap is None or abs(self._drawn_at - want) > 8:

                self._drawn_at = want
                image = self._pdf.render(self._index, want)
                if image is not None:
                    image = image.scaled(want, int(self.height() * ratio),
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                    self._pixmap = QPixmap.fromImage(image)
                    self._pixmap.setDevicePixelRatio(ratio)
            if self._pixmap is not None:
                painter.drawPixmap(0, 0, self._pixmap)
        finally:
            painter.end()


class _Panel(QFrame):


    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        event.accept()


_PANEL_QSS = scale_qss_font_px(
    f"QFrame#filePreviewPanel {{ background: {SURFACE}; border: 1px solid {LINE};"
    " border-radius: 14px; }"
    f"QLabel#filePreviewName {{ color: {INK}; font-size: {FONT_BASE + 1}px; font-weight: 600;"
    " background: transparent; }"
    f"QLabel#filePreviewMeta, QLabel#filePreviewNote {{ color: {INK_2};"
    f" font-size: {FONT_HINT + 1}px; background: transparent; }}"
    f"QLabel#filePreviewTile {{ border-radius: 8px; }}"
    "QScrollArea#filePreviewPages { background: #e9e9e7; border: none;"
    " border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }"
    "QWidget#filePreviewPagesInner { background: #e9e9e7; }"
    f"QPlainTextEdit#filePreviewText {{ background: {SURFACE}; color: {INK}; border: none;"
    f" border-top: 1px solid {LINE}; padding: 12px 16px; font-size: {FONT_BASE}px; }}"
    f"QPushButton#filePreviewOpen {{ background: {INK}; color: {SURFACE}; border: none;"
    f" border-radius: 8px; padding: 5px 14px; font-size: {FONT_BASE}px; font-weight: 600; }}"
)
_BTN_PANEL_CLOSE = (
    "QToolButton { background: transparent; border: none; border-radius: 15px; }"
    "QToolButton:hover { background: rgba(127, 127, 127, 0.18); }"
)


class FilePreview(QDialog):


    def __init__(self, item: dict, anchor: QWidget | None = None):
        window = anchor.window() if anchor is not None else None
        super().__init__(window)
        self.setObjectName("filePreview")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self._window = window
        self._pages: list[_Page] = []
        self.path = item_path(item)
        self.name = str(item.get("name") or os.path.basename(self.path))
        self.family = family_of(self.name)
        self.shows = ""

        self._panel = _Panel(self)
        self._panel.setObjectName("filePreviewPanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel.setStyleSheet(_PANEL_QSS)
        col = QVBoxLayout(self._panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._header())
        col.addWidget(self._body(), 1)



    def _header(self) -> QWidget:
        head = QWidget(self._panel)
        row = QHBoxLayout(head)
        row.setContentsMargins(14, 12, 10, 12)
        row.setSpacing(10)
        tile = QLabel(head)
        tile.setObjectName("filePreviewTile")
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph, color = tile_art(tile, self.name, 16)
        tile.setPixmap(glyph)
        tile.setStyleSheet(f"QLabel {{ background: {color}; border-radius: 8px; }}")
        row.addWidget(tile)
        lines = QVBoxLayout()
        lines.setSpacing(0)
        name = ElidedLabel(self.name, head, Qt.TextElideMode.ElideMiddle)
        name.setObjectName("filePreviewName")
        name.setToolTip(self.path)
        lines.addWidget(name)
        self._meta = QLabel(self._meta_words(), head)
        self._meta.setObjectName("filePreviewMeta")
        lines.addWidget(self._meta)
        row.addLayout(lines, 1)
        self.open_button = None
        if can_open({"path": self.path}):
            from .file_links import is_safe_to_open

            words = self.tr("Open") if is_safe_to_open(self.path) else self.tr("Show in folder")
            self.open_button = QPushButton(words, head)
            self.open_button.setObjectName("filePreviewOpen")
            self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.open_button.clicked.connect(self._open_outside)
            row.addWidget(self.open_button)
        close = IconButton(head, None, 14, self.tr("Close"), _BTN_PANEL_CLOSE)
        close.set_icon("close", 14)
        close.setFixedSize(30, 30)
        close.clicked.connect(self.close)
        row.addWidget(close)
        return head

    def _meta_words(self, pages: int = 0) -> str:
        from .attachments import file_type

        parts = [file_type({"name": self.name})]
        if pages == 1:
            parts.append(self.tr("1 page"))
        elif pages:
            parts.append(self.tr("{count} pages").format(count=pages))
        try:
            parts.append(size_words(os.path.getsize(self.path)))
        except (OSError, ValueError):
            pass
        return "  ·  ".join(p for p in parts if p)

    def _body(self) -> QWidget:
        if not can_open({"path": self.path}):
            return self._note(self.tr("This file is no longer at {path}.").format(path=self.path))
        if self.family == "pdf":
            pdf = _open_pdf(self.path)
            if pdf is not None:
                return self._pdf_pages(pdf)
        if _suffix(self.name) in ("docx", "pptx"):
            read = _office_text(self.path)
        elif _suffix(self.name) in ("pdf", "xlsx", "xls", "ods", "zip", "gz", "7z"):
            read = None
        else:
            read = _read_text(self.path)
        if read is not None and read[0].strip():
            return self._text(*read)
        return self._note(self.tr("No preview for this kind of file."))

    def _pdf_pages(self, pdf: _Pdf) -> QWidget:
        self.shows = "pages"
        count = min(pdf.pages, PDF_MAX_PAGES)
        self._meta.setText(self._meta_words(pdf.pages))
        scroll = QScrollArea(self._panel)
        scroll.setObjectName("filePreviewPages")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(scroll)
        inner.setObjectName("filePreviewPagesInner")
        col = QVBoxLayout(inner)
        col.setContentsMargins(24, 20, 24, 20)
        col.setSpacing(_PAGE_GAP)
        for index in range(count):
            page = _Page(pdf, index, inner)
            self._pages.append(page)
            col.addWidget(page, 0, Qt.AlignmentFlag.AlignHCenter)
        if pdf.pages > count:
            more = QLabel(self.tr("The first {shown} of {total} pages. Open the file to read the rest.")
                          .format(shown=count, total=pdf.pages), inner)
            more.setObjectName("filePreviewNote")
            more.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(more)
        col.addStretch(1)
        scroll.setWidget(inner)
        self._scroll = scroll
        return scroll

    def _text(self, text: str, cut: bool) -> QWidget:
        self.shows = "text"
        view = QPlainTextEdit(self._panel)
        view.setObjectName("filePreviewText")
        view.setReadOnly(True)
        view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        view.setCursorWidth(0)
        if self.family == "code" or _suffix(self.name) in ("csv", "tsv"):
            view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        if cut:
            text += "\n\n" + self.tr("[The first 512 KB. Open the file to read the rest.]")
        view.setPlainText(text)
        return view

    def _note(self, words: str) -> QWidget:
        box = QWidget(self._panel)
        col = QVBoxLayout(box)
        col.setContentsMargins(24, 32, 24, 40)
        col.setSpacing(12)
        col.addStretch(1)
        tile = QLabel(box)
        tile.setFixedSize(64, 64)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph, color = tile_art(tile, self.name, 30)
        tile.setPixmap(glyph)
        tile.setStyleSheet(f"QLabel {{ background: {color}; border-radius: 14px; }}")
        col.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        note = QLabel(words, box)
        note.setObjectName("filePreviewNote")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(note)
        col.addStretch(1)
        return box



    def _open_outside(self) -> None:
        from .external_links import open_local_path
        from .file_links import is_safe_to_open, reveal_target

        target = self.path if is_safe_to_open(self.path) else reveal_target(self.path)
        self.close()
        open_local_path(target, self._window)



    def open_over_window(self) -> None:
        geometry = self._window.frameGeometry() if self._window is not None else None
        screen = self._window.screen() if self._window is not None else None
        usable = screen.availableGeometry() if screen is not None else None
        if geometry is not None and usable is not None and not usable.isEmpty():
            geometry = geometry.intersected(usable)
        if (geometry is None or geometry.isEmpty()) and usable is not None:
            geometry = usable
        if geometry is not None and not geometry.isEmpty():
            self.setGeometry(geometry)
        self._layout_panel()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _layout_panel(self) -> None:
        width = int(min(_PANEL_MAX_W, max(360, self.width() * _FILL_W), self.width() - 32))
        height = int(max(240, self.height() * _FILL_H))
        if not self._pages and self.shows != "text":
            height = min(height, 420)
        self._panel.setGeometry((self.width() - width) // 2, (self.height() - height) // 2,
                                width, height)
        page_width = max(120, width - 48 - 16)
        for page in self._pages:
            page.fit(page_width)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._layout_panel()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 200))
        finally:
            painter.end()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802

        self.close()


def open_file_preview(item: dict, anchor: QWidget | None = None):

    if not isinstance(item, dict):
        return None
    from .attachments import IMAGE_EXTS

    path = item_path(item)
    if os.path.splitext(path)[1].lower() in IMAGE_EXTS and can_open(item):
        shown = open_image_preview({"name": item.get("name") or os.path.basename(path),
                                    "path": path}, anchor)
        if shown is not None:
            return shown
    preview = FilePreview(item, anchor)
    preview.open_over_window()
    return preview
