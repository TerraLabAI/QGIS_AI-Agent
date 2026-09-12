# Changelog

All notable changes to the QGIS AI Agent plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The short form of each entry is the `changelog=` field in `metadata.txt`, which
is what the QGIS Plugin Manager renders.

## [Unreleased]

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
