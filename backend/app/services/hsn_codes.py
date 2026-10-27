"""Curated HSN master lists for the picker dropdowns.
Single source for the product and raw-material HSN pickers: the frontend
fetches these through GET /hsn-codes and the Excel import accepts the same
codes. Kept as plain Python (not JSON) so the frozen .exe bundles it as
code - only frontend/ ships as data files, anything under backend/ stored
as a data file would be missing in the packaged app.

The lists are the business's own curated codes, not the full GST master.
They follow the Oct-2026 business HSN tables: product codes padded to 8
digits, raw entries verbatim as stored on the masters.
Validation stays digits-only everywhere (see normalize_hsn): the picker
suggests, it never constrains, so an existing row or a future code outside
these lists keeps working.
"""

import re
from typing import Any

PRODUCT_HSN = [
    {"code": "09109929", "name": "Amchur / Dry Mango Powder (अमचूर पाउडर)"},
    {"code": "09041200", "name": "Black Pepper Powder (काली मिर्च पाउडर)"},
    {"code": "09083230", "name": "Cardamom Powder (इलायची पाउडर)"},
    {"code": "09109100", "name": "Chaat Masala (चाट मसाला)"},
    {"code": "09109100", "name": "Chicken / Meat Masala (चिकन / मीट मसाला)"},
    {"code": "09062000", "name": "Cinnamon Powder (दलचीनी पाउडर)"},
    {"code": "09092200", "name": "Coriander Powder (धनिया पाउडर)"},
    {"code": "09093200", "name": "Cumin Powder (जीरा पाउडर)"},
    {"code": "09093129", "name": "Cumin Seeds (जीरा)"},
    {"code": "09105000", "name": "Curry Powder blends"},
    {"code": "09101210", "name": "Dry Ginger Powder (सोंठ पाउडर)"},
    {"code": "09096230", "name": "Fennel Powder (सौंफ पाउडर)"},
    {"code": "09109924", "name": "Fenugreek Powder (मेथी पाउडर)"},
    {"code": "21039090", "name": "Garam Masala (गरम मसाला)"},
    {"code": "11061090", "name": "Gram Flour (बेसन)"},
    {"code": "09109990", "name": "Kitchen King (किचन किंग)"},
    {"code": "09109990", "name": "Mix Masala (मिक्स मसाला)"},
    {"code": "09081200", "name": "Nutmeg Powder (जयफल पाउडर)"},
    {"code": "21039090", "name": "Pav Bhaji Masala (पाव भाजी मसाला)"},
    {"code": "09042211", "name": "Red Chili Powder (लाल मिर्च पाउडर)"},
    {"code": "21039090", "name": "Sambar Masala (सांभर मसाला)"},
    {"code": "09109100", "name": "Shahi Biryani Masala (शाही बिरयानी मसाला)"},
    {"code": "09103030", "name": "Turmeric Powder (हल्दी पाउडर)"},
]

