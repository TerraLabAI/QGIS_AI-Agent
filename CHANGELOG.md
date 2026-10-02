# Changelog

All notable changes to the QGIS AI Agent plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The short form of each entry is the `changelog=` field in `metadata.txt`, which
is what the QGIS Plugin Manager renders.

## [Unreleased]

## [1.5.6] - 2026-10-03

### Added

- Undo changes and Redo changes under each answer put the map back to how it was before or after
  that request, with a Map history sheet listing every version kept. A confirmation appears only
  when the undo would lose something (your own edits, a file the run wrote, a later request).
- The memory note the agent proposes at the end of a run is a card you add or decline. A declined
  note is not proposed again for the next 3 conversations.
- Settings has a Memory section: a switch, the saved notes, and a field to add your own.
- Example library tiles show their category, the category rail folds, and tile notes run to two
  lines.

### Changed

- TerraLab's own open data reads as "TerraLab Open Data" with the TerraLab logo in the chat and
  the sources sheet, one card per source.
- GIS data links and the Open button on file cards load the layer in this QGIS, matched by its
  file path.
- Report a problem shows our address with a Copy email address button instead of opening a mail
  app.
- The attach menu says "Add files from your computer".
- Dates follow each language's own day and month order.
- A step the agent retried and got right reads as a success; the failed tries stay behind a small
  link.
- OpenStreetMap themes name OpenStreetMap as their source.
- Making layers permanent, writing an HTML report, saving a 3D view and packaging a project run
  without an approval card; a step that deletes data still asks.
- Nominatim, OpenStreetMap routing and the public Overpass servers are always asked at their
  published rate limit, so they keep answering.
- An empty Examples page says so instead of staying blank.

### Fixed

- QGIS no longer freezes for about 27 s while a large Overture or OSM building layer finishes
  loading; the longest pause is now about a third of a second.
- No more pause at the start of a run while the undo history tidies up.
- A restored memory layer is no longer mistaken for a manual edit when its feature ids moved.
- A link ends at Chinese or Japanese punctuation instead of swallowing it.
- A Processing input or result file served by a host that ignores range requests loads again.
- A QGIS proxy that cannot be reached is named as the cause within a minute.
- The Area of interest keeps its shapes when the project is saved and reopened.

## [1.5.5] - 2026-10-02

### Added

- A remote raster (Cloud Optimized GeoTIFF, STAC asset or raster web address) can load only the box
  you need, saved as a local GeoTIFF, instead of the whole file followed by a clip.
- Print layouts: labels, maps and legends take a frame, a background colour and a font colour or
  family, when added and later on an item already on the sheet.
- Categorized styles take each value's own colour, legend label and size or width.
- Layer statistics can be grouped, filtered and measured, and raster value ranges counted.
- Layer groups can be folded or unfolded.
- A WFS layer can be copied with a filter the service applies, so the copy holds only the matches.
- Each source under an answer shows its licence, access, scene dates and resolution behind a
  chevron, with a button to copy the credits.

### Changed

- Maps render in one pass: about twice as fast with a basemap (an OpenStreetMap basemap and two
  layers went from 7.2 s to 2.8 s).
- Band statistics of a raster read over the network are sampled in the background: about 5 times
  faster, and QGIS no longer freezes while they are computed.
- Large layers, WFS and OGC API layers, style changes and GeoPackage exports are read off the main
  thread, so QGIS stays responsive.
- On a slow or unstable connection the panel connects about 3 times faster and comes back from a
  drop in a few seconds instead of about 27.
- The 11 translations are reworded to read naturally and use QGIS's own terms; the shared zone
  layer is named "Area of interest" in the user's language.
- The chat no longer shows a "QGIS stopped responding" line.
- The sign-in card no longer shows a 4-character match code.
- Web layers the agent adds cannot reach this computer or the local network unless you typed
  the address.
- The chat is plainer: an allowed card leaves nothing behind and a denied one a single line, code
  cards say in words what the code does, and answers no longer carry thumbs or a copy button.
- Local files, Processing results, WMS and WMTS layers and layout exports are built off the main
  thread, so QGIS keeps responding while they load or render.
- Settings open on Connectors; Billing is folded into Account with a Manage plan button.
- Dates, months and status lines follow the panel's language.

### Fixed

