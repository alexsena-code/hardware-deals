"""Extract base model from product names via regex.

Examples:
  "RTX 4060 Ti GAMING OC 8GB" → "RTX 4060 Ti"
  "RX 9070 XT GAMING OC 16G" → "RX 9070 XT"
  "Ryzen 5 7600X" → "Ryzen 5 7600X"
  "Core i7-13700K" → "Core i7-13700K"
  "DDR4 ECC 16GB" → "DDR4 16GB"
  "B550M PRO-VDH WIFI" → "B550M"
"""
import re

# GPU patterns: brand + model number + optional Ti/XT/Super
_GPU_PATTERN = re.compile(
    r"((?:RTX|GTX|RX|Arc|Tesla|Quadro)\s*\d{3,5}\s*(?:Ti|XT|Super|S)?)",
    re.IGNORECASE,
)

# CPU patterns
_CPU_INTEL = re.compile(r"(Core\s+i\d-\d{4,5}\w*)", re.IGNORECASE)
_CPU_AMD = re.compile(r"((?:Ryzen|Athlon|Threadripper)\s+\d?\s*\d{4}\w*)", re.IGNORECASE)
_CPU_XEON = re.compile(r"(Xeon\s+\w+-?\d{4}\w*)", re.IGNORECASE)

# RAM: type + capacity
_RAM_PATTERN = re.compile(r"(DDR\d)\s*(?:ECC\s*)?(\d+\s*(?:x\s*\d+\s*)?GB)", re.IGNORECASE)

# Motherboard: chipset
_MOBO_PATTERN = re.compile(
    r"((?:B|H|Z|X)\d{3,4}\w?)",
    re.IGNORECASE,
)

# SSD: brand + model
_SSD_CAPACITY = re.compile(r"(\d+\s*(?:TB|GB))", re.IGNORECASE)

# PSU: wattage
_PSU_PATTERN = re.compile(r"(\d+\s*W)", re.IGNORECASE)

# Monitor: size + resolution
_MONITOR_SIZE = re.compile(r"(\d+(?:\.\d+)?\")")
_MONITOR_RES = re.compile(r"(4K|QHD|WQHD|Full HD|FHD|UHD)", re.IGNORECASE)


def extract_base_model(name: str, category: str = "") -> str:
    """Extract the base model identifier from a product name.

    Returns a simplified, groupable model name.
    """
    if not name:
        return name

    cat = category.lower()

    if cat == "gpu":
        m = _GPU_PATTERN.search(name)
        if m:
            return m.group(1).strip().upper().replace("  ", " ")

    if cat in ("cpu", "cpu-kit"):
        for pat in [_CPU_INTEL, _CPU_AMD, _CPU_XEON]:
            m = pat.search(name)
            if m:
                return m.group(1).strip()

    if cat == "ram":
        m = _RAM_PATTERN.search(name)
        if m:
            ddr = m.group(1).upper()
            cap = m.group(2).replace(" ", "")
            ecc = " ECC" if "ecc" in name.lower() else ""
            return f"{ddr}{ecc} {cap}"

    if cat == "motherboard":
        m = _MOBO_PATTERN.search(name)
        if m:
            return m.group(1).upper()

    if cat == "ssd":
        m = _SSD_CAPACITY.search(name)
        if m:
            # Try to get brand + capacity
            brand = name.split()[0] if name.split() else ""
            return f"{brand} {m.group(1)}".strip()

    if cat == "psu":
        m = _PSU_PATTERN.search(name)
        if m:
            return m.group(1)

    if cat == "monitor":
        size = _MONITOR_SIZE.search(name)
        res = _MONITOR_RES.search(name)
        parts = []
        if size:
            parts.append(size.group(1))
        if res:
            parts.append(res.group(1).upper())
        if parts:
            return " ".join(parts)

    # Fallback: return first 3 words
    words = name.split()[:3]
    return " ".join(words)
