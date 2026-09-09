# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open a web address, a mail client or a local folder, and say so when nothing opened."""













from __future__ import annotations

from .shared import exec_dialog, tr

URL = "url"
EMAIL = "email"
FOLDER = "folder"


def _copy_to_clipboard(text: str) -> bool:
    """Put ``text`` on the clipboard, reporting whether it landed there."""
    try:
        from qgis.PyQt.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        return True
    except (RuntimeError, AttributeError, ImportError):
        return False


def _wording(kind: str, copied: bool) -> tuple[str, str]:
    """The two sentences for this kind of address: what failed, what to do."""
    if kind == FOLDER:
        return (
            tr("QGIS could not open a file manager."),
            tr("The folder is copied to your clipboard: paste it into your "
               "file manager.") if copied else
            tr("Copy the folder below and paste it into your file manager."))
    if kind == EMAIL:
        return (
            tr("QGIS could not open your email app."),
            tr("The support address is copied to your clipboard: paste it "
               "into your email app.") if copied else
            tr("Copy the support address below into your email app."))
    return (
        tr("QGIS could not open a browser."),
        tr("The address is copied to your clipboard: paste it into a browser "
           "to continue.") if copied else
        tr("Copy the address below and paste it into a browser."))


def _show_address(address: str, parent=None, kind: str = URL) -> None:
    """Show an address the desktop refused to open, ready to be copied."""
    copied = _copy_to_clipboard(address)
    opening, instruction = _wording(kind, copied)
    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("Open it yourself"))
        box.setText(f"{opening} {instruction}")
        box.setInformativeText(address)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        exec_dialog(box)
    except (RuntimeError, AttributeError, ImportError):
        pass  # nosec B110 - no window available; the clipboard already has it


def _try_open(url_object) -> bool:
    """Hand one QUrl to the desktop, treating any Qt failure as a refusal."""
    try:
        from qgis.PyQt.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(url_object))
    except (RuntimeError, AttributeError, ImportError):
        return False


def _show_refusal(url: str, parent=None) -> None:
    """An address the panel will not open, and why."""






    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("This link was not opened"))
        box.setText(tr("This link does not point to a web page, so it was not opened."))
        box.setInformativeText(str(url or "")[:2048])
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        exec_dialog(box)
    except (RuntimeError, AttributeError, ImportError):
        pass  # nosec B110 - no window available; the link is simply not opened


def open_external_url(url: str, parent=None) -> bool:
    """Open ``url`` in the browser, or hand the user the address instead."""




    from qgis.PyQt.QtCore import QUrl

    url_object = QUrl(url)



    if url_object.scheme().lower() not in ("http", "https") or url_object.userInfo():
        _show_refusal(url, parent=parent)
        return False
    if _try_open(url_object):
        return True


    _show_address(url, parent=parent, kind=URL)
    return False


def open_local_path(path: str, parent=None) -> bool:
    """Open a local file or folder, or hand the user the path instead."""
    from qgis.PyQt.QtCore import QUrl

    if _try_open(QUrl.fromLocalFile(path)):
        return True
    _show_address(path, parent=parent, kind=FOLDER)
    return False


def open_email(mailto_url: str, address: str, parent=None) -> bool:
    """Open the mail client, or show the plain address instead."""
    from qgis.PyQt.QtCore import QUrl

    if _try_open(QUrl(mailto_url)):
        return True
    _show_address(address, parent=parent, kind=EMAIL)
    return False
