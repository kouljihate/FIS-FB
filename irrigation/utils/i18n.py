"""Minimal bilingual (English / Arabic) string table.

The app is bilingual by requirement, so user-facing chrome goes through
:func:`translate`.  Arabic strings are the ones shown when the session language
is ``ar`` and the layout switches to right-to-left.
"""

from __future__ import annotations

import re

SUPPORTED = ("en", "ar")

RTL_LANGUAGES = ("ar",)

STRINGS: dict[str, dict[str, str]] = {
    # generic chrome
    "app_name": {"en": "Irrigation Planning System", "ar": "نظام تخطيط الري"},
    "app_short": {"en": "FIS", "ar": "ن.ت.ر"},
    "sign_in": {"en": "Sign in", "ar": "تسجيل الدخول"},
    "sign_out": {"en": "Sign out", "ar": "تسجيل الخروج"},
    "register": {"en": "Create account", "ar": "إنشاء حساب"},
    "username": {"en": "Username", "ar": "اسم المستخدم"},
    "password": {"en": "Password", "ar": "كلمة المرور"},
    "confirm_password": {"en": "Confirm password", "ar": "تأكيد كلمة المرور"},
    "email": {"en": "Email", "ar": "البريد الإلكتروني"},
    "full_name": {"en": "Full name", "ar": "الاسم الكامل"},
    "role": {"en": "Profile", "ar": "الصلاحية"},
    "language": {"en": "Language", "ar": "اللغة"},
    "profile": {"en": "Profile", "ar": "الملف الشخصي"},
    "english": {"en": "English", "ar": "الإنجليزية"},
    "arabic": {"en": "Arabic", "ar": "العربية"},
    # table / crud
    "actions": {"en": "Actions", "ar": "إجراءات"},
    "name": {"en": "Name", "ar": "الاسم"},
    "item": {"en": "Item", "ar": "البند"},
    "kind": {"en": "Type", "ar": "النوع"},
    "material": {"en": "Material", "ar": "المادة"},
    "unit": {"en": "Unit", "ar": "الوحدة"},
    "quantity": {"en": "Quantity", "ar": "الكمية"},
    "unit_price": {"en": "Unit price", "ar": "سعر الوحدة"},
    "line_total": {"en": "Line total", "ar": "الإجمالي"},
    "level": {"en": "Level", "ar": "المستوى"},
    "sector": {"en": "Sector", "ar": "القطاع"},
    "zone": {"en": "Zone", "ar": "المنطقة"},
    "row": {"en": "Row", "ar": "الصف"},
    "records": {"en": "Records", "ar": "السجلات"},
    "notes": {"en": "Notes", "ar": "ملاحظات"},
    "boundary": {"en": "Boundary", "ar": "الحدود"},
    "crop": {"en": "Crop", "ar": "المحصول"},
    "soil": {"en": "Soil", "ar": "التربة"},
    "system": {"en": "System", "ar": "النظام"},
    "valve": {"en": "Valve", "ar": "الصمام"},
    "species": {"en": "Species", "ar": "الصنف"},
    "drippers": {"en": "Drippers", "ar": "القطّارات"},
    "age_years": {"en": "Age (years)", "ar": "العمر (سنة)"},
    "azimuth": {"en": "Azimuth (deg)", "ar": "الاتجاه (درجة)"},
    "taxable_amount": {"en": "Taxable amount", "ar": "المبلغ الخاضع للضريبة"},
    "block_flow_shared": {"en": "Block flow shared by", "ar": "من يتقاسم تدفق الكتلة"},
    "loss": {"en": "Loss (m)", "ar": "الفقد (م)"},
    "apply": {"en": "Apply", "ar": "تطبيق"},
    "delete_permanently": {"en": "Delete permanently", "ar": "حذف نهائي"},
    "seed_quantities": {"en": "Seed quantities from design", "ar": "توليد الكميات من التصميم"},
    "print_pdf": {"en": "Print / PDF", "ar": "طباعة / PDF"},
    "project_details": {"en": "Project details", "ar": "بيانات المشروع"},
    "edit_project": {"en": "Edit project", "ar": "تعديل المشروع"},
    "map_file": {"en": "Land map (KML / KMZ / DXF)", "ar": "خريطة الأرض (KML / KMZ / DXF)"},
    "linked_map": {"en": "Linked map", "ar": "الخريطة المرتبطة"},
    "replace_map": {"en": "Replace map", "ar": "استبدال الخريطة"},
    "download": {"en": "Download", "ar": "تنزيل"},
    "parent_sector": {"en": "Parent sector", "ar": "القطاع الأب"},
    "cut_direction": {"en": "Cut direction", "ar": "اتجاه القطع"},
    "position": {"en": "Position along the axis", "ar": "الموضع على المحور"},
    "number_of": {"en": "Number", "ar": "العدد"},
    "sector_target_ha": {"en": "Sector target (ha)", "ar": "هدف القطاع (هكتار)"},
    "zones_per_sector": {"en": "Zones per sector", "ar": "المناطق لكل قطاع"},
    "pipe_diameters_mm": {"en": "Pipe diameters (mm, CSV)", "ar": "أقطار الأنابيب (مم)"},
    "dripper_flow_lph": {"en": "Dripper flow (L/h)", "ar": "تدفق القطّارة (ل/س)"},
    "dripper_spacing_m": {"en": "Dripper spacing (m)", "ar": "تباعد القطّارات (م)"},
    "lateral_spacing_m": {"en": "Lateral / row spacing (m)", "ar": "تباعد الخطوط (م)"},
    "grant_access": {"en": "Grant access by username", "ar": "منح الوصول باسم المستخدم"},
    "current_password": {"en": "Current password", "ar": "كلمة المرور الحالية"},
    "new_password": {"en": "New password", "ar": "كلمة المرور الجديدة"},
    "confirm_new_password": {"en": "Confirm new password", "ar": "تأكيد كلمة المرور الجديدة"},
    "remember_browser": {"en": "Remember this browser", "ar": "تذكّر هذا المتصفح"},
    "row_spacing_m": {"en": "Row spacing (m)", "ar": "تباعد الصفوف (م)"},
    "add": {"en": "Add", "ar": "إضافة"},
    "edit": {"en": "Edit", "ar": "تعديل"},
    "rename": {"en": "Rename", "ar": "إعادة تسمية"},
    "remove": {"en": "Remove", "ar": "حذف"},
    "save": {"en": "Save", "ar": "حفظ"},
    "cancel": {"en": "Cancel", "ar": "إلغاء"},
    "close": {"en": "Close", "ar": "إغلاق"},
    "confirm": {"en": "Confirm", "ar": "تأكيد"},
    "no_items": {"en": "No records yet.", "ar": "لا توجد سجلات بعد."},
    "select_project": {"en": "Select a project", "ar": "اختر مشروعاً"},
    "project": {"en": "Project", "ar": "المشروع"},
    "geometry": {"en": "Geometry", "ar": "الهندسة"},
    "search": {"en": "Search", "ar": "بحث"},
    "working": {"en": "Working…", "ar": "جارٍ العمل…"},
    "reset": {"en": "Reset", "ar": "إعادة تعيين"},
    "print": {"en": "Print / PDF", "ar": "طباعة / PDF"},
    # geometry
    "sectors": {"en": "Sectors", "ar": "القطاعات"},
    "zones": {"en": "Zones", "ar": "المناطق"},
    "split": {"en": "Split", "ar": "تقسيم"},
    "merge": {"en": "Merge", "ar": "دمج"},
    "swap": {"en": "Swap", "ar": "تبديل"},
    "area": {"en": "Area", "ar": "المساحة"},
    "perimeter": {"en": "Perimeter", "ar": "المحيط"},
    "vertices": {"en": "Vertices", "ar": "الرؤوس"},
    "generate": {"en": "Generate", "ar": "توليد"},
    "validate": {"en": "Validate", "ar": "تحقق"},
    # project
    "new_project": {"en": "New project", "ar": "مشروع جديد"},
    "projects": {"en": "Projects", "ar": "المشاريع"},
    "land": {"en": "Land", "ar": "الأرض"},
    "description": {"en": "Description", "ar": "الوصف"},
    # water
    "well": {"en": "Well", "ar": "بئر"},
    "basin": {"en": "Basin", "ar": "حوض"},
    "other_source": {"en": "Other source", "ar": "مصدر آخر"},
    "flow": {"en": "Flow", "ar": "التدفق"},
    "static_level": {"en": "Static level", "ar": "المستوى الساكن"},
    "depth": {"en": "Depth", "ar": "العمق"},
    "capacity": {"en": "Capacity", "ar": "السعة"},
    # hydrology
    "diameter": {"en": "Diameter", "ar": "القطر"},
    "length": {"en": "Length", "ar": "الطول"},
    "material": {"en": "Material", "ar": "المادة"},
    "velocity": {"en": "Velocity", "ar": "السرعة"},
    "head_loss": {"en": "Head loss", "ar": "الفقد في الضغط"},
    "pressure": {"en": "Pressure", "ar": "الضغط"},
    # trees
    "rows": {"en": "Rows", "ar": "الصفوف"},
    "trees": {"en": "Trees", "ar": "الأشجار"},
    "spacing": {"en": "Spacing", "ar": "التباعد"},
    "count": {"en": "Count", "ar": "العدد"},
    # estimation / report
    "configuration": {"en": "Configuration", "ar": "الإعداد"},
    "result": {"en": "Result (Calculation)", "ar": "النتيجة (الحساب)"},
    "subtotal": {"en": "Subtotal", "ar": "المجموع الفرعي"},
    "discount": {"en": "Discount", "ar": "الخصم"},
    "tax": {"en": "Tax", "ar": "الضريبة"},
    "total": {"en": "Total", "ar": "الإجمالي"},
    "charts": {"en": "Charts", "ar": "المخططات"},
    "bilan_hydrique": {"en": "Bilan Hydrique", "ar": "الميزان المائي"},
    "technical_report": {"en": "Technical Report", "ar": "التقرير التقني"},
    # phase banner
    "phase_pending": {
        "en": "This screen is scheduled for the next build phase.",
        "ar": "هذه الشاشة مبرمجة للمرحلة القادمة.",
    },
}