RAW_MATERIAL_HSN = [
    {"code": "8134010", "name": "Amchoor Powder (आमचूर)"},
    {"code": "13019013", "name": "Asafoetida (हींग)"},
    {"code": "9109911", "name": "Bay Leaf (तेज पत्ता)"},
    {"code": "9083110", "name": "Black Cardamom Large whole (बड़ी इलाइची)"},
    {"code": "9041110", "name": "Black Pepper Whole / Premium (काली मिर्च)"},
    {"code": "25010090", "name": "Black Salt (काला नमक)"},
    {
        "code": "09109990",
        "name": "Blended Masalas (Garam/Kitchen/Sambar/Pav Bhaji/Chaat)",
    },
    {"code": "9109914", "name": "Carom Seed (अजवाइन)"},
    {"code": "07132000", "name": "Chana Dal (चना दाल)"},
    {"code": "07133500", "name": "Chana Dal Roasted (भुना चना दाल)"},
    {"code": "07132000", "name": "Chana Whole Chickpeas (चना)"},
    {"code": "09061110", "name": "Cinnamon Whole bark (दालचीनी)"},
    {"code": "9071010", "name": "Cloves Whole (लौंग)"},
    {"code": "9092110", "name": "Coriander Seed (साबुत धनिया)"},
    {"code": "09093200", "name": "Cumin Powder (जीरा पाउडर)"},
    {"code": "9093129", "name": "Cumin Seed Whole (जीरा)"},
    {"code": "9109929", "name": "Curry Leaves (कढ़ी पत्ता)"},
    {"code": "7129020", "name": "Dehydrated Garlic (सूखा लहसुन)"},
    {"code": "7122000", "name": "Dehydrated Onion (सूखा प्याज)"},
    {"code": "12119029", "name": "Dried Rose Petals (सूखे गुलाब की पंखुड़ियां)"},
    {"code": "9101120", "name": "Dry Ginger (सोंठ)"},
    {"code": "9101210", "name": "Dry Ginger Powder (सोंठ पाउडर)"},
    {"code": "12119070", "name": "Dry Mint Leaves (सूखा पुदीना)"},
    {"code": "25010010", "name": "Edible Common Salt (साधारण नमक)"},
    {"code": "15162091", "name": "Edible Vegetable Oil (खाद्य तेल)"},
    {"code": "09096230", "name": "Fennel Powder (सौंफ पाउडर)"},
    {"code": "09096119", "name": "Fennel Seeds / Saunf (सौंफ)"},
    {"code": "7129090", "name": "Fenugreek Leaves (कसूरी मेथी)"},
    {"code": "9109912", "name": "Fenugreek Seed (मेथी दाना)"},
    {"code": "7129020", "name": "Garlic Powder (लहसुन)"},
    {"code": "11061090", "name": "Gram Flour (बेसन)"},
    {"code": "84425010", "name": "Gravure Printing Cylinder"},
    {"code": "9083120", "name": "Green Cardamom Small whole (हरी इलायची)"},
    {"code": "09042110", "name": "Kashmiri Chili (कश्मीरी मिर्च)"},
    {"code": "9082100", "name": "Mace Whole (जावित्री)"},
    {"code": "998346", "name": "Masala Quality test"},
    {"code": "07133990", "name": "Matar Dal Split peas (मटर दाल)"},
    {"code": "12075000", "name": "Mustard Seed (राई)"},
    {"code": "9093119", "name": "Nigella Seed (कलौंजी)"},
    {"code": "9081100", "name": "Nutmeg Whole (जायफल)"},
    {"code": "8134090", "name": "Pomegranate Seed (अनारदाना)"},
    {"code": "12079100", "name": "Poppy Seeds (खसखस)"},
    {"code": "39199090", "name": "Pouch Packet label"},
    {"code": "9042110", "name": "Red Chili Whole dried (लाल मिर्च)"},
    {"code": "25010020", "name": "Rock Salt (सेंधा नमक)"},
    {"code": "09102010", "name": "Saffron / Kesar (केसर)"},
    {"code": "12074090", "name": "Sesame Seed (तिल)"},
    {"code": "09093129", "name": "Shahi Jeera (शाही जीरा)"},
    {"code": "09109915", "name": "Star Anise (चक्र फूल)"},
    {"code": "8134010", "name": "Tamarind (इमली)"},
    {"code": "07136000", "name": "Toor Dal (अरहर / तुअर दाल)"},
    {"code": "9103020", "name": "Turmeric Fresh/Whole (हल्दी)"},
    {"code": "9041120", "name": "White Pepper (सफेद मिर्च)"},
    {"code": "49111010", "name": "Wrapper (रैपर)"},
]


def get_hsn_codes(kind: str = "all") -> dict:
    """Picker payload. kind is product, raw_material, or all."""
    kind = (kind or "all").strip().lower()
    if kind in ("product", "products"):
        return {"type": "product", "codes": list(PRODUCT_HSN)}
    if kind in ("raw_material", "raw_materials", "raw"):
        return {"type": "raw_material", "codes": list(RAW_MATERIAL_HSN)}
    return {
        "type": "all",
        "products": list(PRODUCT_HSN),
        "raw_materials": list(RAW_MATERIAL_HSN),
    }


