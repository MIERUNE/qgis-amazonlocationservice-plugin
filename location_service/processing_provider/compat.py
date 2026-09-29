"""
Processing enum values that moved between QGIS 3.34 and QGIS 3.36+ / 4.x.

QGIS 3.36 moved the Processing enums into ``Qgis`` and QGIS 4 removed the
old class-level names, while QGIS 3.34 only has the class-level names.
"""

from qgis.core import (
    Qgis,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
)


def _pick(new_enum: str, new_name: str, old_owner: object, old_name: str):
    """Returns the ``Qgis`` enum value when present, else the legacy value."""
    scoped = getattr(Qgis, new_enum, None)
    if scoped is not None:
        return getattr(scoped, new_name)
    return getattr(old_owner, old_name)


SOURCE_POINT = _pick(
    "ProcessingSourceType", "VectorPoint", QgsProcessing, "TypeVectorPoint"
)
SOURCE_LINE = _pick(
    "ProcessingSourceType", "VectorLine", QgsProcessing, "TypeVectorLine"
)
SOURCE_POLYGON = _pick(
    "ProcessingSourceType", "VectorPolygon", QgsProcessing, "TypeVectorPolygon"
)
SOURCE_TABLE = _pick("ProcessingSourceType", "Vector", QgsProcessing, "TypeVector")

NUMBER_INTEGER = _pick(
    "ProcessingNumberParameterType", "Integer", QgsProcessingParameterNumber, "Integer"
)
NUMBER_DOUBLE = _pick(
    "ProcessingNumberParameterType", "Double", QgsProcessingParameterNumber, "Double"
)

FIELD_ANY = _pick(
    "ProcessingFieldParameterDataType", "Any", QgsProcessingParameterField, "Any"
)
FIELD_NUMERIC = _pick(
    "ProcessingFieldParameterDataType",
    "Numeric",
    QgsProcessingParameterField,
    "Numeric",
)

DATETIME = _pick(
    "ProcessingDateTimeParameterDataType",
    "DateTime",
    QgsProcessingParameterDateTime,
    "DateTime",
)

FLAG_ADVANCED = _pick(
    "ProcessingParameterFlag",
    "Advanced",
    QgsProcessingParameterDefinition,
    "FlagAdvanced",
)

ALGORITHM_FLAG_REQUIRES_PROJECT = _pick(
    "ProcessingAlgorithmFlag",
    "RequiresProject",
    QgsProcessingAlgorithm,
    "FlagRequiresProject",
)


def set_advanced(parameter: QgsProcessingParameterDefinition):
    """Marks a parameter as advanced and returns it."""
    parameter.setFlags(parameter.flags() | FLAG_ADVANCED)
    return parameter