- A result download cut mid-way resumes from the byte it reached instead of failing.
- OpenStreetMap areas split across map tiles come back as one feature each (Slovenia: 212
  municipalities, not 234 or 258), and coastal areas selected by an outline are kept whole.
- WMS, WMTS and WCS layers whose name holds a colon load.
- An OpenStreetMap download with lines and polygons no longer reports an error after adding its
  layers.
- Colours written with transparency (#rrggbbaa) read the same way everywhere.
- Graduated and categorized line styles keep their class colours.
- A code step QGIS stops to ask permission for no longer reloads the project when it had changed
  nothing: on a project of 255 layers the window was blocked for 18 to 37 s each time.
- Stop ends a large KML split, an identify on an ArcGIS layer or a run with the link down within
  a second.
- Styling or measuring a layer added in the same step finds it instead of answering that the layer
  does not exist.
- A request that only builds a print layout gets its own Undo, and Undo on an earlier request warns
  that it also removes that layout instead of dropping it silently.
- The shared Area of interest keeps its shape when the project is saved and reopened.
- With a QGIS proxy that cannot be reached, the panel says so within about 45 s instead of asking
  you to sign out and in again.
- Processing on a raster web address whose server cannot send part of a file works again.

## [1.5.3] - 2026-09-28

### Changed

- "Allow for this run" covers only code the plugin can check before it runs; other code keeps its own
  card every time. A message typed during the run asks again.
- Refusing a card for code stopped mid-run discards the edits that code started, and keeps the edits
  that were already open.
- The new messages of 1.5.1 are translated in the 11 languages.

## [1.5.2] - 2026-09-28

The content of 1.5.1, which the QGIS plugin repository never published.

## [1.5.1] - 2026-09-28

### Added

- "Update now" installs the new version in one click and reloads the plugin, with no QGIS restart.
  The update offer shows when the panel opens, and "Later" holds until QGIS restarts.
- Code cards in Balanced mode offer "Allow for this run": the run's later snippets that read or
  write run without a card. Deleting data still asks, and the next run asks again.
- When the server cannot be reached for about a minute, the panel says why: the network cause
  (name resolution, proxy, certificate) or that the server is not answering.

### Changed

- HTML reports load nothing from unknown sites and embed only pictures inside the allowed folders.
- Autopilot asks before a tool sends data to an address nobody but the agent wrote.
- A zipped raster download opens the tile you name in one step.

### Fixed

- Updating the plugin no longer stops on an empty "Plugin dependencies" window.

## [1.5.0] - 2026-09-28

### Added

- Messages typed while the agent works wait in a queue above the message box. A text message
  reaches the running task at its next step; each queued row can be sent now or removed.
- Edit your last message: its attachments come back, and the turn it replaces leaves the chat.
  A failed run offers Undo and retry.
- Every layer the agent adds records where it came from in its metadata: the source, its licence
  and credit, the tool and inputs that made it. Secrets and expiring links are left out.
- QGIS calls you back when a run finishes, stops, or waits for your answer, and each answer has a
  Copy button.
- A thumbs down asks what went wrong, optionally: three choices and a free line.

### Changed

- A stopped or failed run keeps its Undo, and the layers a call added are never taken back out of
  the project on their own.
- Fewer approval cards: code that only reads asks less, and a Processing output written to a new
  file no longer asks.
- The panel says what the run is doing between steps, and the agent's error messages state facts.
- Hosted basemaps are named after their data (OpenStreetMap Light and Dark, Sentinel-2 2021).
- Signing in brings QGIS back to the front on macOS 14 and later.

### Fixed

- A crash in QGIS 3.44 during a long land-cover run.
- QGIS no longer freezes while account details load, while listing a web layer's field values or
  while identifying features on a WMS layer.
- A project with its own layer drawing order shows the run's layers where they belong.
- A CSV or VRT inside a zip loads, and a headerless CSV keeps its first record.

## [1.4.1] - 2026-09-24

### Changed

- A load is never refused or questioned for its number of features: a table, a file or a city's
  buildings go into the project on the first call, whatever their size, and a download of any size
  is written to disk instead of refused past 100 MB. Only data QGIS would have to hold in memory
  (points read from JSON, an ArcGIS service) keeps a ceiling, set from what this computer can hold.
- Fewer approval cards for code that only reads: checking a value's type or reading a named
  attribute no longer asks.
- Clipping Overture data to a country or city finds the boundary faster.

### Fixed

- A raster style with a typed minimum and maximum keeps that range instead of being refused.
- Paris open data searches work again, on the city's new portal address.
- Planetary Computer imagery loads with one signed access for all its files.

## [1.4.0] - 2026-09-23

### Added

- Any file up to 200 MB can be attached to a message: PDF, Word, PowerPoint, Excel, text, Markdown,
  HTML or XML. The panel previews it and the agent reads it.
- A run can end with a self-contained HTML report the user keeps, and the files a run produced show
  as cards under its answer, each opening with one click.
- The Examples library: 46 guided examples with pictures. An example whose data is not in the
  project asks one question with fixed choices, then runs its steps. Data sources have their own
  page inside the library, one picture card per connector.
- The agent can look at what it delivered (a map, a print layout page, a file on disk) before it
  answers, without opening a second project.
- A free account sees how many free runs are left under the message box, with one click to Pro.
- Styles classify by a QGIS expression, take fixed class limits, reverse ramps and draw features
  with no value; layout maps take their own CRS and layers and leave the project's view alone.

### Changed

- Fewer approval cards. Autopilot runs Python code without asking, calls that wait together share
  one card with one Allow or Deny, reading a public web page needs no card, and a code card says in
  plain words what the code does, with the Python behind one link.
- The plan is its own card above the work it pilots and says how far it got; the activity block
  keeps each call on one short line while the agent works.
- Going back shows one row per request with Undo or Put back. Unsaved edits are saved before a
  restore and kept under "Your own changes", and a code rollback never throws away an unsaved edit.
- The message box grows to 40% of the panel's height; chat history shows on an empty chat only.
- The first start after an install or an update is about a third faster, and long chats paint and
  resize faster.
- The agent reads and writes only inside the project, its attachments and the TerraLab exports
  folder; it no longer lists the user's other folders.

### Fixed

- Deleting a chat deletes its file, its undo index and its snapshots.
- On Windows, panel labels and buttons are no longer cut; saves reach the user's own folders on
  OneDrive or a server share; deep project folders, GeoPackage tables and Excel CSVs with accents
  work; and a file held by another program gets a clear next step.
- Vector basemaps (OpenFreeMap Positron, Liberty, Dark) draw their streets on QGIS 3.44.
- A large raster no longer freezes QGIS while a layout page draws, and a 300 dpi print keeps its
  300 dpi.
- A large ArcGIS feature service the user agreed to load is loaded: the yes (or a request for the
  whole area) now reaches it, where it was refused again with the same count.
- An OpenStreetMap query always stays inside the area of the call, so a query written without the
  area no longer searches the whole world for minutes; a slow query no longer marks the
  OpenStreetMap server as down for the next five minutes.
- When the OpenStreetMap server refuses a query, the agent reads the server's own reason (the line
  and the mistake) instead of a bare "HTTP 400".
