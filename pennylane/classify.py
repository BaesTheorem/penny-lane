"""Coarse product type for filtering ("show me food").

Two inputs: the retailer's own category path when a lane returns one
(Dollar General `categoryHierarchies`, Home Depot taxonomy breadcrumbs),
and the item name as the fallback. The mapping is deliberately coarse:
a dozen buckets a person would pick from a menu, not a taxonomy.
"""

from __future__ import annotations

import re

TYPES = [
    ("food", "Food & drink"),
    ("household", "Household & cleaning"),
    ("beauty", "Beauty & personal care"),
    ("health", "Health & medicine"),
    ("baby", "Baby & kids"),
    ("pet", "Pet"),
    ("tools", "Tools & hardware"),
    ("building", "Building & plumbing"),
    ("electrical", "Electrical & lighting"),
    ("garden", "Garden & outdoor"),
    ("home", "Home, decor & storage"),
    ("appliance", "Appliances & kitchen"),
    ("electronics", "Electronics"),
    ("toys", "Toys, games & seasonal"),
    ("apparel", "Apparel"),
    ("auto", "Automotive"),
    ("office", "Office & school"),
    ("other", "Other"),
]
LABELS = dict(TYPES)

# Category-path roots (lowercased, substring match) -> type.
PATH_RULES = [
    ("food", ["food", "beverage", "snack", "candy", "grocery", "treats drinks", "drinks", "pantry", "frozen", "dairy", "produce"]),
    ("household", ["household", "cleaning", "laundry", "paper", "trash", "storage bags", "dish", "air care", "home essentials"]),
    ("beauty", ["beauty", "personal care", "hair", "skin", "cosmetic", "fragrance", "shave", "oral care", "bath & body"]),
    ("health", ["health", "medicine", "pharmacy", "vitamin", "first aid", "wellness"]),
    ("baby", ["baby", "infant", "kids", "diaper", "nursery"]),
    ("pet", ["pet", "dog", "cat ", "cat/", "animal"]),
    ("tools", ["tool", "hardware", "fastener", "power tool", "hand tool", "workwear", "safety"]),
    ("building", ["building", "lumber", "plumbing", "flooring", "paint", "doors", "windows", "roofing", "insulation", "millwork", "drywall", "concrete", "tile"]),
    ("electrical", ["electrical", "lighting", "light bulb", "wire", "ceiling fan", "smart home"]),
    ("garden", ["garden", "outdoor", "lawn", "patio", "grill", "plant", "pest", "fire pit", "snow"]),
    ("home", ["decor", "furniture", "storage", "organization", "bath", "kitchen", "bedding", "rug", "curtain", "closet"]),
    ("appliance", ["appliance", "refrigerator", "washer", "dryer", "dishwasher", "microwave", "range", "cooktop", "vacuum"]),
    ("electronics", ["electronic", "tv", "audio", "phone", "camera", "computer", "battery", "charger"]),
    ("toys", ["toy", "game", "seasonal", "holiday", "christmas", "halloween", "party", "fireworks", "sporting"]),
    ("apparel", ["apparel", "clothing", "shoes", "footwear", "accessories", "sock"]),
    ("auto", ["auto", "automotive", "car care", "tire"]),
    ("office", ["office", "school", "stationery", "craft"]),
]

