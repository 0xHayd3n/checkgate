"""Stateful, standard-library-only repair tasks for the broader benchmark.

The reference files and oracle strings belong to the benchmark driver only.
Only ``files`` is copied into a model-visible fixture.

Later-page pagination exception coverage was strengthened after the first broad
run began. Its frozen task definitions and primary results remain unchanged.
"""

_CACHE_BROKEN = '''from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, ttl, now):
        self.capacity = capacity
        self.ttl = ttl
        self.now = now
        self._entries = OrderedDict()

    def _purge(self, current):
        for key, (_, deadline) in list(self._entries.items()):
            if deadline < current:
                del self._entries[key]

    def get(self, key, default=None):
        self._purge(self.now())
        if key not in self._entries:
            return default
        return self._entries[key][0]

    def put(self, key, value):
        current = self.now()
        self._purge(current)
        self._entries[key] = (value, current + self.ttl)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
'''

_CACHE_FIXED = '''from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, ttl, now):
        self.capacity = capacity
        self.ttl = ttl
        self.now = now
        self._entries = OrderedDict()

    def _purge(self, current):
        for key, (_, deadline) in list(self._entries.items()):
            if deadline <= current:
                del self._entries[key]

    def get(self, key, default=None):
        self._purge(self.now())
        if key not in self._entries:
            return default
        self._entries.move_to_end(key)
        return self._entries[key][0]

    def put(self, key, value):
        current = self.now()
        self._purge(current)
        self._entries[key] = (value, current + self.ttl)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
'''

_CACHE_TESTS = '''import unittest
from ttl_cache import TTLCache


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.time = 0
        self.cache = TTLCache(2, 10, lambda: self.time)

    def test_missing_and_falsy_values(self):
        sentinel = object()
        self.assertIs(self.cache.get("missing", sentinel), sentinel)
        self.cache.put("none", None)
        self.cache.put("zero", 0)
        self.assertIsNone(self.cache.get("none", sentinel))
        self.assertEqual(self.cache.get("zero", sentinel), 0)

    def test_exact_expiry_boundary(self):
        self.cache.put("a", 1)
        self.time = 9.5
        self.assertEqual(self.cache.get("a"), 1)
        self.time = 10
        self.assertEqual(self.cache.get("a", "expired"), "expired")

    def test_read_refreshes_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.assertEqual(self.cache.get("a"), 1)
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), 1)

    def test_replacement_refreshes_recency(self):
        self.cache.put("a", 1)
        self.cache.put("b", 2)
        self.cache.put("a", 11)
        self.cache.put("c", 3)
        self.assertIsNone(self.cache.get("b"))
        self.assertEqual(self.cache.get("a"), 11)

    def test_replacement_renews_ttl(self):
        self.cache.put("a", 1)
        self.time = 6
        self.cache.put("a", 2)
        self.time = 11
        self.assertEqual(self.cache.get("a"), 2)
        self.time = 16
        self.assertIsNone(self.cache.get("a"))

    def test_expired_entries_do_not_evict_live_entries(self):
        self.cache.put("old", 1)
        self.time = 5
        self.cache.put("live", 2)
        self.time = 11
        self.cache.put("new", 3)
        self.assertEqual(self.cache.get("live"), 2)
        self.assertEqual(self.cache.get("new"), 3)
        self.assertIsNone(self.cache.get("old"))

    def test_reads_do_not_extend_ttl_or_revive_expired_value(self):
        self.cache.put("a", 1)
        self.time = 8
        self.assertEqual(self.cache.get("a"), 1)
        self.time = 11
        self.assertEqual(self.cache.get("a", "gone"), "gone")
        self.time = 12
        self.assertEqual(self.cache.get("a", "still gone"), "still gone")

    def test_capacity_one_and_hashable_keys(self):
        cache = TTLCache(1, 10, lambda: self.time)
        cache.put(("a", 1), False)
        self.assertIs(cache.get(("a", 1)), False)
        cache.put(7, "replacement")
        self.assertIsNone(cache.get(("a", 1)))
        self.assertEqual(cache.get(7), "replacement")
'''

_CACHE_ORACLE = '''import random
from ttl_cache import TTLCache


# A list-based model deliberately avoids the implementation's OrderedDict.
for capacity in (1, 2, 4):
    for ttl in (0.5, 3, 11):
        clock = [0.0]
        actual = TTLCache(capacity, ttl, lambda: clock[0])
        expected = []
        rng = random.Random(4100 + capacity * 100 + int(ttl * 10))
        sentinel = object()
        for step in range(240):
            clock[0] += rng.choice((0, 0, 0.25, ttl, ttl + 0.5))
            expected = [entry for entry in expected if clock[0] < entry[2]]
            key = rng.choice((None, "", (1, 2), 8, "other"))
            if rng.randrange(3):
                value = rng.choice((None, False, 0, "", step))
                actual.put(key, value)
                expected = [entry for entry in expected if entry[0] != key]
                expected.append((key, value, clock[0] + ttl))
                expected = expected[-capacity:]
            else:
                found = [entry for entry in expected if entry[0] == key]
                value = found[0][1] if found else sentinel
                assert actual.get(key, sentinel) == value, (capacity, ttl, step, key)
                if found:
                    expected = [entry for entry in expected if entry[0] != key] + found

# Reading at the deadline cannot extend or resurrect an entry.
clock = [2.25]
cache = TTLCache(3, 0.75, lambda: clock[0])
cache.put("edge", "old")
clock[0] = 3.0
assert cache.get("edge", "missing") == "missing"
cache.put("edge", "new")
clock[0] = 3.5
assert cache.get("edge") == "new"
clock[0] = 3.75
assert cache.get("edge", "missing") == "missing"
'''

