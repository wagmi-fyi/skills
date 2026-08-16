"""
Locate-after-fault: confirm whether a faulted QBO save actually posted.

QBO error 10000 ("severe exception" — also raised by the SDK for unparseable
or non-OK responses) and 6240 ("duplicate document number") can mean the
object WAS created server-side even though .save() raised. The publishers
previously recorded such saves as failed with external_id=NULL, so a blind
re-publish double-posted (observed in production, 2026-06). This module answers
"did it actually post?" with a three-outcome, fail-loud contract:

    FOUND        — exactly one matching object exists; link it (no re-create)
    NOT_FOUND    — a fully-enumerated search found nothing; safe to mark
                   failed/retryable (today's behavior)
    AMBIGUOUS    — more than one object matches; NEVER guess. Callers route
                   this to sync status 'verify' (see _shared/sync_status.py),
                   which no publish query selects — a human verifies in QBO.
    INCONCLUSIVE — the search itself failed or was truncated; same loud
                   'verify' handling as AMBIGUOUS.

Identification relies on an idempotency tag — "[bk:<key>]" stamped into
PrivateNote (and optionally DocNumber) at publish time. The key derives from
the stable local record id, so a retry of the same record carries the same
tag. PrivateNote is NOT queryable in QBO's query language, so the search
queries a candidate set by queryable fields (TxnDate / TotalAmt / DocNumber)
and matches the tag client-side, paging to exhaustion.

All SDK entity-class imports live HERE so publisher modules stay free of
cross-entity imports (test_qbo_publish_bundled_wire forbids e.g.
`import CreditMemo` appearing in payments.py).

This module performs READS only. It never re-saves — recovery decisions stay
with the caller (and ultimately the agent), per the skill philosophy.
"""

import re
import time
from typing import Dict, List, NamedTuple, Optional

from quickbooks.exceptions import QuickbooksException

from _shared.auth import resolve_client
from quickbooks.objects.invoice import Invoice
from quickbooks.objects.bill import Bill
from quickbooks.objects.creditmemo import CreditMemo
from quickbooks.objects.vendorcredit import VendorCredit
from quickbooks.objects.payment import Payment
from quickbooks.objects.billpayment import BillPayment
from quickbooks.objects.journalentry import JournalEntry

FOUND = 'found'
NOT_FOUND = 'not_found'
AMBIGUOUS = 'ambiguous'
INCONCLUSIVE = 'inconclusive'

_PAGE_SIZE = 100
# QBO's query index can lag a just-created object (read-after-write). One
# delayed re-read before concluding NOT_FOUND; this retries the READ only,
# never the write.
_CONSISTENCY_RETRY_DELAY = 3.0

_ENTITY_MAP = {
    'Invoice': Invoice,
    'Bill': Bill,
    'CreditMemo': CreditMemo,
    'VendorCredit': VendorCredit,
    'Payment': Payment,
    'BillPayment': BillPayment,
    'JournalEntry': JournalEntry,
}


class LocateResult(NamedTuple):
    state: str
    qbo_id: Optional[str]
    detail: str


def make_tag(key: str) -> str:
    """Deterministic idempotency tag from a stable local record id/key.

    The closing bracket matters: exact-token matching ("[bk:123]") cannot
    prefix-collide the way a bare "Settlement 123" would with "Settlement 1234".
    """
    return f"[bk:{key}]"


def fault_code(exc: Exception) -> int:
    """Extract a usable int error code from a QBO SDK exception.

    The SDK populates error_code with '' (str) or 0 when the QBO Fault
    carries no <code> (quickbooks/client.py handle_exceptions) — comparing
    raw would TypeError mid-run, so coerce defensively.
    """
    code = getattr(exc, 'error_code', 0)
    if isinstance(code, bool):
        return 0
    if isinstance(code, int):
        return code
    try:
        return int(str(code).strip() or 0)
    except (ValueError, TypeError):
        return 0


def is_post_then_fail(exc: Exception) -> bool:
    """True when the fault class means 'the object may exist server-side'.

    >= 10000: severe exception — QBO (or the SDK's response handling) faulted
    after the POST may have committed. 6140/6240: duplicate document number —
    the duplicate IS our object when the tag matches (a prior post-then-fail
    attempt). 6140 verified live against a production realm 2026-06-10
    ("QB Exception 6140: Duplicate Document Number Error"); 6240 kept as the
    documented alternate. Pre-commit validation (2000–4999) and everything
    else keep today's mark-failed behavior.
    """
    if not isinstance(exc, QuickbooksException):
        return False
    code = fault_code(exc)
    return code >= 10000 or code in (6140, 6240)


def extract_qbo_id(exc: Exception) -> Optional[str]:
    """Best-effort parse of a QBO object id out of a fault payload.

    Some 10000/6000 payloads name the created object's id. Only trust an
    unambiguous single id; anything else returns None and the tag search
    decides.
    """
    parts = [str(p) for p in (getattr(exc, 'message', ''), getattr(exc, 'detail', '')) if p]
    text = ' '.join(parts)
    ids = set(re.findall(r"\b[Ii]d\s*[=:]\s*'?(\d{1,12})'?", text))
    if len(ids) == 1:
        return ids.pop()
    return None


def _tag_matches(obj, tag: str) -> bool:
    note = getattr(obj, 'PrivateNote', None) or ''
    doc_number = getattr(obj, 'DocNumber', None) or ''
    return tag in note or tag == doc_number


