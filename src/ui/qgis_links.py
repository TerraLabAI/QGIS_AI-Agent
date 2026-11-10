# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import json

from qgis.PyQt.QtCore import QCoreApplication

from ..core import view3d_windows





_PAGES = {
    "information": ("mOptsPage_Information", "Information"),
    "source": ("mOptsPage_Source", "Source"),
    "symbology": ("mOptsPage_Style", "Symbology"),
    "labels": ("mOptsPage_Labels", "Labels"),
    "fields": ("mOptsPage_SourceFields", "Fields"),
    "form": ("mOptsPage_AttributesForm", "Attributes Form"),
    "joins": ("mOptsPage_Joins", "Joins"),
    "temporal": ("mOptsPage_Temporal", "Temporal"),
    "elevation": ("mOptsPage_Elevation", "Elevation"),
    "metadata": ("mOptsPage_Metadata", "Metadata"),
    "legend": ("mOptsPage_Legend", "Legend"),
    "transparency": ("mOptsPage_Transparency", "Transparency"),
}



_PLACES = {
    "set_layer_style": "symbology", "set_layer_symbology": "symbology", "apply_style_qml": "symbology",
    "set_raster_style": "symbology", "set_raster_class_style": "symbology",
    "set_layer_legend_image": "legend",
    "set_layer_labels": "labels",
    "add_field": "fields", "delete_field": "fields", "rename_field": "fields", "set_field_aliases": "fields",
    "configure_attribute_form": "form",
    "add_table_join": "joins",
    "set_layer_filter": "source", "set_layer_crs": "source", "repair_layer_paths": "source",
    "set_raster_nodata": "transparency",
    "set_layer_temporal": "temporal",
    "set_layer_elevation": "elevation",
    "set_layer_metadata": "metadata",
    "add_data": "information", "add_vector_layer": "information", "add_vector_from_url": "information",
    "add_wms_layer": "information", "add_wfs_layer": "information", "add_xyz_layer": "information",
    "add_arcgis_rest_layer": "information", "add_cog_layer": "information", "add_stac_layer": "information",
    "add_layer_from_connection": "information",
    "field_calculator": "table", "add_features": "table", "update_features": "table",
    "delete_features": "table", "select_by_attribute": "table", "select_by_geometry": "table",
    "select_features": "table", "spatial_join": "table", "zonal_statistics": "table",
    "sample_rasters_at_points": "table", "geocode_layer": "table", "fetch_osm_data": "table",
    "fetch_overture": "table", "fetch_building_footprints": "table", "add_points_from_json": "table",
    "create_print_layout": "layout", "add_layout_map": "layout", "add_layout_label": "layout",
    "add_layout_legend": "layout", "add_layout_scalebar": "layout", "add_layout_north_arrow": "layout",
    "add_layout_picture": "layout", "add_layout_table": "layout", "add_layout_coordinate_grid": "layout",
    "configure_atlas": "layout", "lock_layout_item": "layout", "export_layout": "layout",
    "export_atlas": "layout",
    "configure_3d_map_view": "view3d", "configure_3d_tiles": "view3d",
    "set_project_crs": "project", "set_project_variable": "project", "configure_relations": "project",
}

_GLYPHS = {"table": "table", "layout": "layout", "project": "gear"}

_LAYER_KEYS = ("layer_id", "output_layer", "layer", "layer_name", "target_layer")
_LAYOUT_KEYS = ("layout_name", "layout", "name")


def _tr(text: str) -> str:
    return QCoreApplication.translate("QgisLinks", text)


def _result(detail) -> dict:
    try:
        data = json.loads(detail or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _layer(args: dict, result: dict):

    from qgis.core import QgsProject

    project = QgsProject.instance()
    for source in (result, args):
        for key in _LAYER_KEYS:
            value = source.get(key)
            if not isinstance(value, str) or not value:
                continue
            found = project.mapLayer(value)
            if found is None:
                named = project.mapLayersByName(value)
                found = named[0] if len(named) == 1 else None
            if found is not None:
                return found
    return None


def _layout(args: dict, result: dict):
    from qgis.core import QgsProject

    manager = QgsProject.instance().layoutManager()
    for source in (result, args):
        for key in _LAYOUT_KEYS:
            value = source.get(key)
            found = manager.layoutByName(value) if isinstance(value, str) and value else None
            if found is not None:
                return found
    return None


def _view3d(args: dict, result: dict):


    from ..tools.map_3d import open_3d_views

    views = open_3d_views() or []
    index = result.get("view_index", args.get("view_index", 0))
    view = result.get("view")
    if isinstance(view, dict) and isinstance(view.get("index"), int):
        index = view["index"]
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(views):
        return None
    return views[index].parent()


def _page_title(title: str) -> str:

    for context in ("QgsVectorLayerPropertiesBase", "QgsRasterLayerPropertiesBase"):
        shown = QCoreApplication.translate(context, title)
        if shown != title:
            return shown
    return title


def qgis_link(name: str, args, detail=""):



    place = _PLACES.get(str(name or ""))
    if place is None:
        return None
    args = args if isinstance(args, dict) else {}
    result = _result(detail)
    try:
        from qgis.utils import iface
    except ImportError:
        return None
    if iface is None:
        return None
    if place == "project":
        return (_tr("Project Properties"), "gear", _tr("Open the project's properties"),
                lambda: iface.actionProjectProperties().trigger())
    if place == "view3d":
        if _view3d(args, result) is None:
            return None

        def open_view():
            holder = _view3d(args, result)
            if holder is not None:


                view3d_windows.bring_front(holder)
        return (_tr("3D Map View"), "layers", _tr("Bring this 3D map view to the front"), open_view)
    if place == "layout":
        if _layout(args, result) is None:
            return None

        def open_layout():
            layout = _layout(args, result)
            if layout is not None:
                iface.openLayoutDesigner(layout)
        return (_tr("Layout Designer"), "layout", _tr("Open this layout in the layout designer"), open_layout)
    layer = _layer(args, result)
    if layer is None:
        return None
    if place == "table":
        from qgis.core import QgsVectorLayer
        if not isinstance(layer, QgsVectorLayer):
            return None

        def open_table():
            found = _layer(args, result)
            if found is not None:
                iface.showAttributeTable(found)
        return (_tr("Attribute Table"), "table",
                _tr("Open the attribute table of {layer}").format(layer=layer.name()), open_table)
    page, title = _PAGES[place]
    title = _page_title(title)

    def open_properties():
        found = _layer(args, result)
        if found is not None:
            iface.showLayerProperties(found, page)
    return (_tr("Layer Properties › {tab}").format(tab=title), "gear",
            _tr("Open the properties of {layer} on the {tab} tab").format(layer=layer.name(), tab=title),
            open_properties)
