"""Pre-family SMT rule codes, still accepted as aliases of the family codes."""

# Frozen: debt fingerprints hash these codes, so existing baselines stay valid.
LEGACY_CODES: dict[str, str] = {
    "SMT101": "ARC001",
    "SMT102": "ARC002",
    "SMT103": "ARC003",
    "SMT104": "ARC004",
    "SMT105": "ARC005",
    "SMT106": "ARC006",
    "SMT107": "ARC007",
    "SMT108": "IMP001",
    "SMT109": "ARC008",
    "SMT110": "IMP002",
    "SMT111": "ARC009",
    "SMT112": "ARC010",
    "SMT301": "ARC011",
    "SMT302": "LAY001",
    "SMT305": "ARC012",
    "SMT306": "ARC013",
    "SMT307": "ARC014",
    "SMT308": "IMP003",
    "SMT309": "LAY002",
    "SMT310": "TYP001",
    "SMT311": "TYP002",
    "SMT312": "LAY003",
    "SMT313": "LAY004",
    "SMT314": "IDM001",
    "SMT315": "IDM002",
    "SMT316": "VIS001",
    "SMT317": "LAY005",
    "SMT318": "MOD001",
    "SMT319": "IMP004",
    "SMT320": "VIS002",
    "SMT321": "TYP003",
    "SMT322": "CPX001",
    "SMT323": "MOD002",
    "SMT324": "MOD003",
    "SMT327": "FWK001",
    "SMT328": "FWK002",
    "SMT329": "LAY006",
    "SMT330": "VIS003",
    "SMT331": "LAY007",
    "SMT333": "IDM003",
    "SMT334": "LAY008",
    "SMT335": "VIS004",
    "SMT336": "LAY009",
    "SMT337": "TYP004",
    "SMT338": "TYP005",
    "SMT339": "VIS005",
    "SMT340": "CPX002",
    "SMT341": "MOD004",
    "SMT343": "VIS006",
    "SMT344": "IDM004",
    "SMT345": "CPX003",
    "SMT346": "TYP006",
    "SMT347": "TYP007",
    "SMT348": "IDM005",
    "SMT349": "MOD005",
    "SMT350": "IDM006",
    "SMT351": "MOD006",
    "SMT352": "CPX004",
    "SMT353": "MOD007",
    "SMT354": "MOD008",
    "SMT355": "MOD009",
    "SMT356": "IDM007",
    "SMT357": "MOD010",
    "SMT359": "CPX005",
    "SMT360": "MOD011",
    "SMT361": "MOD012",
    "SMT362": "IDM008",
    "SMT363": "CPX006",
    "SMT364": "CPX007",
    "SMT367": "VIS007",
    "SMT368": "LAY010",
    "SMT369": "VIS008",
    "SMT370": "LAY011",
    "SMT371": "TYP008",
    "SMT401": "TST001",
    "SMT420": "TST002",
    "SMT421": "TST003",
    "SMT422": "TST004",
    "SMT423": "TST005",
    "SMT424": "TST006",
    "SMT425": "TST007",
    "SMT426": "TST008",
    "SMT427": "TST009",
    "SMT428": "TST010",
    "SMT429": "VIS009",
    "SMT430": "VIS010",
    "SMT431": "TST011",
    "SMT901": "MET001",
    "SMT902": "MET002",
    "SMT903": "MET003",
    "SMT904": "MET004",
}

_CURRENT_TO_LEGACY = {new: old for old, new in LEGACY_CODES.items()}


def matches_code_prefix(code: str, prefix: str) -> bool:
    """Whether ``prefix`` selects ``code`` directly or through its SMT code."""
    return code.startswith(prefix) or legacy_code(code).startswith(prefix)


def legacy_prefix_codes(prefix: str) -> tuple[str, ...]:
    """Family codes whose SMT code starts with ``prefix``, like ``SMT3``."""
    wanted = prefix.strip().upper()
    if not wanted.startswith("SMT"):
        return ()
    return tuple(new for old, new in LEGACY_CODES.items() if old.startswith(wanted))


def legacy_code(code: str) -> str:
    """The SMT code a family code replaced; codes without one are returned unchanged."""
    return _CURRENT_TO_LEGACY.get(code, code)