_PAGES_BROKEN = '''def collect_pages(fetch_page, start_cursor=None):
    cursor = start_cursor
    merged = {}
    while True:
        page = fetch_page(cursor)
        if not page["items"]:
            break
        for item in page["items"]:
            merged[item["id"]] = item
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return list(merged.values())
'''

_PAGES_FIXED = '''def collect_pages(fetch_page, start_cursor=None):
    cursor = start_cursor
    visited = set()
    merged = {}
    while True:
        if cursor in visited:
            raise ValueError("Repeated cursor")
        visited.add(cursor)
        page = fetch_page(cursor)
        for item in page["items"]:
            if item["id"] not in merged:
                merged[item["id"]] = item
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return list(merged.values())
'''

_PAGES_TESTS = '''import copy
import unittest
from pagination import collect_pages


class PaginationTests(unittest.TestCase):
    def collect(self, pages, start=None):
        calls = []
        def fetch(cursor):
            calls.append(cursor)
            if len(calls) > len(pages) + 1:
                raise AssertionError("Collector fetched a cursor repeatedly")
            return pages[cursor]
        return collect_pages(fetch, start), calls

    def test_terminal_empty_page(self):
        result, calls = self.collect({None: {"items": [], "next_cursor": None}})
        self.assertEqual(result, [])
        self.assertEqual(calls, [None])

    def test_every_page_including_empty_middle_page(self):
        pages = {
            None: {"items": [{"id": 1}], "next_cursor": "empty"},
            "empty": {"items": [], "next_cursor": "last"},
            "last": {"items": [{"id": 2}], "next_cursor": None},
        }
        result, calls = self.collect(pages)
        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(calls, [None, "empty", "last"])

    def test_first_duplicate_wins_and_order_is_first_seen(self):
        pages = {
            None: {"items": [{"id": 2, "v": "first"}, {"id": 1}], "next_cursor": "p2"},
            "p2": {"items": [{"id": 2, "v": "later"}, {"id": 3}], "next_cursor": None},
        }
        result, _ = self.collect(pages)
        self.assertEqual(result, [{"id": 2, "v": "first"}, {"id": 1}, {"id": 3}])

    def test_duplicate_within_one_page(self):
        pages = {None: {"items": [{"id": "a", "v": 0}, {"id": "a", "v": 1}], "next_cursor": None}}
        result, _ = self.collect(pages)
        self.assertEqual(result, [{"id": "a", "v": 0}])

    def test_nondefault_start_and_empty_string_cursor(self):
        pages = {
            "begin": {"items": [{"id": "a"}], "next_cursor": ""},
            "": {"items": [{"id": "b"}], "next_cursor": None},
        }
        result, calls = self.collect(pages, "begin")
        self.assertEqual(result, [{"id": "a"}, {"id": "b"}])
        self.assertEqual(calls, ["begin", ""])

    def test_repeated_cursor_is_rejected_before_refetch(self):
        calls = []
        def fetch(cursor):
            calls.append(cursor)
            if len(calls) > 2:
                raise AssertionError("Repeated cursor was fetched")
            return {"items": [{"id": cursor}], "next_cursor": "loop"}
        with self.assertRaises(ValueError):
            collect_pages(fetch)
        self.assertEqual(calls, [None, "loop"])

    def test_inputs_are_unchanged_and_first_item_objects_are_returned(self):
        first = {"id": ("group", 1), "data": [1, 2]}
        pages = {None: {"items": [first], "next_cursor": None}}
        original = copy.deepcopy(pages)
        result, _ = self.collect(pages)
        self.assertEqual(pages, original)
        self.assertIs(result[0], first)

    def test_fetch_exception_propagates_without_retry(self):
        error = RuntimeError("fetch failed")
        calls = []
        def fetch(cursor):
            calls.append(cursor)
            raise error
        with self.assertRaises(RuntimeError) as caught:
            collect_pages(fetch)
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, [None])

    def test_later_fetch_exception_preserves_identity_without_retry(self):
        sentinel = RuntimeError("later-page fetch failed")
        calls = []
        def fetch(cursor):
            calls.append(cursor)
            if cursor is None:
                return {"items": [{"id": "first"}], "next_cursor": "later"}
            if cursor == "later":
                raise sentinel
            raise AssertionError("Unexpected cursor")
        with self.assertRaises(RuntimeError) as caught:
            collect_pages(fetch)
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(calls, [None, "later"])
'''

