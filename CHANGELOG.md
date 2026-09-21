# Changelog

All notable changes to the QGIS AI Agent plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The short form of each entry is the `changelog=` field in `metadata.txt`, which
is what the QGIS Plugin Manager renders.

## [Unreleased]

## [1.3.1] - 2026-09-21

### Added

- Undo from the chat takes back an edit already saved to its file, the same way as the Undo
  arrow in the panel.
- An approval card for a Processing run or a GeoPackage save says what happens on disk: the file
  created or replaced, and whether the input is overwritten or only read.

### Changed

- Python code the agent runs asks for approval only when it changes files or the project: a
  snippet that only reads runs without a card (about 1 card in 17 code requests instead of 11).
  A snippet that writes, deletes or edits still shows its own card.
- Python run by the agent can now open zip archives, find files with wildcards, make a
  scratch folder and use `pathlib` paths. Every path still passes the same file check, and
  the permission card still asks before any code runs.
- Point labels take the Cartographic placement by default, and labels gain priority, drawing
  order, obstacle, capitalization, letter spacing, opacity and repeat distance, so a map can
  put town names in front and let region and relief names sit back.
- A processing chain the agent built can be saved as a Graphical Modeler model and opened in
  the Model Designer, laid out top to bottom with inputs on top and each output beside its step.
  A saved model can ask for a point clicked on the map, an extent, the project's CRS and
  bounded numbers with a help line each, and frames its parts in coloured boxes.
- Several layers reprojected in one batch come out named after their source and CRS
  (`roads_28992`) instead of "Reprojected", or after a pattern the agent gives; a table
  written into a GeoPackage is named after the table, not the file.
- A finished project can be packaged as one GeoPackage: every layer as a table, small rasters
  inside, each style as the table's default and the project itself, so the file opens styled
  from the Browser on another computer. Web layers keep their address and large rasters go in
  a folder beside it.

### Fixed

