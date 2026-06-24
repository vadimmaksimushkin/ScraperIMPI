from bisect import bisect_right
from typing import Any
from datetime import datetime, date, timedelta
import base64
import hashlib
import struct
import copy
import orjson


def merge_two_lists(results: list[int], new_results: list[int]) -> list[int]:
    # two sorted lists of ints need to be merged
    # memory and compute efficiency are expected
    # no sorting allowed

    # assuming the lenght of second list is 15_000 entries and first list is
    # 60_000 entries.
    #
    # new_results overlaps the tail of results and runs past it. everything in
    # new_results that is <= results[-1] is already in results, so the only
    # genuinely-new part is the suffix beyond results[-1].
    if not new_results:
        return results

    # binary-search the boundary instead of scanning the overlap: find the
    # first element of new_results strictly greater than results[-1].
    i = bisect_right(new_results, results[-1]) if results else 0

    # merge two lists (in place; no copy of the 60k results)
    results.extend(new_results[i:])
    return results

lst1 = [0,1,2,3,4,5]
lst2 = [4,5,6,7,8,9]
correct_result = [0,1,2,3,4,5,6,7,8,9]
assert merge_two_lists(lst1, lst2) == correct_result

# --- second (main) task ---
# merge two lists of dicts with the schema below
dct_example_form: dict[str, Any] = {
    'id': 1234,
    'date': datetime.today().date() - timedelta(days=2),
    'data': "Some data in this dictionaty",
}


lst1_v2: list[dict[str, Any]] = [
    {
        "id": 6,
        "date": datetime.today().date() - timedelta(days=0),
        "data": "Some data for dictionary 1",
    },
    {
        "id": 5,
        "date": datetime.today().date() - timedelta(days=0),
        "data": "Some data for dictionary 2",
    },
    {
        "id": 10,
        "date": datetime.today().date() - timedelta(days=1),
        "data": "Some data for dictionary 3",
    },
    {
        "id": 1111,
        "date": datetime.today().date() - timedelta(days=1),
        "data": "Some data for dictionary 4",
    },
    {
        "id": 112,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 5",
    },
    {
        "id": 991,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 6",
    },
]
lst2_v2: list[dict[str, Any]] = [
    {
        "id": 112,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 5",
    },
    {
        "id": 991,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 6",
    },
    {
        "id": 123,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 7",
    },
    {
        "id": 456,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 8",
    },
    {
        "id": 76,
        "date": datetime.today().date() - timedelta(days=3),
        "data": "Some data for dictionary 9",
    },
    {
        "id": 54,
        "date": datetime.today().date() - timedelta(days=4),
        "data": "Some data for dictionary 10",
    },
]

correct_result_v2: list[dict[str, Any]] = [
        {
        "id": 6,
        "date": datetime.today().date() - timedelta(days=0),
        "data": "Some data for dictionary 1",
    },
    {
        "id": 5,
        "date": datetime.today().date() - timedelta(days=0),
        "data": "Some data for dictionary 2",
    },
    {
        "id": 10,
        "date": datetime.today().date() - timedelta(days=1),
        "data": "Some data for dictionary 3",
    },
    {
        "id": 1111,
        "date": datetime.today().date() - timedelta(days=1),
        "data": "Some data for dictionary 4",
    },
    {
        "id": 112,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 5",
    },
    {
        "id": 991,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 6",
    },
    {
        "id": 123,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 7",
    },
    {
        "id": 456,
        "date": datetime.today().date() - timedelta(days=2),
        "data": "Some data for dictionary 8",
    },
    {
        "id": 76,
        "date": datetime.today().date() - timedelta(days=3),
        "data": "Some data for dictionary 9",
    },
    {
        "id": 54,
        "date": datetime.today().date() - timedelta(days=4),
        "data": "Some data for dictionary 10",
    },
]

