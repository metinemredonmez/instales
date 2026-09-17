"""SEC Form 4 (and 4/A) — the ownership document an insider files within two business days of a transaction.

`parse_form4` turns the XML (EDGAR's `ownershipDocument`, schema X0306…X0609) into a plain dict that is stored
verbatim as the Disclosure payload: issuer, reporting owners with their relationship flags, the non-derivative and
derivative transaction tables, the footnotes. Numbers stay the strings the filing printed (`services/insiders`
turns them into Decimals); a price the filing does not state (an RSU settlement whose price cell holds only a
footnote) is None — never 0, never looked up. Holding rows (`nonDerivativeHolding`, `derivativeHolding`) are
positions, not transactions, and are left out. Optional nodes are tolerated throughout: the schema has changed
several times and older filings omit whole blocks.

Transaction codes (the meaning of a row — the product never flattens them into "buy" / "sell"):
  P open-market or private purchase   S open-market or private sale   A grant / award       M exercise or conversion
  F payment of exercise price / tax withholding by delivering shares   G gift   D disposition to the issuer
  C conversion of derivative security  X exercise of in-the-money derivative  J other (footnoted)  W will / inheritance
"""

from __future__ import annotations

from xml.etree import ElementTree

# Form 4 relationship flags → the role words stored on each transaction row (comma-joined, in this order).
ROLE_FLAGS = (("isDirector", "director"), ("isOfficer", "officer"), ("isTenPercentOwner", "ten_percent_owner"), ("isOther", "other"))
CODE_LABELS = {"P": "open-market purchase", "S": "open-market sale", "A": "grant or award", "M": "option exercise / RSU settlement",
               "F": "tax withholding", "G": "gift", "D": "disposition to issuer", "C": "conversion", "X": "exercise of derivative",
               "J": "other (see footnotes)", "W": "will or inheritance"}


def _text(el: ElementTree.Element | None, path: str) -> str | None:
    """Stripped text of `path` under `el`, None when the node is missing or empty."""
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _value(el: ElementTree.Element | None, path: str) -> str | None:
    """Most Form 4 cells are `<x><value>…</value></x>`; a cell with only a `<footnoteId/>` has no value."""
    return _text(el, f"{path}/value")


def _flag(el: ElementTree.Element | None, path: str) -> bool:
    """Relationship flags are printed as true/false or 1/0 depending on the filing agent."""
    return (_text(el, path) or "").strip().lower() in ("1", "true")


def _number(text: str | None) -> str | None:
    """A numeric cell as the filing printed it, commas removed; None when absent."""
    if text is None:
        return None
    cleaned = text.replace(",", "").strip()
    return cleaned or None


def _footnote_ids(el: ElementTree.Element) -> list[str]:
    """Every footnote a row points at, anywhere inside it (security title, price, coding …), in document order."""
    seen: list[str] = []
    for ref in el.iter("footnoteId"):
        fid = ref.get("id")
        if fid and fid not in seen:
            seen.append(fid)
    return seen


