"""Static application vocabulary: navigation, profiles, collection names.

``MAIN_TABS`` is the single source of truth for the main navigation order
required by the specification:

    Project - Water Resource - Geometry - Hydrology1 - Trees - Hydrology2
    - Estimation - Report
"""

from __future__ import annotations

APP_VERSION = "2.6.0"

# --- collections -------------------------------------------------------------

COLLECTIONS = {
    "users": "users",
    "projects": "projects",
    "sectors": "sectors",
    "zones": "zones",
    "rows": "rows",
    "trees": "trees",
    "pipes": "pipes",
    "fittings": "fittings",
    "water_resources": "water_resources",
    "estimations": "estimations",
    "reports": "reports",
    "kml_files": "kml_files",
}

# --- profiles ----------------------------------------------------------------

PROFILE_FULL_ADMIN = "full_admin"
PROFILE_ADMIN_ENTREPRENEUR = "admin_entrepreneur"
PROFILE_CUSTOMER = "customer"

PROFILES = (PROFILE_FULL_ADMIN, PROFILE_ADMIN_ENTREPRENEUR, PROFILE_CUSTOMER)

PROFILE_LABELS = {
    PROFILE_FULL_ADMIN: {"en": "Full Admin", "ar": "مدير كامل"},
    PROFILE_ADMIN_ENTREPRENEUR: {"en": "Admin (Entrepreneur)", "ar": "مدير (مقاول)"},
    PROFILE_CUSTOMER: {"en": "Customer", "ar": "عميل"},
}

# --- water resource kinds ----------------------------------------------------

WATER_KINDS = (
    ("well", {"en": "Well", "ar": "بئر"}),
    ("basin", {"en": "Basin", "ar": "حوض"}),
    ("other", {"en": "Other", "ar": "أخرى"}),
)

# --- pipe levels (Hydrology 1) ----------------------------------------------

PIPE_LEVELS = (
    ("MP", {"en": "Main Pipe", "ar": "الأنبوب الرئيسي"}),
    ("SP", {"en": "Secondary Pipe", "ar": "الأنبوب الثانوي"}),
    ("ZP", {"en": "Zone Pipe", "ar": "أنبوب القطاع"}),
)

# --- fittings / emitters (Hydrology 2) --------------------------------------

FITTING_KINDS = (
    ("dripline", {"en": "Dripline", "ar": "خط التنقيط"}),
    ("dripper", {"en": "Dripper", "ar": "القطّارة"}),
    ("reduction", {"en": "Reduction", "ar": "تصغير"}),
    ("pressure_reduction", {"en": "Pressure Reduction", "ar": "خفض الضغط"}),
    ("air_eliminator", {"en": "Air Eliminator", "ar": "طارد الهواء"}),
    ("connector", {"en": "Connector to Dripline", "ar": "وصلة خط التنقيط"}),
)

# --- navigation --------------------------------------------------------------

MAIN_TABS = (
    {
        "key": "project",
        "label": {"en": "Project", "ar": "المشروع"},
        "endpoint": "project.list_projects",
        "sub": (),
    },
    {
        "key": "water_resource",
        "label": {"en": "Water Resource", "ar": "مصدر المياه"},
        "endpoint": "water_resource.index",
        "sub": (
            {"label": {"en": "Well", "ar": "بئر"}, "endpoint": "water_resource.kind", "arg": "well"},
            {"label": {"en": "Basin", "ar": "حوض"}, "endpoint": "water_resource.kind", "arg": "basin"},
            {"label": {"en": "Other", "ar": "أخرى"}, "endpoint": "water_resource.kind", "arg": "other"},
        ),
    },
    {
        "key": "geometry",
        "label": {"en": "Geometry", "ar": "الهندسة"},
        "endpoint": "geometry.sectors",
        "sub": (
            {"label": {"en": "Sectors", "ar": "القطاعات"}, "endpoint": "geometry.sectors"},
            {"label": {"en": "Zones", "ar": "المناطق"}, "endpoint": "geometry.zones"},
        ),
    },
    {
        "key": "hydrology1",
        "label": {"en": "Hydrology1", "ar": "الهيدرولوجيا 1"},
        "endpoint": "hydrology.main_pipe",
        "sub": (
            {"label": {"en": "Main Pipe (MP)", "ar": "الأنبوب الرئيسي"}, "endpoint": "hydrology.main_pipe"},
            {"label": {"en": "Secondary Pipe (SP)", "ar": "الأنبوب الثانوي"}, "endpoint": "hydrology.secondary_pipe"},
            {"label": {"en": "Zone Pipe (ZP)", "ar": "أنبوب القطاع"}, "endpoint": "hydrology.zone_pipe"},
        ),
    },
    {
        "key": "trees",
        "label": {"en": "Trees", "ar": "الأشجار"},
        "endpoint": "trees.rows",
        "sub": (
            {"label": {"en": "Rows", "ar": "الصفوف"}, "endpoint": "trees.rows"},
            {"label": {"en": "Trees", "ar": "الأشجار"}, "endpoint": "trees.trees"},
        ),
    },
    {
        "key": "hydrology2",
        "label": {"en": "Hydrology2", "ar": "الهيدرولوجيا 2"},
        "endpoint": "hydrology2.dripline",
        "sub": (
            {"label": {"en": "Dripline", "ar": "خط التنقيط"}, "endpoint": "hydrology2.dripline"},
            {"label": {"en": "Reduction", "ar": "تصغير"}, "endpoint": "hydrology2.reduction"},
            {"label": {"en": "Pressure Reduction", "ar": "خفض الضغط"}, "endpoint": "hydrology2.pressure_reduction"},
            {"label": {"en": "Air Eliminator", "ar": "طارد الهواء"}, "endpoint": "hydrology2.air_eliminator"},
            {"label": {"en": "Connectors to Dripline", "ar": "وصلات خط التنقيط"}, "endpoint": "hydrology2.connectors"},
        ),
    },
    {
        "key": "estimation",
        "label": {"en": "Estimation", "ar": "التقدير"},
        "endpoint": "estimation.configuration",
        "sub": (
            {"label": {"en": "Configuration", "ar": "الإعداد"}, "endpoint": "estimation.configuration"},
            {"label": {"en": "Result (Calculation)", "ar": "النتيجة (الحساب)"}, "endpoint": "estimation.result"},
        ),
    },
    {
        "key": "report",
        "label": {"en": "Report", "ar": "التقرير"},
        "endpoint": "report.charts",
        "sub": (
            {"label": {"en": "Charts", "ar": "المخططات"}, "endpoint": "report.charts"},
            {"label": {"en": "Bilan Hydrique", "ar": "الميزان المائي"}, "endpoint": "report.bilan_hydrique"},
            {"label": {"en": "Technical Report", "ar": "التقرير التقني"}, "endpoint": "report.technical"},
        ),
    },
)
