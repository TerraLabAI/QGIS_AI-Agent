# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
# ruff: noqa: E501
"""    Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as
    part of Feather (MIT). All other copyright (c) for Lucide are held by
    Lucide Contributors 2022.

    Permission to use, copy, modify, and/or distribute this software for
    any purpose with or without fee is hereby granted, provided that the
    above copyright notice and this permission notice appear in all copies."""















from __future__ import annotations

from qgis.PyQt.QtCore import QByteArray, QRectF
from qgis.PyQt.QtGui import QColor, QPainter

PREFIX = "lu."

_SPARKLE = '<path fill="{colour}" stroke="none" d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z"/>'

SHAPES = {
    "undo-2": '<path d="M9 14 4 9l5-5"/> <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
    "redo-2": '<path d="m15 14 5-5-5-5"/> <path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/> <path d="M3 3v5h5"/> <path d="M12 7v5l4 2"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/> <path d="M12 19V5"/>',
    "book-open": '<path d="M12 7v14"/> <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "braces": '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/> <path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
    "calculator": '<rect width="16" height="20" x="4" y="2" rx="2"/> <line x1="8" x2="16" y1="6" y2="6"/> <line x1="16" x2="16" y1="14" y2="18"/> <path d="M16 10h.01"/> <path d="M12 10h.01"/> <path d="M8 10h.01"/> <path d="M12 14h.01"/> <path d="M8 14h.01"/> <path d="M12 18h.01"/> <path d="M8 18h.01"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/> <circle cx="12" cy="13" r="3"/>',
    "chart-column": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/> <path d="M18 17V9"/> <path d="M13 17V5"/> <path d="M8 17v-3"/>',
    "bug": '<path d="m8 2 1.88 1.88"/> <path d="M14.12 3.88 16 2"/> <path d="M9 7.13v-1a3.003 3.003 0 1 1 6 0v1"/> <path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6"/> <path d="M12 20v-9"/> <path d="M6.53 9C4.6 8.8 3 7.1 3 5"/> <path d="M6 13H2"/> <path d="M3 21c0-2.1 1.7-3.9 3.8-4"/> <path d="M20.97 5c0 2.1-1.6 3.8-3.5 4"/> <path d="M22 13h-4"/> <path d="M17.2 17c2.1.1 3.8 1.9 3.8 4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "circle-check": '<circle cx="12" cy="12" r="10"/> <path d="m9 12 2 2 4-4"/>',
    "cog": '<path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/> <path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/> <path d="M12 2v2"/> <path d="M12 22v-2"/> <path d="m17 20.66-1-1.73"/> <path d="M11 10.27 7 3.34"/> <path d="m20.66 17-1.73-1"/> <path d="m3.34 7 1.73 1"/> <path d="M14 12h8"/> <path d="M2 12h2"/> <path d="m20.66 7-1.73 1"/> <path d="m3.34 17 1.73-1"/> <path d="m17 3.34-1 1.73"/> <path d="m11 13.73-4 6.93"/>',
    "combine": '<path d="M10 18H5a3 3 0 0 1-3-3v-1"/> <path d="M14 2a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2"/> <path d="M20 2a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2"/> <path d="m7 21 3-3-3-3"/> <rect x="14" y="14" width="8" height="8" rx="2"/> <rect x="2" y="2" width="8" height="8" rx="2"/>',
    "corner-down-left": '<polyline points="9 10 4 15 9 20"/> <path d="M20 4v7a4 4 0 0 1-4 4H4"/>',
    "crosshair": '<circle cx="12" cy="12" r="10"/> <line x1="22" x2="18" y1="12" y2="12"/> <line x1="6" x2="2" y1="12" y2="12"/> <line x1="12" x2="12" y1="6" y2="2"/> <line x1="12" x2="12" y1="22" y2="18"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/> <path d="M3 5V19A9 3 0 0 0 21 19V5"/> <path d="M3 12A9 3 0 0 0 21 12"/>',
    "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/> <circle cx="12" cy="12" r="3"/>',
    "file-down": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/> <path d="M14 2v4a2 2 0 0 0 2 2h4"/> <path d="M12 18v-6"/> <path d="m9 15 3 3 3-3"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/> <path d="M14 2v4a2 2 0 0 0 2 2h4"/> <path d="M10 9H8"/> <path d="M16 13H8"/> <path d="M16 17H8"/>',
    "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "globe": '<circle cx="12" cy="12" r="10"/> <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/> <path d="M2 12h20"/>',
    "grid-3x3": '<rect width="18" height="18" x="3" y="3" rx="2"/> <path d="M3 9h18"/> <path d="M3 15h18"/> <path d="M9 3v18"/> <path d="M15 3v18"/>',
    "image": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/> <circle cx="9" cy="9" r="2"/> <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/> <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/> <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "layout-template": '<rect width="18" height="7" x="3" y="3" rx="1"/> <rect width="9" height="7" x="3" y="14" rx="1"/> <rect width="5" height="7" x="16" y="14" rx="1"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/> <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "list-checks": '<path d="m3 17 2 2 4-4"/> <path d="m3 7 2 2 4-4"/> <path d="M13 6h8"/> <path d="M13 12h8"/> <path d="M13 18h8"/>',
    "locate-fixed": '<line x1="2" x2="5" y1="12" y2="12"/> <line x1="19" x2="22" y1="12" y2="12"/> <line x1="12" x2="12" y1="2" y2="5"/> <line x1="12" x2="12" y1="19" y2="22"/> <circle cx="12" cy="12" r="7"/> <circle cx="12" cy="12" r="3"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/> <path d="m9 12 2 2 4-4"/>',
    "paperclip": '<path d="M13.234 20.252 21 12.3"/> <path d="m16 6-8.414 8.586a2 2 0 0 0 0 2.828 2 2 0 0 0 2.828 0l8.414-8.586a4 4 0 0 0 0-5.656 4 4 0 0 0-5.656 0l-8.415 8.585a6 6 0 1 0 8.486 8.486"/>',
    "mic": '<path d="M12 19v3"/> <path d="M19 10v2a7 7 0 0 1-14 0v-2"/> <rect x="9" y="2" width="6" height="13" rx="3"/>',
    "map-pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/> <circle cx="12" cy="10" r="3"/>',
    "message-circle-question": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/> <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/> <path d="M12 17h.01"/>',
    "minus": '<path d="M5 12h14"/>',
    "mountain": '<path d="m8 3 4 8 5-5 5 15H2L8 3z"/>',
    "palette": '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/> <circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/> <circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/> <circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/> <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/> <path d="m15 5 4 4"/>',
    "puzzle": '<path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"/>',
    "refresh-cw": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/> <path d="M21 3v5h-5"/> <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/> <path d="M8 16H3v5"/>',
    "route": '<circle cx="6" cy="19" r="3"/> <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/> <circle cx="18" cy="5" r="3"/>',
    "ruler": '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z"/> <path d="m14.5 12.5 2-2"/> <path d="m11.5 9.5 2-2"/> <path d="m8.5 6.5 2-2"/> <path d="m17.5 15.5 2-2"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/> <path d="M17 3h2a2 2 0 0 1 2 2v2"/> <path d="M21 17v2a2 2 0 0 1-2 2h-2"/> <path d="M7 21H5a2 2 0 0 1-2-2v-2"/>',
    "search": '<circle cx="11" cy="11" r="8"/> <path d="m21 21-4.3-4.3"/>',
    "square-dashed-mouse-pointer": '<path d="M12.034 12.681a.498.498 0 0 1 .647-.647l9 3.5a.5.5 0 0 1-.033.943l-3.444 1.068a1 1 0 0 0-.66.66l-1.067 3.443a.5.5 0 0 1-.943.033z"/> <path d="M5 3a2 2 0 0 0-2 2"/> <path d="M19 3a2 2 0 0 1 2 2"/> <path d="M5 21a2 2 0 0 1-2-2"/> <path d="M9 3h1"/> <path d="M9 21h2"/> <path d="M14 3h1"/> <path d="M3 9v1"/> <path d="M21 9v2"/> <path d="M3 14v1"/>',
    "square-terminal": '<path d="m7 11 2-2-2-2"/> <path d="M11 13h4"/> <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>',
    "table-2": '<path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>',
    "tag": '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/> <circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
    "trash-2": '<path d="M3 6h18"/> <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/> <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/> <line x1="10" x2="10" y1="11" y2="17"/> <line x1="14" x2="14" y1="11" y2="17"/>',
    "thumbs-down": '<path d="M17 14V2"/> <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>',
    "thumbs-up": '<path d="M7 10v12"/> <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
    "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/> <path d="M12 9v4"/> <path d="M12 17h.01"/>',
    "wand-sparkles": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/> <path d="m14 7 3 3"/> <path d="M5 6v4"/> <path d="M19 14v4"/> <path d="M10 2v2"/> <path d="M7 8H3"/> <path d="M21 16h-4"/> <path d="M11 3H9"/>',
    "x": '<path d="M18 6 6 18"/> <path d="m6 6 12 12"/>',
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/> <path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "trending-up": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/> <polyline points="16 7 22 7 22 13"/>',
    "satellite": '<path d="M13 7 9 3 5 7l4 4"/> <path d="m17 11 4 4-4 4-4-4"/> <path d="m8 12 4 4 6-6-4-4Z"/> <path d="m16 8 3-3"/> <path d="M9 21a6 6 0 0 0-6-6"/>',
    "printer": '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/> <path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/> <rect x="6" y="14" width="12" height="8" rx="1"/>',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/> <path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/> <circle cx="12" cy="7" r="4"/>',
    "external-link": '<path d="M15 3h6v6"/> <path d="M10 14 21 3"/> <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/> <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "maximize-2": '<polyline points="15 3 21 3 21 9"/> <polyline points="9 21 3 21 3 15"/> <line x1="21" x2="14" y1="3" y2="10"/> <line x1="3" x2="10" y1="21" y2="14"/>',
    "keyboard": '<path d="M10 8h.01"/> <path d="M12 12h.01"/> <path d="M14 8h.01"/> <path d="M16 12h.01"/> <path d="M18 8h.01"/> <path d="M6 8h.01"/> <path d="M7 16h10"/> <path d="M8 12h.01"/> <rect width="20" height="16" x="2" y="4" rx="2"/>',
    "message-circle": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    "circle-dashed": '<path d="M10.1 2.182a10 10 0 0 1 3.8 0"/> <path d="M13.9 21.818a10 10 0 0 1-3.8 0"/> <path d="M17.609 3.721a10 10 0 0 1 2.69 2.7"/> <path d="M2.182 13.9a10 10 0 0 1 0-3.8"/> <path d="M20.279 17.609a10 10 0 0 1-2.7 2.69"/> <path d="M21.818 10.1a10 10 0 0 1 0 3.8"/> <path d="M3.721 6.391a10 10 0 0 1 2.7-2.69"/> <path d="M6.391 20.279a10 10 0 0 1-2.69-2.7"/>',
    "circle-dot": '<circle cx="12" cy="12" r="10"/> <circle cx="12" cy="12" r="1"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/> <path d="M8.12 8.12 12 12"/> <path d="M20 4 8.12 15.88"/> <circle cx="6" cy="18" r="3"/> <path d="M14.8 14.8 20 20"/>',
    "squares-intersect": '<path d="M10 22a2 2 0 0 1-2-2"/> <path d="M14 2a2 2 0 0 1 2 2"/> <path d="M16 22h-2"/> <path d="M2 10V8"/> <path d="M2 4a2 2 0 0 1 2-2"/> <path d="M20 8a2 2 0 0 1 2 2"/> <path d="M22 14v2"/> <path d="M22 20a2 2 0 0 1-2 2"/> <path d="M4 16a2 2 0 0 1-2-2"/> <path d="M8 10a2 2 0 0 1 2-2h5a1 1 0 0 1 1 1v5a2 2 0 0 1-2 2H9a1 1 0 0 1-1-1z"/> <path d="M8 2h2"/>',
    "squares-subtract": '<path d="M10 22a2 2 0 0 1-2-2"/> <path d="M16 22h-2"/> <path d="M16 4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3a1 1 0 0 0 1-1v-5a2 2 0 0 1 2-2h5a1 1 0 0 0 1-1z"/> <path d="M20 8a2 2 0 0 1 2 2"/> <path d="M22 14v2"/> <path d="M22 20a2 2 0 0 1-2 2"/>',
    "squares-unite": '<path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3a1 1 0 0 0 1 1h3a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2v-3a1 1 0 0 0-1-1z"/>',
    "squares-exclude": '<path d="M16 12v2a2 2 0 0 1-2 2H9a1 1 0 0 0-1 1v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h0"/> <path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3a1 1 0 0 1-1 1h-5a2 2 0 0 0-2 2v2"/>',
    "blend": '<circle cx="9" cy="9" r="7"/> <circle cx="15" cy="15" r="7"/>',
    "split": '<path d="M16 3h5v5"/> <path d="M8 3H3v5"/> <path d="M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3"/> <path d="m15 9 6-6"/>',
    "earth": '<path d="M21.54 15H17a2 2 0 0 0-2 2v4.54"/> <path d="M7 3.34V5a3 3 0 0 0 3 3a2 2 0 0 1 2 2c0 1.1.9 2 2 2a2 2 0 0 0 2-2c0-1.1.9-2 2-2h3.17"/> <path d="M11 21.95V18a2 2 0 0 0-2-2a2 2 0 0 1-2-2v-1a2 2 0 0 0-2-2H2.05"/> <circle cx="12" cy="12" r="10"/>',
    "list-plus": '<path d="M11 12H3"/> <path d="M16 6H3"/> <path d="M16 18H3"/> <path d="M18 9v6"/> <path d="M21 12h-6"/>',
    "list-minus": '<path d="M11 12H3"/> <path d="M16 6H3"/> <path d="M16 18H3"/> <path d="M21 12h-6"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "waypoints": '<circle cx="12" cy="4.5" r="2.5"/> <path d="m10.2 6.3-3.9 3.9"/> <circle cx="4.5" cy="12" r="2.5"/> <path d="M7 12h10"/> <circle cx="19.5" cy="12" r="2.5"/> <path d="m13.8 17.7 3.9-3.9"/> <circle cx="12" cy="19.5" r="2.5"/>',
    "spline": '<circle cx="19" cy="5" r="2"/> <circle cx="5" cy="19" r="2"/> <path d="M5 17A12 12 0 0 1 17 5"/>',
    "pentagon": '<path d="M10.83 2.38a2 2 0 0 1 2.34 0l8 5.74a2 2 0 0 1 .73 2.25l-3.04 9.26a2 2 0 0 1-1.9 1.37H7.04a2 2 0 0 1-1.9-1.37L2.1 10.37a2 2 0 0 1 .73-2.25z"/>',
    "hexagon": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>',
    "square-dashed": '<path d="M5 3a2 2 0 0 0-2 2"/> <path d="M19 3a2 2 0 0 1 2 2"/> <path d="M21 19a2 2 0 0 1-2 2"/> <path d="M5 21a2 2 0 0 1-2-2"/> <path d="M9 3h1"/> <path d="M9 21h1"/> <path d="M14 3h1"/> <path d="M14 21h1"/> <path d="M3 9v1"/> <path d="M21 9v1"/> <path d="M3 14v1"/> <path d="M21 14v1"/>',
    "chart-scatter": '<circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/> <circle cx="18.5" cy="5.5" r=".5" fill="currentColor"/> <circle cx="11.5" cy="11.5" r=".5" fill="currentColor"/> <circle cx="7.5" cy="16.5" r=".5" fill="currentColor"/> <circle cx="17.5" cy="14.5" r=".5" fill="currentColor"/> <path d="M3 3v16a2 2 0 0 0 2 2h16"/>',
    "triangle": '<path d="M13.73 4a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>',
    "flame": '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    "hash": '<line x1="4" x2="20" y1="9" y2="9"/> <line x1="4" x2="20" y1="15" y2="15"/> <line x1="10" x2="8" y1="3" y2="21"/> <line x1="16" x2="14" y1="3" y2="21"/>',
    "sigma": '<path d="M18 7V5a1 1 0 0 0-1-1H6.5a.5.5 0 0 0-.4.8l4.5 6a2 2 0 0 1 0 2.4l-4.5 6a.5.5 0 0 0 .4.8H17a1 1 0 0 0 1-1v-2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/> <path d="M12 2v2"/> <path d="M12 20v2"/> <path d="m4.93 4.93 1.41 1.41"/> <path d="m17.66 17.66 1.41 1.41"/> <path d="M2 12h2"/> <path d="M20 12h2"/> <path d="m6.34 17.66-1.41 1.41"/> <path d="m19.07 4.93-1.41 1.41"/>',
    "triangle-right": '<path d="M22 18a2 2 0 0 1-2 2H3c-1.1 0-1.3-.6-.4-1.3L20.4 4.3c.9-.7 1.6-.4 1.6.7Z"/>',
    "compass": '<path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z"/> <circle cx="12" cy="12" r="10"/>',
    "waves": '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/> <path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/> <path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
    "droplets": '<path d="M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z"/> <path d="M12.56 6.6A10.97 10.97 0 0 0 14 3.02c.5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a6.98 6.98 0 0 1-11.91 4.97"/>',
    "shapes": '<path d="M8.3 10a.7.7 0 0 1-.626-1.079L11.4 3a.7.7 0 0 1 1.198-.043L16.3 8.9a.7.7 0 0 1-.572 1.1Z"/> <rect x="3" y="14" width="7" height="7" rx="1"/> <circle cx="17.5" cy="17.5" r="3.5"/>',
    "grid-2x2": '<path d="M12 3v18"/> <path d="M3 12h18"/> <rect x="3" y="3" width="18" height="18" rx="2"/>',
    "arrow-right-left": '<path d="m16 3 4 4-4 4"/> <path d="M20 7H4"/> <path d="m8 21-4-4 4-4"/> <path d="M4 17h16"/>',
    "paint-bucket": '<path d="m19 11-8-8-8.6 8.6a2 2 0 0 0 0 2.8l5.2 5.2c.8.8 2 .8 2.8 0L19 11Z"/> <path d="m5 2 5 5"/> <path d="M2 13h15"/> <path d="M22 20a2 2 0 1 1-4 0c0-1.6 1.7-2.4 2-4 .3 1.6 2 2.4 2 4Z"/>',
    "radar": '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/> <path d="M4 6h.01"/> <path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/> <path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/> <path d="M12 18h.01"/> <path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/> <circle cx="12" cy="12" r="2"/> <path d="m13.41 10.59 5.66-5.66"/>',
    "chart-area": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/> <path d="M7 11.207a.5.5 0 0 1 .146-.353l2-2a.5.5 0 0 1 .708 0l3.292 3.292a.5.5 0 0 0 .708 0l4.292-4.292a.5.5 0 0 1 .854.353V16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1z"/>',
    "timer": '<line x1="10" x2="14" y1="2" y2="2"/> <line x1="12" x2="15" y1="14" y2="11"/> <circle cx="12" cy="14" r="8"/>',
    "arrow-down-wide-narrow": '<path d="m3 16 4 4 4-4"/> <path d="M7 20V4"/> <path d="M11 4h10"/> <path d="M11 8h7"/> <path d="M11 12h4"/>',
    "copy-x": '<line x1="12" x2="18" y1="12" y2="18"/> <line x1="12" x2="18" y1="18" y2="12"/> <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/> <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "package": '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/> <path d="M12 22V12"/> <polyline points="3.29 7 12 12 20.71 7"/> <path d="m7.5 4.27 9 5.15"/>',
    "move": '<path d="M12 2v20"/> <path d="m15 19-3 3-3-3"/> <path d="m19 9 3 3-3 3"/> <path d="M2 12h20"/> <path d="m5 9-3 3 3 3"/> <path d="m9 5 3-3 3 3"/>',
    "rotate-cw": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/> <path d="M21 3v5h-5"/>',
    "magnet": '<path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/> <path d="m5 8 4 4"/> <path d="m12 15 4 4"/>',
    "boxes": '<path d="M2.97 12.92A2 2 0 0 0 2 14.63v3.24a2 2 0 0 0 .97 1.71l3 1.8a2 2 0 0 0 2.06 0L12 19v-5.5l-5-3-4.03 2.42Z"/> <path d="m7 16.5-4.74-2.85"/> <path d="m7 16.5 5-3"/> <path d="M7 16.5v5.17"/> <path d="M12 13.5V19l3.97 2.38a2 2 0 0 0 2.06 0l3-1.8a2 2 0 0 0 .97-1.71v-3.24a2 2 0 0 0-.97-1.71L17 10.5l-5 3Z"/> <path d="m17 16.5-5-3"/> <path d="m17 16.5 4.74-2.85"/> <path d="M17 16.5v5.17"/> <path d="M7.97 4.42A2 2 0 0 0 7 6.13v4.37l5 3 5-3V6.13a2 2 0 0 0-.97-1.71l-3-1.8a2 2 0 0 0-2.06 0l-3 1.8Z"/> <path d="M12 8 7.26 5.15"/> <path d="m12 8 4.74-2.85"/> <path d="M12 13.5V8"/>',
    "mountain-snow": '<path d="m8 3 4 8 5-5 5 15H2L8 3z"/> <path d="M4.14 15.08c2.62-1.57 5.24-1.43 7.86.42 2.74 1.94 5.49 2 8.23.19"/>',
    "merge": '<path d="m8 6 4-4 4 4"/> <path d="M12 2v10.3a4 4 0 0 1-1.172 2.872L4 22"/> <path d="m20 22-5-5"/>',
    "group": '<path d="M3 7V5c0-1.1.9-2 2-2h2"/> <path d="M17 3h2c1.1 0 2 .9 2 2v2"/> <path d="M21 17v2c0 1.1-.9 2-2 2h-2"/> <path d="M7 21H5c-1.1 0-2-.9-2-2v-2"/> <rect width="7" height="5" x="7" y="7" rx="1"/> <rect width="7" height="5" x="10" y="12" rx="1"/>',
    "list-filter": '<path d="M3 6h18"/> <path d="M7 12h10"/> <path d="M10 18h4"/>',
    "text-cursor-input": '<path d="M12 20h-1a2 2 0 0 1-2-2 2 2 0 0 1-2 2H6"/> <path d="M13 8h7a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-7"/> <path d="M5 16H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h1"/> <path d="M6 4h1a2 2 0 0 1 2 2 2 2 0 0 1 2-2h1"/> <path d="M9 6v12"/>',
    "eraser": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/> <path d="M22 21H7"/> <path d="m5 11 9 9"/>',
    "map-pinned": '<path d="M18 8c0 3.613-3.869 7.429-5.393 8.795a1 1 0 0 1-1.214 0C9.87 15.429 6 11.613 6 8a6 6 0 0 1 12 0"/> <circle cx="12" cy="8" r="2"/> <path d="M8.714 14h-3.71a1 1 0 0 0-.948.683l-2.004 6A1 1 0 0 0 3 22h18a1 1 0 0 0 .948-1.316l-2-6a1 1 0 0 0-.949-.684h-3.712"/>',
    "list-ordered": '<path d="M10 12h11"/> <path d="M10 18h11"/> <path d="M10 6h11"/> <path d="M4 10h2"/> <path d="M4 6h1v4"/> <path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
    "git-fork": '<circle cx="12" cy="18" r="3"/> <circle cx="6" cy="6" r="3"/> <circle cx="18" cy="6" r="3"/> <path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9"/> <path d="M12 12v3"/>',
    "plug": '<path d="M12 22v-5"/> <path d="M9 8V2"/> <path d="M15 8V2"/> <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    "circle-stop": '<circle cx="12" cy="12" r="10"/> <rect x="9" y="9" width="6" height="6" rx="1"/>',
    "git-compare": '<circle cx="18" cy="18" r="3"/> <circle cx="6" cy="6" r="3"/> <path d="M13 6h3a2 2 0 0 1 2 2v7"/> <path d="M11 18H8a2 2 0 0 1-2-2V9"/>',
    "scroll-text": '<path d="M15 12h-5"/> <path d="M15 8h-5"/> <path d="M19 17V5a2 2 0 0 0-2-2H4"/> <path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>',
    "hourglass": '<path d="M5 22h14"/> <path d="M5 2h14"/> <path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22"/> <path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/> <polyline points="7 10 12 15 17 10"/> <line x1="12" x2="12" y1="15" y2="3"/>',
    "mouse-pointer-click": '<path d="M14 4.1 12 6"/> <path d="m5.1 8-2.9-.8"/> <path d="m6 12-1.9 2"/> <path d="M7.2 2.2 8 5.1"/> <path d="M9.037 9.69a.498.498 0 0 1 .653-.653l11 4.5a.5.5 0 0 1-.074.949l-4.349 1.041a1 1 0 0 0-.74.739l-1.04 4.35a.5.5 0 0 1-.95.074z"/>',
    "calendar-clock": '<path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5"/> <path d="M16 2v4"/> <path d="M8 2v4"/> <path d="M3 10h5"/> <path d="M17.5 17.5 16 16.3V14"/> <circle cx="16" cy="16" r="6"/>',
    "menu": '<line x1="4" x2="20" y1="12" y2="12"/> <line x1="4" x2="20" y1="6" y2="6"/> <line x1="4" x2="20" y1="18" y2="18"/>',
    "folder-plus": '<path d="M12 10v6"/> <path d="M9 13h6"/> <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "folder-input": '<path d="M2 9V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1"/> <path d="M2 13h10"/> <path d="m9 16 3-3-3-3"/>',
    "file-plus": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/> <path d="M14 2v4a2 2 0 0 0 2 2h4"/> <path d="M9 15h6"/> <path d="M12 18v-6"/>',
    "info": '<circle cx="12" cy="12" r="10"/> <path d="M12 16v-4"/> <path d="M12 8h.01"/>',
    "list-tree": '<path d="M21 12h-8"/> <path d="M21 6H8"/> <path d="M21 18h-8"/> <path d="M3 6v4c0 1.1.9 2 2 2h3"/> <path d="M3 10v6c0 1.1.9 2 2 2h3"/>',
    "arrow-up-down": '<path d="m21 16-4 4-4-4"/> <path d="M17 20V4"/> <path d="m3 8 4-4 4 4"/> <path d="M7 4v16"/>',
    "sliders-horizontal": '<line x1="21" x2="14" y1="4" y2="4"/> <line x1="10" x2="3" y1="4" y2="4"/> <line x1="21" x2="12" y1="12" y2="12"/> <line x1="8" x2="3" y1="12" y2="12"/> <line x1="21" x2="16" y1="20" y2="20"/> <line x1="12" x2="3" y1="20" y2="20"/> <line x1="14" x2="14" y1="2" y2="6"/> <line x1="8" x2="8" y1="10" y2="14"/> <line x1="16" x2="16" y1="18" y2="22"/>',
    "clock": '<circle cx="12" cy="12" r="10"/> <polyline points="12 6 12 12 16 14"/>',
    "variable": '<path d="M8 21s-4-3-4-9 4-9 4-9"/> <path d="M16 3s4 3 4 9-4 9-4 9"/> <line x1="15" x2="9" y1="9" y2="15"/> <line x1="9" x2="15" y1="9" y2="15"/>',
    "box": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/> <path d="m3.3 7 8.7 5 8.7-5"/> <path d="M12 22V12"/>',
    "type": '<polyline points="4 7 4 4 20 4 20 7"/> <line x1="9" x2="15" y1="20" y2="20"/> <line x1="12" x2="12" y1="4" y2="20"/>',
    "list": '<path d="M3 12h.01"/> <path d="M3 18h.01"/> <path d="M3 6h.01"/> <path d="M8 12h13"/> <path d="M8 18h13"/> <path d="M8 6h13"/>',
    "map": '<path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"/> <path d="M15 5.764v15"/> <path d="M9 3.236v15"/>',
    "navigation": '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
    "book-copy": '<path d="M2 16V4a2 2 0 0 1 2-2h11"/> <path d="M22 18H11a2 2 0 1 0 0 4h10.5a.5.5 0 0 0 .5-.5v-15a.5.5 0 0 0-.5-.5H11a2 2 0 0 0-2 2v12"/> <path d="M5 14H4a2 2 0 1 0 0 4h1"/>',
    "files": '<path d="M20 7h-3a2 2 0 0 1-2-2V2"/> <path d="M9 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7l4 4v10a2 2 0 0 1-2 2Z"/> <path d="M3 7.6v12.8A1.6 1.6 0 0 0 4.6 22h9.8"/>',
    "building-2": '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/> <path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/> <path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/> <path d="M10 6h4"/> <path d="M10 10h4"/> <path d="M10 14h4"/> <path d="M10 18h4"/>',
    "file-code": '<path d="M10 12.5 8 15l2 2.5"/> <path d="m14 12.5 2 2.5-2 2.5"/> <path d="M14 2v4a2 2 0 0 0 2 2h4"/> <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/> <path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "map-pin-plus": '<path d="M19.914 11.105A7.298 7.298 0 0 0 20 10a8 8 0 0 0-16 0c0 4.993 5.539 10.193 7.399 11.799a1 1 0 0 0 1.202 0 32 32 0 0 0 .824-.738"/> <circle cx="12" cy="10" r="3"/> <path d="M16 18h6"/> <path d="M19 15v6"/>',
    "square-plus": '<rect width="18" height="18" x="3" y="3" rx="2"/> <path d="M8 12h8"/> <path d="M12 8v8"/>',
    "save": '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/> <path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/> <path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    "rotate-ccw": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/> <path d="M3 3v5h5"/>',
    "pen-tool": '<path d="M15.707 21.293a1 1 0 0 1-1.414 0l-1.586-1.586a1 1 0 0 1 0-1.414l5.586-5.586a1 1 0 0 1 1.414 0l1.586 1.586a1 1 0 0 1 0 1.414z"/> <path d="m18 13-1.375-6.874a1 1 0 0 0-.746-.776L3.235 2.028a1 1 0 0 0-1.207 1.207L5.35 15.879a1 1 0 0 0 .776.746L13 18"/> <path d="m2.3 2.3 7.286 7.286"/> <circle cx="11" cy="11" r="2"/>',
    "server": '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/> <rect width="20" height="8" x="2" y="14" rx="2" ry="2"/> <line x1="6" x2="6.01" y1="6" y2="6"/> <line x1="6" x2="6.01" y1="18" y2="18"/>',
    "between-vertical-start": '<rect width="7" height="13" x="3" y="8" rx="1"/> <path d="m15 2-3 3-3-3"/> <rect width="7" height="13" x="14" y="8" rx="1"/>',
    "clipboard-list": '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/> <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/> <path d="M12 11h4"/> <path d="M12 16h4"/> <path d="M8 11h.01"/> <path d="M8 16h.01"/>',
    "sheet": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/> <line x1="3" x2="21" y1="9" y2="9"/> <line x1="3" x2="21" y1="15" y2="15"/> <line x1="9" x2="9" y1="9" y2="21"/> <line x1="15" x2="15" y1="9" y2="21"/>',
    "pen-line": '<path d="M12 20h9"/> <path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"/>',
    "swatch-book": '<path d="M11 17a4 4 0 0 1-8 0V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2Z"/> <path d="M16.7 13H19a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H7"/> <path d="M 7 17h.01"/> <path d="m11 8 2.3-2.3a2.4 2.4 0 0 1 3.404.004L18.6 7.6a2.4 2.4 0 0 1 .026 3.434L9.9 19.8"/>',
    "paintbrush": '<path d="m14.622 17.897-10.68-2.913"/> <path d="M18.376 2.622a1 1 0 1 1 3.002 3.002L17.36 9.643a.5.5 0 0 0 0 .707l.944.944a2.41 2.41 0 0 1 0 3.408l-.944.944a.5.5 0 0 1-.707 0L8.354 7.348a.5.5 0 0 1 0-.707l.944-.944a2.41 2.41 0 0 1 3.408 0l.944.944a.5.5 0 0 0 .707 0z"/> <path d="M9 8c-1.804 2.71-3.97 3.46-6.583 3.948a.507.507 0 0 0-.302.819l7.32 8.883a1 1 0 0 0 1.185.204C12.735 20.405 16 16.792 16 15"/>',
    "scan-search": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/> <path d="M17 3h2a2 2 0 0 1 2 2v2"/> <path d="M21 17v2a2 2 0 0 1-2 2h-2"/> <path d="M7 21H5a2 2 0 0 1-2-2v-2"/> <circle cx="12" cy="12" r="3"/> <path d="m16 16-1.9-1.9"/>',
    "arrow-up-from-line": '<path d="m18 9-6-6-6 6"/> <path d="M12 3v14"/> <path d="M5 21h14"/>',
    "zoom-in": '<circle cx="11" cy="11" r="8"/> <line x1="21" x2="16.65" y1="21" y2="16.65"/> <line x1="11" x2="11" y1="8" y2="14"/> <line x1="8" x2="14" y1="11" y2="11"/>',
    "chart-line": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/> <path d="m19 9-5 5-4-4-3 3"/>',
    "chart-spline": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/> <path d="M7 16c.5-2 1.5-7 4-7 2 0 2 3 4 3 2.5 0 4.5-5 5-7"/>',
    "folder-search": '<path d="M10.7 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v4.1"/> <path d="m21 21-1.9-1.9"/> <circle cx="17" cy="17" r="3"/>',
    "square-x": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/> <path d="m15 9-6 6"/> <path d="m9 9 6 6"/>',
    "film": '<rect width="18" height="18" x="3" y="3" rx="2"/> <path d="M7 3v18"/> <path d="M3 7.5h4"/> <path d="M3 12h18"/> <path d="M3 16.5h4"/> <path d="M17 3v18"/> <path d="M17 7.5h4"/> <path d="M17 16.5h4"/>',
    "clapperboard": '<path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z"/> <path d="m6.2 5.3 3.1 3.9"/> <path d="m12.4 3.4 3.1 4"/> <path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "bookmark": '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    "bus": '<path d="M8 6v6"/> <path d="M15 6v6"/> <path d="M2 12h19.6"/> <path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/> <circle cx="7" cy="18" r="2"/> <path d="M9 18h5"/> <circle cx="16" cy="18" r="2"/>',
    "file-search": '<path d="M14 2v4a2 2 0 0 0 2 2h4"/> <path d="M4.268 21a2 2 0 0 0 1.727 1H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v3"/> <path d="m9 18-1.5-1.5"/> <circle cx="5" cy="14" r="3"/>',
    "frame": '<line x1="22" x2="2" y1="6" y2="6"/> <line x1="22" x2="2" y1="18" y2="18"/> <line x1="6" x2="6" y1="2" y2="22"/> <line x1="18" x2="18" y1="2" y2="22"/>',
    "pipette": '<path d="m12 9-8.414 8.414A2 2 0 0 0 3 18.828v1.344a2 2 0 0 1-.586 1.414A2 2 0 0 1 3.828 21h1.344a2 2 0 0 0 1.414-.586L15 12"/> <path d="m18 9 .4.4a1 1 0 1 1-3 3l-3.8-3.8a1 1 0 1 1 3-3l.4.4 3.4-3.4a1 1 0 1 1 3 3z"/> <path d="m2 22 .414-.414"/>',
    "workflow": '<rect width="8" height="8" x="3" y="3" rx="2"/> <path d="M7 11v4a2 2 0 0 0 2 2h4"/> <rect width="8" height="8" x="13" y="13" rx="2"/>',
}