def _transaction(el: ElementTree.Element, *, derivative: bool) -> dict:
    amounts = el.find("transactionAmounts")
    row = {
        "security_title": _value(el, "securityTitle"),
        "date": _value(el, "transactionDate"),
        "deemed_execution_date": _value(el, "deemedExecutionDate"),
        "code": _text(el, "transactionCoding/transactionCode"),
        "form_type": _text(el, "transactionCoding/transactionFormType"),
        "equity_swap_involved": _flag(el, "transactionCoding/equitySwapInvolved"),
        "acquired_disposed": _value(amounts, "transactionAcquiredDisposedCode"),
        "shares": _number(_value(amounts, "transactionShares")),
        "price": _number(_value(amounts, "transactionPricePerShare")),
        "post_shares": _number(_value(el, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
        "ownership_nature": _value(el, "ownershipNature/directOrIndirectOwnership"),
        "nature_of_ownership": _value(el, "ownershipNature/natureOfOwnership"),
        "footnote_ids": _footnote_ids(el),
        "derivative": derivative,
    }
    if derivative:
        row.update({
            "exercise_price": _number(_value(el, "conversionOrExercisePrice")),
            "exercise_date": _value(el, "exerciseDate"),
            "expiration_date": _value(el, "expirationDate"),
            "underlying_title": _value(el, "underlyingSecurity/underlyingSecurityTitle"),
            "underlying_shares": _number(_value(el, "underlyingSecurity/underlyingSecurityShares")),
        })
    return row


def _owner(el: ElementTree.Element) -> dict:
    rel = el.find("reportingOwnerRelationship")
    cik = _text(el, "reportingOwnerId/rptOwnerCik")
    return {
        "cik": str(int(cik)) if cik and cik.isdigit() else cik,
        "name": _text(el, "reportingOwnerId/rptOwnerName"),
        **{role: _flag(rel, flag) for flag, role in ROLE_FLAGS},
        "officer_title": _text(rel, "officerTitle"),
        "other_text": _text(rel, "otherText"),
    }


def roles_of(*owners: dict) -> str:
    """The comma-joined role words of the parsed owner(s): "director,officer" for a CEO who also sits on the board;
    over a joint filing's owners the union — the fund is the ten-percent owner, its managing partner the director."""
    return ",".join(role for _, role in ROLE_FLAGS if any(o.get(role) for o in owners))


def primary_owner(owners: list[dict]) -> dict | None:
    """The owner a joint filing's rows are attributed to. A Form 4 names every reporting owner of one set of rows
    (a director and their family trust, a 10 % owner group of fund, general partner and the individual director)
    and lists them in whatever order the filing agent chose, so the natural person — an owner flagged director or
    officer — comes first, then the first owner with a CIK; None when no owner carries a CIK."""
    with_cik = [o for o in owners if o.get("cik")]
    people = [o for o in with_cik if o.get("director") or o.get("officer")]
    return (people or with_cik or [None])[0]


def officer_title(primary: dict, owners: list[dict]) -> str | None:
    """The officer title of the attributed owner, else the first one any owner states (a trust never has one)."""
    return primary.get("officer_title") or next((o.get("officer_title") for o in owners if o.get("officer_title")), None)


def parse_form4(xml: str) -> dict:
    """The whole ownership document as one JSON-ready dict (see the module docstring for the shape and the rules)."""
    root = ElementTree.fromstring(xml)
    if root.tag != "ownershipDocument":
        raise ValueError(f"not an ownership document: <{root.tag}>")
    issuer = root.find("issuer")
    issuer_cik = _text(issuer, "issuerCik")
    non_derivative = root.find("nonDerivativeTable")
    derivative = root.find("derivativeTable")
    return {
        "document_type": _text(root, "documentType"),
        "schema_version": _text(root, "schemaVersion"),
        "period_of_report": _text(root, "periodOfReport"),
        "date_of_original_submission": _text(root, "dateOfOriginalSubmission"),  # 4/A only: the original's filing date
        "issuer": {
            "cik": str(int(issuer_cik)) if issuer_cik and issuer_cik.isdigit() else issuer_cik,
            "name": _text(issuer, "issuerName"),
            "symbol": _text(issuer, "issuerTradingSymbol"),
        },
        "reporting_owners": [_owner(o) for o in root.findall("reportingOwner")],
        "aff_10b5_one": _flag(root, "aff10b5One") if root.find("aff10b5One") is not None else None,
        "non_derivative_transactions": [_transaction(t, derivative=False) for t in non_derivative.findall("nonDerivativeTransaction")] if non_derivative is not None else [],
        "derivative_transactions": [_transaction(t, derivative=True) for t in derivative.findall("derivativeTransaction")] if derivative is not None else [],
        "footnotes": {fn.get("id"): (fn.text or "").strip() for fn in root.iter("footnote") if fn.get("id")},
        "remarks": _text(root, "remarks"),
    }