def normalize_hsn(value: Any) -> str | None:
    """Digits-only HSN or None.

    Accepts the Excel shapes (int/float/str, surrounding spaces) and keeps
    blank as None. Anything non-numeric is rejected (None) so callers store
    a clean code or nothing - never a half-pasted label like "0904 - Pepper".
    """
    if value is None:
        return None
    if isinstance(value, float):
        value = int(value) if value.is_integer() else str(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    return text if text.isdigit() else None


# Name -> code suggestion tables, first match wins, so order is priority:
# blend words before ingredient words (a "Chilli Masala" is a blend, not
# chilli powder), powder/seed variants before the bare word, and the more
# specific multi-word form ("black cardamom", "chana dal") before its head
# word ("cardamom", "chana"). Raw materials default to the seed/whole
# code; the powder code only fires on an explicit powder word.
_BLEND_WORDS = [
    "garam", "kitchen", "sambar", "sambhar", "chaat", "rasam",
    "pav", "chole", "blend", "masala",
]

_PRODUCT_RULES = [
    ("09109990", ["mix masala"]),
    ("09109100", ["chaat"]),
    ("09109100", ["chicken", "meat"]),
    ("09109100", ["shahi", "biryani"]),
    ("21039090", ["garam", "sambar", "sambhar", "pav"]),
    ("09105000", ["curry"]),
    ("09109990", ["kitchen", "rasam", "chole", "blend", "masala"]),
    ("09109990", ["mix"]),
    ("09103030", ["turmeric", "haldi"]),
    ("09092200", ["coriander", "dhaniya", "dhania"]),
    ("09042211", ["chilli", "chili", "mirch"]),
    ("09093200", ["cumin powder", "jeera powder"]),
    ("09093129", ["cumin", "jeera"]),
    ("09041200", ["pepper", "kalimirch", "kali mirch"]),
    ("11061090", ["besan", "gram flour"]),
    ("09062000", ["cinnamon", "dalchini"]),
    ("09101210", ["ginger", "sonth", "saunth"]),
    ("09081200", ["nutmeg", "jaifal"]),
    ("09083230", ["cardamom", "elaichi", "elachi", "ilayachi"]),
    ("09109924", ["fenugreek", "methi"]),
    ("09096230", ["fennel", "saunf", "sauf"]),
    ("09109929", ["amchur", "amchoor", "mango"]),
]

_RAW_RULES = [
    ("998346", ["quality"]),
    ("39199090", ["pouch", "label"]),
    ("84425010", ["gravure", "cylinder"]),
    ("09109990", _BLEND_WORDS),
    ("9103020", ["turmeric", "haldi"]),
    ("9092110", ["coriander", "dhaniya", "dhania"]),
    ("09042110", ["kashmiri"]),
    ("9042110", ["chilli", "chili", "mirch"]),
    ("09093200", ["cumin powder", "jeera powder"]),
    ("09093129", ["cumin", "jeera"]),
    ("9041120", ["white pepper"]),
    ("9041110", ["pepper", "kalimirch", "kali mirch"]),
    ("07133500", ["roasted"]),
    ("07132000", ["chana dal", "chickpea"]),
    ("07132000", ["chana", "kabuli"]),
    ("9101210", ["ginger powder", "sonth powder", "saunth powder"]),
    ("9101120", ["ginger", "sonth", "saunth"]),
    ("7129020", ["garlic", "lehsun", "lahsun"]),
    ("7122000", ["onion", "pyaz", "pyaaz"]),
    ("8134010", ["amchoor", "amchur", "mango"]),
    ("9071010", ["clove", "laung", "lavang"]),
    ("9083110", ["black cardamom", "badi elaichi", "badi cardamom"]),
    ("9083120", ["cardamom", "elaichi", "elachi", "ilayachi"]),
    ("9109914", ["carom", "ajwain"]),
    ("9109911", ["bay", "tejpatta", "tej patta", "tamalpatra"]),
    ("9081100", ["nutmeg", "jaifal"]),
    ("9082100", ["mace", "javitri", "japatri"]),
    ("09061110", ["cinnamon", "dalchini"]),
    ("12075000", ["mustard", "rai", "sarson"]),
    ("13019013", ["hing", "asafoetida"]),
    ("9109912", ["fenugreek seed", "methi dana"]),
    ("7129090", ["methi", "fenugreek", "kasuri"]),
    ("9093119", ["nigella", "kalonji", "kalongi"]),
    ("49111010", ["wrapper"]),
    ("29181400", ["citric", "tatri", "nimbu"]),
    ("09096230", ["fennel powder", "saunf powder"]),
    ("09096119", ["fennel", "saunf", "sauf"]),
    ("09109915", ["star anise", "anise", "chakra"]),
    ("11061090", ["besan", "gram flour"]),
    ("25010090", ["black salt", "kala namak"]),
    ("25010010", ["common salt", "edible salt", "sadha namak", "sadharan"]),
    ("25010020", ["rock salt", "sendha"]),
    ("25010021", ["iodized", "iodised"]),
    ("8134010", ["tamarind", "imli"]),
    ("12079100", ["poppy", "khaskhas", "khas khas"]),
    ("09102010", ["saffron", "kesar"]),
    ("07136000", ["toor", "arhar"]),
    ("12074090", ["sesame"]),
    ("8134090", ["pomegranate", "anar"]),
    ("9109929", ["curry leaf", "curry leaves", "kadi", "kadhi"]),
    ("12119029", ["rose"]),
    ("12119070", ["mint", "pudina"]),
    ("15162091", ["oil"]),
    ("84425010", ["gravure", "cylinder"]),
    ("07133990", ["matar", "mater", "peas"]),
]


def suggest_hsn_code(name: Any, kind: str = "product") -> dict | None:
    """Suggest {code, name} for a product/raw-material name, or None.

    Best-effort only: the form fills an empty HSN box with the suggestion
    and the user can always change it. No match (or a blank name) returns
    None so a wrong code is never stored silently.
    """
    # Names from the masters can carry non-breaking spaces; collapse all
    # whitespace so multi-word rules still match.
    text = re.sub(r"\s+", " ", str(name or "")).strip().lower()
    if not text:
        return None
    kind = (kind or "product").strip().lower()
    rules = _RAW_RULES if kind.startswith("raw") else _PRODUCT_RULES
    entries = RAW_MATERIAL_HSN if kind.startswith("raw") else PRODUCT_HSN
    for code, words in rules:
        for word in words:
            if re.search(r"\b" + re.escape(word) + r"s?\b", text):
                label = next((e["name"] for e in entries if e["code"] == code), code)
                return {"code": code, "name": label}
    return None
