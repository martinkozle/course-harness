"""Resolve the text colour a template gives its layout placeholders.

PowerPoint inherits a placeholder's first-level text colour from, in order: the placeholder's own
list style, the matching placeholder on the slide master, and the master's text styles. A colour
is either an explicit sRGB value or a theme colour named through the master's colour map (or the
layout's override of it) and adjusted by luminance, tint and shade modifiers.
"""

from __future__ import annotations

import colorsys
from typing import Any

from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml import parse_xml

_TITLE_TYPES = {"title", "ctrTitle"}
# Placeholders that inherit from a master placeholder of their own type rather than the body's.
_OWN_MASTER_TYPES = {"dt", "ftr", "sldNum"}
_COLOR_MAP_NAMES = {"bg1", "tx1", "bg2", "tx2"}
_PRESET_COLORS = {"black": "000000", "white": "FFFFFF"}
_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


def placeholder_text_color(container: Any, placeholder: Any) -> str | None:
    """The ``#RRGGBB`` colour of a placeholder's first-level text, or ``None`` when the template
    does not settle it with a solid colour.

    ``container`` is the python-pptx slide layout or slide master holding ``placeholder``.
    """
    try:
        master = getattr(container, "slide_master", container)
        fill = _inherited_solid_fill(container, master, placeholder.element)
        if fill is None:
            return None
        return _resolve_fill(fill, _color_map(container, master), _theme_colors(master))
    except Exception:
        return None


def _placeholder_type(shape) -> str:
    ph = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
    return ph.get("type", "obj") if ph is not None else "obj"


def _level_one_fill(list_style) -> Any | None:
    """The solid fill of a list style's first level, when the style exists and sets one."""
    if list_style is None:
        return None
    return list_style.find("a:lvl1pPr/a:defRPr/a:solidFill", _NS)


def _inherited_solid_fill(container, master, shape) -> Any | None:
    if (fill := _level_one_fill(shape.find("p:txBody/a:lstStyle", _NS))) is not None:
        return fill
    placeholder_type = _placeholder_type(shape)
    if container is not master:
        master_type = (
            "title"
            if placeholder_type in _TITLE_TYPES
            else placeholder_type
            if placeholder_type in _OWN_MASTER_TYPES
            else "body"
        )
        for master_shape in master.element.iterfind("p:cSld/p:spTree/p:sp", _NS):
            if _placeholder_type(master_shape) == master_type:
                master_style = master_shape.find("p:txBody/a:lstStyle", _NS)
                if (fill := _level_one_fill(master_style)) is not None:
                    return fill
                break
    style = "titleStyle" if placeholder_type in _TITLE_TYPES else "bodyStyle"
    return _level_one_fill(master.element.find(f"p:txStyles/p:{style}", _NS))


def _color_map(container, master) -> dict[str, str]:
    mapping = container.element.find("p:clrMapOvr/a:overrideClrMapping", _NS)
    if mapping is None:
        mapping = master.element.find("p:clrMap", _NS)
    return dict(mapping.attrib) if mapping is not None else {}


def _theme_colors(master) -> dict[str, str]:
    theme = parse_xml(master.part.part_related_by(RT.THEME).blob)
    colors: dict[str, str] = {}
    for scheme in theme.iterfind("a:themeElements/a:clrScheme", _NS):
        for entry in scheme:
            name = entry.tag.split("}")[-1]
            for value in entry:
                kind = value.tag.split("}")[-1]
                hex_value = (
                    value.get("val")
                    if kind == "srgbClr"
                    else value.get("lastClr")
                    if kind == "sysClr"
                    else None
                )
                if hex_value and len(hex_value) == 6:
                    colors[name] = hex_value.upper()
    return colors


def _resolve_fill(fill, color_map: dict[str, str], theme: dict[str, str]) -> str | None:
    for color in fill:
        kind = color.tag.split("}")[-1]
        value = color.get("val", "")
        if kind == "srgbClr":
            base = value
        elif kind == "schemeClr":
            name = color_map.get(value, value) if value in _COLOR_MAP_NAMES else value
            base = theme.get(name, "")
        elif kind == "sysClr":
            base = color.get("lastClr", "")
        elif kind == "prstClr":
            base = _PRESET_COLORS.get(value, "")
        else:
            continue
        if len(base) != 6:
            return None
        try:
            rgb = tuple(int(base[index : index + 2], 16) / 255 for index in (0, 2, 4))
        except ValueError:
            return None
        rgb = _apply_modifiers(rgb, color)
        return "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)
    return None


def _apply_modifiers(rgb: tuple[float, ...], color) -> tuple[float, ...]:
    red, green, blue = rgb
    for modifier in color:
        kind = modifier.tag.split("}")[-1]
        try:
            amount = int(modifier.get("val", "")) / 100_000
        except ValueError:
            continue
        if kind in {"lumMod", "lumOff"}:
            hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
            lightness = lightness * amount if kind == "lumMod" else lightness + amount
            red, green, blue = colorsys.hls_to_rgb(hue, _clamp(lightness), saturation)
        elif kind == "tint":
            red, green, blue = (1 - (1 - channel) * amount for channel in (red, green, blue))
        elif kind == "shade":
            red, green, blue = (channel * amount for channel in (red, green, blue))
    return (_clamp(red), _clamp(green), _clamp(blue))


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