#: Arabic for the labels the route modules hand to the templates (table columns
#: and form fields).  Keyed by the *normalised* English text so that
#: ``"Diameter (mm)"`` and the field name ``"diameter_mm"`` both resolve.
LABELS_AR = {
    "flow m3 h": "التدفق (م³/س)",
    "flow m3h": "التدفق (م³/س)",
    "static level m": "المستوى الساكن (م)",
    "depth m": "العمق (م)",
    "capacity m3": "السعة (م³)",
    "capacity m3 ": "السعة (م³)",
    "notes": "ملاحظات",
    "diameter mm": "القطر (مم)",
    "length m": "الطول (م)",
    "material": "المادة",
    "velocity m s": "السرعة (م/ث)",
    "head loss m": "الفقد في الضغط (م)",
    "spacing m": "التباعد (م)",
    "nominal flow l h": "التدفق الاسمي (ل/س)",
    "emitter flow l h": "تدفق القطّارة (ل/س)",
    "setting m": "الضبط (م)",
    "reduction ratio": "نسبة التصغير",
    "sector": "القطاع",
    "sector name": "القطاع",
    "zone": "المنطقة",
    "zone id": "المنطقة",
    "sector id": "القطاع",
    "row": "الصف",
    "row id": "الصف",
    "row name": "الصف",
    "species": "الصنف",
    "drippers": "القطّارات",
    "drippers count": "عدد القطّارات",
    "age y": "العمر (سنة)",
    "age years": "العمر (سنة)",
    "azimuth deg": "الاتجاه (درجة)",
    "trees": "الأشجار",
    "tree count": "عدد الأشجار",
    "lat": "خط العرض",
    "lon": "خط الطول",
    "inlet pressure m": "ضغط الدخل (م)",
    "flow m3 h ": "التدفق (م³/س)",
    "pipe diameters mm": "أقطار الأنابيب (مم)",
    "dripper flow lph": "تدفق القطّارة (ل/س)",
    "dripper spacing m": "تباعد القطّارات (م)",
    "lateral spacing m": "تباعد الخطوط (م)",
    "sector target ha": "هدف القطاع (هكتار)",
    "zones per sector": "المناطق لكل قطاع",
    "irrigation system": "نظام الري",
    "sector type": "نوع القطاع",
}


def _normalise(text: str) -> str:
    """Lower-case and collapse anything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def label_ar(text: str) -> str:
    """Arabic for a label produced by a route module; the input if unknown.

    Labels arrive as display text (``"Diameter (mm)"``) or as field names
    (``"diameter_mm"``), so both spellings share one normalised key.
    """
    if not text:
        return ""
    key = _normalise(text)
    if key in LABELS_AR:
        return LABELS_AR[key]
    return LABELS_AR.get(_normalise(str(text).replace("_", " ")), text)


def translate(key: str, lang: str = "en") -> str:
    """Look ``key`` up in ``lang``, falling back to English then the key itself."""
    entry = STRINGS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("en") or key


def direction(lang: str) -> str:
    return "rtl" if lang in RTL_LANGUAGES else "ltr"