def merge_v2(
    results: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # results have lenght of ~200K elements
    # new_results have lenght 15_000 element
    # merge two list where they overlap removing duplicates
    # lists are sorted by date from newer to older
    # lists overlap only on last date

    # lst1 last date example [1,2,3]
    # lst2 first date example [1,2,3,4,5,6]
    # tail of lst1 [..., 1,2,3]
    # head of lst2 [1,2,3,4,5,6]

    if not new_results:
        return results
    if not results:
        results.extend(new_results)
        return results

    # the only shared date is the tail date of results (oldest in results,
    # since sorted newer->older). collect the ids already present on that date
    # by scanning backward over just that block -- never touches the 200K bulk.
    overlap_date = results[-1]["date"]
    seen_ids: set[Any] = set()
    k = len(results) - 1
    while k >= 0 and results[k]["date"] == overlap_date:
        seen_ids.add(results[k]["id"])
        k -= 1

    # walk the head of new_results: on the overlap date keep only ids we
    # haven't seen, then append everything from the first older date onward.
    i = 0
    n = len(new_results)
    while i < n and new_results[i]["date"] == overlap_date:
        if new_results[i]["id"] not in seen_ids:
            results.append(new_results[i])
        i += 1

    # merge two lists (in place; no copy of the 200K results)
    results.extend(new_results[i:])
    return results

# snapshot the inputs before merge_v2 mutates lst1_v2 in place, so merge_v3
# is exercised from an identical, independent starting point.
lst1_v3 = copy.deepcopy(lst1_v2)
lst2_v3 = copy.deepcopy(lst2_v2)

assert merge_v2(lst1_v2, lst2_v2) == correct_result_v2


def merge_v3(
    results: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # same contract as merge_v2, but instead of a set we exploit the stable
    # sort: new_results' head replays results' last-day tail in the SAME order,
    # so the duplicates form a contiguous prefix of new_results that mirrors
    # the tail of results. we match that pattern and append whatever is left.
    #
    # trade-off vs merge_v2: O(1) extra memory (no set), and we compare at most
    # L = results' last-day length. but correctness relies on both lists
    # ordering that shared day identically -- if the source can reorder records
    # within a day, use merge_v2 (which dedups by id regardless of order).
    if not new_results:
        return results
    if not results:
        results.extend(new_results)
        return results

    # L = length of results' last-day block (oldest date) = the overlap window.
    overlap_date = results[-1]["date"]
    L = 0
    k = len(results) - 1
    while k >= 0 and results[k]["date"] == overlap_date:
        L += 1
        k -= 1
    start = len(results) - L  # index where the last-day block begins

    # walk the head of new_results against the tail of results (equal length),
    # counting how many leading records coincide. stop at the first mismatch
    # or once the whole results tail is matched -- everything after is new.
    overlap = 0
    while (
        overlap < L
        and overlap < len(new_results)
        and new_results[overlap]["id"] == results[start + overlap]["id"]
    ):
        overlap += 1

    # merge two lists (in place; no copy of the 200K results)
    results.extend(new_results[overlap:])
    return results


assert merge_v3(lst1_v3, lst2_v3) == correct_result_v2



query_params: dict[str, Any] = {
    'integer': 123,
    'float': 123.45,
    'string': "some kind of string",
    "array_of_ints": [1,2,3],
    "array_of_dicts": [
        {
            'integer': 1,
            'string': 'Hello',
        },
        {
            'integer': 0,
            'string': 'World!',
        },
    ],
}
seen_ids: list[int] = [12, 34, 56, 987]
stop_date: date = datetime.now().date() - timedelta(days=100)


def _b64url_decode(s: str) -> bytes:
    # urlsafe_b64decode needs the padding back; re-add it before decoding.
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_CHECKSUM_LEN = 16   # bytes: FIRST slice -- truncated sha-256
_DATE_LEN = 4        # bytes: LAST slice  -- date.toordinal() as uint32


def hash_cursor(params: dict[str, Any], seen: list[int], stop_date: date) -> bytes:
    # stable fingerprint binding the cursor to its query. OPT_SORT_KEYS sorts
    # dict keys recursively so {'a':1,'b':2} and {'b':2,'a':1} hash identically;
    # orjson renders `date` as an ISO string. seen/stop_date are folded in too
    # so a corrupted token fails the check, not just a changed query.
    payload = orjson.dumps(
        {"params": params, "seen": seen, "stop_date": stop_date},
        option=orjson.OPT_SORT_KEYS,
    )
    # 128 bits is ample for a non-adversarial consistency check; keep it short.
    return hashlib.sha256(payload).digest()[:_CHECKSUM_LEN]


def assemble_cursor(check_sum: bytes, seen: list[int], stop_date: date) -> str:
    # byte-pack, base64url'd once: [ checksum | seen... | date ].
    # checksum is fixed-width up FRONT, date fixed-width at the END, so the
    # variable-length seen block sits in the MIDDLE and is recovered by slicing
    # both ends off. ids are packed big-endian uint32 (must fit in 32 bits).
    seen_bytes = b"".join(struct.pack(">I", i) for i in seen)
    buf = check_sum + seen_bytes + struct.pack(">I", stop_date.toordinal())
    return base64.urlsafe_b64encode(buf).decode().rstrip("=")


def cursor_verification(params: dict[str, Any], cursor: str) -> bool:
    # peel the base64 once, slice the raw bytes by fixed offsets, then re-hash
    # the *incoming* params against the cursor's own seen/date. any malformed
    # token (bad base64, wrong length, bad ordinal) is just "not a valid cursor".
    try:
        raw = _b64url_decode(cursor)
        seen_len = len(raw) - _CHECKSUM_LEN - _DATE_LEN
        if seen_len < 0 or seen_len % 4 != 0:
            return False
        check_sum = raw[:_CHECKSUM_LEN]
        seen = list(struct.unpack(f">{seen_len // 4}I", raw[_CHECKSUM_LEN:-_DATE_LEN]))
        stop_date = date.fromordinal(int.from_bytes(raw[-_DATE_LEN:], "big"))
    except (ValueError, struct.error, OverflowError):
        return False
    # matches only if the query is unchanged and the token wasn't altered.
    return check_sum == hash_cursor(params, seen, stop_date)


# round-trip + binding checks
_cs = hash_cursor(query_params, seen_ids, stop_date)
_cursor = assemble_cursor(_cs, seen_ids, stop_date)
assert cursor_verification(query_params, _cursor) is True
# same params in a different key order still verify (sorted hashing)
assert cursor_verification(dict(reversed(query_params.items())), _cursor) is True
# a changed query is rejected
assert cursor_verification({**query_params, "integer": 999}, _cursor) is False
# a token whose date was swapped without recomputing the checksum is rejected
_forged = assemble_cursor(_cs, seen_ids, stop_date - timedelta(days=1))
assert cursor_verification(query_params, _forged) is False
# an empty seen list (no overlap ids) round-trips fine
_empty = assemble_cursor(hash_cursor(query_params, [], stop_date), [], stop_date)
assert cursor_verification(query_params, _empty) is True
print(f"cursor ({len(_cursor)} chars): {_cursor}")