# Plugin icons shipped with AI Agent

Each file here is the icon of one open-source QGIS plugin, taken from that
plugin's own source repository at the URL in the table below. The artwork is
unchanged: no redrawing, no recolouring, no cropping into it. What was done is
mechanical, so that thirty-nine marks of different origins sit evenly in one
list. Each icon was trimmed to its own edges, scaled to fit a 128 by 128
transparent canvas, centred in it, and saved as a palette PNG. Seven of them were
SVG at the source and were rendered at that size instead. The plugin trims the
empty margin again when it paints a row, because an installed plugin's icon is
whatever file its own metadata.txt points at and comes at no particular size;
that trim only removes transparent pixels, and never cuts into the mark.

They ship because the plugin directory in Settings lists plugins the user has
not installed. For an installed one the plugin reads the icon already in the
user's own plugins folder; for the rest there is no such file, and a row with
no mark reads as a broken image rather than as something to install.

Every plugin listed is published under a free licence, GPL-2.0-or-later,
GPL-3.0, LGPL-3.0, BSD-3-Clause or MIT, and its icon lives inside that licensed
source tree,
which is what allows it to be passed on. AI Agent by TerraLab is distributed
under GPL-2.0-or-later; the "or later" is what lets the GPL-3.0 icons travel
with it, since a work carrying them is distributed under GPL-3.0 terms.

If you are the author of one of these plugins and would rather your icon were
not shipped here, write to yvann.barbot@terra-lab.ai and it will be removed.

Two of the files here are ours rather than someone else's: `AI_Edit.png` and
`AI_Segmentation.png` are the icons of the two other TerraLab plugins, copied
from their own source trees. Settings > More plugins wears them, and the
plugin directory uses them for the same reason it uses the rest, so that a
plugin the user has not installed still shows its real mark.