_PAGES_ORACLE = '''import copy
import random
from pagination import collect_pages


for seed in range(25):
    rng = random.Random(7700 + seed)
    cursors = ["start", "", "opaque/2", "next?x=3", "done"]
    pages = {}
    expected = []
    ids = []
    for number, cursor in enumerate(cursors):
        items = [{"id": rng.randrange(7), "revision": (seed, number, i)}
                 for i in range(rng.randrange(5))]
        for item in items:
            if item["id"] not in ids:
                ids.append(item["id"])
                expected.append(item)
        pages[cursor] = {"items": items,
                         "next_cursor": cursors[number + 1] if number + 1 < len(cursors) else None}
    original = copy.deepcopy(pages)
    calls = []
    def fetch(cursor):
        calls.append(cursor)
        assert len(calls) <= len(cursors), "unexpected repeat or retry"
        return pages[cursor]
    result = collect_pages(fetch, "start")
    assert calls == cursors, (seed, calls)
    assert result == expected, (seed, result, expected)
    assert all(actual is wanted for actual, wanted in zip(result, expected))
    assert pages == original

# A cycle through several distinct cursors must fail before the old one is fetched.
calls = []
next_by_cursor = {"a": "b", "b": "c", "c": "a"}
def fetch_cycle(cursor):
    calls.append(cursor)
    assert len(calls) <= 3, "cycle was fetched more than once"
    return {"items": [], "next_cursor": next_by_cursor[cursor]}
try:
    collect_pages(fetch_cycle, "a")
except ValueError:
    pass
else:
    raise AssertionError("Repeated cursor was accepted")
assert calls == ["a", "b", "c"]

# Falsy IDs and a terminal page that still contains data are valid.
first = {"id": None, "value": "first"}
last = {"id": "", "value": "last"}
result = collect_pages(lambda cursor: {"items": [first, {"id": None, "value": "later"}, last],
                                       "next_cursor": None})
assert result == [first, last] and result[0] is first and result[1] is last

# Post-run strengthening: errors on later pages must retain identity and cannot
# be swallowed, wrapped or retried after some data has already been collected.
sentinel = RuntimeError("later-page fetch failed")
calls = []
def fetch_later_error(cursor):
    calls.append(cursor)
    if cursor is None:
        return {"items": [{"id": "first"}], "next_cursor": "later"}
    if cursor == "later":
        raise sentinel
    raise AssertionError("Unexpected cursor: " + repr(cursor))
try:
    collect_pages(fetch_later_error)
except RuntimeError as failure:
    assert failure is sentinel, "Fetch exception was replaced"
else:
    raise AssertionError("Later-page fetch exception was swallowed")
assert calls == [None, "later"], ("Fetch was retried or skipped", calls)
'''


TASKS = [
    {
        "id": "ttl_lru_cache",
        "category": "state_and_time",
        "task": (
            "Repair TTLCache in ttl_cache.py. capacity is a positive integer, ttl is a positive "
            "number, now is an injected nondecreasing numeric clock, and keys are hashable; "
            "those inputs are already valid. put(key, value) stores or replaces a value, resets "
            "its deadline to now() + ttl, and makes it most recently used. get(key, default=None) "
            "returns default for a missing or expired key; a live hit makes the key most "
            "recently used without extending its TTL. An entry expires exactly when now() >= "
            "its deadline and an expired read removes it permanently. Remove expired entries "
            "before capacity eviction, then evict the least recently used live entry if needed. "
            "Falsy stored values and arbitrary hashable keys must work. Preserve the public API. "
            "Do not modify tests. Run the required unit and syntax checks."
        ),
        "files": {"ttl_cache.py": _CACHE_BROKEN, "test_ttl_cache.py": _CACHE_TESTS},
        "editable_files": ["ttl_cache.py"],
        "unit_modules": ["test_ttl_cache"],
        "oracle": _CACHE_ORACLE,
        "reference_files": {"ttl_cache.py": _CACHE_FIXED},
    },
    {
        "id": "cursor_pagination",
        "category": "state_and_pagination",
        "task": (
            "Repair collect_pages(fetch_page, start_cursor=None) in pagination.py. Each synchronous "
            "fetch_page(cursor) call returns a valid dict with items (a list of dicts with hashable "
            "id values) and next_cursor (a string or None). Start with start_cursor, which may be "
            "None or any string. Fetch every page, including empty pages, until a returned "
            "next_cursor is None; an empty string is a valid cursor. Return a list of the original "
            "item objects deduplicated by id in first-seen order, keeping the first item's complete "
            "data when later duplicates occur, including duplicates in one page. Never mutate input "
            "pages or items. If a cursor would be fetched for a second time, raise ValueError before "
            "that fetch. Propagate fetch exceptions unchanged without retrying. No network or async "
            "features are needed. Preserve the public API, do not modify tests, and run the required "
            "unit and syntax checks."
        ),
        "files": {"pagination.py": _PAGES_BROKEN, "test_pagination.py": _PAGES_TESTS},
        "editable_files": ["pagination.py"],
        "unit_modules": ["test_pagination"],
        "oracle": _PAGES_ORACLE,
        "reference_files": {"pagination.py": _PAGES_FIXED},
    },
]