FALLBACK = {
    "workflow": "gear",
    "plug": "link", "circle-stop": "stop", "git-compare": "raster", "scroll-text": "file",
    "hourglass": "clock", "download": "download", "mouse-pointer-click": "polygon",
    "calendar-clock": "clock", "menu": "layout", "folder-plus": "folder", "folder-input": "folder",
    "file-plus": "file", "info": "eye", "list-tree": "layers", "arrow-up-down": "layers",
    "sliders-horizontal": "gear", "clock": "clock", "variable": "code", "box": "package",
    "type": "pencil", "list": "checklist", "map": "layout", "navigation": "crs",
    "book-copy": "book", "files": "file", "building-2": "home", "file-code": "file_html",
    "lock": "lock", "map-pin-plus": "pin", "square-plus": "plus", "save": "download",
    "rotate-ccw": "undo", "pen-tool": "pencil", "server": "server",
    "between-vertical-start": "table", "clipboard-list": "checklist", "sheet": "table",
    "pen-line": "pencil", "swatch-book": "layers", "paintbrush": "pencil", "scan-search": "search",
    "arrow-up-from-line": "arrow_up", "zoom-in": "search", "chart-line": "trending",
    "chart-spline": "trending", "folder-search": "folder", "square-x": "close", "film": "camera",
    "clapperboard": "camera", "bookmark": "pin", "bus": "route", "file-search": "file",
    "frame": "expand", "pipette": "points",
    "circle-dashed": "buffer", "circle-dot": "centroid", "scissors": "clip",
    "squares-intersect": "intersect", "squares-subtract": "clip", "squares-unite": "merge",
    "squares-exclude": "intersect", "blend": "dissolve", "split": "polygon", "earth": "crs",
    "list-plus": "table", "list-minus": "table", "wrench": "warning", "waypoints": "polyline",
    "spline": "polyline", "pentagon": "polygon", "hexagon": "hexgrid", "square-dashed": "polygon",
    "chart-scatter": "points", "triangle": "polygon", "flame": "heatmap", "hash": "table",
    "sigma": "chart", "sun": "terrain", "triangle-right": "terrain", "compass": "crs",
    "waves": "contour", "droplets": "terrain", "shapes": "polygon", "grid-2x2": "raster",
    "arrow-right-left": "download", "paint-bucket": "raster", "radar": "measure",
    "chart-area": "raster", "timer": "isochrone", "arrow-down-wide-narrow": "table",
    "shield": "shield", "shield-check": "shield_check",
    "paperclip": "paperclip",
    "copy-x": "copy", "zap": "bolt", "package": "package", "move": "expand", "rotate-cw": "redo",
    "magnet": "points", "boxes": "cluster", "mountain-snow": "terrain", "merge": "join",
    "group": "merge", "list-filter": "funnel", "text-cursor-input": "pencil", "eraser": "trash",
    "map-pinned": "pin", "list-ordered": "classify", "git-fork": "route",
    "house": "home", "trending-up": "trending", "satellite": "satellite", "printer": "layout",
    "leaf": "points", "user": "person", "external-link": "external", "chevron-right": "chevron_right",
    "chevron-down": "chevron_down", "copy": "copy", "maximize-2": "expand",
    "keyboard": "terminal", "message-circle": "chat_bubble",
    "bug": "warning", "thumbs-up": "check", "thumbs-down": "close",
    "thumbs-up-solid": "check", "thumbs-down-solid": "close",
    "search": "search", "globe": "globe", "map-pin": "pin", "layers": "layers",
    "palette": "pencil", "tag": "pencil", "locate-fixed": "expand", "camera": "camera",
    "file-down": "download", "layout-template": "layout", "cog": "gear",
    "square-terminal": "terminal", "braces": "code", "database": "table", "table-2": "table",
    "chart-column": "chart", "file-text": "file", "message-circle-question": "chat_bubble",
    "trash-2": "trash", "pencil": "pencil", "crosshair": "crs", "ruler": "measure",
    "route": "route", "puzzle": "puzzle", "mountain": "terrain", "grid-3x3": "hexgrid",
    "wand-sparkles": "sparkles", "scan": "expand", "check": "check", "circle-check": "check", "list-checks": "check", "x": "close",
    "minus": "dash", "triangle-alert": "warning", "calculator": "table",
    "square-dashed-mouse-pointer": "polygon", "link": "link", "book-open": "book",
    "filter": "funnel", "combine": "merge", "folder-open": "folder", "eye": "eye",
    "image": "image", "sparkle": "spark", "arrow-up": "arrow_up", "corner-down-left": "undo",
    "refresh-cw": "undo", "undo-2": "undo", "redo-2": "redo", "history": "undo",
}