| File | Plugin | Author | Licence | Source URL |
|---|---|---|---|---|
| cadastre.png | Cadastre | 3Liz | GPL-2.0-or-later | https://plugins.qgis.org/plugins/cadastre/version/2.3.1/download/ (cadastre/icon.png inside the plugin zip) |
| contour.png | Contour plugin | Chris Crook, Lionel Roubeyrie (Land Information New Zealand) | BSD-3-Clause | https://plugins.qgis.org/plugins/contour/version/3.0.0/download/ (contour/contour.png inside the plugin zip) |
| DataPlotly.png | Data Plotly | Matteo Ghetta (Faunalia) | GPL-2.0-or-later | https://raw.githubusercontent.com/ghtmtt/DataPlotly/master/DataPlotly/icon.png |
| DigitizingTools.png | Digitizing Tools | Bernhard Ströbl, Angelos Tzotsos (NTUA) | GPL-2.0-or-later | https://raw.githubusercontent.com/bstroebl/DigitizingTools/master/digitizingtools.png |
| ee_plugin.png | Google Earth Engine | Gennadii Donchyts, Xavier C. Llano, Fedor Baart, Zac Deziel, Anthony Lukach | MIT | https://raw.githubusercontent.com/gee-community/qgis-earthengine-plugin/main/ee_plugin/icons/earth-engine.svg |
| FreehandRasterGeoreferencer.png | Freehand raster georeferencer | Guilhem Vellut | GPL-2.0-or-later | https://raw.githubusercontent.com/gvellut/FreehandRasterGeoreferencer/master/icon.png |
| GeoCoding.png | GeoCoding | Alessandro Pasotti | GPL-3.0-or-later | https://plugins.qgis.org/plugins/GeoCoding/version/2.20/download/ (GeoCoding/geocode_icon.png inside the plugin zip) |
| geometric_attributes.png | Geometric Attributes | Bjorn Nyberg | GPL-2.0-or-later | https://raw.githubusercontent.com/BjornNyberg/Geometric-Attributes-Toolbox/master/geometric_attributes/icon.jpg |
| go2streetview.png | go2streetview | Enrico Ferreguti | GPL-2.0-or-later (source headers; no LICENSE file ships) | https://plugins.qgis.org/plugins/go2streetview/version/10.0/download/ (go2streetview/res/icoStreetview.png inside the plugin zip) |
| GroupStats.png | Group Stats | Rajmund Szostok, ported to QGIS 3 by Borys Jurgiel and Faunalia | GPL-2.0-or-later | https://raw.githubusercontent.com/HenrikSpa/GroupStats/master/icon.png |
| HCMGIS.png | HCMGIS | Thang Quach | GPL-3.0 (LICENSE file) | https://raw.githubusercontent.com/thangqd/HCMGIS/master/icons/hcmgis_opendata.png |
| ImportPhotos.png | Import Photos | Marios S. Kyriakou and others, KIOS Research and Innovation Centre | GPL-3.0-or-later | https://plugins.qgis.org/plugins/ImportPhotos/version/3.0.8/download/ (ImportPhotos/icons/icon.png inside the plugin zip) |
| kmltools.png | KML Tools | Calvin Hamilton | GPL-2.0-or-later | https://raw.githubusercontent.com/hamiltoncj/qgis-kmltools-plugin/main/icon.png |
| latlontools.png | Lat Lon Tools | Calvin Hamilton | GPL-2.0-or-later | https://raw.githubusercontent.com/hamiltoncj/qgis-latlontools-plugin/main/icon.png |
| lftools.png | LF Tools | Leandro França | MIT | https://plugins.qgis.org/plugins/lftools/version/2.12.1/download/ (lftools/images/lftools_logo.png inside the plugin zip) |
| mask.png | Mask | Hugo Mercier, Xavier Culos, Régis Haubourg (Agence de l'eau Adour-Garonne) | GPL-2.0-or-later | https://plugins.qgis.org/plugins/mask/version/1.12.0/download/ (mask/resources/aeag_mask.png inside the plugin zip) |
| mmqgis.png | mmqgis | Michael Minn | GPL-2.0-only | https://plugins.qgis.org/plugins/mmqgis/version/2026.6.12/download/ (icons/mmqgis.png inside the plugin zip) |
| NNJoin.png | NNJoin | Håvard Tveite, NMBU | GPL-2.0-or-later | https://raw.githubusercontent.com/havatv/qgisnnjoinplugin/master/nnjoin.png |
| nominatim.png | OSM place search | Xavier Culos (Agence de l'eau Adour-Garonne) | GPL-2.0-or-later | https://plugins.qgis.org/plugins/nominatim/version/1.6.1/download/ (nominatim/resources/nominatim.png inside the plugin zip) |
| OpenTopography-DEM-Downloader.png | OpenTopography DEM Downloader | Kyaw Naing Win | LGPL-3.0 (LICENSE file); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/knwin/OpenTopography-DEM-Downloader-qgis-plugin/main/icon.png |
| ORStools.png | ORS Tools | HeiGIT gGmbH | MIT | https://raw.githubusercontent.com/GIScience/orstools-qgis-plugin/main/ORStools/gui/img/icon_orstools.png |
| OSMDownloader.png | OSM Downloader | Luiz Andrade | GPL-3.0-or-later | https://plugins.qgis.org/plugins/OSMDownloader/version/1.0.4/download/ (OSMDownloader/rectangle.png inside the plugin zip) |
| plugin_reloader.png | Plugin Reloader | Borys Jurgiel | GPL-3.0 (LICENSE file); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/borysiasty/plugin_reloader/master/reload.png |
| pointsamplingtool.png | Point sampling tool | Borys Jurgiel | GPL-3.0 (LICENSE file); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/borysiasty/pointsamplingtool/master/pointSamplingToolIcon.png |
| profiletool.png | Profile tool | Borys Jurgiel, Patrice Verchere, Etienne Tourigny, Javier Becerra | GPL-2.0-or-later | https://raw.githubusercontent.com/PANOimagen/profiletool/master/icons/profileIcon.png |
| qfieldsync.png | QField Sync | OPENGIS.ch | LGPL-3.0 (LICENSE file); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/opengisch/QFieldSync/master/qfieldsync/resources/icon.png |
| Qgis2threejs.png | Qgis2threejs | Minoru Akagi | GPL-2.0-or-later | https://raw.githubusercontent.com/minorua/Qgis2threejs/master/web/Qgis2threejs.png |
| qgis2web.png | qgis2web | Andrea Ordonselli, Tom Chadwin, Riccardo Klinger, Victor Olaya, Nyall Dawson | GPL-2.0-or-later | https://raw.githubusercontent.com/qgis2web/qgis2web/master/icons/qgis2web.png |
| qgsAzimuth.png | Azimuth and Distance | Mauricio de Paulo, Fred Laplante, Matthew Petroff and others | GPL-2.0-or-later (source headers; no LICENSE file ships) | https://plugins.qgis.org/plugins/qgsAzimuth/version/0.9.19/download/ (qgsAzimuth/qgsazimuth.png inside the plugin zip) |
| quick_map_services.png | NextGIS QuickMapServices | NextGIS | GPL-2.0-or-later | https://raw.githubusercontent.com/nextgis/quickmapservices/master/src/quick_map_services/icons/qms_logo.svg |
| QuickOSM.png | QuickOSM | Etienne Trimaille | GPL-2.0-or-later | https://raw.githubusercontent.com/3liz/QuickOSM/main/QuickOSM/resources/icons/QuickOSM.svg |
| SemiAutomaticClassificationPlugin.png | Semi-Automatic Classification Plugin | Luca Congedo | GPL-3.0-or-later | https://raw.githubusercontent.com/semiautomaticgit/SemiAutomaticClassificationPlugin/master/semiautomaticclassificationplugin.png |
| Serval.png | Serval | Radoslaw Pasiok for Lutra Consulting Ltd. | GPL-3.0 (Serval/license.md); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/lutraconsulting/serval/master/Serval/icons/serval_icon.svg |
| shapetools.png | Shape Tools | Calvin Hamilton | GPL-2.0-or-later | https://raw.githubusercontent.com/hamiltoncj/qgis-shapetools-plugin/main/icon.png |
| splitmultipart.png | Multipart Split | Alexandre Neto | GPL-2.0-or-later | https://raw.githubusercontent.com/SrNetoChan/MultipartSplit/master/splitmultipart/icon.svg |
| SpreadsheetLayers.png | Spreadsheet Layers | Camptocamp | GPL-3.0-or-later | https://plugins.qgis.org/plugins/SpreadsheetLayers/version/2.1.3/download/ (SpreadsheetLayers/resources/icon/mActionAddSpreadsheetLayer.svg inside the plugin zip) |
| SRTM-Downloader.png | SRTM Downloader | Dr. Horst Duester (Kappasys) | GPL-3.0-or-later | https://plugins.qgis.org/plugins/SRTM-Downloader/version/3.3.4/download/ (SRTM-Downloader/icon.png inside the plugin zip) |
| StreetView.png | Street View | Saccon Fabio | GPL-3.0-or-later | https://plugins.qgis.org/plugins/StreetView/version/4.4/download/ (StreetView/icon.png inside the plugin zip) |
| valuetool.png | Value Tool | Jorge Almerio (maintainer) | GPL-3.0 (LICENSE file); source headers say GPL-2.0-or-later | https://raw.githubusercontent.com/jorgealmerio/valuetool/master/core/icon.svg |
