"""Parse PCBuildWizard `details` field into structured specs via regex."""
import re


def parse_gpu_specs(details: str, name: str = "") -> dict:
    specs = {}
    # VRAM: "12 GB", "8 GB"
    m = re.search(r"(\d+)\s*GB", details)
    if m:
        specs["vram_gb"] = int(m.group(1))
    # Memory type: GDDR6, GDDR6X, GDDR7
    m = re.search(r"(GDDR\d+X?)", details)
    if m:
        specs["memory_type"] = m.group(1)
    # PCIe: "PCIe x16 4.0", "PCIe x8 5.0"
    m = re.search(r"PCIe\s+x(\d+)\s+([\d.]+)", details)
    if m:
        specs["pcie_lanes"] = int(m.group(1))
        specs["pcie_gen"] = float(m.group(2))
    # LHR
    if "LHR" in details or "LHR" in name:
        specs["lhr"] = True
    return specs


def parse_cpu_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Cores: "6-Core", "14-Core (6P+8E)"
    m = re.search(r"(\d+)-Core", details)
    if m:
        specs["cores"] = int(m.group(1))
    # P+E cores: "(6P+8E)" — Intel hybrid only
    m = re.search(r"\((\d+)P\+(\d+)E\)", details)
    if m:
        specs["p_cores"] = int(m.group(1))
        specs["e_cores"] = int(m.group(2))
    # HT / SMT → infer threads
    # Match HT/SMT as standalone tokens (avoid matching inside words like "HTTPS")
    has_ht = bool(re.search(r"\bHT\b", details))
    has_smt = bool(re.search(r"\bSMT\b", details))
    if has_ht or has_smt:
        cores = specs.get("cores", 0)
        if "p_cores" in specs and "e_cores" in specs:
            # Intel hybrid: P-cores get HT, E-cores don't
            specs["threads"] = specs["p_cores"] * 2 + specs["e_cores"]
        elif cores > 0:
            specs["threads"] = cores * 2
    elif specs.get("cores"):
        # No HT/SMT = threads == cores
        specs["threads"] = specs["cores"]
    # Socket: LGA 1700, AM4, AM5, LGA 1851
    m = re.search(r"(LGA\s*\d+|AM\d+)", details)
    if m:
        specs["socket"] = m.group(1)
    # PCIe
    m = re.search(r"PCIe\s+x(\d+)\s+([\d.]+)", details)
    if m:
        specs["pcie_lanes"] = int(m.group(1))
        specs["pcie_gen"] = float(m.group(2))
    return specs


def parse_ram_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Capacity from name: "2x16 GB", "2x8 GB"
    m = re.search(r"(\d+)\s*x\s*(\d+)\s*GB", name)
    if m:
        specs["capacity_gb"] = int(m.group(1)) * int(m.group(2))
    else:
        # Single: "16 GB", "32 GB"
        m = re.search(r"(\d+)\s*GB", name)
        if m:
            specs["capacity_gb"] = int(m.group(1))
    # DDR type + speed: "DDR4-3200", "DDR5-6000"
    m = re.search(r"(DDR\d)-(\d+)", name)
    if m:
        specs["type"] = m.group(1)
        specs["speed_mhz"] = int(m.group(2))
    # CL: "CL16", "CL30"
    m = re.search(r"CL(\d+)", name)
    if m:
        specs["cas_latency"] = int(m.group(1))
    # Color from details
    if details and details not in ("", "-"):
        specs["color"] = details.replace(", RGB", "").strip()
    if "RGB" in details or "RGB" in name:
        specs["rgb"] = True
    return specs


def parse_mobo_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Socket: AM4, AM5, LGA 1700, LGA 1851
    m = re.search(r"(AM\d+|LGA\s*\d+)", details)
    if m:
        specs["socket"] = m.group(1)
    # Form factor: ATX, Micro-ATX, Mini-ITX
    m = re.search(r"(Mini-ITX|Micro-ATX|ATX|E-ATX)", details)
    if m:
        specs["form_factor"] = m.group(1)
    # DDR type: DDR4, DDR5
    m = re.search(r"(DDR\d)", details)
    if m:
        specs["memory_type"] = m.group(1)
    # PCIe
    m = re.search(r"PCIe\s+x(\d+)\s+([\d.]+)", details)
    if m:
        specs["pcie_lanes"] = int(m.group(1))
        specs["pcie_gen"] = float(m.group(2))
    return specs