# Name keywords (word-boundary regex) -> type, checked in order; first hit wins.
NAME_RULES = [
    # Textiles named after food ("Ice Cream Mat") are home goods.
    ("home", r"\b(mats?|rugs?|towels?|pillows?|blankets?|throws?|curtains?)\b"),
    ("beauty", r"\b(shampoo|conditioner|hair color|hair dye|lightening|bond care|ointment|cocoa butter|shea butter|body (oil|butter|cream|lotion|mist)|night cream|day cream|moisture|hydrating|dark and lovely|relaxer|skin hydrating|overnight mask|curling|flat iron|ceramic (barrel|iron)|lip gloss|lip balm|loose powder|setting powder|oil-control|primer stick|makeup|eyeshadow|brow|lashes|lipstick|mascara|eyeliner|foundation|concealer|blush|nail polish|lotion|moisturiz|body wash|deodorant|antiperspirant|razor|shave|cologne|perfume|body spray|face wash|cleanser|serum|skin therapy|cocoa butter|micellar|toothpaste|toothbrush|mouthwash|floss|hair spray|gel|pomade|curl|keratin|bleach kit|acne|sunscreen|lip balm|cotton rounds?)\b"),
    ("health", r"\b(vitamin|supplement|ibuprofen|acetaminophen|tylenol|advil|aleve|allergy|antacid|tums|pepto|cold & flu|cough|nasal|thermometer|bandage|band-aid|first aid|melatonin|probiotic|laxative|eye drops?|contact lens|pregnancy test|glucose|blood pressure)\b"),
    ("baby", r"\b(diapers?|wipes|pacifier|sippy|onesie|baby|infant|toddler|formula|teether)\b"),
    ("pet", r"\b(dog|cat|puppy|kitten|pet|litter|kibble|chew toy|flea|leash|aquarium|bird seed)\b"),
    ("household", r"\b(detergent|bleach|cleaner|disinfect|wipes|sponge|paper towels?|toilet paper|tissue|trash bags?|liners?|air freshener|scented oil|refill|candle|dish soap|fabric softener|dryer sheets?|mop|broom|dustpan|swiffer|lysol|clorox|febreze|glade|air wick|tide|gain|downy|ziploc|foil|plastic wrap|storage bags?|hangers?|laundry)\b"),
    ("toys", r"\b(toy|game|puzzle|lego|doll|plush|nerf|balloon|party|costume|halloween|christmas|ornament|wreath|garland|inflatable|fireworks|sparklers?|snaps|pop party|smoke balls?|easter|valentine)\b"),
    # Frozen treats and drinks first: "freezer pops" would otherwise read as an appliance.
    ("food", r"\b(freezer? pops?|freezer? (pouch|bars?)|freeze bars?|ice pops?|italian ices?|smoothie bars?|cola|root beer|ginger ale|franks?|hydration|electrolyte|arizona|clover valley|popsicles?|slushie|icee|soymilk|oat ?milk|almond ?milk|hot dogs?|sausage|bacon|lunch ?meat|bread|buns|rolls|tortillas?|waffles?|pancake|muffins?|donuts?|bagels?|frozen|pizza|burrito|nuggets|fries|ice cream|sherbet|gelato|sprinkles)\b"),
    ("food", r"\b(oz|fl oz|ct|pk|pack|bag|box)\b.*\b(snack|chips?|candy|cookies?|crackers?|cereal|soda|juice|drink|water|coffee|tea|sauce|soup|pasta|rice|beans?|nuts?|gum|mints?|chocolate|bar|jerky|popcorn|pretzels?|milk|cheese|yogurt|bread|buns?|punch|lemonade|energy|protein|granola|oatmeal|syrup|honey|jam|jelly|peanut butter|ketchup|mustard|mayo|dressing|seasoning|spice|salt|sugar|flour|mix|cake|brownie|pudding|gelatin|jello|marshmallow|twists?|sprinkles)\b"),
    ("food", r"\b(capri sun|kool-aid|hawaiian punch|sunnyd|skittles|airheads|starburst|m&m|reese|hershey|snickers|kit kat|twix|oreo|doritos|cheetos|lays|pringles|gatorade|pepsi|coke|coca-cola|dr pepper|sprite|mountain dew|red bull|monster|nestle|kraft|heinz|campbell|chef boyardee|hormel|spam|ramen|maruchan|nissin|betty crocker|pillsbury|duncan hines|jell-o|quaker|kellogg|cheerios|pop-tarts|nutri-grain|slim jim|planters|little debbie|hostess|pop tarts|crystal light|mio)\b"),
    ("food", r"\b(snack|candy|cookie|cracker|cereal|soda|juice|drink|water|milk|cheese|yogurt|coffee|tea bags?|sauce|soup|noodles?|rice|beans|gummies|chocolate|jerky|popcorn|pretzel|lemonade|punch|syrup|seasoning|spice|flour|sugar|cake mix|brownie|pudding|marshmallow|gum|mints?|licorice|taffy|lollipop|fruit snacks?|trail mix|granola|protein bar|energy drink|sports drink|iced tea|hot cocoa|cocoa mix|creamer)s?\b"),
    ("tools", r"\b(drill|driver|impact|saw|sander|grinder|wrench|socket|ratchet|pliers|screwdriver|hammer|level|tape measure|multimeter|tool|packout|toolbox|tool box|bit set|blade|clamp|chisel|utility knife|nail gun|stapler|air compressor|work light|headlamp|flashlight|gloves|safety glasses|knee pads?|tool belt|ladder|sawhorse|wet dry vac|shop vac|vacuum)\b"),
    ("building", r"\b(lumber|plywood|drywall|insulation|shingle|tile|flooring|laminate|vinyl plank|hardwood|carpet|underlayment|grout|mortar|concrete|cement|caulk|sealant|adhesive|primer|paint|stain|roller|brush|pipe|fitting|valve|faucet|toilet|sink|shower|tub|water heater|softener|pvc|pex|copper|door|window|trim|molding|baseboard|lockset|deadbolt|hinge|fence|railing|deck)\b"),
    ("electrical", r"\b(wire|cable|outlet|receptacle|switch|breaker|conduit|light bulb|led bulb|fixture|chandelier|pendant|sconce|ceiling fan|lamp|smart plug|dimmer|extension cord|surge|charger|battery pack|generator|inverter|solar|thermostat|doorbell|camera|floodlight)\b"),
    ("garden", r"\b(mower|trimmer|blower|chainsaw|hedge|edger|hose|sprinkler|nozzle|planter|pot|soil|mulch|fertilizer|seed|grass|weed|pest|rodent|trap|grill|smoker|patio|umbrella|fire pit|outdoor|lawn|garden|rake|shovel|wheelbarrow|snow|ice melt|pool|bird)\b"),
    ("appliance", r"\b(refrigerator|freezer|washer|dryer|dishwasher|microwave|range|oven|cooktop|air fryer|blender|toaster|coffee maker|mini split|air conditioner|dehumidifier|humidifier|heater|water dispenser|ice maker)\b"),
    ("electronics", r"\b(tv|television|headphones?|earbuds?|speaker|bluetooth|wireless|usb|hdmi|router|laptop|tablet|phone case|screen protector|power bank|aa batteries|aaa batteries)\b"),
    ("apparel", r"\b(shirt|t-shirt|tee|hoodie|jacket|pants|jeans|shorts|socks|underwear|bra|shoes|boots|sandals|slippers|hat|cap|gloves|scarf|leggings|dress)\b"),
    ("home", r"\b(cabinet|vanity|shelf|shelving|storage|bin|tote|organizer|basket|rug|curtain|blind|shade|mirror|frame|wall art|clock|pillow|blanket|sheet set|comforter|towel|mat|hooks?|rack|hamper|trash can|wastebasket|chair|table|stool|bench|desk|sofa|mattress|cookware|skillet|pan|knife set|cutlery|plates?|bowls?|mugs?|tumbler|water bottle|container)\b"),
    ("auto", r"\b(motor oil|antifreeze|wiper|car wash|tire|jumper|car charger|air freshener for car|windshield)\b"),
    ("office", r"\b(pens?|pencils?|markers?|notebook|binder|folder|tape|stapler|paper clips|envelopes?|labels?|printer paper|crayons?|glue|scissors)\b"),
]
_COMPILED = [(t, re.compile(rx, re.I)) for t, rx in NAME_RULES]


def classify(name: str = "", category: str = "", retailer: str = "") -> str:
    cat = (category or "").lower()
    if cat:
        for t, keys in PATH_RULES:
            if any(k in cat for k in keys):
                return t
    n = f" {name or ''} "
    # Hardware chains sell no food, and "water", "bar" and "ice" are everywhere there.
    skip_food = retailer in ("homedepot", "lowes")
    for t, rx in _COMPILED:
        if skip_food and t == "food":
            continue
        if rx.search(n):
            return t
    if retailer in ("homedepot", "lowes"):
        return "tools"
    return "other"
