# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A link the user pasted, turned into the address that serves what it points at."""
















from __future__ import annotations

import posixpath
import re
import urllib.parse
from typing import NamedTuple





_TRAILING = "&,.;:!?'\"<>*"


def clean(url: str) -> str:
    """The link without the punctuation pasted around it."""
    text = str(url or "").strip().strip("<>").strip()
    while text[:1] in ("(", "[", '"', "'") and text[1:2]:
        text = text[1:]
    while text:
        last = text[-1]
        if (last in _TRAILING or last == ")" and text.count(")") > text.count("(")
                or last == "]" and text.count("]") > text.count("[")):
            text = text[:-1]
        else:
            break
    return text


def host_of(url) -> str:
    """The lower-case host of a URL, port and userinfo dropped; "" when there is none."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    return (parts.hostname or "").lower()


def host_is(url_or_host, domain: str) -> bool:
    """True when the host is ``domain`` or a subdomain of it, compared by DNS label."""






    text = str(url_or_host or "").strip()
    if "://" in text:
        host = host_of(text)
    else:
        host = text.rsplit("@", 1)[-1].split("/", 1)[0].split(":", 1)[0].strip(".").lower()
    domain = str(domain or "").strip().strip("/").lstrip(".").lower()
    return bool(domain) and (host == domain or host.endswith("." + domain))


class Resolved(NamedTuple):
    """``url`` to fetch; ``kind`` is "unchanged", "file", "listing", "inline" or "unreachable"."""
    url: str
    kind: str
    note: str = ""
    listing: str = ""
    inline: str = ""


    optional: bool = False


_UNCHANGED = "unchanged"


def _query(parts) -> dict:
    out: dict = {}
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        out.setdefault(key.lower(), value)
    return out


def _fragment_params(fragment: str) -> dict:
    out: dict = {}
    for pair in fragment.lstrip("#").split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            out.setdefault(key.lower(), value)
    return out


def _github(parts, segments: list) -> Resolved | None:
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    rest = segments[2:]
    if len(rest) >= 3 and rest[0] in ("blob", "raw"):
        path = "/".join(rest[1:])
        return Resolved(f"https://raw.githubusercontent.com/{owner}/{repo}/{path}", "file",
                        "A GitHub file page: its raw address serves the file itself.")
    if len(rest) >= 2 and rest[0] == "tree":
        ref, path = rest[1], "/".join(rest[2:])
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={urllib.parse.quote(ref)}"
        return Resolved(api, "listing", "A GitHub folder: its files are listed, each with the call that loads it.",
                        listing="github_contents")
    if len(rest) >= 3 and rest[0] == "releases" and rest[1] == "tag":
        api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote('/'.join(rest[2:]))}"
        return Resolved(api, "listing", "A GitHub release: its attached files are listed.",
                        listing="github_release")
    if len(rest) == 2 and rest == ["releases", "latest"]:
        api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        return Resolved(api, "listing", "A GitHub release: its attached files are listed.",
                        listing="github_release")
    return None


def _drive_id(parts, segments: list) -> str:
    if len(segments) >= 3 and segments[0] == "file" and segments[1] == "d":
        return segments[2]
    if segments and segments[0] in ("open", "uc"):
        return _query(parts).get("id", "")
    return ""


def resolve(url: str) -> Resolved:
    """The address that serves what a pasted share or viewer link points at."""
    text = clean(url)
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return Resolved(text, _UNCHANGED)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    segments = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
    if parts.scheme not in ("http", "https") or not host:
        return Resolved(text, _UNCHANGED)

    if host == "github.com":
        found = _github(parts, segments)
        if found:
            return found
    if host == "gist.github.com" and segments:
        gist_id = segments[-1] if re.fullmatch(r"[0-9a-f]{6,40}", segments[-1]) else ""
        if gist_id:
            return Resolved(f"https://api.github.com/gists/{gist_id}", "listing",
                            "A gist: its files are listed with their raw addresses.", listing="gist")
    if "/-/blob/" in parts.path:
        raw = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.replace("/-/blob/", "/-/raw/", 1),
                                       "", ""))
        return Resolved(raw, "file", "A GitLab file page: its raw address serves the file itself.")

    if host == "drive.google.com":
        if len(segments) >= 2 and segments[0] == "drive" and "folders" in segments:
            return Resolved(text, "unreachable",
                            "A Google Drive folder: Drive lists a folder to its owner only. Ask the user for the "
                            "link of the file itself (Share, Anyone with the link).")
        file_id = _drive_id(parts, segments)
        if file_id:

            key = _query(parts).get("resourcekey", "")
            extra = f"&resourcekey={urllib.parse.quote(key)}" if key else ""
            return Resolved(f"https://drive.usercontent.google.com/download?id={urllib.parse.quote(file_id)}"
                            f"&export=download&confirm=t{extra}", "file",
                            "A Google Drive file page: the download address serves the file. A file not shared "
                            "with anyone who has the link answers a sign-in page instead.")
    if host == "docs.google.com" and len(segments) >= 3 and segments[0] == "spreadsheets" and segments[1] == "d":
        if segments[2] == "e" and len(segments) >= 4:


            gid = _query(parts).get("gid") or _fragment_params(parts.fragment).get("gid")
            tab = f"&gid={urllib.parse.quote(gid)}&single=true" if gid else ""
            return Resolved(f"https://docs.google.com/spreadsheets/d/e/{urllib.parse.quote(segments[3])}"
                            f"/pub?output=csv{tab}", "file",
                            "A published Google Sheet: its CSV output serves the tab the link showed.")
        sheet_id = segments[2]
        gid = _query(parts).get("gid") or _fragment_params(parts.fragment).get("gid") or "0"
        return Resolved(f"https://docs.google.com/spreadsheets/d/{urllib.parse.quote(sheet_id)}/export"
                        f"?format=csv&gid={urllib.parse.quote(gid)}", "file",
                        "A Google Sheet: its CSV export serves the tab the link showed.")
    if host in ("google.com", "maps.google.com") and "d" in segments[:1] + segments[1:2] and "mid" in _query(parts):
        mid = _query(parts)["mid"]
        return Resolved(f"https://www.google.com/maps/d/kml?mid={urllib.parse.quote(mid)}&forcekml=1", "file",
                        "A Google My Maps map: its KML export serves every layer of the map.")

    if host_is(host, "dropbox.com") and segments[:1] in (["s"], ["scl"], ["sh"]):
        pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in ("dl", "raw")]
        pairs.append(("dl", "1"))
        return Resolved(urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                                 urllib.parse.urlencode(pairs), "")), "file",
                        "A Dropbox share page: dl=1 serves the file, rlkey kept.")

    if host == "zenodo.org" and len(segments) == 2 and segments[0] in ("records", "record") \
            and segments[1].isdigit():
        return Resolved(f"https://zenodo.org/api/records/{segments[1]}", "listing",
                        "A Zenodo record: its files are listed.", listing="zenodo")
    if host == "doi.org" and len(segments) >= 2 and segments[0] == "10.5281":
        found = re.fullmatch(r"zenodo\.(\d+)", segments[1])
        if found:
            return Resolved(f"https://zenodo.org/api/records/{found.group(1)}", "listing",
                            "A Zenodo DOI: the record's files are listed.", listing="zenodo")

    if host == "huggingface.co" and "blob" in segments:
        index = segments.index("blob")
        if index >= 2:
            path = "/".join(segments[:index] + ["resolve"] + segments[index + 1:])
            return Resolved(f"https://huggingface.co/{path}", "file",
                            "A Hugging Face file page: resolve serves the file itself.")

    if "umap" in host:


        found = re.search(r"^(/[a-z]{2}(?:-[a-z]{2,4})?)?/map/(?:[^/]*_)?(\d+)/?$", parts.path, re.IGNORECASE)
        if found:
            prefix = found.group(1) or ""
            return Resolved(f"{parts.scheme}://{parts.netloc}{prefix}/map/{found.group(2)}/geojson/", "listing",
                            "A uMap map: each of its data layers is listed as a GeoJSON address.",
                            listing="umap")

    portal = _portal_page(parts, host, segments)
    if portal is not None:
        return portal

    if host == "pmtiles.io":
        embedded = _query(parts).get("url") or urllib.parse.unquote(_fragment_params(parts.fragment).get("url", ""))
        if embedded.startswith(("http://", "https://")):
            return Resolved(embedded, "file", "A PMTiles viewer link: the archive it shows is the address inside it.")

    fragment = parts.fragment
    if fragment.startswith("/external/"):
        target = fragment[len("/external/"):]
        if not target.startswith(("http://", "https://")):
            target = "https://" + target
        return Resolved(target, "file", "A STAC Browser link: the catalog it shows is the address inside it.")

    if host == "geojson.io":
        data = _fragment_params(fragment).get("data", "")
        decoded = urllib.parse.unquote(data)
        if decoded.startswith("data:text/x-url,"):
            return Resolved(urllib.parse.unquote(decoded[len("data:text/x-url,"):]), "file",
                            "A geojson.io link: the data it shows is the address inside it.")
        if decoded.startswith("data:application/json,"):
            return Resolved(text, "inline", "A geojson.io link carries its GeoJSON inside the link itself.",
                            inline=decoded[len("data:application/json,"):])

    return Resolved(text, _UNCHANGED)


_SOCRATA_ID = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _portal_page(parts, host: str, segments: list) -> Resolved | None:
    """A dataset page of a portal with a JSON API, read through that API instead of its HTML."""






    base = f"{parts.scheme}://{parts.netloc}"
    if host_is(host, "data.gouv.fr") and "datasets" in segments:
        index = segments.index("datasets")
        if index + 1 < len(segments) and index <= 1:
            slug = segments[index + 1]
            return Resolved(f"https://www.data.gouv.fr/api/1/datasets/{urllib.parse.quote(slug)}/", "listing",
                            "A data.gouv.fr dataset page: its resources are listed, each with the call that "
                            "loads it.", listing="datagouv")



    explore = next((i for i in range(len(segments) - 2) if segments[i:i + 2] == ["explore", "dataset"]), -1)
    if explore >= 0 and len(segments) - explore <= 4:
        dataset = segments[explore + 2]
        return Resolved(f"{base}/api/explore/v2.1/catalog/datasets/{urllib.parse.quote(dataset)}/exports/geojson",
                        "file", "An Opendatasoft explore page: the dataset's GeoJSON export serves the data.")
    if parts.path.endswith("/home/item.html"):
        item_id = _query(parts).get("id", "")
        if _HEX32.match(item_id):
            prefix = parts.path[: -len("/home/item.html")]
            host_base = "https://www.arcgis.com" if host_is(host, "arcgis.com") else base + prefix
            return Resolved(f"{host_base}/sharing/rest/content/items/{item_id}?f=json", "listing",
                            "An ArcGIS item page: the item says which service or file it is.",
                            listing="arcgis_item", optional=not host_is(host, "arcgis.com"))
    if host_is(host, "hub.arcgis.com") and len(segments) >= 2 and segments[0] == "datasets":
        slug = segments[1]
        found = re.match(r"^([0-9a-f]{32})_(\d+)$", slug)
        if found:
            return Resolved(f"https://www.arcgis.com/sharing/rest/content/items/{found.group(1)}?f=json"
                            f"#layer={found.group(2)}", "listing",
                            "An ArcGIS Hub dataset: its item says which service it is.", listing="arcgis_item")
        if "::" in slug:
            return Resolved("https://hub.arcgis.com/api/v3/datasets?filter%5Bslug%5D=" + urllib.parse.quote(slug),
                            "listing", "An ArcGIS Hub dataset: Hub names the layer it stands for.", listing="hub")
    if "dataset" in segments and host not in ("github.com", "zenodo.org"):
        index = segments.index("dataset")
        if index + 1 < len(segments) and index + 2 >= len(segments):
            prefix = segments[:index]
            if prefix and re.fullmatch(r"[a-z]{2}", prefix[-1]):
                prefix = prefix[:-1]
            api_path = "/".join(prefix + ["api", "3", "action", "package_show"])
            return Resolved(f"{base}/{api_path}?id={urllib.parse.quote(segments[index + 1])}", "listing",
                            "A CKAN dataset page: its resources are listed, each with the call that loads it.",
                            listing="ckan", optional=True)
    if segments and _SOCRATA_ID.match(segments[-1]) and (len(segments) == 3 or segments[0] == "d"):
        return Resolved(f"{base}/api/views/{segments[-1]}.json", "listing",
                        "A Socrata dataset page: the data is served as GeoJSON, or as CSV when it has no "
                        "geometry.", listing="socrata", optional=True)
    return None




_MAX_FILES = 30


def _file(name: str, url: str, size=None, fmt: str = "") -> dict:
    name = str(name or posixpath.basename(urllib.parse.urlsplit(str(url)).path) or "file")
    fmt = str(fmt or "").lower().strip(". ")

    if fmt and not posixpath.splitext(name)[1] and re.fullmatch(r"[a-z0-9]{2,10}", fmt):
        name = f"{name}.{fmt}"
    out = {"name": name, "url": str(url)}
    if isinstance(size, int) and size >= 0:
        out["size_bytes"] = size
    return out


_SOCRATA_GEOMETRY = ("point", "multipoint", "line", "multiline", "polygon", "multipolygon", "location")
_ARCGIS_SERVICES = ("Feature Service", "Map Service", "Image Service", "Vector Tile Service")
_ARCGIS_FILE_EXT = {"Shapefile": ".zip", "CSV": ".csv", "GeoJson": ".geojson", "File Geodatabase": ".zip",
                    "KML": ".kml", "GeoPackage": ".gpkg", "Microsoft Excel": ".xlsx", "Image": ".tif"}


def listing_files(listing: str, api_url: str, payload) -> list[dict]:
    """``[{name, url, size_bytes?, folder?}]`` from the JSON a listing address answered."""
    files: list[dict] = []
    if listing == "github_contents":
        entries = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "dir" and entry.get("html_url"):
                files.append(dict(_file(entry.get("name", ""), entry["html_url"]), folder=True))
            elif entry.get("download_url"):
                files.append(_file(entry.get("name", ""), entry["download_url"], entry.get("size")))
    elif listing == "github_release" and isinstance(payload, dict):
        for asset in payload.get("assets") or []:
            if isinstance(asset, dict) and asset.get("browser_download_url"):
                files.append(_file(asset.get("name", ""), asset["browser_download_url"], asset.get("size")))
    elif listing == "gist" and isinstance(payload, dict):
        for name, entry in (payload.get("files") or {}).items():
            if isinstance(entry, dict) and entry.get("raw_url"):
                files.append(_file(name, entry["raw_url"], entry.get("size")))
    elif listing == "zenodo" and isinstance(payload, dict):
        for entry in payload.get("files") or []:
            if not isinstance(entry, dict):
                continue
            link = (entry.get("links") or {}).get("self") or ""
            if link:
                files.append(_file(entry.get("key") or posixpath.basename(link), link, entry.get("size")))
    elif listing == "umap" and isinstance(payload, dict):
        parts = urllib.parse.urlsplit(api_url)
        found = re.search(r"^(.*?)/map/(\d+)/", parts.path)
        prefix, map_id = (found.group(1), found.group(2)) if found else ("", "")
        for layer in (payload.get("properties") or {}).get("datalayers") or []:
            if isinstance(layer, dict) and layer.get("id") and map_id:
                name = (layer.get("properties") or {}).get("name") or layer.get("name") or layer["id"]
                files.append(_file(f"{name}.geojson",
                                   f"{parts.scheme}://{parts.netloc}{prefix}/datalayer/{map_id}/{layer['id']}/"))
    elif listing == "datagouv" and isinstance(payload, dict):
        for resource in payload.get("resources") or []:
            if isinstance(resource, dict) and resource.get("url"):
                files.append(_file(resource.get("title") or "", resource["url"], resource.get("filesize"),
                                   resource.get("format") or ""))
    elif listing == "ckan" and isinstance(payload, dict):
        for resource in (payload.get("result") or {}).get("resources") or []:
            if isinstance(resource, dict) and resource.get("url"):
                fmt = str(resource.get("format") or "")
                if fmt.upper() == "HTML":
                    continue
                files.append(_file(resource.get("name") or "", resource["url"], resource.get("size"), fmt))
    elif listing == "socrata" and isinstance(payload, dict) and payload.get("id"):
        parts = urllib.parse.urlsplit(api_url)
        kinds = [str(c.get("dataTypeName") or "").lower() for c in payload.get("columns") or [] if isinstance(c, dict)]
        geometry = any(kind in _SOCRATA_GEOMETRY for kind in kinds)
        ext = "geojson" if geometry else "csv"
        files.append(_file(f"{payload.get('name') or payload['id']}.{ext}",
                           f"{parts.scheme}://{parts.netloc}/resource/{payload['id']}.{ext}?$limit=10000"))
    elif listing == "arcgis_item" and isinstance(payload, dict) and payload.get("id"):
        parts = urllib.parse.urlsplit(api_url)
        layer = _fragment_params(parts.fragment).get("layer", "")
        kind, title = str(payload.get("type") or ""), str(payload.get("title") or payload["id"])
        if kind in _ARCGIS_SERVICES and payload.get("url"):
            url = str(payload["url"]).rstrip("/")
            if layer and kind == "Feature Service" and not re.search(r"/\d+$", url):
                url = f"{url}/{layer}"
            files.append(dict(_file(title, url), service=kind))
        elif kind in _ARCGIS_FILE_EXT:
            data = f"{parts.scheme}://{parts.netloc}{parts.path}/data"
            files.append(_file(f"{title}{_ARCGIS_FILE_EXT[kind]}" if not posixpath.splitext(title)[1] else title,
                               data, payload.get("size")))
    elif listing == "hub" and isinstance(payload, dict):
        for row in payload.get("data") or []:
            attributes = (row or {}).get("attributes") or {}
            if attributes.get("url"):
                files.append(dict(_file(attributes.get("name") or row.get("id") or "", attributes["url"]),
                                  service=str(attributes.get("type") or "")))
    return files[:_MAX_FILES]


def listing_terms(listing: str, payload) -> dict:
    """``{licence?, publisher?}`` a portal's own API states for the dataset, when it states them."""





    out: dict = {}
    doc = payload if isinstance(payload, dict) else {}
    if listing == "ckan":
        doc = doc.get("result") if isinstance(doc.get("result"), dict) else {}
        licence = doc.get("license_title") or doc.get("license_id")
    elif listing == "datagouv":
        licence = doc.get("license")
    else:
        return out
    owner = doc.get("organization")
    publisher = owner.get("title") or owner.get("name") if isinstance(owner, dict) else ""
    if isinstance(licence, str) and licence.strip() and licence.strip().lower() != "notspecified":
        out["licence"] = licence.strip()[:120]
    if isinstance(publisher, str) and publisher.strip():
        out["publisher"] = publisher.strip()[:120]
    return out



DATA_EXTENSIONS = frozenset({
    ".geojson", ".json", ".gpkg", ".kml", ".kmz", ".zip", ".shp", ".csv", ".fgb", ".parquet", ".geoparquet",
    ".gpx", ".gml", ".tif", ".tiff", ".pmtiles", ".xlsx", ".ods", ".jp2", ".laz", ".las", ".nc", ".gz",
})

_RANK = {**dict.fromkeys((".geojson", ".gpkg", ".shp", ".zip", ".kml", ".kmz", ".fgb", ".parquet",
                           ".geoparquet", ".gpx", ".gml", ".json", ".gz"), 0),
         **dict.fromkeys((".tif", ".tiff", ".pmtiles", ".jp2", ".nc", ".laz", ".las"), 1),
         **dict.fromkeys((".csv", ".xlsx", ".ods"), 2)}


def rank(entry: dict) -> int:
    if entry.get("service"):
        return 0
    if entry.get("folder"):
        return 4
    return _RANK.get(extension(entry.get("name") or "") or extension(entry.get("url") or ""), 3)


def extension(name: str) -> str:
    path = urllib.parse.urlsplit(name).path if "://" in name else name
    return posixpath.splitext(path.rstrip("/"))[1].lower()