- The agent can move a whole layer group up or down the Layers panel, with every layer in it.

## [1.3.2] - 2026-09-21

### Added

- The agent can run SQL in the database behind a saved connection (a PostGIS query, an update, a new
  index) after showing the statement for approval; rows stop at 1,000 and layers from that
  connection reload so a change shows on the map.

### Changed

- A short connection drop (under 15 seconds, for example while the service restarts) no longer
  shows a warning: Send waits with its arrow and the run keeps going. The plugin reconnects at
  once when the service says it is restarting.
- A filtered OpenStreetMap or Overture download fetches only the matching features, so large
  areas that used to fail as too big now load.
- City districts and other boundaries from OpenStreetMap load as polygons, clipped to the place
  asked for, without the neighbouring areas.
- The area of a fetched box is reported as the box's area, so the agent no longer quotes it as
  the area of the features.
- A CSV with no stated CRS is placed from where its points land, and a layer with a wrong CRS
  can be relabelled in one step.

### Fixed

- Hosted map tiles that QGIS's own reader could not reach behind a proxy load through the
  plugin's connection.
- Reading an ArcGIS, WFS or OGC API feature service no longer freezes QGIS while features
  arrive, including from Python the agent runs.
- Raster statistics give the true minimum and maximum of a local raster, not a sample's.
- The raster calculator computes the area asked for instead of a whole satellite tile.
- An ArcGIS item's REST address opens as the item.

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