def _query_all(entity_cls, where_clause: str, client, rate_limiter) -> Optional[List]:
    """Run a paged query to exhaustion. None = the query itself failed.

    Never trust a single truncated page: a just-posted object outside the
    first page would read as a false NOT_FOUND → blind retry → double-post.
    """
    results = []
    start = 1
    while True:
        try:
            rate_limiter.wait()
            page = entity_cls.where(
                where_clause,
                start_position=start,
                max_results=_PAGE_SIZE,
                qb=client,
            )
        except Exception:
            return None
        results.extend(page)
        if len(page) < _PAGE_SIZE:
            return results
        start += _PAGE_SIZE


def locate_posted_object(
    client,
    rate_limiter,
    locator: Dict,
    fault: Optional[Exception] = None,
    sleep=time.sleep,
) -> LocateResult:
    """Read QBO to determine whether a faulted save actually posted.

    locator: {'entity': 'Invoice'|'Bill'|'CreditMemo'|'VendorCredit'|
                        'Payment'|'BillPayment'|'JournalEntry',
              'tag': '[bk:<key>]',            # required — the decider
              'txn_date': 'YYYY-MM-DD',       # candidate narrowing
              'total': float,                 # optional candidate narrowing
              'doc_number': str}              # optional precise narrowing

    The queryable fields only NARROW the candidate set; the tag is always
    the decider. Returns a LocateResult — never raises.
    """
    client = resolve_client(client)  # accepts raw client or ClientHolder
    entity_cls = _ENTITY_MAP.get(locator.get('entity'))
    tag = locator.get('tag')
    if entity_cls is None or not tag:
        return LocateResult(INCONCLUSIVE, None, f"unusable locator: {locator}")

    # Primary: a fault payload sometimes names the created object's id —
    # a direct GET is read-your-write consistent, unlike the query index.
    if fault is not None:
        hint = extract_qbo_id(fault)
        if hint:
            try:
                rate_limiter.wait()
                obj = entity_cls.get(hint, qb=client)
            except Exception:
                obj = None  # 404/610 etc. — fall through to the tag search
            if obj is not None:
                if _tag_matches(obj, tag):
                    return LocateResult(FOUND, str(obj.Id),
                                        f"fault payload named id {hint}; tag matched")
                return LocateResult(AMBIGUOUS, None,
                                    f"fault payload named id {hint} but tag {tag} is not on it")

    # Secondary: tag search over a queryable candidate set.
    clauses = []
    if locator.get('doc_number'):
        doc_number = str(locator['doc_number']).replace("'", r"\'")
        clauses.append(f"DocNumber = '{doc_number}'")
    else:
        if locator.get('txn_date'):
            clauses.append(f"TxnDate = '{locator['txn_date']}'")
        if locator.get('total') is not None:
            clauses.append(f"TotalAmt = '{locator['total']}'")
    if not clauses:
        return LocateResult(INCONCLUSIVE, None, "locator has no queryable fields")
    where_clause = ' AND '.join(clauses)

    candidates = []
    for attempt in (1, 2):
        candidates = _query_all(entity_cls, where_clause, client, rate_limiter)
        if candidates is None:
            return LocateResult(INCONCLUSIVE, None,
                                f"candidate query failed: WHERE {where_clause}")
        matches = [c for c in candidates if _tag_matches(c, tag)]
        if len(matches) == 1:
            return LocateResult(FOUND, str(matches[0].Id),
                                f"single tag match among {len(candidates)} candidates")
        if len(matches) > 1:
            ids = ', '.join(str(m.Id) for m in matches)
            return LocateResult(AMBIGUOUS, None,
                                f"{len(matches)} objects carry tag {tag}: ids {ids}")
        if attempt == 1:
            sleep(_CONSISTENCY_RETRY_DELAY)  # read-after-write lag window
    return LocateResult(NOT_FOUND, None,
                        f"no tag match among {len(candidates)} candidates (2 passes)")


def confirm_payment_lines_applied(client, rate_limiter, entity: str, qbo_id: str,
                                  expected_links: set) -> bool:
    """Fresh-GET a Payment/BillPayment and verify every expected
    (TxnId, TxnType) application is persisted on its Line[].

    Used by the two-step publisher when the step-2 Line update faults: QBO
    sometimes persists the application and still returns an error (observed in
    production: a payment returned a 6000 but its invoice read Balance $0). A fresh
    read of the persisted object — not the save-response echo — is the
    evidence. Returns False on any doubt, so the caller keeps its loud
    LINE_UPDATE_FAILED path.
    """
    client = resolve_client(client)  # accepts raw client or ClientHolder
    entity_cls = _ENTITY_MAP.get(entity)
    if entity_cls is None or not expected_links or not qbo_id:
        return False
    try:
        rate_limiter.wait()
        obj = entity_cls.get(qbo_id, qb=client)
    except Exception:
        return False
    persisted = set()
    for line in (getattr(obj, 'Line', None) or []):
        linked = line.get('LinkedTxn') if isinstance(line, dict) else getattr(line, 'LinkedTxn', None)
        for lt in (linked or []):
            txn_id = lt.get('TxnId') if isinstance(lt, dict) else getattr(lt, 'TxnId', None)
            txn_type = lt.get('TxnType') if isinstance(lt, dict) else getattr(lt, 'TxnType', None)
            if txn_id is not None:
                persisted.add((str(txn_id), str(txn_type)))
    return expected_links.issubset(persisted)