def parse_ssd_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Capacity from name: "1 TB", "500 GB", "2 TB"
    m = re.search(r"(\d+)\s*TB", name)
    if m:
        specs["capacity_gb"] = int(m.group(1)) * 1000
    else:
        m = re.search(r"(\d+)\s*GB", name)
        if m:
            specs["capacity_gb"] = int(m.group(1))
    # Form factor: M.2 2280, M.2 2230, 2.5"
    m = re.search(r"(M\.2\s*\d+|2\.5\"?)", details)
    if m:
        specs["form_factor"] = m.group(1)
    # Interface: NVMe, SATA, USB-C
    if "NVMe" in details:
        specs["interface"] = "NVMe"
    elif "SATA" in details:
        specs["interface"] = "SATA"
    elif "USB" in details:
        specs["interface"] = "USB"
    # PCIe gen
    m = re.search(r"PCIe\s+([\d.]+)", details)
    if m:
        specs["pcie_gen"] = float(m.group(1))
    return specs


def parse_psu_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Wattage: "650 W", "850 W"
    m = re.search(r"(\d+)\s*W", details)
    if m:
        specs["wattage"] = int(m.group(1))
    # Efficiency: "80 PLUS Bronze", "80 PLUS Gold"
    m = re.search(r"80\s*PLUS\s*(\w+)", details)
    if m:
        specs["efficiency"] = f"80+ {m.group(1)}"
    # Modular
    if "Modular" in details or "Modular" in name:
        specs["modular"] = True
    if "Semi" in details or "Semi" in name:
        specs["modular"] = "Semi"
    # ATX version
    m = re.search(r"ATX\s*([\d.]+)", details)
    if m:
        specs["atx_version"] = m.group(1)
    # 12V connector
    if "12V-2x6" in details or "12VHPWR" in details:
        specs["gpu_connector"] = "12V-2x6"
    return specs


def parse_monitor_specs(details: str, name: str = "") -> dict:
    specs = {}
    # Size: 27", 24.5", 31.5"
    m = re.search(r"([\d.]+)\"", details)
    if m:
        specs["size_inches"] = float(m.group(1))
    # Resolution: Full HD, QHD, 4K, WQHD
    for res, label in [("4K", "4K"), ("WQHD", "WQHD"), ("QHD", "QHD"), ("Full HD", "FHD")]:
        if res in details:
            specs["resolution"] = label
            break
    # Panel: IPS, VA, TN, OLED
    m = re.search(r"\b(IPS|VA|TN|OLED)\b", details)
    if m:
        specs["panel"] = m.group(1)
    # Refresh rate: "144 Hz", "165 Hz OC"
    m = re.search(r"(\d+)\s*Hz", details)
    if m:
        specs["refresh_rate"] = int(m.group(1))
    # OC refresh rate
    m = re.search(r"(\d+)\s*Hz\s*OC", details)
    if m:
        specs["refresh_rate_oc"] = int(m.group(1))
    return specs


# Category → parser mapping
PARSERS = {
    "gpu": parse_gpu_specs,
    "cpu": parse_cpu_specs,
    "cpu-kit": parse_cpu_specs,
    "ram": parse_ram_specs,
    "motherboard": parse_mobo_specs,
    "ssd": parse_ssd_specs,
    "psu": parse_psu_specs,
    "cooler": lambda d, n: {},  # No structured details
    "case": lambda d, n: {},
    "monitor": parse_monitor_specs,
}


def parse_specs(category: str, details: str, name: str = "") -> dict:
    """Parse details string into structured specs for a given category."""
    parser = PARSERS.get(category)
    if not parser:
        return {}
    return parser(details or "", name or "")
