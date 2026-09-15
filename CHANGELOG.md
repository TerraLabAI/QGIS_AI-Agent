# Changelog

All notable changes to the QGIS AI Agent plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The short form of each entry is the `changelog=` field in `metadata.txt`, which
is what the QGIS Plugin Manager renders.

## [Unreleased]

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