_RENDERER = None


def _renderer_class():

    global _RENDERER
    if _RENDERER is None:
        try:
            from qgis.PyQt.QtSvg import QSvgRenderer
            _RENDERER = QSvgRenderer
        except ImportError:
            _RENDERER = False
    return _RENDERER


def is_lucide(name: str) -> bool:
    return str(name or "").startswith(PREFIX)


def fallback(name: str) -> str:

    return FALLBACK.get(str(name or "")[len(PREFIX):], "circle")


def svg(name: str, colour: QColor, size: float) -> bytes:

    key = str(name or "")[len(PREFIX):]
    hex_colour = colour.name()


    stroke = 2.4 if size <= 13 else 2.2 if size <= 16 else 2.0
    if key == "sparkle":
        body = _SPARKLE.format(colour=hex_colour)
    elif key.endswith("-solid"):
        body = f'<g fill="{hex_colour}">{SHAPES.get(key[:-6], "")}</g>'
    else:
        body = SHAPES.get(key, "")
    opacity = colour.alphaF()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{hex_colour}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" opacity="{opacity:.3f}">{body}</svg>'
    ).encode()


def render(painter: QPainter, name: str, colour: QColor, size: float) -> bool:


    renderer_class = _renderer_class()
    key = str(name or "")[len(PREFIX):]
    shape = key[:-6] if key.endswith("-solid") else key
    if not renderer_class or (key != "sparkle" and shape not in SHAPES):
        return False
    renderer = renderer_class(QByteArray(svg(name, colour, size)))
    if not renderer.isValid():
        return False
    renderer.render(painter, QRectF(0, 0, size, size))
    return True


__all__ = ["PREFIX", "SHAPES", "fallback", "is_lucide", "render", "svg"]