- A clip to a country, a region or a city keeps only the areas inside it: a neighbour that
  shares a border (Loreto and Amazonas around Brazil's states) is no longer kept.
- A WFS link without a layer name loads the layer it names, or lists the layers the service
  offers when there are several, instead of refusing.
- Nothing in the chat is wider than the panel: a long address, path or code word wraps inside
  its card. The address card is one sentence, with the full address behind Show the address.
- Python written for PyQt5 or PyQt6 runs on QGIS 4, which ships only one of them.
- Watershed outlets resolve to the exact point feature or coordinates given, and a basin that
  reaches the edge of the elevation model is cut where the data ends, with a warning.
- A watershed on a fine elevation model is computed at a coarser cell that fits instead of being
  refused, and the result states the cell size used.
- A download or a service the agent could not read says what the service answered, not only
  that it failed.
- An invalid geometry that stops a Processing run or a Python snippet is named as such, in any
  QGIS language, so the agent repairs the geometry instead of guessing at the cause.
- A layer id the agent writes with the layer's own spelling finds that exact layer, never a
  layer with the same name.
- A large Overture area that goes over the download size is fetched again in smaller pieces
  instead of failing, and says so when it is still too large.
- The highest and lowest values of a local raster are measured on every pixel, so a summit is
  no longer missed.
- An ArcGIS item's web address loads as the item it names.
- Removing a layer that the same run added no longer asks for approval; a layer that was in
  the project before still does.
- A scale bar in a print layout has two segments by default, so it fits beside the map.

## [1.3.0] - 2026-09-20

### Added

- Styling reaches the whole of QGIS: every renderer, symbol layer, effect and blend mode by
  name, sub-symbols, diagrams, label placement, raster classes and rating tables, point cloud
  and mesh styling, and conditional formatting in the attribute table.
- Print layouts and reports: legends curated entry by entry, `.qpt` templates, atlas at a fixed
  scale, coordinate grids, 3D map items, elevation profiles, nested reports and DOCX exports,
  and a preview that draws the project's layer order before printing.
- New ground: mesh and point cloud layers, 3D Tiles, 3D views with building extrusion, GTFS
  feeds, hot spots (Getis-Ord Gi*, local Moran's I), flow lines, animated maps as GIF or MP4,
  elevation models exported for Blender, SketchUp and 3D printing, and styles, models and
  scripts imported from QGIS Hub.
- Attribute forms, relations, snapping, temporal playback, canvas decorations, topology checks
  and Processing scripts are configured from the chat.
- One zone of interest shared with AI Edit and AI Segmentation: draw it once, every plugin
  works inside it.

### Changed

- A refusal about size now delivers the whole zone anyway: coarser pixels, tiled reads and
  background downloads instead of an empty result.
- An answer opens with the result rather than with the check that verified it, and a tool
  shows one card per answer instead of repeating the same question for each layer.
- Long tasks are allowed to finish: earlier turns are folded rather than cut off.

### Fixed

- The panel speaks the language QGIS is set to. A language stored by an earlier build no
  longer overrides it, and is removed at the next start.
- Watersheds, geocoded tables, merged folders, CAD and KML imports and CSV files keep their
  rows, encoding and coordinate system instead of losing them silently.
- A silent WMS server no longer freezes QGIS while a layer is built.

## [1.2.0] - 2026-09-18

### Added

- Undo history: step to any earlier point in a chat, see what each run changed, and go
  back without losing your own edits made since.
- Search filters connectors and examples as you type, and a data source card shows its
  licence and when it was last loaded.

### Changed

- The composer keeps an unsent message and resends it automatically once the connection
  to the agent service drops and comes back.
- Long conversations are compacted instead of cut off, and your own messages stay
  visible either way.

### Fixed

- A layer removed from the project no longer leaves stale references behind, and a 3D
  map view no longer crashes QGIS when its layer is removed.
- Georeferencing and layout extents keep the right coordinate system across canvas
  reprojections.
- All 11 translated locales are complete again: every interface string ships with its
  translation, none left in English.
- On Windows: long folder paths, CSV files saved by Excel, mapped network drives, OneDrive
  folders and `%VAR%` paths now work across loading, exports, downloads and undo.
- Layouts report a legend, scale bar or north arrow cut off by the page edge.

## [1.1.0] - 2026-09-15

### Added

- Charts: a histogram, bar, scatter or line chart of a layer's fields, saved as an image.
- Animation over time: a layer with a date, a year or a start and end field plays on the
  QGIS time controller, and the frames can be exported.
- Watersheds and streams traced from an elevation model in one step.
- Georeferencing: a scanned map or photo is placed from control points, with the error
  of each point reported.
- Typing @ in the message box lists the layers of your project.

### Changed

- Undo restores memory layers of any size, shapefiles, MapInfo tables and GeoPackages
  on a network share, and the history of a project survives a QGIS restart.
- When a step fails, the panel says why and what to try: a timeout, a dropped
  connection, a wrong coordinate system or a join that matched nothing.
- Searching for data works as well in French, German, Spanish, Italian or Portuguese
  as in English, and more datasets load from hosted copies instead of slow public services.
- Without the QuickMapServices plugin, the default basemap is a streets map that still
  loads when one of its tile servers is down.

### Fixed

- Chats are filed under the signed-in account, so a second person on the same computer
  does not see the first one's conversations.
- Signing in works when QGIS keeps its credentials behind a master password.
- On Windows, typing a letter with AltGr no longer triggers a plugin shortcut, and
  exports survive a file locked by an antivirus or OneDrive.
- The thumbs up and down under an answer show the vote you cast.

## [1.0.2] - 2026-09-12

### Added

- Your memory is a folder of Markdown files on your own computer. Open it from the
  settings, reword or delete a note there, and the next conversation follows it.

### Changed

- Satellite imagery arrives as a GeoTIFF file on your disk instead of several layers
  reading from a remote store, so a project with several scenes stays responsive.
- Only a file you would go looking for asks for your approval. A scratch result the
  run writes for itself no longer stops the work with a permission card.
- A tool that modifies the project reports what it actually left behind, measured in
  the project afterwards rather than taken from the tool's own account of itself.
- A basemap you did not ask for becomes a question instead of a silent addition.

### Fixed

- A vegetation index computed from Sentinel imagery reads the bands it needs, so it
  measures the ground rather than the clouds.
- A run in flight survives a reconnection: the panel replays how the run ended
  instead of reporting it as lost.
- Searching open data keeps working when one of the search services is unavailable.

## [1.0.1] - 2026-09-11

### Changed

- The panel follows a light QGIS theme: bubbles, cards, code blocks and buttons keep
  their shape and contrast.
- The history sheet opens at the height it needs, one readable line per group, and
  says when there is more below.

### Fixed

- Going back to an earlier step no longer holds the window, and keeps the
  conversation it restores.

## [1.0.0] - 2026-09-11

### Added

- First public release, after three months of daily use inside TerraLab on our own
  QGIS work.
- Hand it a task and it does the work: finds the data, runs the analysis, styles the
  map, builds the layout.
- You see every step before it runs, changes wait for your approval, and one click
  undoes anything it changed.
- Open data by theme and place: nearly 40 connectors, 300 datasets with their licence,
  30+ public portals, and Overture Maps for a bounding box.
- One conversation per project, and attach files, layers, selections or the map view
  to a message.

[Unreleased]: https://github.com/TerraLabAI/QGIS_AI-Agent/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/TerraLabAI/QGIS_AI-Agent/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/TerraLabAI/QGIS_AI-Agent/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/TerraLabAI/QGIS_AI-Agent/releases/tag/v1.0.0
